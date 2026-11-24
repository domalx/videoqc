"""问题列表：时间/类型/级别/描述/证据 + 已检查项清单（SPEC §7.4）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from videoqc.app.config import Config
from videoqc.core.model import FileResult, Issue, fmt_tc
from videoqc.media.report import _repro, label
from videoqc.ui import theme

COLS = ["#", "时间", "类型", "描述"]
SEV_TEXT = {"high": "高", "medium": "中", "low": "低", "info": "提示"}
STAGE_TEXT = {"probe": "探测", "packets": "读包", "decode": "解码", "color": "抽帧", "skipped": "复用"}


class IssuePanel(QWidget):
    issue_activated = Signal(object)     # 双击/Enter：跳转并播放
    issue_focused = Signal(object)       # 单击：跳转并暂停

    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._result: FileResult | None = None
        self._issues: list[Issue] = []

        self.header = QLabel("未分析 · 点「深度扫描」或双击行", self)
        self.header.setStyleSheet("font-weight:600; padding:4px 2px;")

        self.table = QTableWidget(0, len(COLS), self)
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)       # 行号
        self.table.setColumnWidth(0, 40)
        h.setSectionResizeMode(3, QHeaderView.Stretch)     # 描述吃剩余宽度
        for i, width in ((1, 170), (2, 120)):              # Fixed：内容长短不该挤动描述列
            h.setSectionResizeMode(i, QHeaderView.Fixed)
            h.resizeSection(i, width)
        # 竖向滚动条常显：内容多少不该改变「描述」列宽度（问题数在文件之间跳动很大）
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.itemDoubleClicked.connect(self._activate_current)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 0, 0, 0)
        root.addWidget(self.header)
        root.addWidget(self.table, 1)

    # ---- 对外 ----
    def load(self, r: FileResult | None, stage: str | None = None) -> None:
        self._result = r
        self._issues = list(r.issues) if r else []
        if r is None and stage is None:
            self.header.setText("未分析 · 点「深度扫描」或双击行")
            self.table.setRowCount(0)
            return
        if r is None:
            self.header.setText(f"分析中…（正在{STAGE_TEXT.get(stage or "", stage or "")}）")
            self.table.setRowCount(0)
            return
        self.header.setText(_header_text(r))
        self._fill_table()        # 没问题就是空表：标题行已经写了「未发现异常」

    # ---- 内部 ----
    def _fill_table(self) -> None:
        self.table.setRowCount(len(self._issues))
        for row, i in enumerate(self._issues):
            cells = [str(row + 1), _time_text(i),
                     f"{theme.sev_icon(i.severity)} {label(i.kind)}", i.title]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 2:                     # 级别不再占一列，靠类型着色表达
                    item.setForeground(QBrush(theme.SEV_COLOR[i.severity]))
                item.setToolTip(i.detail or _evidence_text(i))
                item.setData(Qt.UserRole, row)
                self.table.setItem(row, col, item)

    def _current(self) -> Issue | None:
        row = self.table.currentRow()
        return self._issues[row] if 0 <= row < len(self._issues) else None

    def _on_select(self) -> None:
        i = self._current()
        if i is not None:
            self.issue_focused.emit(i)

    def _activate_current(self, *_args) -> None:
        i = self._current()
        if i is not None:
            self.issue_activated.emit(i)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate_current()
            return
        super().keyPressEvent(event)

    def _menu(self, pos) -> None:
        i = self._current()
        if i is None or self._result is None:
            return
        menu = QMenu(self)
        act_time = menu.addAction("复制时间点")
        act_repro = menu.addAction("复制复现命令")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_time:
            QGuiApplication.clipboard().setText(fmt_tc(i.start) if i.start is not None else "")
        elif chosen is act_repro:
            QGuiApplication.clipboard().setText(_repro(self._result.path, self.cfg))


def _header_text(r: FileResult) -> str:
    if r.status == "pending" or r.mode == "none":
        return "未分析 · 点「深度扫描」或双击行"
    if r.status == "cancelled":
        return f"{_name(r)} · 已取消 · 保留已完成的问题"
    highs = sum(1 for i in r.issues if i.severity == "high")
    if not r.issues:
        return f"{_name(r)} · 未发现异常"
    parts = []
    if highs:
        parts.append(f"{highs} 失败")
    if len(r.issues) > highs:
        parts.append(f"{len(r.issues) - highs} 警告/提示")
    return f"{_name(r)} · 发现 {len(r.issues)} 个问题（{'，'.join(parts)}）"


def _name(r: FileResult) -> str:
    from pathlib import Path
    return Path(r.path).name


def _time_text(i: Issue) -> str:
    if i.start is None:
        return "—"
    return f"{fmt_tc(i.start)} → {fmt_tc(i.end)}" if i.end is not None and i.end > i.start else fmt_tc(i.start)


def _evidence_text(i: Issue) -> str:
    if i.evidence:
        return ", ".join(f"{k}={v}" for k, v in i.evidence.items())
    return i.detail or ""
