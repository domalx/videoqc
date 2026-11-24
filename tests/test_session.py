"""Session 合并规则（SPEC §6.1 / D19）：模式深度优先，再比代次。"""
from videoqc.app.session import Session
from videoqc.core.model import FileResult, Issue


def _r(path="a.mp4", mode="fast", gen=0, sev=None, status="done") -> FileResult:
    issues = [Issue("freeze", sev, "冻结")] if sev else []
    return FileResult(path=path, mode=mode, status=status, generation=gen, issues=issues)


def test_later_generation_same_mode_wins():
    s = Session()
    assert s.put(_r(gen=1)) is True
    assert s.put(_r(gen=2, sev="medium")) is True
    assert (s.get("a.mp4") or _r()).issues  # 新结果生效


def test_older_generation_same_mode_is_rejected():
    s = Session()
    s.put(_r(gen=5))
    assert s.put(_r(gen=4, sev="high")) is False
    assert (s.get("a.mp4") or _r()).issues == []


def test_full_beats_fast_even_with_smaller_generation():
    s = Session()
    s.put(_r(mode="full", gen=1))
    assert s.put(_r(mode="fast", gen=9)) is False      # 深度结果不能被快检覆盖
    r = s.get("a.mp4")
    assert r is not None and r.mode == "full"


def test_fast_never_downgrades_full_but_full_upgrades_fast():
    s = Session()
    s.put(_r(mode="fast", gen=7))
    assert s.put(_r(mode="full", gen=2)) is True       # 代次更小也接受：先提交后完成
    r = s.get("a.mp4")
    assert r is not None and r.mode == "full"


def test_summary_counts():
    s = Session()
    s.put(FileResult(path="ok.mp4", mode="fast", status="done"))
    s.put(_r("bad.mp4", sev="high"))
    s.put(_r("warn.mp4", sev="medium"))
    s.put(_r("stop.mp4", status="cancelled"))
    got = s.summary()
    assert (got["total"], got["passed"], got["failed"], got["warned"], got["unfinished"]) == (4, 1, 1, 1, 1)
    assert got["kinds"]["freeze"] == 2


def test_all_is_path_sorted_and_clear():
    s = Session()
    s.put(_r("b.mp4", gen=1))
    s.put(_r("a.mp4", gen=1))
    assert [r.path for r in s.all()] == ["a.mp4", "b.mp4"]
    s.clear()
    assert s.all() == [] and s.summary()["total"] == 0
