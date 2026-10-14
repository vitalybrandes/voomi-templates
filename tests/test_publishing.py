import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_template
from common import ROOT, VoomiError


def test_preview_image_path_accepts_direct_and_object_metadata():
    assert publish_template.preview_image_path("preview/poster.jpg") == "preview/poster.jpg"
    assert publish_template.preview_image_path({"poster": "preview/poster.jpg"}) == "preview/poster.jpg"


def test_latest_published_version_matches_uuid_and_semver(monkeypatch, tmp_path):
    wanted_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())
    tags = [
        {"tagName": "template-money-rain-v1.9.0", "isDraft": False, "isPrerelease": False},
        {"tagName": "template-money-rain-v1.10.0", "isDraft": False, "isPrerelease": False},
    ]

    def fake_run(args, **_kwargs):
        if args[1:3] == ["release", "list"]:
            return json.dumps(tags)
        tag = args[3]
        version_id = other_id if tag.endswith("v1.10.0") else wanted_id
        directory = Path(args[args.index("--dir") + 1])
        (directory / f"{tag}.metadata.json").write_text(json.dumps({"id": version_id}), encoding="utf-8")
        return ""

    monkeypatch.setattr(publish_template, "run", fake_run)
    monkeypatch.setattr(
        publish_template,
        "release_view",
        lambda _repo, tag: {"assets": [{"name": f"{tag}.metadata.json"}]},
    )
    index = {"releases": [{"id": wanted_id, "templateVersion": "1.8.0"}]}

    assert publish_template.latest_published_version("owner/repo", "money-rain", wanted_id, index) == (1, 9, 0, ())


def test_publish_requires_strictly_newer_version():
    current = (1, 10, 0, ())
    assert publish_template.should_publish_version("1.10.1", current)
    assert not publish_template.should_publish_version("1.10.0", current)
    assert not publish_template.should_publish_version("1.9.9", current)


def test_mixed_batch_keeps_failure_but_succeeds_for_valid_packages(tmp_path, monkeypatch):
    packages = [ROOT / "incoming" / f"template-{i}.zip" for i in range(5)]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(publish_template, "discover", lambda: packages)
    monkeypatch.setattr(publish_template, "github_repo", lambda: "owner/repo")

    def process(package, _repo, _dry_run):
        if package.name == "template-2.zip":
            raise VoomiError("invalid license manifest")
        return {"path": str(package.relative_to(ROOT)), "tag": package.stem}

    monkeypatch.setattr(publish_template, "process", process)

    assert publish_template.main() == 0
    report = json.loads((tmp_path / "publish-report.json").read_text(encoding="utf-8"))
    processed = (tmp_path / "processed-packages.txt").read_text(encoding="utf-8").splitlines()
    assert len(report["publishedOrSkipped"]) == 4
    assert report["failures"] == [{"path": str(packages[2].relative_to(ROOT)), "error": "invalid license manifest"}]
    assert len(processed) == 4
    assert str(packages[2].relative_to(ROOT)) not in processed


def test_all_failed_batch_warns_and_retains_paths(tmp_path, monkeypatch):
    packages = [ROOT / "incoming" / "bad.zip"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(publish_template, "discover", lambda: packages)
    monkeypatch.setattr(publish_template, "github_repo", lambda: "owner/repo")
    monkeypatch.setattr(publish_template, "process", lambda *_args: (_ for _ in ()).throw(VoomiError("invalid package")))

    assert publish_template.main() == 0
    report = json.loads((tmp_path / "publish-report.json").read_text(encoding="utf-8"))
    assert report["failures"][0]["error"] == "invalid package"
    assert (tmp_path / "processed-packages.txt").read_text(encoding="utf-8") == ""


def test_cleanup_only_push_does_not_rediscover_unrelated_incoming_zip(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"commits": [{"added": [], "modified": [], "removed": ["incoming/previously-published.zip"]}]}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    assert publish_template.discover() == []
