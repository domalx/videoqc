"""merge_issues 的跨检测项收敛：同一处故障只留一条主问题（SPEC §5.3）。"""
from pathlib import Path

from videoqc.app.config import Config
from videoqc.core.context import FileContext
from videoqc.core.merge import merge_issues
from videoqc.core.model import Issue


def _ctx(issues, dur=120.0):
    ctx = FileContext(path=Path("x.mp4"), cfg=Config())
    ctx.streams = type("S", (), {"duration": dur, "video": {}, "has_audio": True})()
    ctx.issues = list(issues)
    return ctx


def test_freeze_inside_pts_gap_downgraded():
    got = merge_issues(_ctx([
        Issue("pts_gap", "high", "PTS 跳变", start=9.0, end=18.5),
        Issue("freeze", "medium", "画面冻结 9.4s", start=9.1, end=18.5),
    ]))
    freeze = next(i for i in got if i.kind == "freeze")
    assert freeze.severity == "info" and "同一处丢帧" in freeze.detail
    assert next(i for i in got if i.kind == "pts_gap").severity == "high"


def test_freeze_away_from_gap_keeps_severity():
    got = merge_issues(_ctx([
        Issue("pts_gap", "high", "PTS 跳变", start=9.0, end=9.3),
        Issue("freeze", "medium", "画面冻结 4.0s", start=40.0, end=44.0),
    ]))
    assert next(i for i in got if i.kind == "freeze").severity == "medium"


def test_all_black_and_silent_is_high():
    got = merge_issues(_ctx([
        Issue("black", "medium", "黑场 120.0s", start=0.0, end=120.0),
        Issue("silent_audio", "high", "全程近乎无声"),
    ]))
    black = next(i for i in got if i.kind == "black")
    assert black.severity == "high" and "没录到画面" in black.detail


def test_short_black_clip_not_upgraded():
    got = merge_issues(_ctx([
        Issue("black", "medium", "黑场 4.0s", start=0.0, end=4.0),
        Issue("no_audio", "high", "文件不含音频流"),
    ], dur=4.0))
    assert next(i for i in got if i.kind == "black").severity == "medium"
