"""D02/D03/D04/D14 检测器：合成 Context 单测 + gap.mp4 真实夹具集成。"""
import threading

import pytest

from videoqc.app.config import Config
from videoqc.core.context import FileContext
from videoqc.core.detectors.integrity import check_d02, check_d14
from videoqc.core.detectors.timing import check_d03, check_d04
from videoqc.core.model import PacketStats, StreamInfo
from videoqc.core.pipeline import analyze_file


def _ctx(**kw) -> FileContext:
    return FileContext(path=kw.pop("path", "x.mp4"), cfg=Config(), **kw)


def _streams(dv=10.0, da=10.0, vstart=0.0, astart=0.0) -> StreamInfo:
    return StreamInfo(
        path="x.mp4", duration=dv if not da else max(dv, da), container="mov,mp4",
        size_bytes=1, bit_rate=None,
        video={"duration": dv, "start_time": vstart},
        audio={"duration": da, "start_time": astart}, has_audio=True,
    )


def _packets(pts, p50=1 / 30, **kw) -> PacketStats:
    return PacketStats(pts=pts, p50=p50, dts_non_monotonic=kw.get("dtsnm", 0),
                       dup_pts=kw.get("dup", 0), corrupt_flags=0,
                       first_ts=pts[0], last_ts=pts[-1],
                       nominal_fps=kw.get("fps"))


# ---- D02 ----
def test_d02_duration_mismatch():
    issues = check_d02(_ctx(streams=_streams(dv=10.0, da=8.0)))
    assert [i.kind for i in issues] == ["av_duration_mismatch"]
    assert issues[0].severity == "medium"


def test_d02_start_offset():
    issues = check_d02(_ctx(streams=_streams(vstart=0.0, astart=0.8)))
    assert [i.kind for i in issues] == ["av_start_offset"]


def test_d02_clean_and_no_audio():
    assert check_d02(_ctx(streams=_streams())) == []
    assert check_d02(_ctx(streams=_streams(da=None))) == []


# ---- D03 ----
def test_d03_detects_gap_and_severity():
    pts = [i / 30 for i in range(30)] + [i / 30 for i in range(40, 60)]   # 缺 10 帧
    # 时长 120s：缺口占全片 < 0.5% ⇒ medium（10s 片子上 0.33s 缺口就够 high 了）
    issues = check_d03(_ctx(packets=_packets(pts), streams=_streams(dv=120, da=120)))
    assert len(issues) == 1
    gap = issues[0]
    assert gap.kind == "pts_gap" and gap.severity == "medium"
    assert gap.start == pytest.approx(29 / 30) and gap.end == pytest.approx(40 / 30)
    assert gap.evidence["missing_frames"] == 10
    assert "缺约 10 帧" in gap.title


def test_d03_short_holes_are_ignored_and_zero_p50_skips():
    pts = [i / 30 for i in range(60)]
    assert check_d03(_ctx(packets=_packets(pts), streams=_streams())) == []
    dup = _packets([0.0] * 10, p50=0.0)
    assert check_d03(_ctx(packets=dup, streams=_streams())) == []


def test_d03_merges_nearby_gaps():
    pts = ([i / 30 for i in range(10)] + [i / 30 for i in range(21, 23)]
           + [i / 30 for i in range(42, 44)])
    issues = check_d03(_ctx(packets=_packets(pts), streams=_streams(dv=120, da=120)))
    assert len(issues) == 1
    assert issues[0].start == pytest.approx(9 / 30) and issues[0].end == pytest.approx(42 / 30)
    assert issues[0].evidence["missing_frames"] > 20


# ---- D04 ----
def test_d04_reports_all_anomalies():
    pts = [i / 30 for i in range(30)]
    issues = check_d04(_ctx(packets=_packets(pts, dtsnm=3, dup=2, fps=20.0)))
    kinds = {i.kind for i in issues}
    assert {"dts_non_monotonic", "dup_pts", "fps_mismatch"} <= kinds
    assert check_d04(_ctx(packets=None)) == []


def test_d04_vfr_is_info():
    pts, t = [], 0.0
    for d in [1 / 30, 1 / 10] * 30:
        pts.append(t)
        t += d
    issues = check_d04(_ctx(packets=_packets(pts, p50=0.0666)))
    vfr = [i for i in issues if i.kind == "vfr_suspected"]
    assert vfr and vfr[0].severity == "info"


# ---- D14 ----
def test_d14_truncation():
    issues = check_d14(_ctx(streams=_streams(dv=10.0, da=1.0), packets=_packets([0.0, 5.0])))
    assert [i.kind for i in issues] == ["truncated"]
    assert issues[0].severity == "medium" and "5.0s" in issues[0].title


def test_d14_not_fooled_by_long_audio():
    """音轨比视频长是正常封装（10s 视频 + 14s 音轨不能报截断）。"""
    streams = _streams(dv=10.0, da=14.0)
    assert check_d14(_ctx(streams=streams, packets=_packets([0.0, 10.0]))) == []


def test_d14_ignores_small_tail_gap():
    assert check_d14(_ctx(streams=_streams(), packets=_packets([0.0, 9.99]))) == []


# ---- 真实夹具 ----
@pytest.mark.usefixtures("require_binaries")
def test_gap_fixture_end_to_end(data_dir):
    p = data_dir / "gap.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/gap.mp4")
    r = analyze_file(p, Config(), "fast", 0, threading.Event())
    gaps = [i for i in r.issues if i.kind == "pts_gap"]
    assert len(gaps) == 1 and gaps[0].severity == "high"
    assert gaps[0].start == pytest.approx(5.0, abs=0.2)
    assert r.conclusion == "failed"


def test_d04_single_jump_not_mistaken_for_vfr():
    """CFR 视频中间一处跳秒不该把 pstdev 拉高后误判成 VFR（MAD 对离群点免疫）。"""
    pts = [i / 30 for i in range(300)]
    pts[150:] = [p + 1.0 for p in pts[150:]]         # 150 帧后整体跳 1s，帧间隔仍恒定
    kinds = {i.kind for i in check_d04(_ctx(packets=_packets(pts, fps=30.0)))}
    assert "vfr_suspected" not in kinds
    assert "fps_mismatch" not in kinds                # 没被误判 VFR，帧率结论仍可用


def test_d04_fps_mismatch_suppressed_for_vfr():
    """VFR 源上 1/p50 不代表声明帧率，不该再报「帧率不符」。"""
    pts, t = [], 0.0
    for d in [1 / 30, 1 / 10] * 30:
        pts.append(t)
        t += d
    kinds = {i.kind for i in check_d04(_ctx(packets=_packets(pts, p50=0.0666, fps=30.0)))}
    assert "vfr_suspected" in kinds and "fps_mismatch" not in kinds
