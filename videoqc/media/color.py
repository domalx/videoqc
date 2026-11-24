"""纯计算：32×18 rgb24 帧 → 亮度/饱和度/偏色强度与方向（不含 ffmpeg）。"""
from __future__ import annotations

from videoqc.app.config import Config
from videoqc.core.model import Sample

_Y_DARK = 16.0
_Y_BRIGHT = 235.0
_SAT_FLAT = 0.03
_DIRECTIONS = {(0,): "红", (1,): "绿", (2,): "蓝", (0, 1): "黄", (1, 2): "青", (0, 2): "洋红"}


def frame_stats(rgb: bytes) -> tuple[float, float, float, float, float, float]:
    """返回 (r, g, b, y, sat, cast)，取值 0–255（sat 为 0–1）。"""
    n = len(rgb) // 3
    r = sum(rgb[0::3]) / n
    g = sum(rgb[1::3]) / n
    b = sum(rgb[2::3]) / n
    y = 0.299 * r + 0.587 * g + 0.114 * b
    mx, mn = max(r, g, b), min(r, g, b)
    sat = 0.0 if mx == 0 else (mx - mn) / mx
    cast = max(abs(r - y), abs(g - y), abs(b - y))
    return r, g, b, y, sat, cast


def make_sample(t: float, rgb: bytes) -> Sample:
    r, g, b, y, sat, cast = frame_stats(rgb)
    return Sample(t=t, r=r, g=g, b=b, y=y, sat=sat, cast=cast)


def cast_direction(s: Sample) -> str | None:
    """偏色方向；三通道同号（整体偏亮/偏暗）返回 None —— 不算偏色（归 D11）。"""
    diffs = (s.r - s.y, s.g - s.y, s.b - s.y)
    up = tuple(i for i, d in enumerate(diffs) if d > 0)
    if len(up) == 3 or not up:                      # 三通道同号 / 无偏
        return None
    key = up if len(up) == 2 else up[:1]
    return "偏" + _DIRECTIONS[key]


def is_color_cast(s: Sample, cfg: Config) -> bool:
    return s.cast > cfg.color_cast_threshold and s.y > 24 and cast_direction(s) is not None


def is_dark(s: Sample) -> bool:
    return s.y < _Y_DARK


def is_bright(s: Sample) -> bool:
    return s.y > _Y_BRIGHT


def is_flat(s: Sample) -> bool:
    return s.sat < _SAT_FLAT and 32 <= s.y <= 224
