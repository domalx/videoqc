"""解码一趟：事件（freeze/black/silence/音量/错误）+ 32×18 采样（D10/D11）。"""

from __future__ import annotations

import collections
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass

from videoqc.app.config import Config
from videoqc.core.model import DecodeEvents, Sample
from videoqc.media.color import make_sample
from videoqc.media.paths import ffmpeg_path, long_path, no_window_flags

_FREEZE_START = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_FREEZE_END = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_FREEZE_DUR = re.compile(r"lavfi\.freezedetect\.freeze_duration:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_BLACK = re.compile(
    r"black_start:\s*(-?[0-9]+(?:\.[0-9]+)?)\s+black_end:\s*(-?[0-9]+(?:\.[0-9]+)?)\s+black_duration:\s*(-?[0-9]+(?:\.[0-9]+)?)"
)
_BLACK_START = re.compile(r"black_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_SILENCE_END = re.compile(
    r"silence_end:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*(-?[0-9]+(?:\.[0-9]+)?)"
)
_MEAN_VOL = re.compile(r"mean_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB")
_MAX_VOL = re.compile(r"max_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB")
_ERROR = re.compile(
    r"error while decoding|corrupt|Invalid NAL|no frame!|missing picture"
    r"|Invalid data found|Error while decoding stream|Error splitting the input",
    re.IGNORECASE,
)
_HEADER = re.compile(r"^(Input|Output) #")  # 路径里带 "corrupt" 也会命中 _ERROR
_STREAM_INDEX = re.compile(r"stream #\d+:(\d+)")  # ffmpeg: "Error while decoding stream #0:1"
_MAX_ERRORS = 20


@dataclass
class _State:
    """跨行状态：freezedetect/blackdetect/silencedetect 都是「开始一行、结束一行」。"""

    open_freeze: float | None = None
    freeze_dur: float | None = None
    open_black: float | None = None
    open_silence: float | None = None
    video_index: int | None = None  # 把 stream #0:N 的报错归类到视频/音频
    audio_index: int | None = None


def parse_line(line: str, ev: DecodeEvents, st: _State) -> None:
    """解析一行 ffmpeg stderr（时间戳已是流内秒，ffmpeg 默认归一化，无需再减 origin）。"""
    line = line.rstrip("\r\n")
    if _HEADER.match(line):
        return

    m = _FREEZE_START.search(line)
    if m:
        # 上一个 freeze 的 end 行若缺失就来了新 start：用 start+duration 收口，避免吞掉一段
        if st.open_freeze is not None:
            _close_freeze(ev, st, st.open_freeze)
        st.open_freeze, st.freeze_dur = float(m.group(1)), None
        return
    m = _FREEZE_DUR.search(line)
    if m:
        st.freeze_dur = float(m.group(1))
        return
    m = _FREEZE_END.search(line)
    if m and st.open_freeze is not None:
        ev.freezes.append((st.open_freeze, float(m.group(1))))
        st.open_freeze = st.freeze_dur = None
        return

    m = _BLACK.search(line)
    if m:
        st.open_black = None
        ev.blacks.append((float(m.group(1)), float(m.group(2))))
        return
    m = _BLACK_START.search(line)
    if m:
        st.open_black = float(m.group(1))
        return

    m = _SILENCE_START.search(line)
    if m:
        st.open_silence = float(m.group(1))
        return
    m = _SILENCE_END.search(line)
    if m:
        end, dur = float(m.group(1)), float(m.group(2))
        start = st.open_silence if st.open_silence is not None else end - dur
        st.open_silence = None
        ev.silences.append((start, end))
        return

    m = _MEAN_VOL.search(line)
    if m:
        ev.mean_volume = float(m.group(1))
        return
    m = _MAX_VOL.search(line)
    if m:
        ev.max_volume = float(m.group(1))
        return

    if _ERROR.search(line):
        ev.error_count += 1
        idx = _STREAM_INDEX.search(line)
        stream = int(idx.group(1)) if idx else None
        if stream is not None and stream == st.audio_index:
            ev.audio_errors += 1
        else:
            # 无流号的行（如 "Invalid NAL unit size"）几乎都是视频解码器上下文
            ev.video_errors += 1
        if len(ev.errors) < _MAX_ERRORS:
            ev.errors.append(line.strip()[:200])


def _close_freeze(ev: DecodeEvents, st: _State, eof_end: float) -> None:
    """收口冻结区间：优先用 start+duration，缺失时才退到 eof_end（SPEC §4.3）。"""
    if st.open_freeze is None:
        return
    start = st.open_freeze
    end = start + st.freeze_dur if st.freeze_dur is not None else eof_end
    ev.freezes.append((start, end))
    st.open_freeze = st.freeze_dur = None


def close_open(ev: DecodeEvents, st: _State, end: float) -> None:
    """进程结束时仍张着的区间：冻结优先用 duration，否则持续到文件末尾（静止片尾常见）。"""
    _close_freeze(ev, st, end)
    if st.open_black is not None:
        ev.blacks.append((st.open_black, end))
    if st.open_silence is not None:
        ev.silences.append((st.open_silence, end))


def run_decode_pass(
    path: str,
    cfg: Config,
    *,
    has_audio: bool,
    origin_sec: float,
    duration_sec: float,
    hdr: bool = False,
    video_index: int | None = None,
    audio_index: int | None = None,
    cancel_event: threading.Event | None = None,
) -> DecodeEvents:
    """一趟解码同时产出两类结果（全量模式不再为抽帧单独解码一遍）：

    1. freezedetect / blackdetect / silencedetect / volumedetect 的事件与音量；
    2. `fps` 网格上真实帧的 32×18 缩略样本（D10/D11），经 showinfo 的 pts 配对。

    出口时间即流内秒（C1）：ffmpeg 未加 -copyts，滤镜 PTS 已归一化到流起点，不再减 origin_sec。
    不抛异常（FileNotFoundError 除外）。超时/取消则 kill，completed=False，已得结果保留。
    video_index/audio_index 用于把 stderr 里的 stream #0:N 归类到视频/音频（D12 分报）。
    """
    chain = [
        _DETECT_SCALE,  # 先降到 480p 再检测：判据都是相对比例，省掉大部分解码+滤波开销
        f"freezedetect=n={cfg.freeze_noise}:d={cfg.freeze_min_sec}",
        f"blackdetect=d={cfg.black_min_sec}:pic_th=0.98:pix_th=0.10",
        # fps/showinfo 必须排在两个检测滤镜之后：它们要看到完整帧流，且这里要的是真实帧的 pts
        f"fps=1/{cfg.color_sample_sec}",
        "showinfo",
        f"scale={FRAME_W}:{FRAME_H}:flags=area",
    ]
    if hdr and cfg.hdr_tonemap:
        chain.insert(0, _TONEMAP.rstrip(","))
    cmd = [
        str(ffmpeg_path()),
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-v",
        "info",
        "-i",
        long_path(path),
        "-map",
        "0:v:0",
        "-vf",
        ",".join(chain),
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    if has_audio:
        # 第二路输出必须导向 null 文件：两路都写 stdout 会互相踩
        cmd += [
            "-map",
            "0:a:0?",
            "-af",
            f"silencedetect=n={cfg.silence_db}dB:d={cfg.silence_min_sec},volumedetect",
            "-f",
            "null",
            os.devnull,
        ]

    ev = DecodeEvents()
    st = _State(video_index=video_index, audio_index=audio_index)
    stderr_buf: collections.deque[str] = collections.deque(maxlen=50)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=no_window_flags(),
    )
    frames: queue.Queue[bytes | None] = queue.Queue()
    stamps: queue.Queue[float | None] = queue.Queue()

    def _read_frames() -> None:
        out = proc.stdout
        try:
            while out is not None:
                block = out.read(FRAME_BYTES)
                if not block or len(block) < FRAME_BYTES:
                    break
                frames.put(block)
        finally:
            frames.put(None)  # 视频帧流结束

    def _read_stderr() -> None:
        err = proc.stderr
        try:
            if err is None:
                return
            for raw in err:
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                stderr_buf.append(line.rstrip("\r\n"))
                m = _SHOWINFO_PTS.search(line)
                if m:
                    stamps.put(float(m.group(1)))
                # 事件在读线程里就地解析：进程退出后不会因为主循环提前 break 而丢掉尾部行
                # （volumedetect 的 SUMMARY 就是最后几行，丢了会误报 empty_audio）
                parse_line(line, ev, st)
        finally:
            stamps.put(None)

    frame_reader = threading.Thread(target=_read_frames, daemon=True)
    err_reader = threading.Thread(target=_read_stderr, daemon=True)
    frame_reader.start()
    err_reader.start()

    samples: list[Sample] = []
    deadline = time.monotonic() + cfg.timeout_sec
    killed = False
    timed_out = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            killed = True
            break
        if time.monotonic() > deadline:
            killed = timed_out = True
            break
        try:
            block = frames.get(timeout=0.2)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if block is None:
            break
        try:
            pts = stamps.get(timeout=2.0)
        except queue.Empty:
            # 配对失步（showinfo 行缺失/积压）：放弃后续采样，但别杀进程——
            # ffmpeg 还在产出冻结/黑场/静音事件，杀掉会把这些事件一起丢掉
            ev.samples_incomplete = True
            break
        if pts is None:
            # stderr 已收尾，但还有帧没配上（帧流正常时两边 FIFO 等长，先结束的是帧流）
            ev.samples_incomplete = True
            break
        samples.append(make_sample(pts, block))

    # 帧流出完不等于进程退出（volumedetect 的 SUMMARY 在最后几行）；
    # 只有取消/超时才 kill，其余让它自然跑完，事件与音量才算数
    if killed:
        proc.kill()
    elif proc.poll() is None:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            timed_out = True
    rc = proc.wait()
    err_reader.join(timeout=5)  # 等 stderr 读完，尾部事件才算数
    frame_reader.join(timeout=2)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()

    close_open(ev, st, duration_sec)
    # ffmpeg 解码输出的时间戳已由它自己归一化到起点（命令未加 -copyts）：start_time=10s
    # 的文件，freezedetect 仍报 4s 而非 14s。这里若再减 origin_sec 就是重复扣减，
    # 会让 TS/监控录像等 start_time≠0 的素材出现负时间，且与 probe_packets 的相对时间轴错位。
    _ = origin_sec  # 保留参数仅为兼容调用方，不再参与换算
    ev.samples = samples
    ev.samples_timed_out = timed_out
    ev.exit_code = -9 if killed else rc
    ev.completed = not killed and rc == 0
    ev.timed_out = timed_out
    if not ev.completed:  # 仅失败时保留 stderr 尾，供日志排障
        ev.stderr_tail = "\n".join(stderr_buf)
    return ev


FRAME_W, FRAME_H = 32, 18
FRAME_BYTES = FRAME_W * FRAME_H * 3
_SHOWINFO_PTS = re.compile(r"pts_time:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_DETECT_SCALE = "scale=480:-2:flags=area"  # 检测用分辨率（采样最后仍缩到 32x18）
_TONEMAP = "zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:p=bt709,"
