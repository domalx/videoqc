"""ffmpeg stderr 文案解析：事件行（freeze/black/silence/音量）与报错行。

这里是全项目唯一与 ffmpeg 文案耦合的地方——正则匹配的都是 ffmpeg 打印的英文行
（`lavfi.freezedetect.freeze_start`、`black_start`、`silence_end`、`mean_volume` …）。
升级 ffmpeg 大版本前先跑 `tests/data/*.log` 金样；改动本文件的正则必须同步那批金样。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from videoqc.core.model import DecodeEvents

FREEZE_START = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
FREEZE_END = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*(-?[0-9]+(?:\.[0-9]+)?)")
FREEZE_DUR = re.compile(r"lavfi\.freezedetect\.freeze_duration:\s*(-?[0-9]+(?:\.[0-9]+)?)")
BLACK = re.compile(
    r"black_start:\s*(-?[0-9]+(?:\.[0-9]+)?)\s+black_end:\s*(-?[0-9]+(?:\.[0-9]+)?)\s+black_duration:\s*(-?[0-9]+(?:\.[0-9]+)?)"
)
BLACK_START = re.compile(r"black_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
SILENCE_START = re.compile(r"silence_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
SILENCE_END = re.compile(
    r"silence_end:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*(-?[0-9]+(?:\.[0-9]+)?)"
)
MEAN_VOL = re.compile(r"mean_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB")
MAX_VOL = re.compile(r"max_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB")
SHOWINFO_PTS = re.compile(r"pts_time:\s*(-?[0-9]+(?:\.[0-9]+)?)")
ERROR = re.compile(
    r"error while decoding|corrupt|Invalid NAL|no frame!|missing picture"
    r"|Invalid data found|Error while decoding stream|Error splitting the input",
    re.IGNORECASE,
)
HEADER = re.compile(r"^(Input|Output) #")  # 路径里带 "corrupt" 也会命中 ERROR
STREAM_INDEX = re.compile(r"stream #\d+:(\d+)")  # ffmpeg: "Error while decoding stream #0:1"
MAX_ERRORS = 20


@dataclass
class State:
    """跨行状态：freezedetect/blackdetect/silencedetect 都是「开始一行、结束一行」。"""

    open_freeze: float | None = None
    freeze_dur: float | None = None
    open_black: float | None = None
    open_silence: float | None = None
    video_index: int | None = None  # 把 stream #0:N 的报错归类到视频/音频
    audio_index: int | None = None
    last_pts: float | None = None  # 最近一个 showinfo 采样点：给不带时间戳的错误行定位


def parse_line(line: str, ev: DecodeEvents, st: State) -> None:
    """解析一行 ffmpeg stderr（时间戳已是流内秒，ffmpeg 默认归一化，无需再减 origin）。"""
    line = line.rstrip("\r\n")
    if HEADER.match(line):
        return

    m = FREEZE_START.search(line)
    if m:
        # 上一个 freeze 的 end 行若缺失就来了新 start：用 start+duration 收口，避免吞掉一段
        if st.open_freeze is not None:
            close_freeze(ev, st, st.open_freeze)
        st.open_freeze, st.freeze_dur = float(m.group(1)), None
        return
    m = FREEZE_DUR.search(line)
    if m:
        st.freeze_dur = float(m.group(1))
        return
    m = FREEZE_END.search(line)
    if m and st.open_freeze is not None:
        ev.freezes.append((st.open_freeze, float(m.group(1))))
        st.open_freeze = st.freeze_dur = None
        return

    m = BLACK.search(line)
    if m:
        st.open_black = None
        ev.blacks.append((float(m.group(1)), float(m.group(2))))
        return
    m = BLACK_START.search(line)
    if m:
        st.open_black = float(m.group(1))
        return

    m = SILENCE_START.search(line)
    if m:
        st.open_silence = float(m.group(1))
        return
    m = SILENCE_END.search(line)
    if m:
        end, dur = float(m.group(1)), float(m.group(2))
        start = st.open_silence if st.open_silence is not None else end - dur
        st.open_silence = None
        ev.silences.append((start, end))
        return

    m = MEAN_VOL.search(line)
    if m:
        ev.mean_volume = float(m.group(1))
        return
    m = MAX_VOL.search(line)
    if m:
        ev.max_volume = float(m.group(1))
        return

    if ERROR.search(line):
        ev.error_count += 1
        idx = STREAM_INDEX.search(line)
        stream = int(idx.group(1)) if idx else None
        if stream is not None and stream == st.audio_index:
            ev.audio_errors += 1
            if st.last_pts is not None and len(ev.audio_error_times) < MAX_ERRORS:
                ev.audio_error_times.append(st.last_pts)
        else:
            # 无流号的行（如 "Invalid NAL unit size"）几乎都是视频解码器上下文
            ev.video_errors += 1
            if st.last_pts is not None and len(ev.video_error_times) < MAX_ERRORS:
                ev.video_error_times.append(st.last_pts)
        if len(ev.errors) < MAX_ERRORS:
            ev.errors.append(line.strip()[:200])


def close_freeze(ev: DecodeEvents, st: State, eof_end: float) -> None:
    """收口冻结区间：优先用 start+duration，缺失时才退到 eof_end（SPEC §4.3）。"""
    if st.open_freeze is None:
        return
    start = st.open_freeze
    end = start + st.freeze_dur if st.freeze_dur is not None else eof_end
    ev.freezes.append((start, end))
    st.open_freeze = st.freeze_dur = None


def close_open(ev: DecodeEvents, st: State, end: float) -> None:
    """进程结束时仍张着的区间：冻结优先用 duration，否则持续到文件末尾（静止片尾常见）。"""
    close_freeze(ev, st, end)
    if st.open_black is not None:
        ev.blacks.append((st.open_black, end))
    if st.open_silence is not None:
        ev.silences.append((st.open_silence, end))
