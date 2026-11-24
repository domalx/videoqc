"""配置：DEFAULTS、Config、load_config()、config_hash()。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_EXTENSIONS = (
    "mp4",
    "mov",
    "mkv",
    "avi",
    "flv",
    "ts",
    "m2ts",
    "mpg",
    "webm",
    "m4v",
    "wmv",
    "3gp",
)

# 唯一默认值来源：config.json 由这里生成，代码里不再写第二份
DEFAULTS: dict = {
    "config_version": 1,
    "extensions": list(DEFAULT_EXTENSIONS),
    "jobs": 2,
    "timeout_sec": 3600,
    "quick_check_on_select": True,
    "pts_gap_factor": 3.0,
    "pts_gap_min": 0.2,
    "freeze_noise": 0.003,
    "freeze_min_sec": 2.0,
    "black_min_sec": 0.5,
    "silence_db": -50.0,
    "silence_min_sec": 3.0,
    "mean_volume_low_db": -30.0,
    "silent_mean_db": -45.0,
    "clipping_db": -0.1,
    "require_audio": True,
    "av_offset_sec": 0.5,
    "truncation_gap_sec": 2.0,
    "color_cast_threshold": 25.0,
    "color_sample_sec": 1.0,
    "color_window_sec": 15.0,
    "hdr_tonemap": True,
    "report_dir": "reports",
}

# 合法范围：越界 clamp
RANGES: dict[str, tuple[float, float]] = {
    "jobs": (1, 8),
    "timeout_sec": (60, 86400),
    "pts_gap_factor": (1.5, 50),
    "pts_gap_min": (0.02, 5),
    "freeze_noise": (0.0001, 0.1),
    "freeze_min_sec": (0.5, 60),
    "black_min_sec": (0.1, 30),
    "silence_db": (-90, -10),
    "silence_min_sec": (0.5, 120),
    "mean_volume_low_db": (-60, -5),
    "silent_mean_db": (-90, -20),
    "clipping_db": (-10, 0),
    "av_offset_sec": (0.05, 5),
    "truncation_gap_sec": (0.5, 30),
    "color_cast_threshold": (5, 80),
    "color_sample_sec": (0.5, 60),
    "color_window_sec": (2, 300),
}
INT_KEYS = {"jobs", "timeout_sec"}
BOOL_KEYS = {"quick_check_on_select", "hdr_tonemap", "require_audio"}


@dataclass(frozen=True)
class Config:
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    jobs: int = 2
    timeout_sec: int = 3600
    quick_check_on_select: bool = True
    pts_gap_factor: float = 3.0
    pts_gap_min: float = 0.2
    freeze_noise: float = 0.003
    freeze_min_sec: float = 2.0
    black_min_sec: float = 0.5
    silence_db: float = -50.0
    silence_min_sec: float = 3.0
    mean_volume_low_db: float = -30.0
    silent_mean_db: float = -45.0
    clipping_db: float = -0.1
    require_audio: bool = True
    av_offset_sec: float = 0.5
    truncation_gap_sec: float = 2.0
    color_cast_threshold: float = 25.0
    color_sample_sec: float = 1.0
    # 偏色要持续这么久才算一段：15s 的强偏色可能只是布景/霓虹（_CAST_MIN_SPAN 同类口径）
    color_window_sec: float = 15.0
    hdr_tonemap: bool = True
    report_dir: str = "reports"


def _coerce(key: str, raw: Any, warnings: list[str]) -> Any:
    default = DEFAULTS[key]
    ok_type = (
        (
            isinstance(raw, bool)
            if key in BOOL_KEYS
            else isinstance(raw, (int, float)) and not isinstance(raw, bool)
        )
        if key not in ("extensions", "report_dir")
        else (isinstance(raw, list) if key == "extensions" else isinstance(raw, str))
    )
    if not ok_type:
        warnings.append(f"{key} 无效({raw!r})，已用默认 {default}")
        return default
    if key == "extensions":
        if not isinstance(raw, list):
            return default
        items = [str(e).lower().lstrip(".") for e in raw if str(e).strip()]
        if not items:
            warnings.append(f"{key} 为空，已用默认")
            return default
        return tuple(items)
    if key == "report_dir":
        return raw or default
    if key in BOOL_KEYS:
        return bool(raw)
    value = raw
    if key in RANGES:
        lo, hi = RANGES[key]
        clamped = min(max(value, lo), hi)
        if clamped != value:
            warnings.append(f"{key} 超出范围({value})，已钳位到 {clamped}")
            value = clamped
    return int(value) if key in INT_KEYS else float(value)


def load_config(path: str | Path | None) -> tuple[Config, list[str]]:
    """返回 (Config, 告警列表)。文件缺失/语法错 → 全默认 + 告警，不自动创建。"""
    warnings: list[str] = []
    if path is None:
        return Config(), warnings
    p = Path(path)
    if not p.exists():
        return Config(), [f"{p} 不存在，使用默认配置"]
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        return Config(), [
            f"config.json 语法错误（第 {e.lineno} 行第 {e.colno} 列）：{e.msg}，使用默认配置"
        ]
    except OSError as e:
        return Config(), [f"{p} 读取失败：{e}，使用默认配置"]
    if not isinstance(raw, dict):
        return Config(), ["config.json 顶层不是对象，使用默认配置"]

    version = raw.get("config_version", 1)
    if isinstance(version, (int, float)) and version > 1:
        warnings.append(f"config_version={raw.get('config_version')} 高于程序支持的 1，将按 1 解析")
    for key in raw:
        if key not in DEFAULTS:
            warnings.append(f"未知配置项 {key}（已忽略）")

    values = {
        k: _coerce(k, raw.get(k, DEFAULTS[k]), warnings) for k in DEFAULTS if k != "config_version"
    }
    return Config(**values), warnings


def config_hash(cfg: Config) -> str:
    """canonical = key 排序 + 紧凑分隔符；报告与界面共用同一哈希。"""
    canon = json.dumps(asdict(cfg), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
