"""D05–D12 的解码通道：真实 stderr 样本解析 + 夹具全量集成。"""

import threading
from pathlib import Path

import pytest

from videoqc.app.config import Config
from videoqc.core.detectors.audio import check_d06, check_d07, check_d08
from videoqc.core.detectors.corruption import check_d12
from videoqc.core.detectors.freeze import check_d05, check_d09
from videoqc.core.model import DecodeEvents
from videoqc.core.pipeline import analyze_file
from videoqc.media.stderr_parse import State, close_open, parse_line

pytestmark = pytest.mark.usefixtures("require_binaries")


def _parse(name: str, data_dir) -> DecodeEvents:
    ev, st = DecodeEvents(), State()
    for line in (data_dir / name).read_text(encoding="utf-8", errors="replace").splitlines():
        parse_line(line, ev, st)
    return ev


# ---- 解析真实 ffmpeg stderr（ffmpeg 升级换文案时这里会先炸）----
def test_parse_freeze(data_dir):
    ev = _parse("freeze.log", data_dir)
    assert ev.freezes == [(4.0, 7.0)]


def test_parse_black(data_dir):
    ev = _parse("black.log", data_dir)
    assert len(ev.blacks) == 1
    assert ev.blacks[0] == pytest.approx((0.0, 3.966667), abs=1e-3)


def test_parse_silence_and_volume(data_dir):
    ev = _parse("silence.log", data_dir)
    assert ev.silences == [(0.0, 20.0)]
    assert (ev.mean_volume, ev.max_volume) == (-91.0, -91.0)

    ev = _parse("volume.log", data_dir)
    assert ev.mean_volume == pytest.approx(-21.1) and ev.max_volume == pytest.approx(-17.8)


def test_parse_errors_splits_streams():
    """stderr 里带 stream #0:N 的报错要归到对应流：音频报错不能算成花屏。"""
    ev, st = DecodeEvents(), State(video_index=0, audio_index=1)
    parse_line("[aac @ x] Error while decoding stream #0:1: Invalid data found", ev, st)
    parse_line("[h264 @ x] Invalid NAL unit size", ev, st)
    assert (ev.audio_errors, ev.video_errors) == (1, 1)
    assert ev.error_count == 2


def test_parse_errors_ignores_file_header(data_dir):
    ev = _parse("errors.log", data_dir)
    assert ev.error_count > 5
    # 文件名本身含 "corrupt"，但 "Input #0 ... from 'corrupt.mp4'" 不能被算成解码错误
    assert all("from '" not in e for e in ev.errors)
    assert any("Invalid NAL" in e for e in ev.errors)


# ---- 解码主循环 ----
def test_pairing_loss_keeps_events(data_dir, monkeypatch):
    """showinfo 配对失步时只放弃采样，不能 kill 进程——否则冻结/黑场事件跟着一起丢。"""
    import re

    from videoqc.media import runners

    monkeypatch.setattr(runners, "SHOWINFO_PTS", re.compile(r"^NOMATCH$"))
    ev = runners.run_decode_pass(
        str(data_dir / "freeze3.mp4"),
        Config(),
        has_audio=True,
        origin_sec=0.0,
        duration_sec=20.0,
    )
    assert ev.completed and ev.exit_code == 0
    assert ev.samples == [] and ev.samples_incomplete
    assert ev.freezes  # 事件仍然完整，这才是修 bug 的意义


def test_closed_at_eof_when_end_missing():
    ev, st = DecodeEvents(), State()
    parse_line("[Parsed_freezedetect_0 @ x] lavfi.freezedetect.freeze_start: 2", ev, st)
    close_open(ev, st, 10.0)
    assert ev.freezes == [(2.0, 10.0)]


def test_freeze_duration_used_when_end_missing():
    """SPEC §4.3：只有 duration 没有 end 时，end = start + duration，不能拖到 EOF 误报长冻结。"""
    ev, st = DecodeEvents(), State()
    parse_line("[Parsed_freezedetect_0 @ x] lavfi.freezedetect.freeze_start: 2", ev, st)
    parse_line("[Parsed_freezedetect_0 @ x] lavfi.freezedetect.freeze_duration: 1.5", ev, st)
    close_open(ev, st, 20.0)  # EOF=20，但 duration 说只冻到 3.5
    assert ev.freezes == [(2.0, 3.5)]


def test_next_start_closes_previous_with_duration():
    """上一个冻结的 end 行缺失又来了新 start：用 duration 收口，不能把两段并成一段。"""
    ev, st = DecodeEvents(), State()
    parse_line("[Parsed_freezedetect_0 @ x] lavfi.freezedetect.freeze_start: 2", ev, st)
    parse_line("[Parsed_freezedetect_0 @ x] lavfi.freezedetect.freeze_duration: 1.5", ev, st)
    parse_line("[Parsed_freezedetect_0 @ x] lavfi.freezedetect.freeze_start: 10", ev, st)
    close_open(ev, st, 20.0)
    assert ev.freezes == [(2.0, 3.5), (10.0, 20.0)]


# ---- 检测器（合成 DecodeEvents）----
def _ctx(decode, dur=20.0, has_audio=True):
    from videoqc.core.context import FileContext
    from videoqc.core.model import StreamInfo

    s = StreamInfo(
        path="x.mp4",
        duration=dur,
        container="mov",
        size_bytes=1,
        bit_rate=None,
        video={"duration": dur},
        audio={"duration": dur} if has_audio else None,
        has_audio=has_audio,
    )
    return FileContext(path=Path("x.mp4"), cfg=Config(), streams=s, decode=decode)


def test_d05_no_divzero_when_duration_zero():
    """容器无时长 + 包探测失败时 dur=0；D05 不能抛 ZeroDivisionError 被吞成 internal_error。"""
    ev = DecodeEvents(freezes=[(2.0, 5.0)])
    issues = check_d05(_ctx(ev, dur=0.0))
    assert len(issues) == 1 and issues[0].kind == "freeze"


def test_d05_skips_zero_and_inverted_freeze():
    """d=0 时 black/d 会除零；d<0（EOF 收口的片长早于 start）也不能算出离谱的比值。"""
    ev = DecodeEvents(freezes=[(5.0, 5.0), (9.0, 7.0), (4.0, 7.0)])
    issues = check_d05(_ctx(ev))
    assert [i.title for i in issues] == ["画面冻结 3.0s"]


def test_d05_freeze_severities():
    ev = DecodeEvents(freezes=[(4.0, 7.0), (10.0, 21.0)], blacks=[])
    got = {i.title: i.severity for i in check_d05(_ctx(ev))}
    assert got == {"画面冻结 3.0s": "medium", "画面冻结 11.0s": "high"}


def test_d05_inside_black_is_info():
    ev = DecodeEvents(freezes=[(1.0, 6.0)], blacks=[(0.0, 20.0)])
    assert [i.severity for i in check_d05(_ctx(ev))] == ["info"]


def test_d09_black_severities():
    ev = DecodeEvents(blacks=[(0.0, 4.0), (8.0, 9.5)])
    assert [i.severity for i in check_d09(_ctx(ev, dur=20.0))] == ["info", "medium"]


def test_d06_silence_and_full_silent_suppression():
    ev = DecodeEvents(silences=[(5.0, 10.0)], mean_volume=-20.0)
    assert [i.severity for i in check_d06(_ctx(ev))] == ["medium"]

    quiet = DecodeEvents(silences=[(0.0, 20.0)], mean_volume=-91.0)
    assert check_d06(_ctx(quiet)) == []  # 整轨无声由 D07 报一条
    assert [i.kind for i in check_d07(_ctx(quiet))] == ["silent_audio"]


def test_d07_low_volume_and_clipping():
    ev = DecodeEvents(mean_volume=-35.0, max_volume=0.0)
    kinds = {i.kind: i.severity for i in check_d07(_ctx(ev))}
    assert kinds == {"low_volume": "medium", "clipping": "low"}


def test_d08_no_audio():
    assert [i.kind for i in check_d08(_ctx(None, has_audio=False))] == ["no_audio"]
    assert check_d08(_ctx(None)) == []


def test_d12_needs_decode_and_scales():
    assert check_d12(_ctx(None)) == []
    ev = DecodeEvents(error_count=3, video_errors=3, errors=["Invalid NAL unit size"])
    assert [i.severity for i in check_d12(_ctx(ev))] == ["medium"]
    ev = DecodeEvents(error_count=60, video_errors=60)
    assert [i.severity for i in check_d12(_ctx(ev))] == ["high"]


def test_d12_bad_packets_are_localized_in_time():
    """坏包要能跳转定位（v0.3.5）：首/末时间、最密集 5s 窗口都进 evidence。"""
    from videoqc.core.model import PacketStats

    ctx = _ctx(DecodeEvents())
    ctx.packets = PacketStats(
        pts=[i * 0.04 for i in range(1000)],
        p50=0.04,
        dts_non_monotonic=0,
        dup_pts=0,
        corrupt_flags=5,
        first_ts=0.0,
        last_ts=39.96,
        corrupt_pts=[1.0, 10.0, 10.2, 10.4, 30.0],
        corrupt_last_ts=30.0,
    )
    (issue,) = check_d12(ctx)
    assert issue.start == pytest.approx(1.0) and issue.end == pytest.approx(30.0)
    assert issue.evidence["busiest_sec"] == pytest.approx(10.0)  # 5s 窗口 10.0-10.4 最密
    assert "00:00:01.000" in issue.title


def test_d12_decode_error_lines_carry_estimated_time():
    """解码报错行本身没时间戳：用最近一个 showinfo 采样点估位置（v0.3.5）。"""
    from videoqc.media.stderr_parse import State, parse_line

    ev = DecodeEvents()
    st = State(video_index=0, audio_index=1)
    st.last_pts = 12.0
    parse_line("Error while decoding stream #0:0: Invalid NAL unit size", ev, st)
    parse_line("Error while decoding stream #0:1: garbage", ev, st)
    assert ev.video_errors == 1 and ev.audio_errors == 1
    assert ev.video_error_times == [pytest.approx(12.0)]
    assert ev.audio_error_times == [pytest.approx(12.0)]
    (issue,) = [i for i in check_d12(_ctx(ev)) if i.kind == "decode_errors"]
    assert issue.start == pytest.approx(12.0)


def test_d12_audio_errors_are_not_reported_as_corruption():
    """音频解码报错不能冒充“花屏/码流损坏”，也不能因为单个坏包把整片判 failed。"""
    ev = DecodeEvents(error_count=5, audio_errors=5)
    got = {i.kind: i.severity for i in check_d12(_ctx(ev))}
    assert got == {"audio_decode_errors": "medium"}

    from videoqc.core.model import PacketStats

    ctx = _ctx(DecodeEvents())
    ctx.packets = PacketStats(
        pts=[0.0] * 1000,
        p50=0.04,
        dts_non_monotonic=0,
        dup_pts=0,
        corrupt_flags=1,
        first_ts=0.0,
        last_ts=39.0,
    )
    assert [i.severity for i in check_d12(ctx)] == ["medium"]  # 1/1000 = 0.1%，零星 C 位
    ctx.packets = PacketStats(
        pts=[0.0] * 1000,
        p50=0.04,
        dts_non_monotonic=0,
        dup_pts=0,
        corrupt_flags=4,
        first_ts=0.0,
        last_ts=39.0,
    )
    assert [i.severity for i in check_d12(ctx)] == ["high"]  # 4 个以上 → 真损坏


# ---- 夹具全量集成（§10.2 回归表）----
def _scan(path, mode="full"):
    return analyze_file(path, Config(), mode, 0, threading.Event())


def _kinds(r):
    return {i.kind: i.severity for i in r.issues}


def test_fixture_freeze(data_dir):
    r = _scan(data_dir / "freeze3.mp4")
    got = _kinds(r)
    assert got.get("freeze") == "medium"
    assert not any(i.kind == "pts_gap" for i in r.issues)


def test_fixture_black(data_dir):
    assert _kinds(_scan(data_dir / "black4.mp4")).get("black") == "medium"


def test_fixture_muted(data_dir):
    got = _kinds(_scan(data_dir / "muted.mp4"))
    assert got.get("silent_audio") == "high"
    assert "silence" not in got  # 不逐段重复报


def test_fixture_hot(data_dir):
    got = _kinds(_scan(data_dir / "hot.mp4"))
    assert got.get("clipping") == "low"


def test_fixture_noaudio(data_dir):
    assert _kinds(_scan(data_dir / "noaudio.mp4")).get("no_audio") == "high"


def test_fixture_corrupt(data_dir):
    got = _kinds(_scan(data_dir / "corrupt.mp4"))
    assert got.get("decode_errors") in ("medium", "high")


def test_fixture_base_is_clean(data_dir):
    r = _scan(data_dir / "base.mp4")
    assert [i for i in r.issues if i.severity in ("low", "medium", "high")] == []


def test_d05_freeze_with_silence_is_info():
    ev = DecodeEvents(freezes=[(4.0, 16.0)], silences=[(4.0, 16.0)])
    issues = check_d05(_ctx(ev))
    assert [i.severity for i in issues] == ["info"]
    assert "暂停" in issues[0].title


def test_d07_empty_audio_stream():
    ev = DecodeEvents(completed=True, mean_volume=None)
    assert [i.kind for i in check_d07(_ctx(ev))] == ["empty_audio"]


def test_d08_respects_require_audio_switch():
    from dataclasses import replace

    ctx = _ctx(None, has_audio=False)
    assert [i.kind for i in check_d08(ctx)] == ["no_audio"]
    off = replace(ctx, cfg=replace(ctx.cfg, require_audio=False))
    assert check_d08(off) == []


def test_d07_quiet_but_not_silent_is_medium():
    """均音量刚到 -46dB 但只有 2 成时长是静音：算音量偏低，不能判 failed。"""
    ev = DecodeEvents(mean_volume=-46.0, silences=[(0.0, 20.0)])
    issues = check_d07(_ctx(ev, dur=100.0))
    assert [i.kind for i in issues] == ["low_volume"] and issues[0].severity == "medium"
