"""ffprobe 封装与报告渲染（需要 bin/ffmpeg.exe、tests/data/base.mp4）。"""

from datetime import datetime

import pytest

from videoqc.app.config import Config
from videoqc.core.model import FileResult
from videoqc.media.probe import ProbeError, probe_packets, probe_streams, short_version
from videoqc.media.report import dump_json, label, render_markdown, report_paths

pytestmark = pytest.mark.usefixtures("require_binaries")


@pytest.fixture
def base(data_dir):
    p = data_dir / "base.mp4"
    if not p.exists():
        pytest.skip("缺少夹具 tests/data/base.mp4")
    return p


def test_probe_streams(base):
    s = probe_streams(base, timeout=30)
    assert s.duration == pytest.approx(20.0, abs=0.2)
    assert s.has_audio is True
    assert s.video and s.video["codec"] == "h264"
    assert (s.video["width"], s.video["height"]) == (320, 180)
    assert "mp4" in s.container


def test_probe_packets(base):
    p = probe_packets(base, start_time=0.0, timeout=30)
    assert len(p.pts) == pytest.approx(600, abs=2)
    assert p.p50 == pytest.approx(1 / 30, abs=0.005)
    assert p.dup_pts == 0 and p.corrupt_flags == 0


def _csv(*rows: str) -> str:
    return "".join(r + chr(10) for r in rows)


def _fake_probe(monkeypatch, text: str):
    """把 ffprobe 换成固定 CSV（现在是流式读，故要伪造 Popen 而不是 _run）。"""
    import io

    import videoqc.media.probe as probe

    class _Proc:
        def __init__(self) -> None:
            self.stdout = io.StringIO(text)
            self.stderr = io.StringIO("")

        def wait(self, *_a) -> int:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(probe.subprocess, "Popen", lambda *_a, **_k: _Proc())


def test_probe_packets_counts_only_corrupt_flag(monkeypatch):
    """D12 坏包口径：只有 C(Corrupt) 算坏包，D(Discard) 是正常的可丢弃包。"""
    # csv=p=0 列序：stream_index,pts_time,dts_time,flags
    _fake_probe(
        monkeypatch,
        _csv(
            "0,0.000000,0.000000,K__",
            "0,0.033333,0.033333,KD_",  # Discard，不是坏包
            "0,0.066667,0.066667,_D_",  # Discard，不是坏包
            "0,0.100000,0.100000,K_C",  # Corrupt，才是坏包
        ),
    )
    p = probe_packets("dummy.mp4", start_time=0.0, timeout=30)
    assert p.corrupt_flags == 1  # 旧实现会得 3
    assert p.corrupt_pts == [pytest.approx(0.1)] and p.corrupt_last_ts == pytest.approx(0.1)
    assert p.audio is None


def test_probe_packets_splits_audio_stream(monkeypatch):
    """一次读包要同时给出视频统计与音频时间戳（音频侧异常只能从这里看出来）。"""
    _fake_probe(
        monkeypatch,
        _csv(
            "0,0.000000,0.000000,K__",
            "0,0.033333,0.033333,___",
            "1,0.000000,0.000000,___",
            "1,0.021333,0.021333,___",
            "1,0.021333,0.021333,___",  # 音频重复 PTS
        ),
    )
    p = probe_packets("dummy.mp4", start_time=0.0, timeout=30, video_index=0, audio_index=1)
    assert len(p.pts) == 2 and p.first_ts == 0.0 and p.audio is not None
    assert p.audio.packets == 3 and p.audio.dup_pts == 1
    assert p.audio.first_ts == 0.0 and abs(p.audio.last_ts - 0.021333) < 1e-6


def test_probe_packets_records_audio_gaps(monkeypatch):
    """音频包间隔 >0.2s 记为断点：D03 靠它区分剪辑切点与录制丢帧（v0.3.4）。"""
    _fake_probe(
        monkeypatch,
        _csv(
            "0,0.000000,0.000000,K__",
            "0,5.000000,5.000000,___",
            "1,0.000000,0.000000,___",
            "1,0.021333,0.021333,___",
            "1,3.000000,3.000000,___",  # 音频从 0.021 跳到 3.0
        ),
    )
    p = probe_packets("dummy.mp4", start_time=0.0, timeout=30, video_index=0, audio_index=1)
    assert p.audio is not None and len(p.audio.gaps) == 1
    assert p.audio.gaps[0][0] == pytest.approx(0.021333, abs=1e-6)
    assert p.audio.gaps[0][1] == pytest.approx(3.0)


def test_probe_packets_infers_video_index_without_hint(monkeypatch):
    """未给 index 时以首行流号为视频流，其余归音频（老调用点只传路径也不串味）。"""
    _fake_probe(
        monkeypatch,
        _csv(
            "0,0.000000,0.000000,K__",
            "1,0.000000,0.000000,___",
        ),
    )
    p = probe_packets("dummy.mp4", start_time=0.0, timeout=30)
    assert len(p.pts) == 1 and p.audio is not None and p.audio.packets == 1


def test_probe_packets_ignores_non_audio_streams_without_audio_hint(monkeypatch):
    """没音轨时不能把字幕/数据流当音频（否则 D04 会拿字幕包报“音频时间戳异常”）。"""
    _fake_probe(
        monkeypatch,
        _csv(
            "0,0.000000,0.000000,K__",
            "0,0.033333,0.033333,___",
            "2,0.000000,0.000000,___",
            "2,9.000000,9.000000,___",  # 字幕流：给了视频流号、没给音频流号
        ),
    )
    p = probe_packets("dummy.mp4", start_time=0.0, timeout=30, video_index=0, audio_index=None)
    assert len(p.pts) == 2 and p.audio is None


def test_probe_packets_raises_without_video(monkeypatch):
    """明确给了 video_index 却一个视频包都没有（纯音频文件）⇒ 走 ProbeError，不是空统计。"""
    _fake_probe(monkeypatch, _csv("1,0.000000,0.000000,0.021333,___"))
    with pytest.raises(ProbeError):
        probe_packets("dummy.mp4", start_time=0.0, timeout=30, video_index=0, audio_index=1)


def test_probe_streams_sample_rate_is_int(base):
    """D13 依赖 isinstance(sample_rate, int)；ffprobe 给的是字符串，探测层必须转好。"""
    s = probe_streams(base, timeout=30)
    assert s.audio is not None
    assert isinstance(s.audio["sample_rate"], int)
    assert s.audio["sample_rate"] > 0


def test_probe_corrupt(tmp_path):
    """非视频文件必须抛 ProbeError（不能返回半个 StreamInfo 让下游算空时长）。"""
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"definitely not a video")
    with pytest.raises(ProbeError):
        probe_streams(bad, timeout=30)


def test_label_covers_known_kind_and_falls_back():
    assert label("corrupt_packet") == "坏包"
    assert label("some_unregistered_kind") == "some_unregistered_kind"


def test_short_version():
    line = "ffmpeg version 9.1-essentials_build-www.gyan.dev Copyright (c) 2000-2026"
    assert short_version(line) == "9.1-essentials_build-www.gyan.dev"


def test_report_paths_never_overwrite(tmp_path):
    when = datetime(2026, 1, 2, 3, 4)
    md1, json1 = report_paths(tmp_path, when)
    md1.write_text("x", encoding="utf-8")
    json1.write_text("{}", encoding="utf-8")
    md2, _ = report_paths(tmp_path, when)
    assert md2.name == "report_20260102_0304_2.md"


def test_render_markdown_contract():
    cfg = Config()
    when = datetime(2026, 1, 2, 3, 4, 5)
    r = FileResult(path="a.mp4", mode="fast", status="done", stats={"duration": 42.0})
    md = render_markdown([r], root="D:/v", cfg=cfg, when=when, version="0.3.0", ffmpeg_ver="9.1")
    assert md.startswith("# 录像质量检测报告")
    assert "工具：videoqc 0.3.0 · ffmpeg 9.1 · 模式：快检 · 阈值：" in md
    assert "## 概览" in md and "## 附录" in md and "### 检测项" in md
    payload = dump_json([r], root="D:/v", when=when, version="0.3.0", ffmpeg_ver="9.1", cfg=cfg)
    assert '"schema_version": 2' in payload and '"passed": 1' in payload


def test_render_markdown_survives_missing_stream_fields():
    """avg_frame_rate=0/0（损坏/异形流）时详情渲染曾抛 TypeError，导致整份报告丢。"""
    from videoqc.core.model import Issue, StreamInfo

    s = StreamInfo(
        path="a.mp4",
        duration=10.0,
        container="mov,mp4",
        size_bytes=1,
        bit_rate=None,
        video={
            "codec": "h264",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "0/0",
            "fps": None,
        },
        audio={"codec": "aac", "sample_rate": None, "channels": None},
        has_audio=True,
    )
    r = FileResult(
        path="a.mp4",
        mode="full",
        status="done",
        streams=s,
        issues=[Issue("freeze", "medium", "画面冻结 3.0s", start=2.0, end=5.0)],
    )
    md = render_markdown(
        [r],
        root="D:/v",
        cfg=Config(),
        when=datetime(2026, 1, 2, 3, 4),
        version="0.3.2",
        ffmpeg_ver="9.1",
    )
    assert "帧率未知" in md and "1920x1080" in md and "码率未知" in md
    assert "aac" in md and "NoneHz" not in md


def test_dump_json_returns_text_with_summary():
    """GUI/CLI 都把返回值写字串——曾误传 path= 关键字导致导出崩。"""
    import json

    r = FileResult(path="a.mp4", mode="fast", status="done")
    text = dump_json(
        [r],
        root="D:/v",
        when=datetime(2026, 1, 2, 3, 4),
        version="0.3.2",
        ffmpeg_ver="9.1",
        cfg=Config(),
    )
    assert isinstance(text, str)
    data = json.loads(text)
    assert data["summary"]["total"] == 1 and data["files"][0]["ok"] is True
