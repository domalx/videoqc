"""ffprobe 封装：probe_streams()、probe_packets()、ffmpeg_version()。"""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from itertools import pairwise
from pathlib import Path

from videoqc.core.model import AudioStats, PacketStats, StreamInfo
from videoqc.media.paths import ffmpeg_path, ffprobe_path, long_path, no_window_flags

_AUDIO_GAP_MIN = 0.2  # 音频包间隔超过它就算断点（AAC 一帧 ~21ms，0.2s 已是十帧空档）
_MAX_CORRUPT_PTS = 8192  # 坏包位置最多留这么多个（超过只更新 corrupt_last_ts）


class ProbeError(Exception):
    def __init__(self, message: str, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.stderr_tail = stderr_tail


def _run(cmd: list[str], timeout: float) -> str:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=no_window_flags(),
        )
    except FileNotFoundError as e:
        raise ProbeError(f"找不到可执行文件：{cmd[0]}（检查 bin/ 是否完整）") from e
    except subprocess.TimeoutExpired as e:
        raise ProbeError(f"超时（{timeout:g}s）：{Path(cmd[-1]).name}") from e
    if proc.returncode != 0:
        raise ProbeError(f"ffprobe 退出码 {proc.returncode}", proc.stderr[-2000:])
    return proc.stdout


def parse_rate(text) -> float | None:
    """'30000/1001' → 29.97；'0/0' 或非法 → None。"""
    try:
        num, den = str(text).split("/")
        return float(num) / float(den) if float(den) else None
    except (ValueError, AttributeError):
        try:
            return float(text)
        except (TypeError, ValueError):
            return None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(v) -> int | None:
    f = _float(v)
    return round(f) if f else None


def _rotation(stream: dict) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(_float(side["rotation"]) or 0) % 360
    return int(_float((stream.get("tags") or {}).get("rotate")) or 0) % 360


def ffmpeg_version() -> str:
    """首行形如 `ffmpeg version 9.1-essentials_build-...`，失败返回 'unknown'。"""
    try:
        out = _run([str(ffmpeg_path()), "-version"], 15)
    except ProbeError:
        return "unknown"
    return out.splitlines()[0].strip() if out else "unknown"


def short_version(line: str) -> str:
    """'ffmpeg version 9.1-essentials_build-...' → '9.1-essentials_build-www.gyan.dev'。"""
    parts = line.split()
    if "version" in parts and parts.index("version") + 1 < len(parts):
        return parts[parts.index("version") + 1]
    return line.strip() or "unknown"


def probe_streams(path: str | Path, timeout: float) -> StreamInfo:
    out = _run(
        [
            str(ffprobe_path()),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            long_path(path),
        ],
        timeout,
    )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise ProbeError("ffprobe 输出不是合法 JSON", out[:500]) from e

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)

    video = None
    if v is not None:
        video = {
            "index": v.get("index"),
            "codec": v.get("codec_name"),
            "width": v.get("width"),
            "height": v.get("height"),
            "pix_fmt": v.get("pix_fmt"),
            "r_frame_rate": v.get("r_frame_rate"),
            "avg_frame_rate": v.get("avg_frame_rate"),
            "fps": parse_rate(v.get("avg_frame_rate")) or parse_rate(v.get("r_frame_rate")),
            "start_time": _float(v.get("start_time")),
            "duration": _float(v.get("duration")),
            "nb_frames": v.get("nb_frames"),
            "color_transfer": v.get("color_transfer"),
            "rotate": _rotation(v),
            "sample_aspect_ratio": v.get("sample_aspect_ratio"),
        }
    audio = None
    if a is not None:
        audio = {
            "index": a.get("index"),
            "codec": a.get("codec_name"),
            # ffprobe 给的是字符串（"44100"），统一转 int，否则 D13 的 isinstance(int) 永远为假
            "sample_rate": _as_int(a.get("sample_rate")),
            "channels": _as_int(a.get("channels")),
            "start_time": _float(a.get("start_time")),
            "duration": _float(a.get("duration")),
            "bit_rate": _float(a.get("bit_rate")),
        }

    duration = (
        _float(fmt.get("duration"))
        or _float((video or {}).get("duration"))
        or _float((audio or {}).get("duration"))
    )

    size = fmt.get("size")
    try:
        size_bytes = int(float(size or 0))
    except (TypeError, ValueError):
        size_bytes = 0
    if not size_bytes:
        size_bytes = Path(path).stat().st_size

    return StreamInfo(
        path=str(path),
        duration=duration,
        container=fmt.get("format_name", ""),
        size_bytes=size_bytes,
        bit_rate=_as_int(fmt.get("bit_rate")),
        video=video,
        audio=audio,
        has_audio=a is not None,
    )


class _StreamAcc:
    """单个流的时间戳累加器：流式读包时不保留 CSV 原文，只留统计所需的原始 PTS。"""

    __slots__ = (
        "corrupt_flags",
        "corrupt_last_ts",
        "corrupt_pts",
        "count",
        "dts_non_monotonic",
        "dts_prev",
        "missing_pts",
        "pts",
    )

    def __init__(self) -> None:
        self.pts: list[float] = []
        self.corrupt_pts: list[float] = []  # 坏包位置（首 8192 个，够定位密度峰值）
        self.corrupt_last_ts: float | None = None
        self.missing_pts = 0
        self.dts_prev: float | None = None
        self.dts_non_monotonic = 0
        self.corrupt_flags = 0
        self.count = 0

    def add(self, pts_raw: str, dts_raw: str, flags: str, start_time: float) -> None:
        self.count += 1
        dts = _float(dts_raw)
        if dts is not None:
            if self.dts_prev is not None and dts < self.dts_prev:
                self.dts_non_monotonic += 1
            self.dts_prev = dts
        t = _float(pts_raw)
        # ffprobe 的 flags 是 3 字符位串 K/D/C（Key/Discard/Corrupt，见 ffprobe.c show_packet）：
        # D = 可丢弃包（B 帧重排时正常出现），C = 包内容损坏。原实现误把 D 当坏包。
        if "C" in flags:
            self.corrupt_flags += 1
            if t is not None:
                self.corrupt_last_ts = t - start_time
                if len(self.corrupt_pts) < _MAX_CORRUPT_PTS:
                    self.corrupt_pts.append(self.corrupt_last_ts)
        if t is None:
            self.missing_pts += 1
            return
        self.pts.append(t - start_time)

    def ordered(self) -> list[float]:
        """B 帧使文件顺序的 PTS 不单调：先排序再去重，中位帧间隔才有意义。"""
        seq = sorted(self.pts)
        return [p for i, p in enumerate(seq) if i == 0 or p != seq[i - 1]]

    def p50(self, pts: list[float]) -> float:
        deltas = [b - a for a, b in pairwise(pts) if b > a]
        return statistics.median(deltas) if deltas else 0.0

    def dup_pts(self, pts: list[float]) -> int:
        return len(self.pts) - len(pts)

    def audio_stats(self) -> AudioStats | None:
        pts = self.ordered()
        if not pts:
            return None
        # 音频断点：AAC 帧长 ~21ms，0.2s 以上的空档只可能是真断（D03 用它判剪辑断点）
        gaps = [(a, b) for a, b in pairwise(pts) if b - a > _AUDIO_GAP_MIN]
        return AudioStats(
            first_ts=pts[0],
            last_ts=pts[-1],
            packets=self.count,
            dup_pts=self.dup_pts(pts),
            dts_non_monotonic=self.dts_non_monotonic,
            missing_pts=self.missing_pts,
            gaps=gaps,
        )


def probe_packets(
    path: str | Path,
    start_time: float,
    timeout: float,
    *,
    video_index: int | None = None,
    audio_index: int | None = None,
) -> PacketStats:
    """一次流式读全流包：视频统计 + 音频时间戳（语音/音画不同步只能从音频包看出来）。

    流式读（不信 `capture_output`）是为了内存恒定：10h@60fps 的 CSV 能到几十 MB。
    未给 index 时以首行流号为视频流、其余归音频（兼容只传路径的老调用）。
    """
    cmd = [
        str(ffprobe_path()),
        "-v",
        "error",
        "-show_entries",
        # 不要 duration_time：它占 CSV 约 1/5 字节且没有任何检测项用它（实测 197MB 文件 998→801ms）
        "packet=stream_index,pts_time,dts_time,flags",
        "-of",
        "csv=p=0",
        long_path(path),
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=no_window_flags(),
        )
    except FileNotFoundError as e:
        raise ProbeError(f"找不到可执行文件：{cmd[0]}（检查 bin/ 是否完整）") from e

    deadline = time.monotonic() + timeout
    seen: dict[int, _StreamAcc] = {}
    video_acc: _StreamAcc | None = None
    audio_acc: _StreamAcc | None = None
    # 老调用（两个 index 都没给）才降级：首流当视频、其余当音频
    legacy = video_index is None and audio_index is None
    err = ""
    rc = 0
    out = proc.stdout
    try:
        if out is not None:
            for line in out:
                if time.monotonic() > deadline:
                    proc.kill()
                    raise ProbeError(f"超时（{timeout:g}s）：读取包时间戳")
                parts = line.rstrip("\r\n").split(",")
                if len(parts) < 4:
                    continue
                idx_f = _float(parts[0])
                if idx_f is None:
                    continue
                idx = int(idx_f)  # 不能用 _as_int：它把 0 当“未知”返回 None，而流号 0 是合法的
                acc = seen.get(idx)
                if acc is None:
                    acc = seen[idx] = _StreamAcc()
                    if video_index is None and audio_index is None and len(seen) == 1:
                        video_index = idx  # 未给 index：首行流号当视频流
                if idx == video_index:
                    video_acc = acc
                elif idx == audio_index or legacy:
                    # 调用方给了视频流号却没给音频流号 = 这文件没有音轨：
                    # 不能把字幕/数据流当音频，否则 D04 会拿字幕包报“音频时间戳异常”
                    audio_acc = acc
                acc.add(parts[1], parts[2], parts[3], start_time)
    finally:
        if out is not None:
            out.close()
        rc = proc.wait()
        if proc.stderr is not None:
            err = proc.stderr.read() or ""
            proc.stderr.close()

    if rc != 0:
        raise ProbeError(f"ffprobe 退出码 {rc}", err[-2000:])

    video = video_acc or _StreamAcc()
    pts = video.ordered()
    if not pts:
        raise ProbeError("no video packets")
    return PacketStats(
        pts=pts,
        p50=video.p50(pts),
        dts_non_monotonic=video.dts_non_monotonic,
        dup_pts=video.dup_pts(pts),
        corrupt_flags=video.corrupt_flags,
        corrupt_pts=sorted(video.corrupt_pts),
        corrupt_last_ts=video.corrupt_last_ts,
        first_ts=pts[0],
        last_ts=pts[-1],
        missing_pts=video.missing_pts,
        audio=audio_acc.audio_stats() if audio_acc is not None else None,
    )
