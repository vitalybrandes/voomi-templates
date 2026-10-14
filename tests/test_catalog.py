import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rebuild_catalog import build


def record(version, digest):
    return {"templateId":"money-rain","templateVersion":version,"name":"Money Rain","description":"Make a video.","category":"Fun","categories":["Fun"],"tags":["money"],"durationMs":15000,"mediaSlots":[{"id":"main","type":"photo_or_video","required":True}],"minimumAppVersion":"1.0.0","tag":f"template-money-rain-v{version}","assetName":f"money-rain-v{version}.zip","sha256":digest,"sizeBytes":20,"draft":False,"prerelease":False}


def test_catalog_selects_latest_semver_and_stable_version_without_change():
    index = {"releases":[record("1.9.0", "a"*64), record("1.10.0", "b"*64)]}
    first = build(index)
    assert first["templates"][0]["version"] == "1.10.0"
    assert "previewUrl" in first["templates"][0]
    second = build(index, first)
    assert second["catalogVersion"] == first["catalogVersion"]
    assert second["updatedAt"] == first["updatedAt"]


def test_older_release_does_not_downgrade():
    index = {"releases":[record("2.0.0", "a"*64), record("1.9.0", "b"*64)]}
    assert build(index)["templates"][0]["version"] == "2.0.0"
