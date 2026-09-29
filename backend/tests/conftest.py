import shutil
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[2] / "examples" / "demo"


@pytest.fixture
def demo(tmp_path):
    """A copy of the example project."""
    dst = tmp_path / "demo"
    shutil.copytree(DEMO, dst, ignore=shutil.ignore_patterns("build"))
    return dst
