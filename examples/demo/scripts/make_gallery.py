"""Writes 300 linked scratch timelines (``scratch/timelines/<id>.pdf`` and sidecars) for the gallery."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pintu_sdk import save  # noqa: E402

from recipes.cohort import patients, timeline  # noqa: E402

OUT = ROOT / "scratch" / "timelines"


def main(n=300):
    t = time.perf_counter()
    for pid, arm, stage in patients(n):
        params = {"patient": pid, "arm": arm, "stage": stage}
        save(timeline(89, 40, **params), OUT / f"{pid}.pdf", recipe="recipes.cohort:timeline", params=params)
    print(f"wrote {n} timelines to {OUT} in {time.perf_counter() - t:.1f} s")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 300)
