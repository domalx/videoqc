"""headless 入口：退出码契约（SPEC §6.3）。这片代码以前 0% 覆盖，一直靠手工冒烟。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from videoqc.app import cli
from videoqc.media.paths import ROOT

DATA = ROOT / "tests" / "data"


@pytest.fixture
def clips(tmp_path) -> Path:
    """直接借用 tests/data 的合成夹具（20s、带音轨），只把坏文件写到临时目录。"""
    shutil.copy(DATA / "base.mp4", tmp_path / "base.mp4")
    (tmp_path / "bad.mp4").write_bytes(b"not an mp4" * 512)   # probe 失败 → 退出码 1
    return Path(tmp_path)


def test_exit_0_when_everything_passes(clips, tmp_path, capsys):
    out = tmp_path / "r.md"
    assert cli.main(["--fast", str(clips / "base.mp4"), "-o", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    assert "videoqc" in md and "通过" in md
    assert out.with_suffix(".json").exists()
    assert "[1/1]" in capsys.readouterr().err


def test_exit_1_when_a_file_fails(clips, tmp_path):
    out = tmp_path / "r.md"
    assert cli.main(["--fast", str(clips / "bad.mp4"), "-o", str(out)]) == 1
    assert "失败" in out.read_text(encoding="utf-8")


def test_exit_2_on_usage_error(tmp_path, capsys):
    assert cli.main(["--fast", str(tmp_path / "nope")]) == 2
    assert "参数错误" in capsys.readouterr().err
