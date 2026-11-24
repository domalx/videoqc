"""analyze_file：单文件四阶段编排（探测 → 包级 → 解码 → 采样 → 检测 → 合并）。

每个阶段一个 `_stage_*` 函数，失败就地记 Issue 并返回，编排层只负责顺序、取消与状态。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from videoqc.app.config import Config
from videoqc.app.logging_setup import get_logger
from videoqc.core.constants import PACKETS_TIMEOUT_MAX, PROBE_TIMEOUT_SEC
from videoqc.core.context import FileContext
from videoqc.core.detectors import DETECTORS
from videoqc.core.merge import merge_issues
from videoqc.core.model import FileResult, Issue, StreamInfo
from videoqc.media.probe import ProbeError, parse_rate, probe_packets, probe_streams
from videoqc.media.runners import run_decode_pass


def analyze_file(
    path: str | Path,
    cfg: Config,
    mode: str,
    generation: int,
    cancel_event,
    *,
    on_stage: Callable[[str], None] | None = None,
    warm_streams: StreamInfo | None = None,
) -> FileResult:
    t0 = time.perf_counter()
    ctx = FileContext(Path(path), cfg)
    status = "done"
    error: str | None = None
    log = get_logger()
    log.info("开始分析 %s（mode=%s, generation=%d）", path, mode, generation)

    def stage(name: str) -> None:
        if on_stage is None or cancel_event.is_set():
            return
        with suppress(Exception):
            on_stage(name)  # 进度回调失败不影响流程

    def finish() -> FileResult:
        return FileResult(
            path=str(ctx.path),
            mode=mode,
            status=status,
            generation=generation,
            streams=ctx.streams,
            stats=_stats(ctx, mode),
            issues=ctx.issues,
            error=error,
            elapsed_sec=round(time.perf_counter() - t0, 2),
        )

    stage("probe")
    error = _stage_probe(ctx, warm_streams, log)
    if error is not None:
        status = "failed"
        return finish()
    if cancel_event.is_set():
        return _cancelled(finish, error)

    video = ctx.streams.video if ctx.streams else None
    if video is None:
        status = "failed"  # 无视频流：唯一中断流程的致命问题
        return finish()
    origin = (video or {}).get("start_time") or 0.0

    stage("packets")
    _stage_packets(ctx, video, log)
    if cancel_event.is_set():
        return _cancelled(finish, error)

    if mode == "full":
        stage("decode")
        _stage_decode(ctx, cfg, video, origin, cancel_event, log)
        if cancel_event.is_set():
            return _cancelled(finish, error)

    _run_detectors(ctx, mode)
    status = _resolve_status(ctx)
    ctx.issues = merge_issues(ctx)
    result = finish()
    log.info(
        "完成 %s（status=%s, conclusion=%s, issues=%d, %.2fs）",
        path,
        status,
        result.conclusion,
        len(result.issues),
        result.elapsed_sec,
    )
    return result


def _cancelled(finish: Callable[[], FileResult], error: str | None) -> FileResult:
    """取消：结论按 unfinished 处理，不覆盖已记录的探测错误。"""
    result = finish()
    result.status = "cancelled"
    result.error = error
    return result


def _stage_probe(
    ctx: FileContext, warm_streams: StreamInfo | None, log: logging.Logger
) -> str | None:
    """探测流信息。返回错误串表示致命失败（无法解析的文件）。"""
    try:
        # 热启动：已有同一文件的探测结果就直接用（快检 → 深度扫描时省掉一次 ffprobe）
        ctx.streams = warm_streams or probe_streams(ctx.path, PROBE_TIMEOUT_SEC)
    except Exception as e:
        error = str(e)[:300]
        log.error("探测失败 %s: %s", ctx.path, error)
        ctx.issues.append(Issue("probe_failed", "high", "无法解析该文件", detail=error))
        return error
    return None


def _stage_packets(ctx: FileContext, video: dict | None, log: logging.Logger) -> None:
    """包级扫描。失败不阻断快检（D01 仍可用），只记一条 medium。"""
    streams = ctx.streams
    audio = streams.audio if streams else None
    try:
        ctx.packets = probe_packets(
            ctx.path,
            (video or {}).get("start_time") or 0.0,
            _packets_timeout(ctx.duration),
            video_index=(video or {}).get("index"),
            audio_index=(audio or {}).get("index"),
        )
        ctx.packets.nominal_fps = parse_rate((video or {}).get("avg_frame_rate"))
    except ProbeError as e:
        ctx.issues.append(
            Issue(
                "probe_packets_failed",
                "medium",
                "时间戳读取失败，跳秒/截断检查未执行",
                detail=str(e)[:200],
            )
        )
    except Exception as e:
        log.error("包级扫描异常 %s: %r", ctx.path, e)
        ctx.issues.append(
            Issue(
                "probe_packets_failed",
                "medium",
                "时间戳读取失败，跳秒/截断检查未执行",
                detail=repr(e)[:200],
            )
        )


def _stage_decode(
    ctx: FileContext,
    cfg: Config,
    video: dict | None,
    origin: float,
    cancel_event,
    log: logging.Logger,
) -> None:
    """一趟解码同时拿事件与采样（旧实现分两趟，耗时翻倍）。"""
    streams = ctx.streams
    audio = streams.audio if streams else None
    try:
        ctx.decode = run_decode_pass(
            str(ctx.path),
            cfg,
            has_audio=bool(streams and streams.has_audio),
            origin_sec=origin,
            duration_sec=ctx.duration,
            hdr=(video or {}).get("color_transfer") in ("smpte2084", "arib-std-b67"),
            video_index=(video or {}).get("index"),
            audio_index=(audio or {}).get("index"),
            cancel_event=cancel_event,
        )
        ctx.samples = ctx.decode.samples
    except Exception as e:
        log.error("解码阶段异常 %s: %r", ctx.path, e)
        ctx.issues.append(Issue("internal_error", "medium", "解码阶段失败", detail=repr(e)[:200]))
        return
    if ctx.decode.stderr_tail:
        log.error("解码 stderr 尾 50 行 %s:\n%s", ctx.path, ctx.decode.stderr_tail)


def _run_detectors(ctx: FileContext, mode: str) -> None:
    for det, full_only in DETECTORS:
        if full_only and mode != "full":  # 唯一门控点：快检不跑画面/音频类
            continue
        try:
            ctx.issues += det(ctx)
        except Exception as e:
            ctx.issues.append(
                Issue("internal_error", "medium", "检测项内部错误", detail=repr(e)[:200])
            )


def _resolve_status(ctx: FileContext) -> str:
    """状态优先级：cancelled > timeout > failed > done（SPEC §5.1 第 8 步）。

    cancel 已在各阶段末尾提前返回，这里只需在 failed 之上判断 timeout。
    """
    decode = ctx.decode
    timed_out = bool(decode and (decode.timed_out or decode.samples_timed_out))
    if decode is not None and not decode.completed and not timed_out:
        ctx.issues.append(Issue("decode_aborted", "medium", "解码中途中断，画面类结论可能不完整"))
    if timed_out:  # 优先级 timeout > failed：同时无视频流也应归入 unfinished
        return "timeout"
    return "done"


def _packets_timeout(duration: float) -> float:
    """包级扫描要读完整个文件的所有视频包：超时随片长缩放，否则长片会静默降级。"""
    return max(PROBE_TIMEOUT_SEC, min(PACKETS_TIMEOUT_MAX, duration * 0.2))


def _stats(ctx: FileContext, mode: str) -> dict:
    packets, decode = ctx.packets, ctx.decode
    return {
        "duration": ctx.duration,
        "fps_nominal": packets.nominal_fps if packets else None,
        "p50_delta": packets.p50 if packets else None,
        "packet_count": len(packets.pts) if packets else 0,
        "sample_count": len(ctx.samples) if ctx.samples else 0,
        "mean_volume": decode.mean_volume if decode else None,
        "max_volume": decode.max_volume if decode else None,
        "mode": mode,
    }
