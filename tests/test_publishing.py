import json
import sys
import uuid
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_template
from common import VoomiError


def test_batch_rejects_before_any_package_is_processed(monkeypatch, tmp_path):
    packages = [tmp_path / "a.zip", tmp_path / "b.zip"]
    monkeypatch.setattr(publish_template, "validate", lambda path: (_ for _ in ()).throw(VoomiError("bad package")) if path.name == "b.zip" else {"valid": True})
    with pytest.raises(VoomiError, match="before publishing") as error:
        publish_template.validate_batch(packages)
    assert "bad package" in str(error.value)


def test_release_tag_uses_uuid_and_version(monkeypatch, tmp_path):
    template_id = str(uuid.uuid4())
    meta = {"schemaVersion": "2.0", "templateId": template_id, "templateVersion": "1.0.0", "name": "T", "description": "D", "durationMs": 1000, "canvas": {"width": 720, "height": 1280, "fps": 30}, "preview": "preview.jpg", "mediaSlots": [], "layers": [{"id": "image", "type": "image", "source": "assets/images/a.png", "startMs": 0, "durationMs": 1000}]}
    path = tmp_path / "sample.zip"
    from PIL import Image
    import io
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "JPEG")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("template.json", json.dumps(meta))
        archive.writestr("preview.jpg", buffer.getvalue())
        archive.writestr("assets/images/a.png", buffer.getvalue())
    monkeypatch.setattr(publish_template, "ROOT", tmp_path)
    monkeypatch.setattr(publish_template, "INDEX", tmp_path / "release-index.json")
    monkeypatch.setattr(publish_template, "validate", lambda _path: {"sha256": "a" * 64, "sizeBytes": path.stat().st_size})
    monkeypatch.setattr(publish_template, "release_view", lambda *_args: None)
    monkeypatch.setattr(publish_template, "run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(publish_template, "verify_release", lambda *_args: None)
    monkeypatch.setattr(publish_template, "semver_key", lambda value: (1, 0, 0, ()))
    result = publish_template.process(path, "owner/repo", report={"sha256": "a" * 64, "sizeBytes": path.stat().st_size})
    assert result["tag"] == f"template-{template_id}-v1.0.0"
    assert result["assetName"] == "template.zip"


def test_duplicate_version_with_different_content_is_rejected(monkeypatch, tmp_path):
    template_id = str(uuid.uuid4())
    path = tmp_path / "sample.zip"
    meta = {"schemaVersion": "2.0", "templateId": template_id, "templateVersion": "1.0.0"}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("template.json", json.dumps(meta))
    monkeypatch.setattr(publish_template, "validate", lambda _path: {"sha256": "b" * 64, "sizeBytes": 1})
    monkeypatch.setattr(publish_template, "INDEX", tmp_path / "release-index.json")
    (tmp_path / "release-index.json").write_text(json.dumps({"releases": [{"templateId": template_id, "templateVersion": "1.0.0", "sha256": "a" * 64}]}))
    with pytest.raises(VoomiError, match="different content"):
        publish_template.process(path, "owner/repo")


def test_manual_dispatch_without_path_does_not_select_everything(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.delenv("INPUT_ZIP_PATH", raising=False)
    assert publish_template.discover() == []
