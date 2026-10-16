"""Shared security, metadata and GitHub helpers for Voomi template tooling."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_ZIP_BYTES = int(os.getenv("VOOMI_MAX_ZIP_BYTES", str(250 * 1024 * 1024)))
DEFAULT_MAX_UNCOMPRESSED_BYTES = int(os.getenv("VOOMI_MAX_UNCOMPRESSED_BYTES", str(1024 * 1024 * 1024)))
MAX_ZIP_ENTRIES = int(os.getenv("VOOMI_MAX_ZIP_ENTRIES", "10000"))
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")


class VoomiError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise VoomiError(f"Cannot read JSON {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_semver(value: str) -> tuple[int, int, int, tuple[Any, ...]]:
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise VoomiError(f"Invalid semantic version: {value}")
    major, minor, patch, prerelease, _build = match.groups()
    if prerelease:
        for identifier in prerelease.split("."):
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise VoomiError(f"Invalid semantic version: {value}")
    pre: tuple[Any, ...] = ()
    if prerelease:
        parts: list[Any] = []
        for item in prerelease.split("."):
            parts.append((0, int(item)) if item.isdigit() else (1, item))
        pre = tuple(parts)
    return int(major), int(minor), int(patch), pre


def semver_key(value: str) -> tuple[Any, ...]:
    major, minor, patch, pre = parse_semver(value)
    # A release without prerelease identifier sorts after prereleases.
    return major, minor, patch, (1, ()) if not pre else (0, pre)


def safe_zip_entries(zip_path: Path, *, max_compressed: int = DEFAULT_MAX_ZIP_BYTES,
                     max_uncompressed: int = DEFAULT_MAX_UNCOMPRESSED_BYTES) -> dict[str, zipfile.ZipInfo]:
    if not zip_path.is_file():
        raise VoomiError(f"ZIP file does not exist: {zip_path}")
    size = zip_path.stat().st_size
    if size > max_compressed:
        raise VoomiError(f"Compressed ZIP exceeds limit ({size} > {max_compressed} bytes)")
    try:
        zf = zipfile.ZipFile(zip_path)
        infos = zf.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise VoomiError(f"Corrupt ZIP file: {exc}") from exc
    if len(infos) > MAX_ZIP_ENTRIES:
        raise VoomiError(f"ZIP contains too many entries ({len(infos)} > {MAX_ZIP_ENTRIES})")
    total = 0
    names: dict[str, zipfile.ZipInfo] = {}
    with zf:
        for info in infos:
            raw = info.filename
            if "\\" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
                raise VoomiError(f"Unsafe ZIP path: {raw}")
            parts = raw.split("/")
            if info.is_dir() and parts[-1] == "":
                parts.pop()
            if not parts or any(part in ("", ".", "..") for part in parts):
                raise VoomiError(f"Unsafe ZIP path: {raw}")
            normalized = "/".join(parts)
            key = unicodedata.normalize("NFC", normalized).casefold()
            if key in names:
                raise VoomiError(f"Duplicate normalized ZIP filename: {raw}")
            names[key] = info
            mode = info.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                raise VoomiError(f"Symbolic links are not allowed: {raw}")
            if info.flag_bits & 0x1:
                raise VoomiError(f"Encrypted ZIP entries are not supported: {raw}")
            if not info.is_dir():
                total += info.file_size
            suffix = PurePosixPath(raw).suffix.lower()
            if mode & 0o111 or suffix in {".exe", ".dll", ".so", ".bat", ".cmd", ".ps1", ".sh", ".com", ".msi", ".apk", ".jar", ".pyc"}:
                raise VoomiError(f"Unexpected executable file in ZIP: {raw}")
        if total > max_uncompressed:
            raise VoomiError(f"ZIP decompressed size exceeds limit ({total} > {max_uncompressed} bytes)")
        try:
            bad = zf.testzip()
        except (NotImplementedError, RuntimeError, zipfile.BadZipFile, OSError) as exc:
            raise VoomiError(f"Unsupported or corrupt ZIP compression/data: {exc}") from exc
        if bad:
            raise VoomiError(f"Corrupt ZIP entry: {bad}")
    return names


def extract_member(zip_path: Path, member: str, destination: Path) -> None:
    """Extract one already-validated member without trusting ZIP paths."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as src, destination.open("wb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(chunk)


def github_repo() -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "").strip()
    if repo:
        return repo
    try:
        url = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "OWNER/voomi-templates"
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+)(?:\.git)?$", url)
    return match.group(1) if match else "OWNER/voomi-templates"


def raw_base(repo: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/main"
