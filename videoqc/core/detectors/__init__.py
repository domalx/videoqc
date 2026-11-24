"""检测器注册表：CHECKS 是报告「检测项」附录与执行顺序的唯一来源。

DETECTORS 带 full_only 标记，由 pipeline 按 mode 单点门控（检测器内部不再各判一次
mode/decode，只保留 decode is None 的兜底），避免两处真值源漂移。
"""
from __future__ import annotations

from collections.abc import Callable

from videoqc.core.context import FileContext
from videoqc.core.detectors.audio import check_d06, check_d07, check_d08
from videoqc.core.detectors.colorcast import check_d10, check_d11
from videoqc.core.detectors.corruption import check_d12
from videoqc.core.detectors.freeze import check_d05, check_d09
from videoqc.core.detectors.integrity import check_d01, check_d02, check_d13, check_d14
from videoqc.core.detectors.timing import check_d03, check_d04
from videoqc.core.model import Issue

Check = Callable[[FileContext], list[Issue]]

# (ID, 名称, 函数, 仅全量模式)
CHECKS: list[tuple[str, str, Check, bool]] = [
    ("D01", "文件完整性", check_d01, False),
    ("D02", "音视频对齐", check_d02, False),
    ("D03", "跳秒", check_d03, False),
    ("D04", "时间戳异常", check_d04, False),
    ("D05", "画面冻结", check_d05, True),
    ("D06", "静音段", check_d06, True),
    ("D07", "音量异常", check_d07, True),
    ("D08", "无音轨", check_d08, False),
    ("D09", "黑场", check_d09, True),
    ("D10", "偏色", check_d10, True),
    ("D11", "曝光/纯色", check_d11, True),
    ("D12", "坏包", check_d12, True),
    ("D13", "素材口径提示", check_d13, False),
    ("D14", "录制中断截断", check_d14, False),
]

# (函数, 仅全量模式)
DETECTORS: list[tuple[Check, bool]] = [(fn, full_only) for _, _, fn, full_only in CHECKS]
