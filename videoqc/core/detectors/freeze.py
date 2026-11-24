"""D05 画面冻结、D09 黑场（含「主要现象是黑场」的互相降级）。"""
from __future__ import annotations

from videoqc.core.constants import FREEZE_BLACK_OVERLAP, FREEZE_HIGH_SEC
from videoqc.core.context import FileContext
from videoqc.core.model import Issue


def check_d05(ctx: FileContext) -> list[Issue]:
    decode, dur = ctx.decode, ctx.duration
    if decode is None:
        return []

    out: list[Issue] = []
    for start, end in decode.freezes:
        d = end - start
        if d <= 0:  # 零长/倒挂区间（stderr 半行、EOF 收口时片长小于 start）：比值无意义
            continue
        black = _max_overlap((start, end), decode.blacks)
        quiet = _max_overlap((start, end), decode.silences)
        # dur 可能为 0（容器无时长 + 包级探测失败）；此时"整片静止"判断无意义，跳过即可，
        # 不能让 d/0 抛 ZeroDivisionError 把整项检测吞成 internal_error。
        if dur > 0 and d / dur >= 0.9:
            sev, why = "info", "（静止的监控画面）"
        elif black / d >= FREEZE_BLACK_OVERLAP:
            sev, why = "info", "（主要现象是黑场）"
        elif quiet / d >= FREEZE_BLACK_OVERLAP:
            sev, why = "info", "（同期静音，可能为暂停/加载画面）"
        elif d >= FREEZE_HIGH_SEC:
            sev, why = "high", ""
        else:
            sev, why = "medium", ""
        out.append(Issue("freeze", sev, f"画面冻结 {d:.1f}s{why}", start=start, end=end,
                         evidence={"duration": round(d, 2), "black_overlap": round(black, 2),
                                   "silence_overlap": round(quiet, 2)}))
    return out


def check_d09(ctx: FileContext) -> list[Issue]:
    decode, dur = ctx.decode, ctx.duration
    if decode is None:
        return []

    out: list[Issue] = []
    for start, end in decode.blacks:
        d = end - start
        head_tail = (start <= 0.5 or end >= dur - 0.5) and d < 0.3 * dur
        if d >= 0.8 * dur:
            sev = "medium"                          # 几乎全片黑场：不能当片头片尾忽略
        elif head_tail:
            sev = "info"
        elif d > 5:
            sev = "high"
        elif d > 1:
            sev = "medium"
        else:
            sev = "info"
        out.append(Issue("black", sev, f"黑场 {d:.1f}s", start=start, end=end,
                         evidence={"duration": round(d, 2)}))
    return out


def _max_overlap(span: tuple[float, float], others: list[tuple[float, float]]) -> float:
    best = 0.0
    for a, b in others:
        best = max(best, min(span[1], b) - max(span[0], a))
    return best
