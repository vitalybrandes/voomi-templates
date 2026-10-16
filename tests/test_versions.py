import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from common import VoomiError, parse_semver, semver_key


def test_semver_is_numeric_not_lexical():
    assert semver_key("1.10.0") > semver_key("1.9.0")


def test_release_sorts_after_prerelease():
    assert semver_key("1.0.0") > semver_key("1.0.0-rc.1")


@pytest.mark.parametrize("value", ["1.0", "01.0.0", "v1.0.0", "1.0.0-01"])
def test_invalid_semver(value):
    with pytest.raises(VoomiError):
        parse_semver(value)
