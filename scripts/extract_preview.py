#!/usr/bin/env python3
"""Extract only validated public preview images from a template package."""
import argparse
import json
import zipfile
from pathlib import Path

from common import VoomiError, canonical_template_id, extract_member, safe_zip_entries


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--zip", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    try:
        entries = safe_zip_entries(args.zip)
        expected = {"template.json", "preview/poster.jpg", "preview/thumbnail.jpg"}
        if not expected.issubset({name.casefold() for name in entries}):
            raise VoomiError("ZIP is missing template metadata or preview images")
        with zipfile.ZipFile(args.zip) as zf:
            meta = json.loads(zf.read("template.json"))
        template_id, version = canonical_template_id(meta.get("templateId")), meta.get("templateVersion")
        if not isinstance(version, str) or not all(c.isalnum() or c in ".-+" for c in version):
            raise VoomiError("Unsafe or missing templateVersion")
        args.output.mkdir(parents=True, exist_ok=True)
        extract_member(args.zip, "preview/poster.jpg", args.output / "poster.jpg")
        extract_member(args.zip, "preview/thumbnail.jpg", args.output / "thumbnail.jpg")
        print(f"Extracted previews for {template_id} {version} into {args.output}")
        return 0
    except (VoomiError, json.JSONDecodeError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
