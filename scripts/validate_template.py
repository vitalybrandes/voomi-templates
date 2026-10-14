#!/usr/bin/env python3
"""Validate an untrusted Voomi template ZIP without executing its contents."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image, ImageFile

from common import ROOT, VoomiError, canonical_template_id, load_json, parse_semver, safe_zip_entries

ImageFile.LOAD_TRUNCATED_IMAGES = False
REQUIRED_FILES = {"template.json", "README.md", "preview/preview.jpg", "licenses/asset-manifest.json"}
ALLOWED_TOP = {"template.json", "README.md", "placeholders", "assets", "audio", "preview", "licenses"}
SUPPORTED_FEATURES = {"basic_timeline", "transitions", "text_overlay", "image_overlay", "audio_track", "video_slot", "photo_slot", "photo_or_video_slot", "keyframe_animation", "particle_effect", "mask", "blend_modes", "speed_control", "crop_transform"}
ALLOWED_LICENSES = {"CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "Public-Domain", "Proprietary-Authorized"}


def fail(msg: str) -> None:
    raise VoomiError(msg)


def member_map(entries: dict[str, zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    return {name.casefold(): info for name, info in entries.items()}


def image_info(zf: zipfile.ZipFile, name: str, expected: tuple[int, int]) -> None:
    try:
        with zf.open(name) as raw:
            im = Image.open(raw)
            im.verify()
        with zf.open(name) as raw:
            im = Image.open(raw)
            im.load()
            if im.format != "JPEG":
                fail(f"{name} must be JPEG")
            if im.size != expected:
                fail(f"{name} resolution is {im.width}x{im.height}; expected {expected[0]}x{expected[1]}")
            if im.getexif().get(274, 1) != 1:
                fail(f"{name} must have normal orientation (EXIF orientation 1 or absent)")
    except (OSError, ValueError) as exc:
        fail(f"Invalid image {name}: {exc}")


def validate_manifest(data: Any, entries: dict[str, zipfile.ZipInfo]) -> None:
    if not isinstance(data, dict):
        fail("licenses/asset-manifest.json must contain an object")
    assets = data.get("assets")
    if not isinstance(assets, list):
        fail("License manifest must contain an assets array")
    known = {unicodedata.normalize("NFC", n).casefold() for n, info in entries.items() if not info.is_dir()}
    seen: set[str] = set()
    for i, item in enumerate(assets):
        label = f"License manifest asset {i + 1}"
        if not isinstance(item, dict):
            fail(f"{label} must be an object")
        path = item.get("path")
        if not isinstance(path, str) or unicodedata.normalize("NFC", path).casefold() not in known:
            fail(f"{label} references a missing asset")
        if path.casefold() in seen:
            fail(f"Duplicate license manifest path: {path}")
        seen.add(unicodedata.normalize("NFC", path).casefold())
        for key in ("creator", "sourceUrl", "license", "licenseUrl", "attribution"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                fail(f"{label} missing {key}")
        if item["license"] not in ALLOWED_LICENSES:
            fail(f"{label} uses license outside allowlist: {item['license']}")
        if item.get("redistributionApproved") is not True:
            fail(f"Asset license redistribution not approved: {path}")
        for key in ("sourceUrl", "licenseUrl"):
            if not item[key].startswith(("https://", "http://")):
                fail(f"{label} has invalid {key}")
    # Require each distributable asset (except metadata and previews) to be represented.
    for path, info in entries.items():
        if info.is_dir() or path.casefold().split("/", 1)[0] not in {"assets", "audio", "placeholders"}:
            continue
        if path.casefold() not in seen:
            fail(f"Asset license manifest entry missing for {path}")


def is_app_native(meta: Any) -> bool:
    """Recognize the Voomi app export shape used by the supplied package."""
    if not isinstance(meta, dict):
        return False
    preview = meta.get("preview")
    image_path = preview if isinstance(preview, str) else preview.get("preview") if isinstance(preview, dict) else None
    return isinstance(image_path, str) and image_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))


def is_legacy_voomi(meta: Any) -> bool:
    """Recognize the earlier Voomi template.json package format."""
    return (isinstance(meta, dict)
            and isinstance(meta.get("templateId", meta.get("id")), str)
            and isinstance(meta.get("version"), str)
            and isinstance(meta.get("preview"), str)
            and isinstance(meta.get("canvas"), dict)
            and isinstance(meta.get("mediaSlots"), list)
            and isinstance(meta.get("layers"), list))


def validate_legacy_voomi(meta: dict[str, Any], entries: dict[str, zipfile.ZipInfo], zf: zipfile.ZipFile) -> None:
    """Validate the supported legacy shape without rewriting its ZIP."""
    try:
        canonical_template_id(meta.get("templateId", meta.get("id")))
        parse_semver(meta["version"])
    except (KeyError, VoomiError) as exc:
        fail(str(exc))
    if not isinstance(meta.get("name"), str) or not meta["name"].strip():
        fail("Legacy template metadata requires a non-empty name")
    duration = meta.get("durationSeconds")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 1 <= duration <= 60:
        fail("durationSeconds must be between 1 and 60")
    canvas = meta["canvas"]
    if not all(isinstance(canvas.get(k), int) and 360 <= canvas[k] <= 2160 for k in ("width", "height")):
        fail("canvas width and height must be integers from 360 to 2160")
    if canvas.get("fps") not in {24, 25, 30}:
        fail("canvas.fps must be 24, 25, or 30")
    files = {name.casefold() for name, info in entries.items() if not info.is_dir()}
    preview = meta["preview"]
    if preview.casefold() not in files:
        fail(f"preview references a missing image: {preview}")
    try:
        with zf.open(preview) as raw:
            image = Image.open(raw)
            image.verify()
        with zf.open(preview) as raw:
            image = Image.open(raw)
            image.load()
            if image.format not in {"PNG", "JPEG"}:
                fail("Legacy preview must be PNG or JPEG")
    except (OSError, ValueError) as exc:
        fail(f"Invalid preview image {preview}: {exc}")
    slots = meta["mediaSlots"]
    slot_ids = set()
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict) or not isinstance(slot.get("id"), str) or not isinstance(slot.get("type"), str):
            fail(f"mediaSlots[{index}] must have string id and type")
        if slot["id"] in slot_ids:
            fail(f"Duplicate media-slot ID: {slot['id']}")
        slot_ids.add(slot["id"])
        placeholder = slot.get("placeholder")
        if placeholder is not None and (not isinstance(placeholder, str) or placeholder.casefold() not in files):
            fail(f"Media slot {slot['id']} references a missing placeholder")
    if not meta["layers"]:
        fail("Legacy template must contain at least one layer")
    for index, layer in enumerate(meta["layers"]):
        if not isinstance(layer, dict) or layer.get("type") not in {"media", "video", "image"}:
            fail(f"Unsupported legacy layer at index {index}")
        bounds = layer.get("bounds")
        if not isinstance(bounds, dict) or not all(isinstance(bounds.get(k), (int, float)) and not isinstance(bounds.get(k), bool) for k in ("x", "y", "width", "height")):
            fail(f"Layer {index} requires numeric bounds")
        if layer["type"] == "media":
            if layer.get("slot") not in slot_ids:
                fail(f"Layer {index} references an unknown media slot")
        else:
            source = layer.get("src")
            if not isinstance(source, str) or source.casefold() not in files:
                fail(f"Layer {index} references a missing asset")
        key = layer.get("chromaKey")
        if key is not None:
            if (layer["type"] != "video" or not isinstance(key, dict)
                    or key.get("mode") != "luma"
                    or str(key.get("keyColor", "#000000")).upper() != "#000000"):
                fail(f"Layer {index} uses unsupported chromaKey settings")
            threshold, softness = key.get("threshold"), key.get("softness")
            if (not isinstance(threshold, (int, float)) or isinstance(threshold, bool)
                    or not isinstance(softness, (int, float)) or isinstance(softness, bool)
                    or threshold < 0 or softness <= 0 or threshold + softness > 1):
                fail(f"Layer {index} has invalid luma-key threshold or softness")


def validate_app_native(meta: dict[str, Any], entries: dict[str, zipfile.ZipInfo], zf: zipfile.ZipFile) -> None:
    required = ("templateId", "templateVersion", "name", "category", "durationMs", "fps", "resolution", "minimumAppVersion", "requiredRendererFeatures", "mediaSlots", "scenes", "preview")
    missing = [key for key in required if key not in meta]
    if missing:
        fail(f"App template metadata missing {missing[0]}")
    if not (isinstance(meta.get("description"), str) and meta["description"].strip()) and not (isinstance(meta.get("shortDescription"), str) and meta["shortDescription"].strip()):
        fail("App template metadata requires description or shortDescription")
    canonical_template_id(meta["templateId"])
    validate_guid(meta)
    parse_semver(meta["templateVersion"])
    parse_semver(meta["minimumAppVersion"])


    if not isinstance(meta["durationMs"], int) or isinstance(meta["durationMs"], bool) or meta["durationMs"] <= 0:
        fail("durationMs must be a positive integer")
    if not isinstance(meta["fps"], (int, float)) or isinstance(meta["fps"], bool) or meta["fps"] <= 0:
        fail("fps must be positive")
    if not isinstance(meta["resolution"], dict) or not all(isinstance(meta["resolution"].get(k), int) and meta["resolution"][k] > 0 for k in ("width", "height")):
        fail("resolution must contain positive width and height")
    if not isinstance(meta["requiredRendererFeatures"], list) or not all(isinstance(x, str) and x for x in meta["requiredRendererFeatures"]):
        fail("requiredRendererFeatures must be an array of non-empty strings")
    if not isinstance(meta["mediaSlots"], list) or not isinstance(meta["scenes"], list) or not meta["scenes"]:
        fail("mediaSlots and scenes must be arrays, and scenes cannot be empty")
    files = {name.casefold() for name, info in entries.items() if not info.is_dir()}
    for slot in meta["mediaSlots"]:
        if not isinstance(slot, dict) or not isinstance(slot.get("id"), str) or not isinstance(slot.get("type"), str):
            fail("Each media slot must have string id and type")
        placeholder = slot.get("placeholder")
        if placeholder and (not isinstance(placeholder, str) or placeholder.casefold() not in files):
            fail(f"Media slot {slot['id']} references a missing placeholder")
    known_slots = {slot["id"] for slot in meta["mediaSlots"] if isinstance(slot, dict) and isinstance(slot.get("id"), str)}
    scene_ids: set[str] = set()
    for scene in meta["scenes"]:
        if not isinstance(scene, dict) or not isinstance(scene.get("id"), str):
            fail("Each scene must have a string id")
        if scene["id"] in scene_ids:
            fail(f"Duplicate scene ID: {scene['id']}")
        scene_ids.add(scene["id"])
        for slot_id in scene.get("mediaSlotIds", []):
            if slot_id not in known_slots:
                fail(f"Scene {scene['id']} references unknown media slot: {slot_id}")
        for layer in scene.get("layers", []):
            if isinstance(layer, dict) and layer.get("type") == "image":
                asset = layer.get("source")
                if not isinstance(asset, str) or asset.casefold() not in files:
                    fail(f"Scene {scene['id']} references a missing image asset")


    preview = meta["preview"]
    preview_path = preview if isinstance(preview, str) else preview.get("preview") if isinstance(preview, dict) else None
    if not isinstance(preview_path, str) or preview_path.casefold() not in files:
        fail("preview references a missing image")
    try:
        with zf.open(preview_path) as raw:
            im = Image.open(raw)
            im.verify()
        with zf.open(preview_path) as raw:
            im = Image.open(raw)
            im.load()
            if im.format not in {"PNG", "JPEG", "WEBP"}:
                fail("App-native preview must be an image (PNG, JPEG, or WebP)")
    except (OSError, ValueError) as exc:
        fail(f"Invalid preview image {preview_path}: {exc}")
    for audio in (meta.get("audio") or {}).values() if isinstance(meta.get("audio"), dict) else []:
        if isinstance(audio, dict) and isinstance(audio.get("source"), str) and audio["source"].casefold() not in files:
            fail(f"Audio references missing file: {audio['source']}")


def validate_guid(meta: dict[str, Any]) -> None:
    value = meta.get("id")
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value.lower():
            raise ValueError(value)
    except (ValueError, AttributeError):
        fail("template.json id must be a UUID")


def validate(zip_path: Path) -> dict[str, Any]:
    entries = safe_zip_entries(zip_path)
    actual = {name.casefold() for name in entries if not name.endswith("/")}
    if "template.json" not in actual:
        fail("Required package file missing: template.json")
    with zipfile.ZipFile(zip_path) as zf:
        try:
            meta = json.loads(zf.read("template.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            fail(f"Invalid template.json: {exc}")
        native = is_app_native(meta)
        legacy = is_legacy_voomi(meta)
        required_files = {"template.json", "README.md"} if native else ({"template.json", "README.md", meta["preview"]} if legacy else REQUIRED_FILES)
    missing = sorted(p for p in required_files if p.casefold() not in actual)
    if missing:
        fail(f"Required package file missing: {missing[0]}")
    for name in actual:
        if name.split("/", 1)[0] not in {x.casefold() for x in ALLOWED_TOP}:
            fail(f"Unexpected package root path: {name}")
    with zipfile.ZipFile(zip_path) as zf:
        if native:
            validate_app_native(meta, entries, zf)
        elif legacy:
            validate_legacy_voomi(meta, entries, zf)
            validate_guid(meta)
        else:
            schema = load_json(ROOT / "schemas" / "template.schema.json")
            errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(meta), key=lambda e: list(map(str, e.path)))
            if errors:
                err = errors[0]
                fail(f"template.json schema error at {'.'.join(map(str, err.path)) or '<root>'}: {err.message}")
            try:
                parse_semver(meta["templateVersion"])
                parse_semver(meta["minimumAppVersion"])
                canonical_template_id(meta["templateId"])
                validate_guid(meta)
            except VoomiError as exc:
                fail(str(exc))
            slots = meta["mediaSlots"]
            slot_ids = [s["id"] for s in slots]
            if len(slot_ids) != len(set(slot_ids)):
                fail("Duplicate media-slot ID")
            unknown = sorted(set(meta["requiredRendererFeatures"]) - SUPPORTED_FEATURES)
            if unknown:
                fail(f"Unsupported renderer feature: {unknown[0]}")
            scene_ids = [scene["id"] for scene in meta["scenes"]]
            if len(scene_ids) != len(set(scene_ids)):
                fail("Duplicate scene ID")
            previous_order = -1
            previous_end = 0
            slot_set = set(slot_ids)
            for index, scene in enumerate(meta["scenes"]):
                order = scene.get("order", index)
                if order <= previous_order:
                    fail("Scene ordering must be strictly increasing")
                previous_order = order
                refs = scene.get("mediaSlotIds", scene.get("mediaSlots", []))
                if not isinstance(refs, list):
                    fail(f"Scene {scene['id']} media slot references must be an array")
                for ref in refs:
                    if ref not in slot_set:
                        fail(f"Scene {scene['id']} references unknown media slot: {ref}")
                if "startMs" in scene and "durationMs" in scene:
                    start, end = scene["startMs"], scene["startMs"] + scene["durationMs"]
                    if index and start < previous_end:
                        fail(f"Scene {scene['id']} has invalid overlap")
                    if index and start > previous_end:
                        fail(f"Scene {scene['id']} creates an unintended timeline gap")
                    previous_end = end
            if all("startMs" in s and "durationMs" in s for s in meta["scenes"]):
                timeline_end = max(s["startMs"] + s["durationMs"] for s in meta["scenes"])
                if abs(timeline_end - meta["durationMs"]) > 50:
                    fail("Scene timeline total does not match durationMs within 50 ms")
            manifest_path = meta["licenseManifest"]
            if manifest_path != "licenses/asset-manifest.json":
                fail("licenseManifest must be licenses/asset-manifest.json")
            try:
                manifest = json.loads(zf.read(manifest_path))
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                fail(f"Invalid license manifest: {exc}")
            validate_manifest(manifest, entries)
            image_info(zf, "preview/preview.jpg", (1080, 1920))
            if meta["preview"].get("preview") != "preview/preview.jpg":
                fail("preview.preview must be preview/preview.jpg")
    digest = hashlib.sha256()
    with zip_path.open("rb") as package:
        for chunk in iter(lambda: package.read(1024 * 1024), b""):
            digest.update(chunk)
    template_id = meta.get("templateId", meta.get("id")) if legacy else meta["templateId"]
    template_version = meta["templateVersion"] if native else meta["version"] if legacy else meta["templateVersion"]
    return {"valid": True, "id": meta["id"], "templateId": template_id, "templateVersion": template_version, "sha256": digest.hexdigest(), "sizeBytes": zip_path.stat().st_size, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.zip)
        print(json.dumps(report, indent=2))
        code = 0
    except VoomiError as exc:
        report = {"valid": False, "errors": [str(exc)]}
        print(f"ERROR: {exc}")
        code = 1
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
