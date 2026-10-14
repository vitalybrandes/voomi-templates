#!/usr/bin/env python3
"""Validate, release, and stage catalog data for incoming Voomi ZIP packages."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from common import ROOT, VoomiError, canonical_template_id, github_repo, load_json, parse_semver, safe_zip_entries, sha256_file
from validate_template import is_app_native, is_legacy_voomi, validate

INCOMING = ROOT / "incoming"
INDEX = ROOT / "release-index.json"
CATALOG = ROOT / "catalog.json"


def ensure_guid(path: Path) -> str:
    """Write a stable UUID into template.json, preserving the logical template key."""
    safe_zip_entries(path)
    with zipfile.ZipFile(path) as zf:
        meta = json.loads(zf.read("template.json"))
        old_legacy_id = meta.get("id") if is_legacy_voomi(meta) else None
    logical_id = meta.get("templateId", old_legacy_id or meta.get("id"))
    tid = canonical_template_id(logical_id)
    current = meta.get("id")
    if isinstance(current, str):
        try:
            return str(uuid.UUID(current))
        except ValueError:
            pass
    stable = None
    if INDEX.exists():
        for record in load_json(INDEX).get("releases", []):
            if canonical_template_id(record.get("sourceId", record.get("templateId", ""))) == tid and record.get("id"):
                stable = record["id"]
                break
    if stable is None and CATALOG.exists():
        for item in load_json(CATALOG).get("templates", []):
            if canonical_template_id(item.get("sourceId", item.get("id", ""))) == tid:
                try:
                    stable = str(uuid.UUID(item["id"]))
                except (KeyError, ValueError):
                    pass
                if stable:
                    break
    guid = str(uuid.UUID(stable)) if stable else str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{github_repo()}/templates/{tid}"))
    if old_legacy_id:
        meta["templateId"] = old_legacy_id
    meta["id"] = guid
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp_path, "w") as dst:
            for info in src.infolist():
                data = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8") + b"\n" if info.filename.casefold() == "template.json" else src.read(info.filename)
                dst.writestr(info, data)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return guid


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
            changed: set[str] = set()
            removed: set[str] = set()
            for commit in payload.get("commits", []):
                changed.update(commit.get("added", []))
                changed.update(commit.get("modified", []))
                removed.update(commit.get("removed", []))
            zip_changes = {f for f in changed if f.startswith("incoming/") and f.lower().endswith(".zip")}
            removed_zips = {f for f in removed if f.startswith("incoming/") and f.lower().endswith(".zip")}
            selected = [(ROOT / f).resolve() for f in zip_changes]
            selected = [p for p in selected if p.is_relative_to(INCOMING.resolve()) and p.is_file()]
            if zip_changes or removed_zips:
                # A deletion-only push (including the bot cleanup commit) must not
                # fall back to retrying unrelated failed packages in incoming/.
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


def latest_published_version(repo: str, tid: str, guid: str, index: dict) -> tuple | None:
    """Find the highest version for this UUID in the index and existing releases."""
    versions = [parse_semver(item["templateVersion"]) for item in index.get("releases", [])
               if item.get("id") == guid]
    try:
        raw = run(["gh", "release", "list", "--repo", repo, "--limit", "1000",
                   "--json", "tagName,isDraft,isPrerelease"])
        releases = json.loads(raw)
    except (VoomiError, json.JSONDecodeError) as exc:
        raise VoomiError(f"Cannot check existing release versions: {exc}") from exc
    prefix = f"template-{tid}-v"
    for release in releases:
        tag = release.get("tagName", "")
        if release.get("isDraft") or release.get("isPrerelease") or not tag.startswith(prefix):
            continue
        version = tag[len(prefix):]
        try:
            version_key = parse_semver(version)
        except VoomiError:
            continue
        view = release_view(repo, tag)
        if not view:
            continue
        metadata_name = f"{tag}.metadata.json"
        if not any(asset.get("name") == metadata_name for asset in view.get("assets", [])):
            continue
        with tempfile.TemporaryDirectory(prefix="voomi-version-check-") as temp:
            try:
                run(["gh", "release", "download", tag, "--repo", repo,
                     "--pattern", metadata_name, "--dir", temp])
                sidecar = json.loads((Path(temp) / metadata_name).read_text(encoding="utf-8"))
            except (VoomiError, OSError, json.JSONDecodeError):
                continue
        sidecar_id = sidecar.get("id")
        try:
            sidecar_id = str(uuid.UUID(sidecar_id)) if sidecar_id else str(uuid.uuid5(
                uuid.NAMESPACE_URL, f"https://github.com/{repo}/templates/{tid}"))
        except (ValueError, TypeError, AttributeError):
            continue
        if sidecar_id == guid:
            versions.append(version_key)
    return max(versions) if versions else None


def should_publish_version(candidate: str, current: tuple | None) -> bool:
    """Publish only when candidate SemVer is strictly newer than current."""
    return current is None or parse_semver(candidate) > current


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
    guid = ensure_guid(path)
    report = validate(path)
    with __import__("zipfile").ZipFile(path) as zf:
        meta = json.loads(zf.read("template.json"))
    native = is_app_native(meta)
    legacy = is_legacy_voomi(meta)
    if legacy:
        legacy_slots = []
        for slot in meta["mediaSlots"]:
            raw_type = slot["type"]
            slot_type = {"image_or_video": "imageOrVideo", "photo_or_video": "imageOrVideo"}.get(raw_type, raw_type)
            legacy_slots.append({"id": slot["id"], "type": slot_type, "required": slot.get("required", True) is not False})
        catalog_meta = {
            "templateId": meta.get("templateId", meta["id"]),
            "templateVersion": meta["version"],
            "name": meta["name"],
            "description": meta.get("description") or meta["name"],
            "category": meta.get("category") or "fun",
            "categories": meta.get("categories") or [meta.get("category") or "fun"],
            "tags": meta.get("tags") or [],
            "durationMs": round(meta["durationSeconds"] * 1000),
            "mediaSlots": legacy_slots,
            "minimumAppVersion": meta.get("minimumAppVersion") or "1.0.0",
            "preview": {"poster": meta["preview"]},
        }
    else:
        catalog_meta = meta
    source_id, version = catalog_meta["templateId"], catalog_meta["templateVersion"]
    tid = canonical_template_id(source_id)
    semver = parse_semver(version)
    if semver[3]:
        raise VoomiError("Prerelease template versions cannot be published to the public catalog")
    expected_name = f"{tid}-v{version}.zip"
    accepted_names = {expected_name, f"{source_id}_v{version}.zip", f"{source_id}-v{version}.zip"}
    if not native and not legacy and path.name not in accepted_names:
        raise VoomiError(f"Package must be named {expected_name} (uppercase and underscores in the metadata ID are accepted and normalized)")
    tag = f"template-{tid}-v{version}"
    digest, size = report["sha256"], report["sizeBytes"]
    if dry_run:
        print(json.dumps({"action":"would-publish","path":str(path.relative_to(ROOT)),"tag":tag,"sha256":digest,"sizeBytes":size}, indent=2))
        return {"path":str(path.relative_to(ROOT)), "metadata":catalog_meta, "tag":tag, "assetName":expected_name,"sha256":digest,"sizeBytes":size}
    repo_index = load_json(INDEX) if INDEX.exists() else {"releases": []}
    latest = latest_published_version(repo, tid, guid, repo_index)
    if not should_publish_version(version, latest):
        print(f"Skipping {tid} v{version}: published version for id {guid} is already current or newer")
        return {"path":str(path.relative_to(ROOT)), "action":"skipped-not-newer", "id":guid, "version":version}
    same = next((x for x in repo_index["releases"] if x.get("id") == guid and x["templateVersion"] == version), None)
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
        description = catalog_meta.get("description") or catalog_meta.get("shortDescription") or catalog_meta["name"]
        notes = f"{description}\n\nTemplate ID: `{tid}`\nVersion: `{version}`\nMinimum app version: `{catalog_meta['minimumAppVersion']}`\nSHA-256: `{digest}`"
        categories = catalog_meta.get("categories") or [catalog_meta["category"]]
        tags = catalog_meta.get("tags") or []
        preview_member = catalog_meta["preview"]["poster"] if native or legacy else "preview/poster.jpg"
        preview_name = Path(preview_member).name
        sidecar = {"id":guid,"templateId":tid,"sourceId":source_id,"templateVersion":version,"name":catalog_meta["name"],"description":description,"category":catalog_meta["category"],"categories":categories,"tags":tags,"durationMs":catalog_meta["durationMs"],"mediaSlots":catalog_meta["mediaSlots"],"minimumAppVersion":catalog_meta["minimumAppVersion"],"previewImageName":preview_name,"tag":tag,"assetName":expected_name,"sha256":digest,"sizeBytes":size}
        with tempfile.TemporaryDirectory(prefix="voomi-release-") as temp:
            release_dir = Path(temp)
            zip_path = release_dir / expected_name
            sidecar_path = release_dir / f"{tag}.metadata.json"
            notes_path = release_dir / "release-notes.md"
            shutil.copyfile(path, zip_path)
            sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            notes_path.write_text(notes, encoding="utf-8")
            run(["gh", "release", "create", tag, str(zip_path), str(sidecar_path), "--repo", repo, "--title", f"{catalog_meta['name']} v{version}", "--notes-file", str(notes_path)])
        verify_release(repo, tag, expected_name, digest, size)
    categories = catalog_meta.get("categories") or [catalog_meta["category"]]
    tags = catalog_meta.get("tags") or []
    description = catalog_meta.get("description") or catalog_meta.get("shortDescription") or catalog_meta["name"]
    preview_member = catalog_meta["preview"]["poster"] if native or legacy else "preview/poster.jpg"
    preview_name = Path(preview_member).name
    item = {"id":guid,"templateId":tid,"sourceId":source_id,"templateVersion":version,"name":catalog_meta["name"],"description":description,"category":catalog_meta["category"],"categories":categories,"tags":tags,"durationMs":catalog_meta["durationMs"],"mediaSlots":catalog_meta["mediaSlots"],"minimumAppVersion":catalog_meta["minimumAppVersion"],"previewImageName":preview_name,"tag":tag,"assetName":expected_name,"sha256":digest,"sizeBytes":size,"draft":False,"prerelease":False}
    repo_index["releases"] = [x for x in repo_index["releases"] if not (x.get("id") == guid and x["templateVersion"] == version)] + [item]
    repo_index["releases"].sort(key=lambda x:(x["templateId"], __import__("common").semver_key(x["templateVersion"])))
    INDEX.write_text(json.dumps(repo_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    preview_dir = ROOT / "previews" / tid / version
    preview_dir.mkdir(parents=True, exist_ok=True)
    from common import extract_member
    extract_member(path, preview_member, preview_dir / preview_name)
    if not native and not legacy:
        extract_member(path, "preview/thumbnail.jpg", preview_dir / "thumbnail.jpg")
    elif preview_name != "thumbnail.jpg":
        extract_member(path, preview_member, preview_dir / "thumbnail.jpg")
    return {"path":str(path.relative_to(ROOT)), "metadata":meta, "tag":tag, "assetName":expected_name,"sha256":digest,"sizeBytes":size}


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    try:
        packages = discover()
        if not packages:
            if dry_run:
                print("No ZIP packages found; nothing to do")
                return 0
            if os.getenv("GITHUB_EVENT_NAME") == "push":
                report = {"publishedOrSkipped": [], "failures": [], "message": "No newly added or modified ZIP packages; nothing to publish."}
                Path("publish-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                Path("processed-packages.txt").write_text("", encoding="utf-8")
                print(report["message"])
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
        for failure in failures:
            print(f"WARNING: package not published: {failure['path']}: {failure['error']}", file=sys.stderr)
        # Package-level failures are isolated: retain those ZIPs in incoming/,
        # report warnings, and let the workflow continue even if every package
        # in this batch is invalid. Systemic publishing failures still raise
        # above and fail the job.
        return 0
    except VoomiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        Path("publish-report.json").write_text(json.dumps({"publishedOrSkipped":[],"failures":[{"error":str(exc)}]}, indent=2) + "\n", encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
