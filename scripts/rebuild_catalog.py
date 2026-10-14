#!/usr/bin/env python3
"""Rebuild the public catalog from validated metadata sidecars."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import ROOT, VoomiError, canonical_template_id, load_json, raw_base, semver_key, sha256_file

INDEX = ROOT / "release-index.json"
CATALOG = ROOT / "catalog.json"


def build(index: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    repo = os.getenv("GITHUB_REPOSITORY", "vitalybrandes/voomi-templates")
    if repo.count("/") != 1 or not all(repo.split("/", 1)):
        raise VoomiError("GITHUB_REPOSITORY must have OWNER/REPOSITORY form")
    templates: dict[str, dict[str, Any]] = {}
    for record in index.get("releases", []):
        if record.get("draft") or record.get("prerelease"):
            continue
        tid, version = record.get("templateId"), record.get("templateVersion")
        if not tid or not version:
            raise VoomiError("Release index entry missing template ID or version")
        tid = canonical_template_id(tid)
        guid = record.get("id") or str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{repo}/templates/{tid}"))
        try:
            guid = str(uuid.UUID(guid))
        except (ValueError, TypeError, AttributeError) as exc:
            raise VoomiError(f"Invalid template UUID for {tid}") from exc
        key = semver_key(version)
        if guid not in templates or key > semver_key(templates[guid]["version"]):
            templates[guid] = {
                "id": guid,
                "templateId": tid,
                "sourceId": record.get("sourceId", record.get("templateId", tid)),
                "name": record["name"],
                "version": version,
                "description": record["description"],
                "category": record["category"],
                "categories": sorted(set(record["categories"])),
                "tags": sorted(set(record["tags"])),
                "durationMs": record["durationMs"],
                "mediaSlots": record["mediaSlots"],
                "minAppVersion": record["minimumAppVersion"],
                "previewUrl": f"{raw_base(repo)}/previews/{tid}/{version}/{record.get('previewImageName', 'preview.jpg')}",
                "downloadUrl": f"https://github.com/{repo}/releases/download/{record['tag']}/{record['assetName']}",
                "sizeBytes": record["sizeBytes"],
                "sha256": record["sha256"],
            }
    ordered = sorted(templates.values(), key=lambda t: t["id"])
    categories = sorted({c for item in ordered for c in item["categories"]} | {item["category"] for item in ordered})
    content = {"catalogSchemaVersion": "1.0.0", "categories": categories, "templates": ordered}
    old_content = None
    if old:
        old_content = {k: old.get(k) for k in ("catalogSchemaVersion", "categories", "templates")}
    changed = content != old_content
    version = (old.get("catalogVersion", 0) if old else 0) + (1 if changed else 0)
    updated = old.get("updatedAt") if old and not changed else datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    result = {"catalogSchemaVersion": content["catalogSchemaVersion"], "catalogVersion": version, "updatedAt": updated, "categories": categories, "templates": ordered}
    if changed:
        result["catalogVersion"] = version
    return result


def recover_release_index(index: dict[str, Any], check_remote: bool = True) -> dict[str, Any]:
    """Discover manually created public releases and validate attached sidecars."""
    if not check_remote:
        return index
    repo = os.getenv("GITHUB_REPOSITORY", "vitalybrandes/voomi-templates")
    try:
        raw = subprocess.check_output(["gh", "release", "list", "--repo", repo, "--limit", "1000", "--json", "tagName,isDraft,isPrerelease"], text=True)
        releases = json.loads(raw)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise VoomiError(f"Cannot enumerate GitHub releases: {exc}") from exc
    known = {(item.get("tag"), item.get("templateVersion")) for item in index.get("releases", [])}
    for release in releases:
        tag = release.get("tagName", "")
        if release.get("isDraft") or release.get("isPrerelease") or not tag.startswith("template-"):
            continue
        if any(existing_tag == tag for existing_tag, _ in known):
            continue
        try:
            info = subprocess.check_output(["gh", "release", "view", tag, "--repo", repo, "--json", "assets,body"], text=True)
            obj = json.loads(info)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        metadata_name = f"{tag}.metadata.json"
        metadata_asset = next((a for a in obj.get("assets", []) if a.get("name") == metadata_name), None)
        if not metadata_asset:
            continue
        with __import__("tempfile").TemporaryDirectory(prefix="voomi-metadata-") as temp:
            try:
                subprocess.run(["gh", "release", "download", tag, "--repo", repo, "--pattern", metadata_name, "--dir", temp], check=True, capture_output=True)
                metadata_path = Path(temp) / metadata_name
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
        asset_name = metadata.get("assetName")
        zip_asset = next((a for a in obj.get("assets", []) if a.get("name") == asset_name), None)
        if not zip_asset or zip_asset.get("size") != metadata.get("sizeBytes"):
            continue
        if (metadata.get("tag") != tag
                or not isinstance(metadata.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", metadata["sha256"])):
            continue
        try:
            template_id = canonical_template_id(metadata.get("templateId"))
            version = metadata.get("templateVersion")
            semver_key(version)
        except (VoomiError, TypeError):
            continue
        try:
            template_guid = str(uuid.UUID(metadata.get("id")))
        except (ValueError, TypeError, AttributeError):
            template_guid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{repo}/templates/{template_id}"))
        if (template_id != metadata.get("templateId")
                or metadata.get("tag") != f"template-{template_id}-v{version}"
                or asset_name != f"{template_id}-v{version}.zip"
                or not isinstance(metadata.get("sizeBytes"), int)
                or isinstance(metadata.get("sizeBytes"), bool)
                or metadata["sizeBytes"] <= 0):
            continue
        # Verify bytes from GitHub before trusting the sidecar for catalog data.
        with __import__("tempfile").TemporaryDirectory(prefix="voomi-package-") as temp:
            try:
                subprocess.run(["gh", "release", "download", tag, "--repo", repo, "--pattern", asset_name, "--dir", temp], check=True, capture_output=True)
                package = Path(temp) / asset_name
                if not package.is_file() or sha256_file(package) != metadata["sha256"]:
                    continue
            except (OSError, subprocess.CalledProcessError):
                continue
        item = dict(metadata)
        item["id"] = template_guid
        item.update({"draft":False,"prerelease":False})
        index.setdefault("releases", []).append(item)
        known.add((tag, item.get("templateVersion")))
    index["releases"].sort(key=lambda x:(x["templateId"], semver_key(x["templateVersion"])))
    return index


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--index", type=Path, default=INDEX)
    p.add_argument("--output", type=Path, default=CATALOG)
    p.add_argument("--dry-run", action="store_true", help="write catalog.preview.json without changing catalog.json")
    p.add_argument("--skip-remote", action="store_true", help="do not inspect GitHub releases for recoverable metadata assets")
    args = p.parse_args()
    try:
        index = recover_release_index(load_json(args.index), not args.skip_remote)
        old = load_json(args.output) if args.output.exists() else None
        catalog = build(index, old)
        target = ROOT / "catalog.preview.json" if args.dry_run else args.output
        target.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Catalog {'preview' if args.dry_run else 'written'}: {target}")
        return 0
    except (VoomiError, KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
