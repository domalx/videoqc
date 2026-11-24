"""D10/D11：帧统计纯计算 + 偏色检测器 + cast 夹具集成。"""

import threading
from pathlib import Path

import pytest

from videoqc.app.config import Config
from videoqc.core.context import FileContext
from videoqc.core.detectors.colorcast import check_d10, check_d11
from videoqc.core.model import DecodeEvents, Sample, StreamInfo
from videoqc.core.pipeline import analyze_file
from videoqc.media.color import cast_direction, frame_stats, is_color_cast

PIXELS = 32 * 18


def _frame(*rgb: tuple[int, int, int]) -> bytes:
    """把若干种像素均匀铺满一帧。"""
    per = PIXELS // len(rgb)
    out = bytearray()
    for c in rgb:
        out += bytes(c) * per
    return bytes(out)


def test_frame_stats_black_and_white():
    assert frame_stats(b"\0" * (PIXELS * 3)) == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    r, g, b, y, sat, cast = frame_stats(b"\xff" * (PIXELS * 3))
    assert (r, g, b, y, sat, cast) == (255.0, 255.0, 255.0, 255.0, 0.0, 0.0)


def test_frame_stats_half_red():
    r, g, b, y, sat, cast = frame_stats(_frame((255, 0, 0), (0, 0, 0)))
    assert r == pytest.approx(127.5) and g == 0 and b == 0
    assert y == pytest.approx(0.299 * 127.5, abs=0.01)
    assert sat == pytest.approx(1.0)
    assert cast == pytest.approx(127.5 - y, abs=0.01)


def _s(r, g, b) -> Sample:
    y = 0.299 * r + 0.587 * g + 0.114 * b
    mx, mn = max(r, g, b), min(r, g, b)
    return Sample(
        t=0.0,
        r=r,
        g=g,
        b=b,
        y=y,
        sat=0.0 if not mx else (mx - mn) / mx,
        cast=max(abs(r - y), abs(g - y), abs(b - y)),
    )


def test_cast_direction():
    assert cast_direction(_s(200, 60, 60)) == "偏红"
    assert cast_direction(_s(60, 60, 200)) == "偏蓝"
    assert cast_direction(_s(200, 200, 60)) == "偏黄"
    assert cast_direction(_s(60, 200, 200)) == "偏青"
    assert cast_direction(_s(200, 60, 200)) == "偏洋红"
    # 三通道同号在数学上不可达（三个偏差按权重求和为 0）：整体提亮靠 cast 阈值挡，
    # 这里只断言近中性灰不产生方向
    assert cast_direction(_s(128, 128, 128)) is None


def test_is_color_cast_needs_brightness_and_direction():
    cfg = Config()
    assert is_color_cast(_s(200, 60, 60), cfg) is True
    assert is_color_cast(_s(20, 6, 6), cfg) is False  # 太暗，y 不足
    assert is_color_cast(_s(200, 190, 185), cfg) is False  # 整体偏亮


def _ctx(samples, dur=20.0):
    ctx = FileContext(path=Path("x.mp4"), cfg=Config())
    ctx.samples = samples
    ctx.streams = StreamInfo(
        path="x.mp4",
        duration=dur,
        container="mov",
        size_bytes=1,
        bit_rate=None,
        video={},
        audio=None,
        has_audio=True,
    )
    return ctx


def _run(n=3, cast_pixels=(200, 60, 60), step=5.0):
    return [
        Sample(
            t=i * step,
            r=cast_pixels[0],
            g=cast_pixels[1],
            b=cast_pixels[2],
            y=0.299 * cast_pixels[0] + 0.587 * cast_pixels[1] + 0.114 * cast_pixels[2],
            sat=0.7,
            cast=40.0,
        )
        for i in range(n)
    ]


def test_d10_reports_window_of_casts():
    issues = check_d10(_ctx(_run(3)))
    assert [i.kind for i in issues] == ["color_cast"]
    assert issues[0].severity == "medium"
    assert "偏红" in issues[0].title
    assert check_d10(_ctx(_run(2))) == []  # 不足窗口长度


def test_d10_whole_clip_is_high():
    issues = check_d10(_ctx(_run(5, step=4.0), dur=20.0))
    assert issues and issues[0].severity == "high"


def test_d11_missing_samples_is_reported():
    """全量解码正常结束却抽不到帧：不能静默当作通过（画面类结论根本没算）。"""
    ctx = _ctx([])
    ctx.decode = DecodeEvents(completed=True)
    assert [i.kind for i in check_d11(ctx)] == ["no_samples"]

    short = _ctx([], dur=2.0)  # 短于两个采样间隔，抽不到样本是正常的
    short.decode = DecodeEvents(completed=True)
    assert check_d11(short) == []


def test_d11_partial_sampling_is_reported():
    ctx = _ctx(_run(3))
    ctx.decode = DecodeEvents(completed=True, samples_incomplete=True)
    assert [i.kind for i in check_d11(ctx)] == ["samples_incomplete"]


def test_d11_exposure_buckets():
    dark = _ctx([Sample(t=0, r=5, g=5, b=5, y=5, sat=0, cast=0)])
    assert [i.kind for i in check_d11(dark)] == ["underexposed", "low_sample_count"]

    bright = _ctx([Sample(t=0, r=250, g=250, b=250, y=250, sat=0, cast=0)] * 3)
    assert [i.kind for i in check_d11(bright)] == ["overexposed"]

    flat = _ctx([Sample(t=i * 5.0, r=128, g=128, b=128, y=128, sat=0, cast=0) for i in range(3)])
    assert [i.kind for i in check_d11(flat)] == ["flat_frame"]


@pytest.mark.usefixtures("require_binaries")
def test_cast_fixture_end_to_end(data_dir):
    p = data_dir / "cast.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/cast.mp4")
    r = analyze_file(p, Config(), "full", 0, threading.Event())
    got = {i.kind: i.severity for i in r.issues}
    assert got.get("color_cast") in ("medium", "high")
    assert "black" not in got


@pytest.mark.usefixtures("require_binaries")
def test_sampling_grid_not_shrunk_by_keyframe_interval(data_dir):
    """抽帧时间戳必须与画面真实对齐，且不能因 GOP > 采样间隔而丢点。

    base.mp4 的 GOP≈8.33s > color_sample_sec=5s。用 -skip_frame nokey 时，
    fps 会把 0/8.33/16.66 的关键帧贴成 0/5/10 的名义时间，丢掉 t=15 且画面错位。
    去掉 skip_frame 后应在 0/5/10/15 四个网格点各采一帧（见 OPTIMIZATION #1）。
    """
    from videoqc.media.runners import run_decode_pass

    p = data_dir / "base.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/base.mp4")
    samples = run_decode_pass(
        p, Config(), has_audio=True, origin_sec=0.0, duration_sec=20.0
    ).samples
    assert samples is not None and len(samples) == 4
    assert [s.t for s in samples] == pytest.approx([0.0, 5.0, 10.0, 15.0], abs=0.2)


@pytest.mark.usefixtures("require_binaries")
def test_nonzero_start_time_stays_stream_relative(tmp_path, data_dir):
    """start_time≠0 的素材（TS/监控录像）：ffmpeg 已归一化，不能再减 origin 产生负时间。"""
    import subprocess

    from videoqc.media.paths import ffmpeg_path
    from videoqc.media.runners import run_decode_pass

    out = tmp_path / "offset.mp4"
    subprocess.run(
        [
            str(ffmpeg_path()),
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-i",
            str(data_dir / "base.mp4"),
            "-c",
            "copy",
            "-output_ts_offset",
            "10",
            str(out),
        ],
        check=True,
    )
    s = run_decode_pass(out, Config(), has_audio=True, origin_sec=10.0, duration_sec=20.0).samples
    assert s is not None and len(s) == 4
    # 必须是从流起点算的 0/5/10/15，而不是重复扣减后的 -10/-5/0/5
    assert [x.t for x in s] == pytest.approx([0.0, 5.0, 10.0, 15.0], abs=0.2)


@pytest.mark.usefixtures("require_binaries")
def test_whole_clip_cast_is_high_not_medium(data_dir):
    """全片偏色覆盖率应为 1.0 并判 high；丢采样点会把它错降成 medium（见 OPTIMIZATION #1）。"""
    p = data_dir / "cast.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/cast.mp4")
    r = analyze_file(p, Config(), "full", 0, threading.Event())
    casts = [i for i in r.issues if i.kind == "color_cast"]
    assert casts and casts[0].severity == "high"
    assert casts[0].evidence["ratio"] == pytest.approx(1.0, abs=0.01)


@pytest.mark.usefixtures("require_binaries")
def test_base_fixture_has_no_color_issue(data_dir):
    p = data_dir / "base.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/base.mp4")
    r = analyze_file(p, Config(), "full", 0, threading.Event())
    assert not [i for i in r.issues if i.kind in ("color_cast", "underexposed", "overexposed")]


def test_d11_splits_disjoint_runs():
    dark = Sample(t=0, r=5, g=5, b=5, y=5, sat=0, cast=0)
    normal = Sample(t=5, r=120, g=100, b=90, y=104, sat=0.25, cast=8)
    samples = [dark, normal, Sample(t=10, r=5, g=5, b=5, y=5, sat=0, cast=0), normal]
    runs = [i for i in check_d11(_ctx(samples)) if i.kind == "underexposed"]
    assert len(runs) == 2  # 两个相隔 10s 的暗点各自成条
    assert (runs[0].start, runs[0].end) == (0.0, 0.0)


def test_solid_screen_owns_pure_color():
    green = [Sample(t=i * 5.0, r=0, g=255, b=0, y=149.7, sat=1.0, cast=149.7) for i in range(3)]
    issues = check_d11(_ctx(green))
    solid = [i for i in issues if i.kind == "solid_screen"]
    assert solid and solid[0].severity == "high"
    assert not [i for i in issues if i.kind == "underexposed"]
    assert check_d10(_ctx(green)) == []  # 已由纯色屏报告，不重复报偏色

    one = check_d11(_ctx(green[:1]))
    assert [i.severity for i in one if i.kind == "solid_screen"] == ["medium"]
