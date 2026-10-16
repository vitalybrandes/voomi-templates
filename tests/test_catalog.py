import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rebuild_catalog import build


def record(version, digest, template_id):
    return {"templateId": template_id, "templateVersion": version, "schemaVersion": "2.0", "name": "Money Rain", "description": "Make it rain.", "categories": ["Fun"], "tags": ["money"], "durationMs": 10000, "requiredFeatures": ["alpha-compositing"], "tag": f"template-{template_id}-v{version}", "assetName": "template.zip", "fileSize": 20, "sha256": digest, "draft": False, "prerelease": False}


def test_catalog_selects_latest_semver_and_stable_version_without_change(monkeypatch):
    template_id = str(uuid.uuid4())
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    index = {"releases": [record("1.9.0", "a" * 64, template_id), record("1.10.0", "b" * 64, template_id)]}
    first = build(index)
    assert first["templates"][0]["templateVersion"] == "1.10.0"
    assert first["templates"][0]["previewUrl"].endswith(f"/{template_id}/1.10.0/preview.jpg")
    assert first["templates"][0]["requiredFeatures"] == ["alpha-compositing"]
    second = build(index, first)
    assert second["catalogVersion"] == first["catalogVersion"]
    assert second["updatedAt"] == first["updatedAt"]


def test_legacy_releases_are_excluded(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    assert build({"releases": [{"templateId": "legacy-slug", "templateVersion": "1.0.0"}]})["templates"] == []
