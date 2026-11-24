"""D06 静音段、D07 音量异常、D08 无音轨。"""
from __future__ import annotations

from videoqc.core.context import FileContext
from videoqc.core.model import Issue


def check_d06(ctx: FileContext) -> list[Issue]:
    """静音段；整轨无声时由 D07 报一条 silent_audio，这里不再逐段重复（C 类）。"""
    decode, dur = ctx.decode, ctx.duration
    if decode is None or dur <= 0 or not _has_audio(ctx):
        return []

    full_silent = decode.mean_volume is not None and decode.mean_volume < ctx.cfg.silent_mean_db
    ratio = _silence_ratio(decode, dur)

    out: list[Issue] = []
    for start, end in decode.silences:
        d = end - start
        if full_silent or d < ctx.cfg.silence_min_sec:
            continue
        head_tail = (start <= 0.5 or end >= dur - 0.5) and d < 0.3 * dur
        sev = "info" if head_tail else ("high" if ratio > 0.6 else "medium")
        out.append(Issue("silence", sev, f"静音 {d:.1f}s", start=start, end=end,
                         evidence={"duration": round(d, 2), "total_ratio": round(ratio, 2)}))
    return out


def check_d07(ctx: FileContext) -> list[Issue]:
    decode, dur = ctx.decode, ctx.duration
    if decode is None or not _has_audio(ctx):
        return []

    out: list[Issue] = []
    mv, xv = decode.mean_volume, decode.max_volume
    silent = mv is not None and mv < ctx.cfg.silent_mean_db and dur > 10
    # 均音量低但一直有底噪（不到八成时长被 silencedetect 判为静音）时，只算「音量偏低」：
    # 写成 high 会把安静但正常的片子判成 failed。
    if silent and _silence_ratio(decode, dur) >= 0.8:
        out.append(Issue("silent_audio", "high", f"全程近乎无声（均 {mv:.1f}dB）",
                         evidence={"mean_volume": mv}))
    elif mv is not None and (silent or mv < ctx.cfg.mean_volume_low_db):
        out.append(Issue("low_volume", "medium", f"音量偏低（均 {mv:.1f}dB）",
                         evidence={"mean_volume": mv}))
    if decode.mean_volume is None and decode.completed:
        out.append(Issue("empty_audio", "high", "音频流没有有效采样",
                         detail="有音轨但解不出声音，可能是空轨道"))
    if xv is not None and xv >= ctx.cfg.clipping_db:
        out.append(Issue("clipping", "low", f"存在削波风险（峰值 {xv:.1f}dB）",
                         evidence={"max_volume": xv}))
    return out


def check_d08(ctx: FileContext) -> list[Issue]:
    if ctx.streams is not None and not ctx.streams.has_audio and ctx.cfg.require_audio:
        return [Issue("no_audio", "high", "文件不含音频流", detail="不会再有静音/音量结论")]
    return []


def _silence_ratio(decode, dur: float) -> float:
    covered = sum(max(0.0, min(e, dur) - max(s, 0.0)) for s, e in decode.silences)
    return covered / dur if dur > 0 else 0.0


def _has_audio(ctx: FileContext) -> bool:
    return ctx.streams is None or ctx.streams.has_audio
