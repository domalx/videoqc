"""FileContext：检测器的唯一输入容器（管线逐阶段填充）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from videoqc.app.config import Config
from videoqc.core.model import DecodeEvents, Issue, PacketStats, Sample, StreamInfo


@dataclass
class FileContext:
    path: Path
    cfg: Config
    streams: StreamInfo | None = None
    packets: PacketStats | None = None
    decode: DecodeEvents | None = None
    samples: list[Sample] | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.streams and self.streams.duration:
            return self.streams.duration
        if self.packets and self.packets.pts:
            return self.packets.last_ts
        return 0.0
