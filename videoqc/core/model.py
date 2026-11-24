"""数据模型：Issue/StreamInfo/PacketStats/DecodeEvents/Sample/FileResult + fmt_tc。"""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("info", "low", "medium", "high")  # 升序，比较用 index
SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}


@dataclass
class Issue:
    kind: str
    severity: str
    title: str
    detail: str = ""
    start: float | None = None
    end: float | None = None
    evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.severity in SEV_ORDER, self.severity
        assert self.start is not None or self.end is None, "start=None ⇒ end=None"


@dataclass
class StreamInfo:
    path: str
    duration: float | None
    container: str
    size_bytes: int
    bit_rate: int | None
    video: dict | None
    audio: dict | None
    has_audio: bool


@dataclass
class AudioStats:
    """音频包时间戳统计（D02/D04 用；视频侧见 PacketStats）。"""

    first_ts: float
    last_ts: float
    packets: int
    dup_pts: int = 0
    dts_non_monotonic: int = 0
    missing_pts: int = 0

    @property
    def span(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)


@dataclass
class PacketStats:
    pts: list[float]
    p50: float
    dts_non_monotonic: int
    dup_pts: int
    corrupt_flags: int
    first_ts: float
    last_ts: float
    missing_pts: int = 0
    nominal_fps: float | None = None
    audio: AudioStats | None = None  # 没有音轨时为 None（一次 ffprobe 全流读包）


@dataclass
class DecodeEvents:
    freezes: list[tuple[float, float]] = field(default_factory=list)
    blacks: list[tuple[float, float]] = field(default_factory=list)
    silences: list[tuple[float, float]] = field(default_factory=list)
    mean_volume: float | None = None
    max_volume: float | None = None
    errors: list[str] = field(default_factory=list)
    error_count: int = 0  # 音视频合计（D12 标题用）
    video_errors: int = 0  # 按 stderr 里的 stream #0:N 归到视频流
    audio_errors: int = 0
    exit_code: int = 0
    completed: bool = True
    timed_out: bool = False  # True = 被超时 kill（非取消），驱动 status="timeout"
    stderr_tail: str = ""  # 失败时保留 stderr 尾 50 行（SPEC §1.2），仅日志用
    samples: list[Sample] | None = None  # 同一趟解码顺带抽出 32×18 缩略帧（D10/D11）
    samples_timed_out: bool = False
    samples_incomplete: bool = False  # showinfo 配对失步导致中途放弃采样（事件仍完整）


@dataclass
class Sample:
    t: float
    r: float
    g: float
    b: float
    y: float
    sat: float
    cast: float


@dataclass
class FileResult:
    path: str
    mode: str  # "fast"|"full"|"none"
    status: str  # "done"|"cancelled"|"timeout"|"failed"|"pending"
    generation: int = 0
    streams: StreamInfo | None = None
    stats: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    error: str | None = None
    elapsed_sec: float = 0.0

    @property
    def ok(self) -> bool:
        """派生值，不是字段：构造处不得传 ok（避免与 conclusion 各算一遍）。"""
        return self.conclusion == "passed"

    @property
    def conclusion(self) -> str:
        """passed|warned|failed|unfinished —— 全局唯一结论实现（C8）。

        high Issue 优先于 status：探测致命失败的 status 也是 "failed"，
        此时结论必须仍为 failed，而不是 unfinished（§1A、§5.1 第 8 步）。
        """
        sev = {i.severity for i in self.issues}
        if "high" in sev:
            return "failed"
        # 检测器/阶段自己崩了 ⇒ 结论不该显示“警告”，否则工具缺陷会伪装成素材问题
        if self.status != "done" or any(i.kind == "internal_error" for i in self.issues):
            return "unfinished"
        if sev & {"medium", "low"}:
            return "warned"
        return "passed"


def fmt_tc(sec: float | None) -> str:
    """None → '—'；否则 HH:MM:SS.mmm。先取整毫秒再归一化，避免 59.9996 显示成 60.000。"""
    if sec is None:
        return "—"
    ms = max(0, round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
