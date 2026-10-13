import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import VoomiError, safe_zip_entries


def make_zip(tmp_path, entries):
    p = tmp_path / "test.zip"
    with zipfile.ZipFile(p, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return p


def test_rejects_path_traversal(tmp_path):
    with pytest.raises(VoomiError, match="Unsafe ZIP path"):
        safe_zip_entries(make_zip(tmp_path, [("../escape", "x")]))


def test_rejects_absolute_path(tmp_path):
    with pytest.raises(VoomiError, match="Unsafe ZIP path"):
        safe_zip_entries(make_zip(tmp_path, [("/root/file", "x")]))


def test_rejects_duplicate_normalized_names(tmp_path):
    with pytest.raises(VoomiError, match="Duplicate normalized"):
        safe_zip_entries(make_zip(tmp_path, [("assets/a.txt", "x"), ("ASSETS/a.txt", "y")]))


def test_rejects_executable_extension(tmp_path):
    with pytest.raises(VoomiError, match="Unexpected executable"):
        safe_zip_entries(make_zip(tmp_path, [("assets/tool.exe", "x")]))
