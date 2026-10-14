import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import VoomiError
from validate_template import validate


def test_missing_template_json_fails(tmp_path):
    p = tmp_path / "missing.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("README.md", "x")
    with pytest.raises(VoomiError, match="Required package file missing"):
        validate(p)


def test_invalid_json_fails(tmp_path):
    p = tmp_path / "invalid.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("template.json", "{")
        for name in ("README.md", "preview/poster.jpg", "preview/thumbnail.jpg", "licenses/asset-manifest.json"):
            z.writestr(name, "x")
    with pytest.raises(VoomiError, match="Invalid template.json"):
        validate(p)


def test_schema_requires_metadata_fields(tmp_path):
    p = tmp_path / "metadata.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("template.json", json.dumps({"schemaVersion":"1.0.0"}))
        for name in ("README.md", "preview/poster.jpg", "preview/thumbnail.jpg", "licenses/asset-manifest.json"):
            z.writestr(name, "x")
    with pytest.raises(VoomiError, match="template.json schema error"):
        validate(p)
