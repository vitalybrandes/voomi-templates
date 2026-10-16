import io
import json
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import VoomiError
from validate_template import validate


def image_bytes(fmt="JPEG", color=(240, 200, 20)):
    buffer = io.BytesIO()
    Image.new("RGB", (90, 160), color).save(buffer, format=fmt)
    return buffer.getvalue()


def single_template():
    return {
        "schemaVersion": "2.0", "templateId": str(uuid.uuid4()), "templateVersion": "1.0.0",
        "name": "Money Rain", "description": "Animated coins over your media.", "categories": ["Fun"], "tags": ["money"],
        "durationMs": 10000, "canvas": {"width": 720, "height": 1280, "fps": 30}, "preview": "preview.jpg",
        "requiredFeatures": ["layer-transform", "alpha-compositing", "media-crop", "keyframe-animation", "chroma-key", "audio-mixing"],
        "mediaSlots": [{"id": "main_media", "type": "photo_or_video", "label": "Choose your photo or video", "required": True}],
        "stages": [{"id": "stage_1", "name": "Money Rain", "startMs": 0, "durationMs": 10000}],
        "layers": [
            {"id": "background", "type": "image", "source": "assets/backgrounds/gold.jpg", "zIndex": 0, "startMs": 0, "durationMs": 10000, "layout": {"unit": "normalized", "x": 0, "y": 0, "width": 1, "height": 1, "rotationDeg": 0, "anchor": "topLeft"}, "content": {"fit": "cover", "scale": 1, "offsetX": 0, "offsetY": 0}},
            {"id": "user-media", "type": "userMedia", "slotId": "main_media", "zIndex": 10, "startMs": 0, "durationMs": 10000, "layout": {"unit": "normalized", "x": 0, "y": 0, "width": 1, "height": 1, "rotationDeg": 0, "anchor": "topLeft"}, "content": {"fit": "cover", "scale": 1, "offsetX": 0, "offsetY": 0}},
            {"id": "coins", "type": "videoOverlay", "source": "assets/overlays/coins.mp4", "zIndex": 20, "startMs": 0, "durationMs": 10000, "layout": {"unit": "normalized", "x": 0, "y": 0, "width": 1, "height": 1, "rotationDeg": 0, "anchor": "topLeft"}, "content": {"fit": "cover", "scale": 1, "offsetX": 0, "offsetY": 0}, "compositing": {"mode": "chromaKey", "keyColor": "green", "similarity": 0.25, "smoothness": 0.1}},
        ],
    }


def package(tmp_path, meta, extra=None, preview=True):
    path = tmp_path / "template.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("template.json", json.dumps(meta))
        if preview:
            archive.writestr("preview.jpg", image_bytes())
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return path


def test_valid_single_stage_template(tmp_path):
    path = package(tmp_path, single_template(), {"assets/backgrounds/gold.jpg": image_bytes(), "assets/overlays/coins.mp4": b"mp4"})
    assert validate(path)["valid"]


def test_valid_two_stage_sequential_user_video_template(tmp_path):
    meta = single_template()
    meta.update({"durationMs": 12000, "mediaSlots": [
        {"id": "photo_1", "type": "photo", "label": "Choose photo", "required": True},
        {"id": "video_1", "type": "video", "label": "Choose video", "required": True},
    ], "stages": [
        {"id": "stage_1", "name": "Photo", "startMs": 0, "durationMs": 5000},
        {"id": "stage_2", "name": "Video", "startMs": 5000, "durationMs": 7000},
    ], "layers": [
        {"id": "photo", "type": "userMedia", "slotId": "photo_1", "zIndex": 1, "startMs": 0, "durationMs": 5000, "layout": {"unit": "normalized", "x": 0, "y": 0, "width": 1, "height": 1, "rotationDeg": 0, "anchor": "topLeft"}, "content": {"fit": "cover", "scale": 1, "offsetX": 0, "offsetY": 0}},
        {"id": "video", "type": "userMedia", "slotId": "video_1", "zIndex": 1, "startMs": 5000, "durationMs": 7000, "layout": {"unit": "normalized", "x": 0, "y": 0, "width": 1, "height": 1, "rotationDeg": 0, "anchor": "topLeft"}, "content": {"fit": "cover", "scale": 1, "offsetX": 0, "offsetY": 0}},
    ]})
    assert validate(package(tmp_path, meta))["valid"]


def test_overlapping_visual_layers_are_valid(tmp_path):
    meta = single_template()
    assert validate(package(tmp_path, meta, {"assets/backgrounds/gold.jpg": image_bytes(), "assets/overlays/coins.mp4": b"mp4"}))["valid"]


def test_invalid_guid_fails(tmp_path):
    meta = single_template()
    meta["templateId"] = "not-a-guid"
    with pytest.raises(VoomiError, match="schema error|UUID v4"):
        validate(package(tmp_path, meta))


def test_missing_preview_fails(tmp_path):
    with pytest.raises(VoomiError, match="preview.jpg"):
        validate(package(tmp_path, single_template(), preview=False))


def test_missing_asset_fails(tmp_path):
    with pytest.raises(VoomiError, match="missing asset"):
        validate(package(tmp_path, single_template()))


def test_unsupported_asset_directory_fails(tmp_path):
    meta = single_template()
    meta["layers"][0]["source"] = "assets/models/bg.jpg"
    with pytest.raises(VoomiError, match="Unsupported package path|Unsupported asset directory"):
        validate(package(tmp_path, meta, {"assets/models/bg.jpg": image_bytes(), "assets/overlays/coins.mp4": b"mp4"}))


def test_invalid_media_slot_reference_fails(tmp_path):
    meta = single_template()
    meta["layers"][1]["slotId"] = "unknown"
    with pytest.raises(VoomiError, match="invalid media slot"):
        validate(package(tmp_path, meta, {"assets/backgrounds/gold.jpg": image_bytes(), "assets/overlays/coins.mp4": b"mp4"}))


def test_invalid_timeline_interval_fails(tmp_path):
    meta = single_template()
    meta["layers"][1]["durationMs"] = 20000
    with pytest.raises(VoomiError, match="timing exceeds"):
        validate(package(tmp_path, meta, {"assets/backgrounds/gold.jpg": image_bytes(), "assets/overlays/coins.mp4": b"mp4"}))


def test_unsupported_animation_property_fails(tmp_path):
    meta = single_template()
    meta["layers"][1]["animations"] = [{"target": "layout", "property": "hacked", "keyframes": [{"timeMs": 0, "value": 0}, {"timeMs": 10, "value": 1}]}]
    with pytest.raises(VoomiError, match="Unsupported animation property"):
        validate(package(tmp_path, meta, {"assets/backgrounds/gold.jpg": image_bytes(), "assets/overlays/coins.mp4": b"mp4"}))


def test_invalid_chroma_key_fails(tmp_path):
    meta = single_template()
    meta["layers"][2]["compositing"]["similarity"] = 2
    with pytest.raises(VoomiError, match="similarity"):
        validate(package(tmp_path, meta, {"assets/backgrounds/gold.jpg": image_bytes(), "assets/overlays/coins.mp4": b"mp4"}))
