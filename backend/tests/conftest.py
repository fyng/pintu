import os
import shutil
from pathlib import Path
from typing import Optional

import pytest

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "examples" / "demo"
KARE_ENV = "PINTU_KARE_DIR"


def kare_dir() -> Optional[Path]:
    """A kare checkout: ``$PINTU_KARE_DIR``, then ``../kare``, then ``../academic-design-system``; None if absent."""
    env = os.environ.get(KARE_ENV)
    for d in ([Path(env)] if env else []) + [REPO.parent / "kare", REPO.parent / "academic-design-system"]:
        if (d / "core").is_dir():
            return d.resolve()
    return None


KARE_SKIP = f"no kare checkout (set ${KARE_ENV}, or check out kare next to pintu)"


@pytest.fixture
def demo(tmp_path):
    """A copy of the example project."""
    dst = tmp_path / "demo"
    shutil.copytree(DEMO, dst, ignore=shutil.ignore_patterns("build", "scratch"))
    return dst
