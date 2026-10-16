#!/usr/bin/env python3
"""Verify catalog structure, deterministic ordering, URLs and release assets."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from common import ROOT, VoomiError, load_json, parse_semver


def verify(path: Path, check_remote: bool = True) -> None:
    catalog = load_json(path)
    schema = load_json(ROOT / "schemas" / "catalog.schema.json")
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(catalog))
    if errors:
        raise VoomiError(f"Catalog schema validation failed: {errors[0].message}")
    ids = [t["templateId"] for t in catalog["templates"]]
    if len(ids) != len(set(ids)):
        raise VoomiError("Catalog has duplicate template IDs")
    if ids != sorted(ids):
        raise VoomiError("Catalog templates are not deterministically ordered")
    for item in catalog["templates"]:
        if item.get("schemaVersion") not in {"2.0", "3.0"}:
            raise VoomiError(f"Catalog contains an unsupported schema version: {item.get('templateId', '<missing id>')}")
        try:
            if str(uuid.UUID(item["templateId"])) != item["templateId"].lower():
                raise ValueError(item["templateId"])
        except (ValueError, AttributeError):
            raise VoomiError(f"Catalog templateId is not a UUID: {item['templateId']}")
        parse_semver(item["templateVersion"])
        if not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise VoomiError(f"Invalid SHA-256 for {item['templateId']}")
        if item["fileSize"] <= 0:
            raise VoomiError(f"Invalid package size for {item['templateId']}")
        if not item["previewUrl"].startswith("https://raw.githubusercontent.com/"):
            raise VoomiError(f"Invalid previewUrl for {item['templateId']}")
        if not item["downloadUrl"].startswith("https://github.com/"):
            raise VoomiError(f"Invalid download URL for {item['templateId']}")
        if check_remote:
            match = re.search(r"github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/?#]+)$", item["downloadUrl"])
            if not match:
                raise VoomiError(f"Cannot identify release asset from URL for {item['templateId']}")
            repo, tag, asset_name = match.groups()
            ok = False
            for attempt in range(5):
                try:
                    raw = subprocess.check_output(["gh", "release", "view", tag, "--repo", repo, "--json", "assets,isDraft,isPrerelease"], text=True, stderr=subprocess.DEVNULL)
                    release = json.loads(raw)
                    asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name), None)
                    if asset and not release.get("isDraft") and not release.get("isPrerelease") and asset.get("size") == item["fileSize"]:
                        ok = True
                        break
                except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
                    pass
                time.sleep(min(2 ** attempt, 8))
            if not ok:
                raise VoomiError(f"Release asset missing or invalid: {item['templateId']} {item['templateVersion']}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", type=Path, default=ROOT / "catalog.json")
    p.add_argument("--skip-remote", action="store_true", help="validate local structure without gh release checks")
    args = p.parse_args()
    try:
        verify(args.catalog, not args.skip_remote)
        print(f"Catalog verified: {args.catalog}")
        return 0
    except VoomiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
