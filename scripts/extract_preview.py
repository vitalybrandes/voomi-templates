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
        if "template.json".casefold() not in {name.casefold() for name in entries}:
            raise VoomiError("ZIP is missing template metadata")
        with zipfile.ZipFile(args.zip) as zf:
            meta = json.loads(zf.read("template.json"))
        template_id, version = canonical_template_id(meta.get("templateId")), meta.get("templateVersion")
        if not isinstance(version, str) or not all(c.isalnum() or c in ".-+" for c in version):
            raise VoomiError("Unsafe or missing templateVersion")
        args.output.mkdir(parents=True, exist_ok=True)
        preview = meta.get("preview", {})
        if isinstance(preview, str):
            image_path = preview
        elif isinstance(preview, dict):
            image_path = preview.get("preview", "preview/preview.jpg")
        else:
            raise VoomiError("template.json preview must declare an image path")
        if not isinstance(image_path, str) or image_path not in entries:
            raise VoomiError("ZIP is missing its declared preview image")
        extract_member(args.zip, image_path, args.output / Path(image_path).name)
        print(f"Extracted previews for {template_id} {version} into {args.output}")
        return 0
    except (VoomiError, json.JSONDecodeError, KeyError, OSError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
