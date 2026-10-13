#!/usr/bin/env python3
"""Validate, release, and stage catalog data for incoming Voomi ZIP packages."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from common import ROOT, VoomiError, canonical_template_id, github_repo, load_json, parse_semver, sha256_file
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
            files = set()
            for commit in payload.get("commits", []):
                files.update(commit.get("added", []))
                files.update(commit.get("modified", []))
            selected = [(ROOT / f).resolve() for f in files if f.startswith("incoming/") and f.lower().endswith(".zip")]
            selected = [p for p in selected if p.is_relative_to(INCOMING.resolve()) and p.is_file()]
            if selected:
                return sorted(selected)
        except (OSError, json.JSONDecodeError):
            pass
    return sorted(p for p in INCOMING.rglob("*.zip") if p.is_file())


def release_asset(tag: str, asset: str) -> str | None:
    try:
        value = json.loads(run(["gh", "release", "view", tag, "--repo", github_repo(), "--json", "assets"]))
        return asset if any(item.get("name") == asset for item in value.get("assets", [])) else None
    except VoomiError:
        return None


def release_view(repo: str, tag: str) -> dict | None:
    try:
        return json.loads(run(["gh", "release", "view", tag, "--repo", repo, "--json", "assets,isDraft,isPrerelease"]))
    except VoomiError as exc:
        message = str(exc).lower()
        if "404" in message or "not found" in message:
            return None
        raise


def verify_release(repo: str, tag: str, asset_name: str, expected_sha: str, expected_size: int) -> None:
    for attempt in range(6):
        try:
            value = run(["gh", "release", "view", tag, "--repo", repo, "--json", "assets,isDraft,isPrerelease"])
            release = json.loads(value)
            if release.get("isDraft") or release.get("isPrerelease"):
                raise VoomiError("Release must be public and non-prerelease")
            asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name), None)
            if not asset:
                raise VoomiError(f"Release asset not visible yet: {asset_name}")
            if asset.get("size") != expected_size:
                raise VoomiError("Uploaded release asset size does not match source package")
            # Fetch uploaded bytes through gh, then hash exactly what GitHub serves.
            with tempfile.TemporaryDirectory(prefix="voomi-release-check-") as temp:
                run(["gh", "release", "download", tag, "--repo", repo, "--pattern", asset_name, "--dir", temp])
                downloaded = Path(temp) / asset_name
                if not downloaded.exists() or sha256_file(downloaded) != expected_sha:
                    raise VoomiError("Uploaded release asset checksum does not match source package")
            return
        except VoomiError:
            if attempt == 5:
                raise
            time.sleep(2 ** attempt)


def process(path: Path, repo: str, dry_run: bool) -> dict:
    report = validate(path)
    with __import__("zipfile").ZipFile(path) as zf:
        meta = json.loads(zf.read("template.json"))
    source_id, version = meta["templateId"], meta["templateVersion"]
    tid = canonical_template_id(source_id)
    parse_semver(version)
    expected_name = f"{tid}-v{version}.zip"
    accepted_names = {expected_name, f"{source_id}_v{version}.zip", f"{source_id}-v{version}.zip"}
    if path.name not in accepted_names:
        raise VoomiError(f"Package must be named {expected_name} (uppercase and underscores in the metadata ID are accepted and normalized)")
    tag = f"template-{tid}-v{version}"
    digest, size = report["sha256"], report["sizeBytes"]
    if dry_run:
        print(json.dumps({"action":"would-publish","path":str(path.relative_to(ROOT)),"tag":tag,"sha256":digest,"sizeBytes":size}, indent=2))
        return {"path":str(path.relative_to(ROOT)), "metadata":meta, "tag":tag, "assetName":expected_name,"sha256":digest,"sizeBytes":size}
    repo_index = load_json(INDEX) if INDEX.exists() else {"releases": []}
    same = next((x for x in repo_index["releases"] if x["templateId"] == tid and x["templateVersion"] == version), None)
    existing = release_view(repo, tag)
    if same:
        if same["sha256"] != digest:
            raise VoomiError("Release tag already exists with a different SHA-256")
        if existing and any(a.get("name") == expected_name for a in existing.get("assets", [])):
            verify_release(repo, tag, expected_name, digest, size)
            # Continue below to refresh index/previews and let the workflow commit
            # generated state before it removes the idempotent intake ZIP.
    # Check GitHub before attempting create; never overwrite immutable tags/assets.
    if existing:
        names = {a.get("name") for a in existing.get("assets", [])}
        sidecar_name = f"{tag}.metadata.json"
        if expected_name in names and sidecar_name in names:
            verify_release(repo, tag, expected_name, digest, size)
        elif expected_name not in names and sidecar_name not in names:
            raise VoomiError("Release tag already exists without the expected package; refusing to mutate it")
        else:
            raise VoomiError("Release has an incomplete asset set; refusing to mutate immutable release")
    else:
        notes = f"{meta['description']}\n\nTemplate ID: `{tid}`\nVersion: `{version}`\nMinimum app version: `{meta['minimumAppVersion']}`\nSHA-256: `{digest}`"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as f:
            f.write(notes)
            notes_path = f.name
        sidecar = {"templateId":tid,"templateVersion":version,"name":meta["name"],"description":meta["description"],"category":meta["category"],"categories":meta["categories"],"tags":meta["tags"],"durationMs":meta["durationMs"],"mediaSlots":meta["mediaSlots"],"minimumAppVersion":meta["minimumAppVersion"],"tag":tag,"assetName":expected_name,"sha256":digest,"sizeBytes":size}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)
            f.write("\n")
            sidecar_path = f.name
        try:
            run(["gh", "release", "create", tag, str(path), sidecar_path, "--repo", repo, "--title", f"{meta['name']} v{version}", "--notes-file", notes_path])
        finally:
            Path(notes_path).unlink(missing_ok=True)
            Path(sidecar_path).unlink(missing_ok=True)
        verify_release(repo, tag, expected_name, digest, size)
    item = {"templateId":tid,"templateVersion":version,"name":meta["name"],"description":meta["description"],"category":meta["category"],"categories":meta["categories"],"tags":meta["tags"],"durationMs":meta["durationMs"],"mediaSlots":meta["mediaSlots"],"minimumAppVersion":meta["minimumAppVersion"],"tag":tag,"assetName":expected_name,"sha256":digest,"sizeBytes":size,"draft":False,"prerelease":False}
    repo_index["releases"] = [x for x in repo_index["releases"] if not (x["templateId"] == tid and x["templateVersion"] == version)] + [item]
    repo_index["releases"].sort(key=lambda x:(x["templateId"], __import__("common").semver_key(x["templateVersion"])))
    INDEX.write_text(json.dumps(repo_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    preview_dir = ROOT / "previews" / tid / version
    preview_dir.mkdir(parents=True, exist_ok=True)
    from common import extract_member
    extract_member(path, "preview/poster.jpg", preview_dir / "poster.jpg")
    extract_member(path, "preview/thumbnail.jpg", preview_dir / "thumbnail.jpg")
    return {"path":str(path.relative_to(ROOT)), "metadata":meta, "tag":tag, "assetName":expected_name,"sha256":digest,"sizeBytes":size}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    try:
        packages = discover()
        if not packages:
            if dry_run:
                print("No ZIP packages found; nothing to do")
                return 0
            raise VoomiError("No ZIP packages found to publish")
        repo = github_repo()
        successes = []
        failures = []
        committed_paths: list[str] = []
        for package in packages:
            try:
                successes.append(process(package, repo, dry_run))
                if not dry_run:
                    committed_paths.append(str(package.relative_to(ROOT)))
            except Exception as exc:
                failures.append({"path":str(package.relative_to(ROOT)),"error":str(exc)})
                print(f"ERROR: {package}: {exc}", file=sys.stderr)
        report = {"publishedOrSkipped":successes,"failures":failures}
        Path("publish-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        # The workflow removes inputs only after validation, release verification,
        # catalog verification, and its final repository commit are ready.
        Path("processed-packages.txt").write_text("\n".join(committed_paths) + ("\n" if committed_paths else ""), encoding="utf-8")
        return 1 if failures else 0
    except VoomiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        Path("publish-report.json").write_text(json.dumps({"publishedOrSkipped":[],"failures":[{"error":str(exc)}]}, indent=2) + "\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
