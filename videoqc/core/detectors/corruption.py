"""D12 坏包/解码错误（视频与音频分开报，避免把音频爆音说成花屏）。"""

from __future__ import annotations

from videoqc.core.constants import CORRUPT_RATIO_HIGH
from videoqc.core.context import FileContext
from videoqc.core.model import Issue, fmt_tc

_VIDEO_ERROR_HIGH = 50  # 视频解码错误超过这个数量视为持续花屏


def check_d12(ctx: FileContext) -> list[Issue]:
    if ctx.decode is None:
        return []
    return _corrupt(ctx) + _decode_errors(ctx)


def _corrupt(ctx: FileContext) -> list[Issue]:
    packets = ctx.packets
    corrupt = packets.corrupt_flags if packets else 0
    if not corrupt:
        return []
    total = len(packets.pts) if packets else 0
    ratio = corrupt / total if total else 1.0
    # 单个 C 位在 mpegts/无线回传里很常见，不能一个包就把整片判成 failed
    severe = corrupt >= 4 or ratio > CORRUPT_RATIO_HIGH
    times = packets.corrupt_pts if packets else []
    first = times[0] if times else None
    last = packets.corrupt_last_ts if packets else None
    return [
        Issue(
            "corrupt_packet",
            "high" if severe else "medium",
            f"视频包标记损坏 {corrupt}/{total}（{ratio:.3%}）{_where(first, last)}",
            detail="包级 C 位，常见于传输丢包；少量时画面未必可见",
            start=first,
            end=last if first is not None else None,
            evidence={
                "corrupt_flags": corrupt,
                "packets": total,
                "ratio": round(ratio, 5),
                "first_sec": _round(first),
                "last_sec": _round(last),
                "busiest_sec": _busiest(times) if times else None,
            },
        )
    ]


def _where(first: float | None, last: float | None) -> str:
    """标题里的位置尾巴：首处必给，末处只在真的跳得远时给。"""
    if first is None:
        return ""
    tail = f"，首处 {fmt_tc(first)}"
    return tail + (f"，末处 {fmt_tc(last)}" if last is not None and last > first else "")


def _busiest(times: list[float], window: float = 5.0) -> float | None:
    """坏包最密集的 5s 窗口起点（times 已排序）：跳过去看得最快。"""
    best_n, best_at, lo = 0, None, 0
    for hi, t in enumerate(times):
        while t - times[lo] > window:
            lo += 1
        if hi - lo + 1 > best_n:
            best_n, best_at = hi - lo + 1, times[lo]
    return round(best_at, 3) if best_at is not None else None


def _decode_errors(ctx: FileContext) -> list[Issue]:
    ev = ctx.decode
    assert ev is not None
    out: list[Issue] = []
    errs = ev.errors
    if ev.video_errors:
        sev = "high" if ev.video_errors > _VIDEO_ERROR_HIGH else "medium"
        out.append(
            Issue(
                "decode_errors",
                sev,
                f"视频解码错误 {ev.video_errors} 处（疑似花屏/码流损坏）{_span(ev.video_error_times)}",
                detail=" | ".join(errs[:3]),
                start=min(ev.video_error_times) if ev.video_error_times else None,
                end=max(ev.video_error_times) if ev.video_error_times else None,
                evidence={
                    "decode_errors": ev.video_errors,
                    "corrupt_flags": ctx.packets.corrupt_flags if ctx.packets else 0,
                    "first_sec": _round(
                        min(ev.video_error_times) if ev.video_error_times else None
                    ),
                    "last_sec": _round(max(ev.video_error_times) if ev.video_error_times else None),
                },
            )
        )
    if ev.audio_errors:
        out.append(
            Issue(
                "audio_decode_errors",
                "medium",
                f"音频解码错误 {ev.audio_errors} 处（爆音/丢帧）{_span(ev.audio_error_times)}",
                detail=" | ".join(errs[:3]),
                start=min(ev.audio_error_times) if ev.audio_error_times else None,
                end=max(ev.audio_error_times) if ev.audio_error_times else None,
                evidence={
                    "audio_decode_errors": ev.audio_errors,
                    "first_sec": _round(
                        min(ev.audio_error_times) if ev.audio_error_times else None
                    ),
                    "last_sec": _round(max(ev.audio_error_times) if ev.audio_error_times else None),
                },
            )
        )
    return out


def _span(times: list[float]) -> str:
    """错误行的时间估计（ffmpeg 报错不带时间戳，按最近一个采样点估，误差 ≤ 采样间隔）。"""
    return f"，首处 {fmt_tc(min(times))}" if times else ""


def _round(t: float | None) -> float | None:
    return round(t, 3) if t is not None else None
