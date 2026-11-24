"""D10 偏色、D11 曝光/纯色（只吃抽帧样本，快检模式跳过）。"""

from __future__ import annotations

from statistics import median

from videoqc.core.constants import SAMPLE_COVERAGE_MIN
from videoqc.core.context import FileContext
from videoqc.core.model import Issue, Sample
from videoqc.media.color import cast_direction, is_bright, is_color_cast, is_dark, is_flat

_SOLID_SAT = 0.6  # 纯色屏：饱和度下限
_SOLID_CAST = 60.0  # 纯色屏：单通道偏离亮度下限（正常画面很少同时满足）
_SOLID_MIN_SEC = 5.0  # 纯色屏判 high 的最短持续时长：短于此可能只是纯色场景/转场
_FLAT_MIN_SEC = 2.0  # 过暗/过曝/纯色/白屏要至少持续这么久：1s 网格下单帧暗部多半是转场/夜景
_CAST_MIN_SPAN = 60.0  # 判 high 的最短持续时长：15s 的强偏色可能只是布景/霓虹
_CAST_HIGH = 40.0  # 判 high 的偏色强度下限（cast/255）


def check_d10(ctx: FileContext) -> list[Issue]:
    samples, dur = ctx.samples, ctx.duration
    if not samples or dur <= 0:
        return []

    win = ctx.cfg.color_window_sec
    out: list[Issue] = []
    for run in _runs(samples, lambda s: is_color_cast(s, ctx.cfg) and not _is_solid(s)):
        first, last = run[0], run[-1]
        span = _span(run, ctx.cfg.color_sample_sec)
        if span < win:
            continue
        ratio = span / dur
        cast = median(s.cast for s in run)
        # 偏色要“够强 + 够久”才判 failed：整片偏色是未做白平衡的典型特征，
        # 而短时强偏色多半是舞台灯/霓虹布景，只算警告，避免把成片判死
        sustained = cast > _CAST_HIGH and span >= _CAST_MIN_SPAN
        sev = "high" if (ratio >= SAMPLE_COVERAGE_MIN or sustained) else "medium"
        out.append(
            Issue(
                "color_cast",
                sev,
                f"疑似{cast_direction(first) or '偏色'}（cast={cast:.0f}/255）",
                start=max(0.0, first.t),
                end=min(dur, last.t + ctx.cfg.color_sample_sec),
                detail=(
                    "整片偏色多为未做白平衡；若为调色/舞台灯可忽略"
                    if ratio >= SAMPLE_COVERAGE_MIN
                    else ""
                ),
                evidence={
                    "r": round(first.r),
                    "g": round(first.g),
                    "b": round(first.b),
                    "ratio": round(ratio, 2),
                },
            )
        )
    return out


def check_d11(ctx: FileContext) -> list[Issue]:
    samples, decode, dur = ctx.samples, ctx.decode, ctx.duration
    out: list[Issue] = []
    if not samples:
        # 解码正常跑完却一个样本都没有：抽帧/showinfo 解析对不上，画面类结论不可用。
        # 时长太短（< 两个采样间隔）时抽不到样本是正常的，不报。
        if decode is not None and decode.completed and dur >= 2 * ctx.cfg.color_sample_sec:
            out.append(
                Issue(
                    "no_samples",
                    "medium",
                    "未取到画面样本，色彩/曝光结论不可用",
                    detail="解码正常结束但抽样帧为空（时间戳配对或滤镜输出异常）",
                )
            )
        return out

    if decode is not None and decode.samples_incomplete:
        out.append(
            Issue(
                "samples_incomplete",
                "medium",
                f"画面采样中途丢失（仅 {len(samples)} 个），色彩/曝光结论不完整",
                evidence={"samples": len(samples)},
            )
        )

    blacks = decode.blacks if decode else []
    checks = (
        (lambda s: is_dark(s) and not _in_blacks(s, blacks), "underexposed", "low", "画面过暗"),
        (is_bright, "overexposed", "low", "画面过曝"),
        (is_flat, "flat_frame", "low", "疑似纯色/白屏"),
        (_is_solid, "solid_screen", "high", "疑似纯色屏（绿屏/蓝屏等）"),
    )
    for pred, kind, sev, text in checks:
        for run in _runs(samples, pred):
            span = _span(run, ctx.cfg.color_sample_sec)
            if kind == "solid_screen":
                if span < _SOLID_MIN_SEC:
                    sev = "medium"  # 短于 5s 的纯色可能只是纯色场景/转场
            elif span < _FLAT_MIN_SEC:  # 过暗/过曝/纯色/白屏：短于 2s 的孤立采样点不算问题
                continue
            out.append(
                Issue(
                    kind,
                    sev,
                    f"{text}（{span:.0f}s）",
                    start=run[0].t,
                    end=run[-1].t,
                    evidence={"samples": len(run), "span_sec": round(span, 1)},
                )
            )
    if len(samples) < 3:
        out.append(
            Issue("low_sample_count", "info", f"仅采到 {len(samples)} 个样本，色彩结论仅供参考")
        )
    return out


def _span(run: list[Sample], interval: float) -> float:
    """一段命中的时长：首末样本差 + 一个采样间隔。判据按秒，不按采样点数。"""
    return run[-1].t - run[0].t + interval


def _is_solid(s: Sample) -> bool:
    """纯色屏（绿屏/蓝屏/粉屏）：高饱和且单通道远偏离亮度；正常画面极少同时满足。"""
    return s.sat >= _SOLID_SAT and s.cast >= _SOLID_CAST and 24 < s.y < 235


def _runs(samples: list[Sample], pred) -> list[list[Sample]]:
    """把连续命中的采样点切成段（采样本身是等间隔的）。"""
    runs: list[list[Sample]] = []
    cur: list[Sample] = []
    for s in samples:
        if pred(s):
            cur.append(s)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def _in_blacks(s: Sample, blacks) -> bool:
    return any(a <= s.t <= b for a, b in blacks)
