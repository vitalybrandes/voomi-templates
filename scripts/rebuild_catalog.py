#!/usr/bin/env python3
"""Rebuild catalog from successfully validated and published release records."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import ROOT, VoomiError, load_json, raw_base, semver_key

INDEX = ROOT / "release-index.json"
CATALOG = ROOT / "catalog.json"


def build(index: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    repo = os.getenv("GITHUB_REPOSITORY", "vitalybrandes/voomi-templates")
    if repo.count("/") != 1 or not all(repo.split("/", 1)):
        raise VoomiError("GITHUB_REPOSITORY must have OWNER/REPOSITORY form")
    latest: dict[str, dict[str, Any]] = {}
    for record in index.get("releases", []):
        if record.get("schemaVersion") not in {"2.0", "3.0"} or record.get("draft") or record.get("prerelease"):
            continue
        template_id, version = record.get("templateId"), record.get("templateVersion")
        try:
            canonical_id = str(uuid.UUID(template_id))
            version_key = semver_key(version)
        except (ValueError, TypeError, AttributeError) as exc:
            raise VoomiError("Release index contains an invalid v2 template ID or version") from exc
        if canonical_id != template_id.lower():
            raise VoomiError(f"Release index UUID must be canonical: {template_id}")
        if template_id not in latest or version_key > semver_key(latest[template_id]["templateVersion"]):
            latest[template_id] = record
    templates = []
    for template_id, record in latest.items():
        version = record["templateVersion"]
        tag = record["tag"]
        asset_name = record["assetName"]
        entry = {"templateId": template_id, "templateVersion": version, "schemaVersion": record["schemaVersion"], "name": record["name"], "description": record["description"], "categories": sorted(set(record.get("categories", []))), "tags": sorted(set(record.get("tags", []))), "durationMs": record["durationMs"], "previewUrl": f"{raw_base(repo)}/previews/{template_id}/{version}/preview.jpg", "downloadUrl": f"https://github.com/{repo}/releases/download/{tag}/{asset_name}", "fileSize": record["fileSize"], "sha256": record["sha256"]}
        if "requiredFeatures" in record:
            entry["requiredFeatures"] = record["requiredFeatures"]
        if "engine" in record:
            entry["engine"] = record["engine"]
        templates.append(entry)
    templates.sort(key=lambda item: item["templateId"])
    content = {"catalogSchemaVersion": "2.0", "templates": templates}
    old_content = {key: old.get(key) for key in content} if old else None
    changed = content != old_content
    version = (old.get("catalogVersion", 0) if old else 0) + (1 if changed else 0)
    updated = old.get("updatedAt") if old and not changed else datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {"catalogSchemaVersion": "2.0", "catalogVersion": version, "updatedAt": updated, "templates": templates}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--output", type=Path, default=CATALOG)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        index = load_json(args.index)
        output = args.output
        old = load_json(output) if output.exists() else None
        catalog = build(index, old)
        target = ROOT / "catalog.preview.json" if args.dry_run else output
        target.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Catalog {'preview' if args.dry_run else 'written'}: {target}")
        return 0
    except (VoomiError, KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
