"""单次运行的扫描结果容器（SPEC §6.1）。"""
from __future__ import annotations

from collections import Counter

from videoqc.core.model import FileResult

MODE_RANK = {None: 0, "none": 0, "fast": 1, "full": 2}


class Session:
    def __init__(self) -> None:
        self._results: dict[str, FileResult] = {}

    def get(self, path: str) -> FileResult | None:
        return self._results.get(path)

    def put(self, r: FileResult) -> bool:
        """接受则存入并返回 True。先比模式深度，同级再比代次（SPEC §6.1）。"""
        old = self._results.get(r.path)
        if old is not None:
            if MODE_RANK.get(r.mode, 0) < MODE_RANK.get(old.mode, 0):
                return False
            if MODE_RANK.get(r.mode, 0) == MODE_RANK.get(old.mode, 0) and r.generation <= old.generation:
                return False
        self._results[r.path] = r
        return True

    def clear(self) -> None:
        self._results.clear()

    def drop(self, paths) -> None:
        """重新扫描前丢掉这几条旧结果，避免界面显示上一轮的结论。"""
        for p in paths:
            self._results.pop(p, None)

    def all(self) -> list[FileResult]:
        return [self._results[p] for p in sorted(self._results)]

    def summary(self) -> dict:
        out = Counter(r.conclusion for r in self._results.values())
        kinds: Counter[str] = Counter()
        for r in self._results.values():
            kinds.update(i.kind for i in r.issues)
        return {
            "total": len(self._results),
            "passed": out["passed"],
            "warned": out["warned"],
            "failed": out["failed"],
            "unfinished": out["unfinished"],
            "kinds": dict(kinds),
        }
