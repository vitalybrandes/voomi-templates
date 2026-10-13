import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import publish_template
from common import ROOT, VoomiError


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
