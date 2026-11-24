"""管线端到端：真实 mp4 快检 + 损坏文件致命失败。"""
import threading

import pytest

from videoqc.app.config import Config
from videoqc.core.pipeline import analyze_file

pytestmark = pytest.mark.usefixtures("require_binaries")


def test_fast_scan_passes_on_valid_file(data_dir):
    p = data_dir / "base.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/base.mp4")
    r = analyze_file(p, Config(), "fast", 0, threading.Event())
    assert (r.status, r.conclusion) == ("done", "passed")
    assert r.stats["packet_count"] == pytest.approx(600, abs=2)
    assert r.elapsed_sec > 0


def test_decode_timeout_yields_unfinished(data_dir, monkeypatch):
    """解码超时必须 status="timeout" → conclusion="unfinished"，不能混进 warned 统计。

    用 monkeypatch 注入一个 timed_out 的 DecodeEvents，避免真的等 3600s 超时。
    """
    import videoqc.core.pipeline as pl
    from videoqc.core.model import DecodeEvents

    p = data_dir / "base.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/base.mp4")

    def _fake_decode(*_a, **_k):
        return DecodeEvents(completed=False, timed_out=True, exit_code=-9)

    monkeypatch.setattr(pl, "run_decode_pass", _fake_decode)
    r = analyze_file(p, Config(), "full", 0, threading.Event())
    assert r.status == "timeout"
    assert r.conclusion == "unfinished" and r.ok is False
    # 超时不应再追加 decode_aborted（那是非超时的"崩溃"专属）
    assert "decode_aborted" not in [i.kind for i in r.issues]


def test_corrupt_file_is_failed_not_unfinished(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00" * 2048)
    r = analyze_file(bad, Config(), "fast", 0, threading.Event())
    assert r.status == "failed"
    assert r.conclusion == "failed" and r.ok is False
    assert [i.kind for i in r.issues] == ["probe_failed"]
    assert r.issues[0].severity == "high"
