"""文件表格模型与视图（SPEC §7.2）。

行 = 目录里发现的文件（始终全量），结果存在 dict 里按 path 回填。
早期版本用「结果列表 + 待分析集合」两套数据拼行号，排序/回填后会错位（表现为少一行、空行）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    Qt,
    Signal,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView

from videoqc.core.model import FileResult, fmt_tc
from videoqc.ui import theme

COLUMNS = ["#", "名称", "时长", "问题数"]
RANGE_TEXT = {"fast": "快检", "full": "全量", "none": "—", "": "—"}


class FileTableModel(QAbstractTableModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._paths: list[str] = []
        self._results: dict[str, FileResult] = {}
        self._durations: dict[str, float] = {}
        self._names: dict[str, str] = {}

    # ---- 数据进出 ----
    def set_paths(self, paths: list[str]) -> None:
        self.beginResetModel()
        self._paths = list(dict.fromkeys(paths))
        self._results.clear()
        self._durations.clear()
        self._names = _display_names(self._paths)
        self.endResetModel()

    def set_result(self, r: FileResult) -> None:
        self._results[r.path] = r
        self._touch(r.path)

    def set_duration(self, path: str, seconds: float) -> None:
        self._durations[path] = seconds
        self._touch(path, column=2)

    def reset_results(self, paths) -> None:
        """新一轮扫描开始：只清掉将被重扫的行，保留文件列表与已探测的时长。"""
        drop = [p for p in paths if p in self._results]
        for p in drop:
            del self._results[p]
        if drop and self._paths:
            self.dataChanged.emit(self.index(0, 0),
                                  self.index(len(self._paths) - 1, len(COLUMNS) - 1))

    def clear(self) -> None:
        self.beginResetModel()
        self._paths.clear()
        self._results.clear()
        self._durations.clear()
        self._names.clear()
        self.endResetModel()

    def row_of(self, path: str) -> int:
        try:
            return self._paths.index(path)
        except ValueError:
            return -1

    def path_at(self, row: int) -> str:
        return self._paths[row] if 0 <= row < len(self._paths) else ""

    def result_at(self, row: int) -> FileResult | None:
        return self._results.get(self.path_at(row))

    def _touch(self, path: str, column: int | None = None) -> None:
        row = self.row_of(path)
        if row < 0:
            return
        right = len(COLUMNS) - 1 if column is None else column
        self.dataChanged.emit(self.index(row, 0 if column is None else column),
                              self.index(row, right))

    # ---- Qt 接口 ----
    def rowCount(self, parent=None) -> int:
        return 0 if (parent is not None and parent.isValid()) else len(self._paths)

    def columnCount(self, parent=None) -> int:
        return 0 if (parent is not None and parent.isValid()) else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or orientation != Qt.Horizontal:
            return None
        return COLUMNS[section]

    def data(self, index: QModelIndex | QPersistentModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.result_at(index.row())
        path = self.path_at(index.row())
        if role == Qt.DisplayRole:
            if index.column() == 0:
                return index.row() + 1          # 行号跟行序走，排序后重排
            return _cell_text(r, path, index.column(), self._names, self._durations)
        if role == Qt.BackgroundRole and r is not None:
            return QBrush(theme.ROW_BG.get(_state(r), QColor("transparent")))
        if role == Qt.ToolTipRole:
            if r is None:
                return f"{path}\n未分析"
            return (f"{path}\n结论：{theme.CONCLUSION_TEXT[r.conclusion]} · 状态：{r.status}"
                    f" · 模式：{RANGE_TEXT.get(r.mode or 'none', '—')}")
        return None

class FileTableView(QTableView):
    open_requested = Signal(int)          # 双击/Enter 行号

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(False)
        self.verticalHeader().setVisible(False)   # 行号是第 0 列，不用表头（表头格子像复选框）
        self.doubleClicked.connect(lambda idx: self.open_requested.emit(idx.row()))

    def setModel(self, model) -> None:
        super().setModel(model)
        self._style_header()
        if model is not None:
            model.modelReset.connect(self._style_header)

    def _style_header(self) -> None:
        header = self.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 44)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        # 时长/问题数用固定宽度：ResizeToContents 会随内容（"—" → "00:11:45.633"）变宽，
        # 把 Stretch 的名称列挤来挤去；名称列宽度只应随窗口变化。
        for i, width in ((2, 104), (3, 64)):
            header.setSectionResizeMode(i, QHeaderView.Fixed)
            header.resizeSection(i, width)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentIndex().isValid():
            self.open_requested.emit(self.currentIndex().row())
            return
        super().keyPressEvent(event)


def _state(r: FileResult) -> str:
    return r.status if r.status != "done" else r.conclusion


def _cell_text(r, path: str, col: int, names: dict[str, str], durations: dict[str, float]):
    if col == 1:
        return names.get(path, Path(path).name)
    if col == 2:
        dur = (r.streams.duration if r and r.streams else 0.0) or durations.get(path)
        return fmt_tc(dur) if dur else "—"
    if col == 3:
        return len(r.issues) if r else None
    return None


def _display_names(paths: list[str]) -> dict[str, str]:
    """重名时带上最近一级父目录（SPEC §6.4）。"""
    by_name: dict[str, list[str]] = {}
    for p in paths:
        by_name.setdefault(Path(p).name.lower(), []).append(p)
    names: dict[str, str] = {}
    for group in by_name.values():
        for p in group:
            names[p] = Path(p).name if len(group) == 1 else f"{Path(p).parent.name}/{Path(p).name}"
    return names
