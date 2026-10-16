#!/usr/bin/env python3
"""Move successfully published intake ZIPs into the permanent Production archive."""
from __future__ import annotations

import json
import shutil
import uuid
import zipfile
from pathlib import Path

from common import ROOT, VoomiError, github_repo, parse_semver, semver_key, sha256_file

INCOMING = ROOT / "incoming"
PRODUCTION = ROOT / "Production"
PROCESSED = ROOT / "processed-packages.txt"


def archive_processed() -> list[Path]:
    if not PROCESSED.exists():
        return []

    archived: list[Path] = []
    records: list[dict] = []
    for raw_path in PROCESSED.read_text(encoding="utf-8").splitlines():
        if not raw_path.strip():
            continue
        source = (ROOT / raw_path).resolve()
        if not source.is_relative_to(INCOMING.resolve()) or not source.is_file() or source.suffix.lower() != ".zip":
            raise VoomiError(f"Processed package is missing or outside incoming/: {raw_path}")

        try:
            with zipfile.ZipFile(source) as archive:
                metadata = json.loads(archive.read("template.json"))
            template_id = str(uuid.UUID(metadata["templateId"]))
            version = metadata["templateVersion"]
            parse_semver(version)
        except (KeyError, ValueError, OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
            raise VoomiError(f"Cannot read template identity from {raw_path}: {exc}") from exc

        destination = PRODUCTION / template_id / version / "template.zip"
        source_hash = sha256_file(source)
        if destination.exists():
            if sha256_file(destination) != source_hash:
                raise VoomiError(
                    f"Production archive already has different bytes for {template_id} v{version}; refusing to overwrite"
                )
            source.unlink()
            archived.append(destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.tmp")
            try:
                shutil.copy2(source, temporary)
                if sha256_file(temporary) != source_hash:
                    raise VoomiError(f"Archive copy checksum mismatch for {raw_path}")
                temporary.replace(destination)
                source.unlink()
            finally:
                temporary.unlink(missing_ok=True)
            archived.append(destination)

        tag = f"template-{template_id}-v{version}"
        repo = github_repo()
        records.append({
            "templateId": template_id,
            "templateVersion": version,
            "name": metadata["name"],
            "tag": tag,
            "sha256": source_hash,
            "fileSize": destination.stat().st_size,
            "archivePath": destination.relative_to(PRODUCTION).as_posix(),
            "releaseUrl": f"https://github.com/{repo}/releases/tag/{tag}",
        })

    if records:
        index_path = PRODUCTION / "index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
        else:
            index = {"source": "GitHub Releases", "repository": github_repo(), "versions": []}
        by_version = {
            (entry["templateId"], entry["templateVersion"]): entry
            for entry in index.get("versions", [])
        }
        for record in records:
            key = (record["templateId"], record["templateVersion"])
            prior = by_version.get(key)
            if prior and prior.get("sha256") != record["sha256"]:
                raise VoomiError(f"Production index checksum conflict for {key[0]} v{key[1]}")
            by_version[key] = {**(prior or {}), **record}
        index["repository"] = github_repo()
        index["versions"] = sorted(
            by_version.values(),
            key=lambda entry: (entry["templateId"], semver_key(entry["templateVersion"])),
        )
        index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return archived


def main() -> int:
    try:
        archived = archive_processed()
        for path in archived:
            print(path.relative_to(ROOT).as_posix())
        print(f"Archived {len(archived)} successfully published package(s).")
        return 0
    except VoomiError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
