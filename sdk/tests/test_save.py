import json
import sys
from pathlib import Path

from pintu_sdk import save
from pintu_sdk.save import code_hash, sidecar_path


class FakeFig:
    """Stands in for a matplotlib Figure; the SDK has no dependencies."""

    def __init__(self, w_in, h_in):
        self.size = (w_in, h_in)

    def savefig(self, path, **kw):
        Path(path).write_bytes(b"%PDF-1.4 fake")

    def get_size_inches(self):
        return self.size


def test_save_writes_output_and_sidecar(tmp_path, monkeypatch):
    (tmp_path / "pintu.toml").write_text("")
    (tmp_path / "rec").mkdir()
    (tmp_path / "rec/__init__.py").write_text("")
    (tmp_path / "rec/plot.py").write_text("from . import util\ndef tl(w, h, patient): ...\n")
    (tmp_path / "rec/util.py").write_text("X = 1\n")
    monkeypatch.chdir(tmp_path / "rec")
    out = tmp_path / "scratch/P001.pdf"
    side = save(FakeFig(4, 2), out, recipe="rec.plot:tl", params={"patient": "P001", "n": 3})
    assert out.read_bytes().startswith(b"%PDF") and side == sidecar_path(out)
    assert side.name == "P001.pdf.meta.json"
    meta = json.loads(side.read_text())
    assert set(meta) == {"recipe", "params", "width_mm", "height_mm", "code_hash", "git_sha", "created"}
    assert meta["recipe"] == "rec.plot:tl" and meta["params"] == {"patient": "P001", "n": 3}
    assert (meta["width_mm"], meta["height_mm"]) == (101.6, 50.8)
    assert meta["code_hash"] == code_hash(tmp_path, "rec.plot") and len(meta["code_hash"]) == 64


def test_code_hash_matches_backend_worker(tmp_path):
    backend = Path(__file__).resolve().parents[2] / "backend" / "src" / "pintu"
    sys.path.insert(0, str(backend))
    try:
        import worker
    finally:
        sys.path.remove(str(backend))
    (tmp_path / "a.py").write_text("import b\nfrom c import d\n")
    (tmp_path / "b.py").write_text("Y = 2\n")
    (tmp_path / "c.py").write_text("d = 3\n")
    assert code_hash(tmp_path, "a") == worker.code_hash(str(tmp_path), "a") is not None
    assert code_hash(tmp_path, "missing") is None


def test_save_unlinked(tmp_path):
    side = save(FakeFig(1, 1), tmp_path / "x.png")
    meta = json.loads(side.read_text())
    assert meta["recipe"] is None and meta["code_hash"] is None and meta["params"] == {}
