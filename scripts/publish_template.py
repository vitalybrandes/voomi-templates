#!/usr/bin/env python3
"""Validate and publish immutable Schema v2/v3 template ZIP releases."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from common import ROOT, VoomiError, github_repo, load_json, parse_semver, sha256_file, semver_key
from validate_template import validate

INCOMING = ROOT / "incoming"
INDEX = ROOT / "release-index.json"


def run(args: list[str], *, capture: bool = True) -> str:
    try:
        result = subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=capture)
        return result.stdout.strip() if capture else ""
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise VoomiError(f"Command failed ({' '.join(args[:3])}): {detail}") from exc
    except FileNotFoundError as exc:
        raise VoomiError(f"Required command not found: {args[0]}") from exc


def discover() -> list[Path]:
    supplied = os.getenv("INPUT_ZIP_PATH", "").strip()
    if supplied:
        path = (ROOT / supplied).resolve()
        if not path.is_relative_to(INCOMING.resolve()) or not path.is_file() or path.suffix.lower() != ".zip":
            raise VoomiError("workflow_dispatch zip_path must name an existing .zip file under incoming/")
        return [path]
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if event_path and Path(event_path).exists():
        try:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
            changed, removed = set(), set()
            for commit in payload.get("commits", []):
                changed.update(commit.get("added", []))
                changed.update(commit.get("modified", []))
                removed.update(commit.get("removed", []))
            zip_changes = {item for item in changed if item.startswith("incoming/") and item.lower().endswith(".zip")}
            removed_zips = {item for item in removed if item.startswith("incoming/") and item.lower().endswith(".zip")}
            selected = [(ROOT / item).resolve() for item in zip_changes]
            selected = [item for item in selected if item.is_relative_to(INCOMING.resolve()) and item.is_file()]
            if zip_changes or removed_zips:
                return sorted(selected)
        except (OSError, json.JSONDecodeError):
            pass
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        return []
    return sorted(path for path in INCOMING.rglob("*.zip") if path.is_file())


def release_view(repo: str, tag: str) -> dict | None:
    try:
        return json.loads(run(["gh", "release", "view", tag, "--repo", repo, "--json", "assets,isDraft,isPrerelease"]))
    except VoomiError as exc:
        if "404" in str(exc).lower() or "not found" in str(exc).lower():
            return None
        raise


def verify_release(repo: str, tag: str, asset_name: str, expected_sha: str, expected_size: int) -> None:
    for attempt in range(6):
        try:
            release = json.loads(run(["gh", "release", "view", tag, "--repo", repo, "--json", "assets,isDraft,isPrerelease"]))
            if release.get("isDraft") or release.get("isPrerelease"):
                raise VoomiError("Release must be public and non-prerelease")
            asset = next((item for item in release.get("assets", []) if item.get("name") == asset_name), None)
            if not asset or asset.get("size") != expected_size:
                raise VoomiError("Release ZIP asset is missing or has an unexpected size")
            with tempfile.TemporaryDirectory(prefix="voomi-release-check-") as temp:
                run(["gh", "release", "download", tag, "--repo", repo, "--pattern", asset_name, "--dir", temp])
                if sha256_file(Path(temp) / asset_name) != expected_sha:
                    raise VoomiError("Uploaded release asset checksum does not match source package")
            return
        except VoomiError:
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)


def process(path: Path, repo: str, dry_run: bool = False, report: dict | None = None) -> dict:
    report = report or validate(path)
    with zipfile.ZipFile(path) as archive:
        meta = json.loads(archive.read("template.json"))
    index = load_json(INDEX) if INDEX.exists() else {"releases": []}
    digest, size = report["sha256"], report["sizeBytes"]
    same = next((item for item in index.get("releases", []) if item.get("templateId") == meta.get("templateId") and item.get("templateVersion") == meta.get("templateVersion")), None)
    if same and same.get("sha256") != digest:
        raise VoomiError("This templateId and templateVersion are already published with different content")
    template_id, version = meta["templateId"], meta["templateVersion"]
    parse_semver(version)
    prior_dates = [
        item.get("addedAt")
        for item in index.get("releases", [])
        if item.get("templateId") == template_id and item.get("addedAt")
    ]
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    added_at = min(prior_dates) if prior_dates else now
    updated_at = same.get("updatedAt", now) if same else now
    tag = f"template-{template_id}-v{version}"
    asset_name = "template.zip"
    record = {
        "templateId": template_id, "templateVersion": version, "schemaVersion": meta["schemaVersion"],
        "name": meta["name"], "description": meta["description"],
        "categories": meta.get("categories", []), "tags": meta.get("tags", []),
        "durationMs": meta["durationMs"], "requiredFeatures": meta.get("requiredFeatures", []),
        "addedAt": added_at, "updatedAt": updated_at,
        "tag": tag, "assetName": asset_name, "sha256": digest, "fileSize": size,
        "draft": False, "prerelease": False,
    }
    if "engine" in meta:
        record["engine"] = meta["engine"]
    if dry_run:
        return {"path": str(path.relative_to(ROOT)), "action": "would-publish", **record}
    existing = release_view(repo, tag)
    if existing:
        names = {item.get("name") for item in existing.get("assets", [])}
        if asset_name not in names:
            raise VoomiError("Immutable release tag exists without template.zip; refusing to modify it")
        verify_release(repo, tag, asset_name, digest, size)
    else:
        description = meta["description"]
        with tempfile.TemporaryDirectory(prefix="voomi-release-") as temp:
            release_dir = Path(temp)
            package_path = release_dir / asset_name
            sidecar_path = release_dir / f"{tag}.metadata.json"
            notes_path = release_dir / "release-notes.md"
            shutil.copyfile(path, package_path)
            sidecar_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            notes_path.write_text(f"{description}\n\nTemplate ID: `{template_id}`\nVersion: `{version}`\nSHA-256: `{digest}`\n", encoding="utf-8")
            run(["gh", "release", "create", tag, str(package_path), str(sidecar_path), "--repo", repo, "--title", f"{meta['name']} v{version}", "--notes-file", str(notes_path)])
        verify_release(repo, tag, asset_name, digest, size)
    record["previewUrl"] = f"https://raw.githubusercontent.com/{repo}/main/previews/{template_id}/{version}/preview.jpg"
    index["releases"] = [item for item in index.get("releases", []) if not (item.get("templateId") == template_id and item.get("templateVersion") == version)] + [record]
    index["releases"].sort(key=lambda item: (item["templateId"], semver_key(item["templateVersion"])))
    INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    preview_dir = ROOT / "previews" / template_id / version
    preview_dir.mkdir(parents=True, exist_ok=True)
    from common import extract_member
    extract_member(path, "preview.jpg", preview_dir / "preview.jpg")
    return {"path": str(path.relative_to(ROOT)), "action": "published-or-verified", **record}


def validate_batch(packages: list[Path]) -> dict[Path, dict]:
    """Validate each intake ZIP once before any release is created."""
    failures = []
    reports = {}
    for package in packages:
        try:
            reports[package] = validate(package)
        except Exception as exc:
            failures.append(f"{package.name}: {exc}")
    if failures:
        raise VoomiError("Submission batch rejected before publishing:\n- " + "\n- ".join(failures))
    return reports


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    try:
        packages = discover()
        if not packages:
            report = {"publishedOrSkipped": [], "failures": [], "message": "No new template ZIP packages found."}
            if not dry_run:
                Path("publish-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                Path("processed-packages.txt").write_text("", encoding="utf-8")
            return 0
        validation_reports = validate_batch(packages)
        successes, failures, processed = [], [], []
        for package in packages:
            try:
                successes.append(process(package, github_repo(), dry_run, validation_reports[package]))
                if not dry_run:
                    processed.append(str(package.relative_to(ROOT)))
            except Exception as exc:
                failures.append({"path": str(package.relative_to(ROOT)), "error": str(exc)})
                print(f"ERROR: {package}: {exc}", file=sys.stderr)
        if not dry_run:
            Path("publish-report.json").write_text(json.dumps({"publishedOrSkipped": successes, "failures": failures}, indent=2) + "\n", encoding="utf-8")
            Path("processed-packages.txt").write_text("\n".join(processed) + ("\n" if processed else ""), encoding="utf-8")
        return 0
    except VoomiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
