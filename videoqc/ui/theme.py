"""配色与图标（文字图标，避免额外资源文件）。"""
from __future__ import annotations

from PySide6.QtGui import QColor

from videoqc.media.report import SEV_ICON

ROW_BG = {
    "passed": QColor("#E9F7EF"),
    "failed": QColor("#FDECEA"),
    "warned": QColor("#FFF6E0"),
    "unfinished": QColor("#EEEEEE"),
}
SEV_COLOR = {
    "high": QColor("#C0392B"),
    "medium": QColor("#D68910"),
    "low": QColor("#7F8C8D"),
    "info": QColor("#A6ACAF"),
}
CONCLUSION_TEXT = {"passed": "通过", "warned": "警告", "failed": "失败", "unfinished": "未完成"}


def sev_icon(s: str) -> str:
    return SEV_ICON.get(s, "·")
