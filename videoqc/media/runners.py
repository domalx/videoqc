"""解码一趟：事件（freeze/black/silence/音量/错误）+ 32×18 采样（D10/D11）。

stderr 的文案解析在 `media/stderr_parse.py`；本文件只负责拼命令、拉进程、配对帧与时间戳。
"""

from __future__ import annotations

import collections
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass

from videoqc.app.config import Config
from videoqc.core.model import DecodeEvents, Sample
from videoqc.media.color import make_sample
from videoqc.media.paths import ffmpeg_path, long_path, no_window_flags
from videoqc.media.stderr_parse import SHOWINFO_PTS, State, close_open, parse_line

FRAME_W, FRAME_H = 32, 18
FRAME_BYTES = FRAME_W * FRAME_H * 3
_DETECT_SCALE = "scale=480:-2:flags=area"  # 检测用分辨率（采样最后仍缩到 32x18）
_TONEMAP = "zscale=t=linear:npl=100,tonemap=hable,zscale=t=bt709:m=bt709:p=bt709,"
_STDERR_TAIL = 50
_PAIR_TIMEOUT = 2.0  # 帧与 showinfo 行配对的等待上限


@dataclass
class _Run:
    """一趟解码的进程与其两个读线程。"""

    proc: subprocess.Popen[bytes]
    frames: queue.Queue[bytes | None]
    stamps: queue.Queue[float | None]
    frame_reader: threading.Thread
    err_reader: threading.Thread
    stderr_buf: collections.deque[str]
    st: State


def _filter_chain(cfg: Config, hdr: bool) -> str:
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
    return ",".join(chain)


def _build_cmd(path: str, cfg: Config, *, has_audio: bool, hdr: bool) -> list[str]:
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
        _filter_chain(cfg, hdr),
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
    return cmd


def _drain_frames(proc: subprocess.Popen[bytes], frames: queue.Queue[bytes | None]) -> None:
    """线程：按固定块长读 rawvideo。读到短块/EOF 即收尾。"""
    out = proc.stdout
    try:
        while out is not None:
            block = out.read(FRAME_BYTES)
            if not block or len(block) < FRAME_BYTES:
                break
            frames.put(block)
    finally:
        frames.put(None)  # 视频帧流结束


def _drain_stderr(
    proc: subprocess.Popen[bytes],
    ev: DecodeEvents,
    st: State,
    stderr_buf: collections.deque[str],
    stamps: queue.Queue[float | None],
) -> None:
    """线程：逐行解析 stderr，同时把 showinfo 的 pts 送进 stamps 队列供配对。"""
    err = proc.stderr
    try:
        if err is None:
            return
        for raw in err:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            stderr_buf.append(line.rstrip("\r\n"))
            m = SHOWINFO_PTS.search(line)
            if m:
                st.last_pts = float(m.group(1))
                stamps.put(st.last_pts)
            # 事件在读线程里就地解析：进程退出后不会因为主循环提前 break 而丢掉尾部行
            # （volumedetect 的 SUMMARY 就是最后几行，丢了会误报 empty_audio）
            parse_line(line, ev, st)
    finally:
        stamps.put(None)


def _start(
    path: str,
    cfg: Config,
    *,
    has_audio: bool,
    hdr: bool,
    ev: DecodeEvents,
    video_index: int | None,
    audio_index: int | None,
) -> _Run:
    proc = subprocess.Popen(
        _build_cmd(path, cfg, has_audio=has_audio, hdr=hdr),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=no_window_flags(),
    )
    st = State(video_index=video_index, audio_index=audio_index)
    frames: queue.Queue[bytes | None] = queue.Queue()
    stamps: queue.Queue[float | None] = queue.Queue()
    stderr_buf: collections.deque[str] = collections.deque(maxlen=_STDERR_TAIL)
    frame_reader = threading.Thread(target=_drain_frames, args=(proc, frames), daemon=True)
    err_reader = threading.Thread(
        target=_drain_stderr, args=(proc, ev, st, stderr_buf, stamps), daemon=True
    )
    frame_reader.start()
    err_reader.start()
    return _Run(proc, frames, stamps, frame_reader, err_reader, stderr_buf, st)


def _collect_samples(
    run: _Run,
    ev: DecodeEvents,
    deadline: float,
    cancel_event: threading.Event | None,
) -> tuple[list[Sample], bool, bool]:
    """主循环：帧与 pts 一一配对成 Sample。返回 (样本, 是否被杀, 是否超时)。"""
    samples: list[Sample] = []
    killed = timed_out = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            killed = True
            break
        if time.monotonic() > deadline:
            killed = timed_out = True
            break
        try:
            block = run.frames.get(timeout=0.2)
        except queue.Empty:
            if run.proc.poll() is not None:
                break
            continue
        if block is None:
            break
        try:
            pts = run.stamps.get(timeout=_PAIR_TIMEOUT)
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
    return samples, killed, timed_out


def _finish(run: _Run, deadline: float, *, killed: bool, timed_out: bool) -> tuple[int, bool]:
    """收尾：只有取消/超时才 kill，其余让它自然跑完（事件与音量在最后几行）。"""
    proc = run.proc
    if killed:
        proc.kill()
    elif proc.poll() is None:
        try:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            timed_out = True
    rc = proc.wait()
    run.err_reader.join(timeout=5)  # 等 stderr 读完，尾部事件才算数
    run.frame_reader.join(timeout=2)
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()
    return rc, timed_out


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
    ev = DecodeEvents()
    deadline = time.monotonic() + cfg.timeout_sec
    run = _start(
        path,
        cfg,
        has_audio=has_audio,
        hdr=hdr,
        ev=ev,
        video_index=video_index,
        audio_index=audio_index,
    )
    samples, killed, timed_out = _collect_samples(run, ev, deadline, cancel_event)
    rc, timed_out = _finish(run, deadline, killed=killed, timed_out=timed_out)

    close_open(ev, run.st, duration_sec)
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
        ev.stderr_tail = "\n".join(run.stderr_buf)
    return ev
