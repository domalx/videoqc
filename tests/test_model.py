"""模型与结论规则（C8）：无 Qt、无 ffmpeg。"""
import pytest

from videoqc.core.model import FileResult, Issue, fmt_tc


@pytest.mark.parametrize("sec,expected", [
    (None, "—"),
    (0, "00:00:00.000"),
    (59.9996, "00:01:00.000"),
    (3661.5, "01:01:01.500"),
    (-3, "00:00:00.000"),
])
def test_fmt_tc(sec, expected):
    assert fmt_tc(sec) == expected


def _result(issues, status="done"):
    return FileResult(path="x.mp4", mode="fast", status=status, issues=issues)


def test_conclusion_rules():
    assert _result([]).conclusion == "passed"
    assert _result([Issue("container_mismatch", "info", "i")]).conclusion == "passed"
    assert _result([Issue("probe_packets_failed", "low", "l")]).conclusion == "warned"
    assert _result([Issue("no_duration", "high", "h")]).conclusion == "failed"
    assert _result([], status="cancelled").conclusion == "unfinished"
    # 探测致命失败：status="failed" + high Issue 必须是 failed，不能算 unfinished
    assert _result([Issue("probe_failed", "high", "h")], status="failed").conclusion == "failed"


def test_ok_follows_conclusion():
    assert _result([]).ok is True
    assert _result([Issue("probe_failed", "high", "h")]).ok is False
    assert _result([], status="pending").ok is False


def test_issue_invariants():
    with pytest.raises(AssertionError):
        Issue("x", "urgent", "bad severity")
    with pytest.raises(AssertionError):
        Issue("x", "low", "end without start", end=1.0)
