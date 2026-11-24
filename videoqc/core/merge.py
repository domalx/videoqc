"""merge_issues：合并同类、冻结降级、钳位、排序、断言（SPEC §5.3）。"""
from __future__ import annotations

from videoqc.core.constants import FREEZE_BLACK_OVERLAP
from videoqc.core.context import FileContext
from videoqc.core.model import SEV_ORDER, Issue


def _bounds(i: Issue) -> tuple[float, float]:
    """start 为 None 的 issue 不参与合并，调用方先判空；这里给 0 只是为了类型收敛。"""
    start = 0.0 if i.start is None else i.start
    return start, start if i.end is None else i.end


def _mergeable(a: Issue, b: Issue) -> bool:
    if a.start is None or b.start is None:
        return False
    a0, a1 = _bounds(a)
    b0, b1 = _bounds(b)
    union = max(a1, b1) - min(a0, b0)
    overlap = min(a1, b1) - max(a0, b0)
    return (overlap / union > 0.5) if union > 0 else abs(a0 - b0) < 0.5


def _correlate(merged: list[Issue], dur: float) -> None:
    """把同一处故障的多种表征收敛成一条主问题（就地改 severity/detail）。

    - 跳秒段内的冻结：时间戳断掉本身就是丢帧，画面不动只是它的表现，降为 info。
    - 整段黑场 + 无声/无音轨：基本都是没录到画面，升为 high。
    """
    gaps = [i for i in merged if i.kind == "pts_gap" and i.start is not None]
    for f in merged:
        if not f.kind.startswith("freeze") or f.start is None:
            continue
        f0, f1 = _bounds(f)
        d = f1 - f0
        if d <= 0:
            continue
        if any(min(f1, _bounds(g)[1]) - max(f0, _bounds(g)[0]) >= 0.5 * d for g in gaps):
            f.severity = "info"
            f.detail = f"{f.detail}（与跳秒同段，可能是同一处丢帧）".strip()

    if dur < 10 or not any(i.kind in ("silent_audio", "no_audio") for i in merged):
        return
    for b in merged:
        if b.kind != "black" or b.start is None:
            continue
        b0, b1 = _bounds(b)
        if (b1 - b0) / dur >= 0.9:
            b.severity = "high"
            b.detail = f"{b.detail}（同段无声，可能整段没录到画面）".strip()


def _merge_group(group: list[Issue]) -> list[Issue]:
    group.sort(key=lambda i: i.start if i.start is not None else float("inf"))
    out: list[Issue] = []
    counts: dict[int, int] = {}                 # id(issue) → 合并了几段
    for issue in group:
        if out and _mergeable(out[-1], issue):
            cur = out[-1]
            lo, hi = _bounds(cur)
            nlo, nhi = _bounds(issue)
            cur.start, cur.end = min(lo, nlo), max(hi, nhi)
            counts[id(cur)] = counts.get(id(cur), 1) + 1
        else:
            out.append(issue)
    for i in out:
        n = counts.get(id(i), 1)
        if n > 1:
            i.detail = f"{i.detail}（合并 {n} 段）" if i.detail else f"合并 {n} 段"
    return out


def merge_issues(ctx: FileContext) -> list[Issue]:
    dur = ctx.duration
    for i in ctx.issues:                                   # C7 钳位
        if i.start is not None and dur > 0:
            i.start = min(max(i.start, 0.0), dur)
            if i.end is not None:
                i.end = min(max(i.end, i.start), dur)

    merged: list[Issue] = []
    for kind in dict.fromkeys(i.kind for i in ctx.issues):
        merged += _merge_group([i for i in ctx.issues if i.kind == kind])

    blacks = [i for i in merged if i.kind == "black" and i.start is not None]
    for f in merged:                                       # 黑场内的冻结降为 info
        if not f.kind.startswith("freeze") or f.start is None:
            continue
        f_start, f_end = _bounds(f)
        d = f_end - f_start
        if d <= 0:
            continue
        if any(min(f_end, _bounds(b)[1]) - max(f_start, _bounds(b)[0]) >= FREEZE_BLACK_OVERLAP * d
               for b in blacks):
            f.severity = "info"
            f.detail = f"{f.detail}（区间内为黑场，降级）".strip()

    _correlate(merged, dur)

    merged.sort(key=lambda i: (
        -SEV_ORDER[i.severity],
        i.start if i.start is not None else float("inf"),
        i.kind,
    ))
    for i in merged:                                       # 不变式 §2.1
        assert i.severity in SEV_ORDER
        assert i.start is not None or i.end is None
        assert i.start is None or i.end is None or i.start <= i.end
    return merged
