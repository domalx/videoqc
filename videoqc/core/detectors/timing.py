"""D03 跳秒、D04 时间戳异常（只吃 packet 级统计，不依赖解码）。"""

from __future__ import annotations

from itertools import pairwise
from statistics import median

from videoqc.core.constants import MISSING_PTS_RATIO
from videoqc.core.context import FileContext
from videoqc.core.model import Issue

_GAP_MERGE_WINDOW = 2.0  # 相邻跳变起点相距 < 2s 合成一段（SPEC D03）
_JITTER_SIGMA = 6.0  # 跳变阈值 ≥ p50 + 6σ（σ 由 MAD 估），让 VFR 的常态抖动不误报
_VFR_RATIO = 0.3  # 帧间隔标准差 > 0.3*p50 ⇒ 疑似 VFR
_FPS_TOLERANCE = 0.2  # 声明帧率与实测偏差 > 20% ⇒ 提示


def check_d03(ctx: FileContext) -> list[Issue]:
    packets = ctx.packets
    if packets is None or len(packets.pts) < 3:
        return []
    p50 = packets.p50
    if p50 <= 0:  # 全部重复 pts，交给 D04 报 dup_pts
        return []

    pts = packets.pts
    deltas = [b - a for a, b in pairwise(pts)]
    # 阈值同时高于「常态抖动」：手机屏录/VFR 的帧间隔本身就有大有小，用固定的 p50 倍数会误报。
    # 用 MAD 量抖动（对少数真跳变免疫，分位数会被跳变自己污染）。
    mad = median(abs(d - p50) for d in deltas)
    thr = max(
        p50 * ctx.cfg.pts_gap_factor, p50 + ctx.cfg.pts_gap_min, p50 + _JITTER_SIGMA * 1.4826 * mad
    )
    gaps = [(pts[i], pts[i + 1], deltas[i]) for i in range(len(deltas)) if deltas[i] > thr]
    if not gaps:
        return []

    out: list[Issue] = []
    dur = ctx.duration
    tail = pts[-1]
    # 音频断点：剪辑/重封装会把两轨在同一处一起剪掉，而录制丢帧只丢视频
    audio_gaps = packets.audio.gaps if packets.audio else []
    tol = ctx.cfg.av_offset_sec
    for seg in _merge_gaps(gaps):
        start, end, deltas_in_seg = seg
        if end >= tail - 1e-6:
            # 断点到文件末尾 ⇒ 这是尾部被截掉，不归 D03（D05 截断、D14 音视频时长差已经在报）
            continue
        max_delta = max(deltas_in_seg)
        missing = sum(max(0, round(d / p50) - 1) for d in deltas_in_seg)
        paired = any(a <= end + tol and b >= start - tol for a, b in audio_gaps)
        if paired:
            # 两轨同时断开 ⇒ 剪辑切点，不是录制缺陷：降到 medium，不当“缺帧”报
            sev = "medium"
            title = f"音视频同处断开（{max_delta:.3f}s），疑似剪辑/重封装"
        else:
            sev = "high" if (max_delta >= 1.0 or missing * p50 > 0.005 * dur) else "medium"
            title = f"PTS 跳变，缺约 {max(1, missing)} 帧（{max_delta:.3f}s）"
        out.append(
            Issue(
                "pts_gap",
                sev,
                title,
                detail=f"delta={max_delta:.3f}s, p50={p50:.3f}s, thr={thr:.3f}s"
                + ("，音频在同一时间也断开" if paired else ""),
                start=start,
                end=end,
                evidence={
                    "delta": round(max_delta, 3),
                    "p50": round(p50, 4),
                    "thr": round(thr, 4),
                    "missing_frames": missing,
                    "av_paired": paired,
                },
            )
        )
    return out


def check_d04(ctx: FileContext) -> list[Issue]:
    packets = ctx.packets
    if packets is None:
        return []
    out: list[Issue] = []

    if packets.dts_non_monotonic > 0:
        out.append(
            Issue(
                "dts_non_monotonic",
                "medium",
                f"DTS 非单调 {packets.dts_non_monotonic} 次",
                evidence={"count": packets.dts_non_monotonic},
            )
        )
    total = len(packets.pts) + packets.missing_pts
    if packets.missing_pts and total and packets.missing_pts / total > MISSING_PTS_RATIO:
        out.append(
            Issue(
                "pts_missing",
                "info",
                f"时间戳缺失 {packets.missing_pts}/{total}",
                detail="这些帧 D03 统计不到",
                evidence={"missing": packets.missing_pts},
            )
        )
    if packets.dup_pts > 0:
        out.append(
            Issue(
                "dup_pts",
                "medium",
                f"重复时间戳 {packets.dup_pts} 个",
                evidence={"count": packets.dup_pts},
            )
        )

    audio = packets.audio
    if audio is not None:
        bad = audio.dup_pts + audio.dts_non_monotonic
        a_total = audio.packets
        if bad or (a_total and audio.missing_pts / a_total > MISSING_PTS_RATIO):
            out.append(
                Issue(
                    "audio_pts_anomaly",
                    "medium",
                    f"音频时间戳异常：重复 {audio.dup_pts} · 非单调 {audio.dts_non_monotonic}"
                    f" · 缺失 {audio.missing_pts}/{a_total}",
                    detail="音频侧时间戳问题会导致音画不同步或声音卡顿（视频侧看不出来）",
                    evidence={
                        "dup": audio.dup_pts,
                        "non_monotonic": audio.dts_non_monotonic,
                        "missing": audio.missing_pts,
                        "packets": a_total,
                    },
                )
            )

    p50, fps = packets.p50, packets.nominal_fps
    vfr = False
    if len(packets.pts) > 2 and p50 > 0:
        deltas = [b - a for a, b in pairwise(packets.pts)]
        # 用 MAD 估计的稳健标准差，而非 pstdev：单个跳秒（如 1s）会把 pstdev 拉高十几倍，
        # 把恒定帧率视频误判成 VFR，还会连带抑制 fps_mismatch。MAD 对少数离群点免疫。
        mad = median(abs(d - p50) for d in deltas)
        robust_sd = 1.4826 * mad  # MAD → 正态分布 σ 的一致估计
        vfr = robust_sd > _VFR_RATIO * p50
        if vfr:
            out.append(
                Issue(
                    "vfr_suspected",
                    "info",
                    "帧间隔波动大，疑似可变帧率",
                    detail=f"robust_stdev={robust_sd:.4f}s, p50={p50:.4f}s（此时帧率结论仅供参考）",
                    evidence={"stdev": round(robust_sd, 4), "p50": round(p50, 4)},
                )
            )
    # 可变帧率下 1/p50 不代表声明帧率，这条只在恒定帧率时有意义
    if fps and p50 > 0 and not vfr:
        measured = 1 / p50
        if abs(measured - fps) / fps > _FPS_TOLERANCE:
            out.append(
                Issue(
                    "fps_mismatch",
                    "low",
                    f"声明 {fps:.2f}fps，实测 {measured:.2f}fps",
                    evidence={"declared": fps, "measured": round(measured, 3)},
                )
            )
    return out


def _merge_gaps(gaps: list[tuple[float, float, float]]) -> list[tuple[float, float, list[float]]]:
    """把起点相距很近的跳变合成一段，返回 (start, end, 段内所有 delta)。"""
    segs: list[list] = [[gaps[0][0], gaps[0][1], [gaps[0][2]]]]
    for start, end, delta in gaps[1:]:
        if start - segs[-1][0] < _GAP_MERGE_WINDOW:
            segs[-1][1] = end
            segs[-1][2].append(delta)
        else:
            segs.append([start, end, [delta]])
    return [(float(s), float(e), d) for s, e, d in segs]
