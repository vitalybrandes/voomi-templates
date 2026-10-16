#!/usr/bin/env python3
"""Validate an untrusted Voomi Template Schema v2 ZIP package."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from PIL import Image

from common import ROOT, VoomiError, load_json, parse_semver, safe_zip_entries

ALLOWED_ASSET_DIRS = {"backgrounds", "images", "stickers", "frames", "animations", "overlays", "audio", "fonts"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
SUPPORTED_FEATURES = {"layer-transform", "alpha-compositing", "media-crop", "keyframe-animation", "chroma-key", "audio-mixing"}
ANIMATABLE_PROPERTIES = {"x", "y", "width", "height", "rotationDeg", "scale", "offsetX", "offsetY", "opacity", "volume"}


def fail(message: str) -> None:
    raise VoomiError(message)


def _safe_member_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        fail(f"{label} must be a safe relative package path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        fail(f"{label} contains an unsafe path: {value}")
    return path.as_posix()


def _require_number(value: Any, label: str, minimum: float | None = None, maximum: float | None = None) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        fail(f"{label} must be a number")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        fail(f"{label} must be between {minimum} and {maximum}")


def _check_image(zf: zipfile.ZipFile, path: str, label: str, jpeg_only: bool = False) -> None:
    try:
        with zf.open(path) as stream:
            image = Image.open(stream)
            image.verify()
        with zf.open(path) as stream:
            image = Image.open(stream)
            image.load()
            if jpeg_only and image.format != "JPEG":
                fail(f"{label} must be JPEG encoded")
            if not jpeg_only and image.format not in {"JPEG", "PNG", "WEBP"}:
                fail(f"Unsupported image format for {label}: {image.format}")
    except (OSError, ValueError) as exc:
        fail(f"Invalid image {label}: {exc}")


def _check_asset_extension(path: str, media_type: str, label: str) -> None:
    suffix = PurePosixPath(path).suffix.lower()
    if media_type in {"image", "sticker", "frame"} and suffix not in IMAGE_EXTENSIONS:
        fail(f"{label} must reference a supported image file")
    if media_type in {"videoOverlay", "video"} and suffix not in VIDEO_EXTENSIONS:
        fail(f"{label} must reference a supported video file")
    if media_type == "audio" and suffix not in AUDIO_EXTENSIONS:
        fail(f"{label} must reference a supported audio file")


def validate_semantics(meta: dict[str, Any], entries: dict[str, zipfile.ZipInfo], zf: zipfile.ZipFile) -> None:
    try:
        parsed = uuid.UUID(meta["templateId"])
        if parsed.version != 4 or str(parsed) != meta["templateId"].lower():
            fail("templateId must be a canonical UUID v4")
    except (ValueError, AttributeError):
        fail("templateId must be a UUID v4")
    parse_semver(meta["templateVersion"])
    if not isinstance(meta["name"], str) or not meta["name"].strip():
        fail("name must not be empty")
    if not isinstance(meta["description"], str) or not meta["description"].strip():
        fail("description must not be empty")
    duration = meta["durationMs"]
    if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 300000:
        fail("durationMs must be an integer between 1 and 300000")
    canvas = meta["canvas"]
    if not isinstance(canvas["width"], int) or not 1 <= canvas["width"] <= 4320 or not isinstance(canvas["height"], int) or not 1 <= canvas["height"] <= 4320:
        fail("canvas width and height must be integers between 1 and 4320")
    if not isinstance(canvas["fps"], (int, float)) or isinstance(canvas["fps"], bool) or not 1 <= canvas["fps"] <= 120:
        fail("canvas.fps must be between 1 and 120")

    files = {name.casefold(): name for name, info in entries.items() if not info.is_dir()}
    for name, info in entries.items():
        if info.is_dir():
            continue
        parts = name.split("/")
        if parts[0] == "template.json":
            continue
        if parts[0] != "preview.jpg" and (len(parts) < 2 or parts[0] != "assets" or parts[1] not in ALLOWED_ASSET_DIRS):
            fail(f"Unsupported package path: {name}. Assets must be inside approved assets/ folders")
        if len(parts) > 2 and parts[0] == "assets" and parts[1] not in ALLOWED_ASSET_DIRS:
            fail(f"Unsupported asset directory: assets/{parts[1]}")
    if "preview.jpg" not in files:
        fail("Package must contain exactly one root preview.jpg")
    preview_files = [name for name in files.values() if PurePosixPath(name).name.lower() in {"preview.jpg", "poster.jpg", "thumbnail.jpg", "preview.png", "preview.webp", "preview.mp4"}]
    if preview_files != ["preview.jpg"]:
        fail("Package may contain only one preview image named preview.jpg")
    if meta["preview"] != "preview.jpg":
        fail("preview must be preview.jpg")
    _check_image(zf, "preview.jpg", "preview.jpg", jpeg_only=True)

    slots = meta["mediaSlots"]
    slot_ids: set[str] = set()
    for index, slot in enumerate(slots):
        sid = slot["id"]
        if sid in slot_ids:
            fail(f"Duplicate media-slot ID: {sid}")
        slot_ids.add(sid)
        if not isinstance(slot.get("label"), str) or not slot["label"].strip():
            fail(f"mediaSlots[{index}].label must not be empty")

    stages = meta.get("stages", [])
    stage_ids: set[str] = set()
    for stage in stages:
        if stage["id"] in stage_ids:
            fail(f"Duplicate stage ID: {stage['id']}")
        stage_ids.add(stage["id"])
    ordered_stages = sorted(stages, key=lambda item: item["startMs"])
    for previous, current in zip(ordered_stages, ordered_stages[1:]):
        if current["startMs"] < previous["startMs"] + previous["durationMs"]:
            fail(f"Stages {previous['id']} and {current['id']} overlap")
    if ordered_stages and max(s["startMs"] + s["durationMs"] for s in stages) > duration:
        fail("Stage timeline exceeds durationMs")

    layers = meta["layers"]
    layer_ids: set[str] = set()
    for index, layer in enumerate(layers):
        label = f"layers[{index}]"
        if layer["id"] in layer_ids:
            fail(f"Duplicate layer ID: {layer['id']}")
        layer_ids.add(layer["id"])
        start, layer_duration = layer["startMs"], layer["durationMs"]
        if start + layer_duration > duration:
            fail(f"{label} timing exceeds durationMs")
        if "zIndex" in layer and not isinstance(layer["zIndex"], int):
            fail(f"{label}.zIndex must be an integer")
        if layer.get("stageId") is not None and layer["stageId"] not in stage_ids:
            fail(f"{label} references unknown stageId: {layer['stageId']}")
        layout = layer.get("layout")
        if layout:
            for key in ("x", "y", "width", "height"):
                _require_number(layout[key], f"{label}.layout.{key}", -4 if key in {"x", "y"} else 0, 4 if key in {"x", "y"} else 4)
            if layout["width"] <= 0 or layout["height"] <= 0:
                fail(f"{label} layout width and height must be positive")
            if not -360 <= layout.get("rotationDeg", 0) <= 360:
                fail(f"{label}.layout.rotationDeg must be between -360 and 360")
        content = layer.get("content")
        if content:
            _require_number(content.get("scale", 1), f"{label}.content.scale", 0.01, 20)
            _require_number(content.get("offsetX", 0), f"{label}.content.offsetX", -10, 10)
            _require_number(content.get("offsetY", 0), f"{label}.content.offsetY", -10, 10)
        if layer["type"] == "userMedia":
            if layer["slotId"] not in slot_ids:
                fail(f"{label} references invalid media slot: {layer['slotId']}")
        else:
            source = layer.get("source")
            path = _safe_member_path(source, f"{label}.source")
            if path.casefold() not in files:
                fail(f"{label} references missing asset: {path}")
            if path.startswith("assets/") and path.split("/")[1] not in ALLOWED_ASSET_DIRS:
                fail(f"Unsupported asset directory in {label}: {path}")
            _check_asset_extension(path, layer["type"], f"{label}.source")
            if layer["type"] in {"image", "sticker", "frame"}:
                _check_image(zf, files[path.casefold()], f"{label}.source")
        comp = layer.get("compositing")
        if comp:
            if comp["mode"] == "chromaKey":
                if layer["type"] not in {"videoOverlay", "video"}:
                    fail(f"{label} chromaKey compositing requires a video layer")
                if comp.get("keyColor") not in {"green", "#00FF00", "#00ff00", "black", "#000000"}:
                    fail(f"{label} chroma-key keyColor must be green, black, #00FF00, or #000000")
                _require_number(comp.get("similarity"), f"{label}.compositing.similarity", 0, 1)
                _require_number(comp.get("smoothness"), f"{label}.compositing.smoothness", 0, 1)
            elif comp["mode"] != "alpha":
                fail(f"Unsupported compositing mode in {label}: {comp['mode']}")
        for animation in layer.get("animations", []):
            prop = animation["property"]
            if prop not in ANIMATABLE_PROPERTIES:
                fail(f"Unsupported animation property: {prop}")
            frames = animation["keyframes"]
            if len(frames) < 2:
                fail(f"{label} animation requires at least two keyframes")
            previous_time = -1
            for frame in frames:
                if frame["timeMs"] <= previous_time or frame["timeMs"] > layer_duration:
                    fail(f"{label} animation keyframe times must increase within the layer duration")
                previous_time = frame["timeMs"]
                if isinstance(frame["value"], bool) or not isinstance(frame["value"], (int, float)):
                    fail(f"{label} animation keyframe values must be numbers")

    known_features = set(meta.get("requiredFeatures", []))
    unsupported = known_features - SUPPORTED_FEATURES
    if unsupported:
        fail(f"Unsupported required feature: {sorted(unsupported)[0]}")
    if any(layer.get("compositing", {}).get("mode") == "alpha" for layer in layers):
        known_features.add("alpha-compositing")
    if any(layer.get("compositing", {}).get("mode") == "chromaKey" for layer in layers):
        known_features.add("chroma-key")
    if any(layer.get("animations") for layer in layers):
        known_features.add("keyframe-animation")


def validate(zip_path: Path) -> dict[str, Any]:
    entries = safe_zip_entries(zip_path)
    files = {name.casefold() for name, info in entries.items() if not info.is_dir()}
    if "template.json" not in files:
        fail("Required package file missing: template.json")
    with zipfile.ZipFile(zip_path) as zf:
        try:
            meta = json.loads(zf.read("template.json"))
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            fail(f"Invalid template.json: {exc}")
        schema = load_json(ROOT / "schemas" / "template.schema.json")
        errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(meta), key=lambda error: list(map(str, error.path)))
        if errors:
            error = errors[0]
            fail(f"template.json schema error at {'.'.join(map(str, error.path)) or '<root>'}: {error.message}")
        validate_semantics(meta, entries, zf)
    digest = hashlib.sha256()
    with zip_path.open("rb") as package:
        for chunk in iter(lambda: package.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"valid": True, "templateId": meta["templateId"], "templateVersion": meta["templateVersion"], "sha256": digest.hexdigest(), "sizeBytes": zip_path.stat().st_size, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
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
