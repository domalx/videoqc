"""D12 坏包/解码错误（视频与音频分开报，避免把音频爆音说成花屏）。"""

from __future__ import annotations

from videoqc.core.constants import CORRUPT_RATIO_HIGH
from videoqc.core.context import FileContext
from videoqc.core.model import Issue

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
    return [
        Issue(
            "corrupt_packet",
            "high" if severe else "medium",
            f"视频包标记损坏 {corrupt}/{total}（{ratio:.3%}）",
            detail="包级 C 位，常见于传输丢包；少量时画面未必可见",
            evidence={"corrupt_flags": corrupt, "packets": total, "ratio": round(ratio, 5)},
        )
    ]


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
                f"视频解码错误 {ev.video_errors} 处（疑似花屏/码流损坏）",
                detail=" | ".join(errs[:3]),
                evidence={
                    "decode_errors": ev.video_errors,
                    "corrupt_flags": ctx.packets.corrupt_flags if ctx.packets else 0,
                },
            )
        )
    if ev.audio_errors:
        out.append(
            Issue(
                "audio_decode_errors",
                "medium",
                f"音频解码错误 {ev.audio_errors} 处（爆音/丢帧）",
                detail=" | ".join(errs[:3]),
                evidence={"audio_decode_errors": ev.audio_errors},
            )
        )
    return out
