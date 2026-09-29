import os

import pytest

from pintu.project import PathError, Project


def test_resolve_confined(demo, tmp_path):
    p = Project.open(demo)
    assert p.resolve("plots/lines.pdf") == demo / "plots/lines.pdf"
    assert p.resolve("") == demo
    for bad in ["../x", "plots/../../x", "/etc/passwd", "C:/x", "plots\\x", "a\x00b"]:
        with pytest.raises(PathError):
            p.resolve(bad)


def test_symlink_escape(demo, tmp_path):
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "s.pdf").write_bytes(b"%PDF")
    os.symlink(outside, demo / "link")
    os.symlink(demo / "plots", demo / "inner")
    p = Project.open(demo)
    with pytest.raises(PathError):
        p.resolve("link/s.pdf")
    assert p.resolve("inner/lines.pdf").is_file()
    names = [e["name"] for e in p.list_dir("")]
    assert "link" not in names and "inner" in names


def test_list_dir(demo):
    p = Project.open(demo)
    entries = p.list_dir("plots")
    assert [e["name"] for e in entries] == ["bars.pdf", "heatmap.png", "lines.pdf", "scatter.svg"]
    assert entries[0]["path"] == "plots/bars.pdf" and entries[1]["kind"] == "raster"
    with pytest.raises(PathError):
        p.list_dir("plots/lines.pdf")


def test_board_names(demo):
    assert Project.open(demo).board_names() == ["demo", "recipes"]
    with pytest.raises(PathError):
        Project.open(demo).board_path("../x")
