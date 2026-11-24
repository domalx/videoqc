"""D01 文件完整性、D02 音视频对齐、D14 截断（D13 随采样一起落地）。"""
from __future__ import annotations

from videoqc.core.context import FileContext
from videoqc.core.model import Issue

# 扩展名 → container(format_name) 里应出现的关键字
_CONTAINER_HINTS = {
    "mp4": ("mp4", "mov"), "mov": ("mov", "mp4"), "m4v": ("mp4", "mov"),
    "mkv": ("matroska", "webm"), "webm": ("webm", "matroska"),
    "ts": ("mpegts",), "m2ts": ("mpegts",), "mpg": ("mpeg",), "avi": ("avi",),
    "flv": ("flv",), "wmv": ("asf",), "3gp": ("3gp", "mp4"),
}


def check_d01(ctx: FileContext) -> list[Issue]:
    if ctx.streams is None:
        return []                                   # probe 失败已由 pipeline 报出，不重复报
    if ctx.streams.video is None:
        return [Issue("no_video_stream", "high", "文件中没有视频流")]

    out: list[Issue] = []
    dur = ctx.streams.duration
    if not dur or dur <= 0:
        out.append(Issue("no_duration", "high", "容器未给出有效时长"))

    ext = ctx.path.suffix.lower().lstrip(".")
    hints = _CONTAINER_HINTS.get(ext)
    container = (ctx.streams.container or "").lower()
    if hints and container and not any(h in container for h in hints):
        out.append(Issue(
            "container_mismatch", "info",
            f"扩展名 .{ext} 与容器 {ctx.streams.container} 不符",
        ))
    return out


def check_d02(ctx: FileContext) -> list[Issue]:
    """音视频对齐：优先用实测包时间（stream duration 在 mp4/TS 里常等于容器时长，会失真）。"""
    streams = ctx.streams
    if streams is None or streams.video is None or streams.audio is None:
        return []

    out: list[Issue] = []
    packets = ctx.packets
    audio = packets.audio if packets is not None else None

    if packets is not None and audio is not None:
        # 尾端差：视频结束时刻（末帧 pts + 中位帧长）减音频末包
        v_end = packets.last_ts + (packets.p50 or 0.0)
        tail = v_end - audio.last_ts
        if abs(tail) > ctx.cfg.av_offset_sec:
            out.append(Issue(
                "av_duration_mismatch", "medium", f"音视频尾端差 {abs(tail):.2f}s",
                detail="实测包时间（不含封装元数据误差）",
                evidence={"video_end": round(v_end, 3), "audio_end": round(audio.last_ts, 3)},
            ))
        # 起点差：两侧 pts 都以视频起点为原点，音频首包即偏移量
        off = audio.first_ts - packets.first_ts
        if abs(off) > ctx.cfg.av_offset_sec:
            out.append(Issue(
                "av_start_offset", "medium", f"音频起点偏移 {off:+.2f}s",
                detail="声音类时间点可能有同等偏移",
                evidence={"offset": round(off, 3)},
            ))
        return out

    dv = streams.video.get("duration")  # 包级失败时的退路：只能用声明时长
    da = streams.audio.get("duration")
    if dv and da and abs(dv - da) > ctx.cfg.av_offset_sec:
        out.append(Issue(
            "av_duration_mismatch", "medium", f"音视频时长差 {abs(dv - da):.2f}s",
            evidence={"video": round(dv, 3), "audio": round(da, 3)},
        ))

    off = (streams.audio.get("start_time") or 0.0) - (streams.video.get("start_time") or 0.0)
    if abs(off) > ctx.cfg.av_offset_sec:
        out.append(Issue(
            "av_start_offset", "medium", f"音频起点偏移 {off:+.2f}s",
            detail="声音类时间点可能有同等偏移",
            evidence={"offset": round(off, 3)},
        ))
    return out


def check_d14(ctx: FileContext) -> list[Issue]:
    """视频末帧比自己声明的时长早太多 ⇒ 录制中断。

    基准用**视频流自己的**时长：音轨比视频长是正常封装，用容器时长会误报。
    """
    if ctx.packets is None or not ctx.streams or not ctx.streams.duration:
        return []
    expected = _video_duration(ctx) or ctx.streams.duration
    last_ts = ctx.packets.last_ts
    gap = expected - (last_ts + (ctx.packets.p50 or 0.0))  # 末帧结束时刻，不把一帧量当截断
    if gap <= ctx.cfg.truncation_gap_sec:
        return []

    detail = ""
    if ctx.decode is not None and any(e >= last_ts - 5 for _, e in ctx.decode.blacks):
        detail = "片尾存在黑场，可能录制中断"
    return [Issue(
        "truncated", "medium", f"视频末帧比时长早 {gap:.1f}s，疑似录制中断",
        detail=detail, start=max(0.0, last_ts),
        evidence={"duration": round(expected, 3), "last_ts": round(last_ts, 3),
                  "gap": round(gap, 3)},
    )]


def _video_duration(ctx: FileContext) -> float:
    video = ctx.streams.video if ctx.streams else None
    dur = video.get("duration") if video else None
    return dur if isinstance(dur, (int, float)) else 0.0


def check_d13(ctx: FileContext) -> list[Issue]:
    """只发 info：HDR / 旋转 / 非方形像素——影响判定口径，不是缺陷。"""
    if not ctx.streams or not ctx.streams.video:
        return []
    v = ctx.streams.video
    out: list[Issue] = []

    if v.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        out.append(Issue("hdr", "info", "HDR 素材，偏色判定使用 tonemap 后结果",
                         evidence={"color_transfer": v["color_transfer"]}))
    rotate = v.get("rotate") or 0
    if rotate:
        out.append(Issue("rotated", "info", f"视频带旋转元数据 {rotate}°",
                         evidence={"rotate": rotate}))
    sar = v.get("sample_aspect_ratio")
    if sar and sar not in ("1:1", "1/1", "0:1"):
        out.append(Issue("non_square_pixels", "info", f"非方形像素 {sar}",
                         evidence={"sar": sar}))

    a = ctx.streams.audio
    if a:
        rate = a.get("sample_rate")
        if isinstance(rate, int) and 0 < rate < 32000:
            out.append(Issue("low_sample_rate", "info", f"音频采样率偏低 {rate}Hz",
                             evidence={"sample_rate": rate}))
        ch = a.get("channels")
        if isinstance(ch, int) and ch > 2:
            out.append(Issue("channel_anomaly", "info", f"音频声道数异常 {ch}",
                             evidence={"channels": ch}))
    return out
