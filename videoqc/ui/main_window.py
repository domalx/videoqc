"""主窗口：工具栏 + 表格 + 播放器 + 问题面板（SPEC §7.1/§7.5）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from videoqc.app.config import Config, config_hash
from videoqc.app.controller import ScanController
from videoqc.app.session import MODE_RANK, Session
from videoqc.core.constants import PROBE_TIMEOUT_SEC
from videoqc.core.model import FileResult, StreamInfo
from videoqc.media import probe
from videoqc.media.paths import ROOT, binaries_present, iter_videos
from videoqc.media.report import dump_json, render_markdown, report_paths
from videoqc.ui.file_table import FileTableModel, FileTableView
from videoqc.ui.issue_panel import IssuePanel
from videoqc.ui.player_panel import PlayerPanel, _clock


def _fmt_size(n: int | None) -> str | None:
    if not n:
        return None
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _info_text(s: StreamInfo | None) -> str:
    """播放器下方那行：编码/分辨率 · 帧率 · 时长 · 体积 · 码率 · 音轨。"""
    if s is None:
        return ""
    parts: list[str] = []
    v = s.video
    if v:
        codec = v.get("codec") or "?"
        if v.get("width") and v.get("height"):
            codec += f" {v['width']}×{v['height']}"
        parts.append(codec)
        fps = v.get("fps")
        if fps:
            parts.append(f"{fps:.2f}".rstrip("0").rstrip(".") + "fps")
    if s.duration:
        parts.append(_clock(int(s.duration * 1000)))
    for extra in (_fmt_size(s.size_bytes),
                  f"{s.bit_rate / 1e6:.1f} Mbps" if s.bit_rate else None):
        if extra:
            parts.append(extra)
    a = s.audio
    parts.append(f"{a.get('codec') or '?'} {a.get('sample_rate') or '?'}Hz "
                 f"{a.get('channels') or '?'}ch" if a else "无音轨")
    return " · ".join(parts)


class MainWindow(QMainWindow):
    duration_ready = Signal(str, float)  # path, 秒（轻量 ffprobe，只为先填上时长列）
    info_ready = Signal(str, object)  # path, StreamInfo（播放器信息行）

    def __init__(self, cfg: Config, session: Session | None = None) -> None:
        super().__init__()
        self.cfg = cfg
        self.session = session or Session()
        self.model = FileTableModel(self)
        self.table = FileTableView(self)
        self.table.setModel(self.model)
        self.player = PlayerPanel(self)
        self.issues = IssuePanel(cfg, self)
        self.controller = ScanController(cfg, self.session, self)
        self.ask_before_quit = True  # 测试可置 False：扫描中关窗不弹确认框
        self._paths: list[str] = []
        self._dir: str = ""
        self._dur_gen = 0
        self._infos: dict[str, StreamInfo] = {}
        self._current = ""  # 播放器里正在放的文件
        self._dur_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="duration")
        self._stages: dict[str, str] = {}

        self.setWindowTitle("videoqc — 视频质检")
        self.resize(1280, 800)
        self.setMinimumSize(1100, 700)
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_toolbar()
        self._build_shortcuts()
        self._wire()
        self._restore_state()
        self._set_summary()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        self.summary_label = QLabel(self)
        self.summary_label.setStyleSheet("font-weight:600; padding:2px;")

        left = QWidget(self)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.table, 1)

        # 右区：播放器固定右上 1/4×1/4，问题面板吃掉剩余空间
        player_row = QHBoxLayout()
        player_row.setContentsMargins(0, 0, 0, 0)
        player_row.addWidget(self.player)  # 宽度铺满右半区，高度由 _apply_layout_sizes 钉死
        right = QWidget(self)
        self.right = right  # _apply_layout_sizes 要钉死它的宽度
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addLayout(player_row)
        rv.addWidget(self.issues, 1)

        self.split = QSplitter(Qt.Horizontal, self)
        self.split.addWidget(left)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setChildrenCollapsible(False)

        central = QWidget(self)
        cv = QVBoxLayout(central)
        cv.setContentsMargins(6, 4, 6, 0)
        cv.addWidget(self.summary_label)
        cv.addWidget(self.split, 1)
        self.setCentralWidget(central)

        self.progress = QProgressBar(self)
        self.progress.setFixedWidth(180)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.status_left = QLabel("", self)
        self.status_right = QLabel("", self)
        self.statusBar().addWidget(self.status_left, 1)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.status_right)

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("主")
        tb.setMovable(False)
        self.act_open = QAction("选择目录…", self)
        self.act_open.setShortcut(QKeySequence("Ctrl+O"))
        self.act_fast = QAction("快速扫描", self)
        self.act_fast.setShortcut(QKeySequence("F5"))
        self.act_full = QAction("深度扫描", self)
        self.act_full.setShortcut(QKeySequence("F6"))
        self.act_stop = QAction("停止", self)
        self.act_stop.setShortcut(QKeySequence("Esc"))
        self.act_stop.setEnabled(False)
        self.act_export = QAction("导出报告", self)
        self.act_export.setShortcut(QKeySequence("Ctrl+S"))
        for a in (self.act_open, self.act_fast, self.act_full, self.act_stop):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.act_export)

    def _build_shortcuts(self) -> None:
        """SPEC §7.5：播放/逐帧快捷键挂窗口，避免焦点在表格时失效。"""
        bindings = [
            ("Space", self.player.toggle_play),
            (
                "Left",
                lambda: self.player.seek_to(
                    self.player.slider.value() / 1000 - self.player._origin - 5, False
                ),
            ),
            (
                "Right",
                lambda: self.player.seek_to(
                    self.player.slider.value() / 1000 - self.player._origin + 5, False
                ),
            ),
            ("Shift+Left", lambda: self.player.step(-1)),
            ("Shift+Right", lambda: self.player.step(1)),
        ]
        for keys, fn in bindings:
            act = QAction(self)
            act.setShortcut(QKeySequence(keys))
            act.setShortcutContext(Qt.WindowShortcut)
            act.triggered.connect(fn)
            self.addAction(act)

    def _wire(self) -> None:
        self.act_open.triggered.connect(self.choose_directory)
        self.act_fast.triggered.connect(lambda: self.scan("fast"))
        self.act_full.triggered.connect(lambda: self.scan("full"))
        self.act_stop.triggered.connect(self.stop_scan)
        self.act_export.triggered.connect(self.export_report)
        self.table.open_requested.connect(self._open_row)
        self.table.selectionModel().currentRowChanged.connect(
            lambda cur, _prev: self._on_row(cur.row())
        )
        self.issues.issue_activated.connect(self._on_issue_activated)
        self.issues.issue_focused.connect(self._on_issue_focused)
        self.player.playing_changed.connect(self.controller.set_playing)
        self.duration_ready.connect(self.model.set_duration)
        self.info_ready.connect(self._on_info)

        c = self.controller
        c.file_started.connect(self._on_file_started)
        c.file_stage.connect(self._on_file_stage)
        c.file_done.connect(self._on_file_done)
        c.scan_finished.connect(self._on_scan_finished)
        c.scan_error.connect(lambda msg: QMessageBox.warning(self, "扫描错误", msg))

    def _restore_state(self) -> None:
        self.status_right.setText(f"阈值 {config_hash(self.cfg)[:8]}")
        self.status_left.setText("就绪")

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._apply_layout_sizes()

    def _apply_layout_sizes(self) -> None:
        """左栏 50% 宽；播放器 50% 宽 × 50% 高（SPEC §7.1）。"""
        w, h = self.width(), self.height()
        left_w = max(340, int(w * 0.5))
        # 右半区宽度钉死：左列表加载文件、滚动条进出、换选中文件都不许挤动右半区
        self.right.setFixedWidth(max(0, w - left_w - self.split.handleWidth()))
        self.split.setSizes([left_w, self.right.width()])
        # 高度固定为窗口 1/2；宽度交给布局铺满右半区——把宽度也写成 50%·w 会超过右半区
        # （分割条还占几个像素），把左侧列表挤到不足 1/2。
        self.player.setFixedHeight(max(240, int(h * 0.5)))

    # ---- 文件 ----
    def choose_directory(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择目录", str(ROOT))
        if not d:
            return
        self._load_dir(d)

    def _load_dir(self, d: str) -> None:
        self._dir = str(Path(d).resolve())
        self.controller.stop()
        self.session.clear()
        self._infos.clear()
        self._current = ""
        self.player.set_info("")
        self._paths = [str(p) for p in iter_videos(d, self.cfg.extensions)]
        self.model.set_paths(self._paths)
        self.issues.load(None)
        self.player.player.stop()
        self._set_summary()
        self.status_left.setText(f"已加载 {len(self._paths)} 个文件")
        self._prefetch_durations()
        if self._paths and not binaries_present():
            self.controller.scan_error.emit("bin/ 下缺少 ffmpeg.exe 或 ffprobe.exe")

    def _prefetch_durations(self) -> None:
        """选完目录立刻把时长填上：只读容器元数据，几百毫秒级（SPEC §7.2）。"""
        self._dur_gen += 1
        gen = self._dur_gen
        for path in self._paths:
            self._dur_pool.submit(self._probe_duration, path, gen)

    def _probe_duration(self, path: str, gen: int) -> None:
        """预取容器元数据：填时长列 + 播放器信息行（只读头，不占分析用的长超时）。"""
        try:
            info = probe.probe_streams(path, PROBE_TIMEOUT_SEC)
        except Exception:
            return
        if gen != self._dur_gen:
            return
        self._infos[path] = info
        if info.duration:
            self.duration_ready.emit(path, info.duration)
        self.info_ready.emit(path, info)

    def _on_info(self, path: str, info: StreamInfo) -> None:
        if path == self._current:
            self.player.set_info(_info_text(info))

    def _info_for(self, path: str) -> StreamInfo | None:
        r = self.session.get(path)
        if r is not None and r.streams is not None:
            return r.streams  # 分析过就直接用（比预取更全）
        return self._infos.get(path)

    def _show_info(self, path: str) -> None:
        self._current = path
        self.player.set_info(_info_text(self._info_for(path)))
        if self._info_for(path) is None:
            self._dur_pool.submit(self._probe_duration, path, self._dur_gen)

    def _on_row(self, row: int) -> None:
        path = self.model.path_at(row) if row >= 0 else ""
        if not path:
            return
        r = self.session.get(path)
        self.issues.load(r, self._stages.get(path))
        if r is None and self.cfg.quick_check_on_select:
            self.controller.start([path], "fast")

    def _open_row(self, row: int) -> None:
        path = self.model.path_at(row)
        if not path:
            return
        r = self.session.get(path)
        origin = 0.0
        fps = None
        if r and r.streams and r.streams.video:
            origin = _as_float(r.streams.video.get("start_time"))
            fps = r.streams.video.get("fps")
        self.player.load(path, origin)
        self._show_info(path)
        self.player.fps(fps)
        self.player.set_has_audio(bool(r and r.streams and r.streams.has_audio))
        self.player.seek_to(0.0, autoplay=True)  # 双击即播（SPEC §7.5）
        if r and r.mode == "full":
            self.issues.load(r)

    # ---- 扫描 ----
    def scan(self, mode: str) -> None:
        """深度扫描总是重扫全部文件；快速扫描只补没有全量结果的文件（SPEC §7.5）。"""
        paths = [p for p in dict.fromkeys(self._paths) if p]
        if mode == "fast":
            paths = [p for p in paths if self._rank(p) < MODE_RANK["full"]]
        if not paths:
            self.status_left.setText("没有需要扫描的文件")
            return
        # 开工先清上一轮的颜色/问题数，每个文件扫完再各自上色
        self.session.drop(paths)
        self.model.reset_results(paths)
        row = self.table.currentIndex().row()
        if row >= 0 and self.model.path_at(row) in set(paths):
            self.issues.load(None)
        self.act_stop.setEnabled(True)
        self.status_left.setText(
            f"开始{'深度' if mode == 'full' else '快速'}扫描：{len(paths)} 个文件"
        )
        self.progress.setRange(0, len(paths))
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m")
        self.progress.show()
        self.controller.start(paths, mode)

    def _rank(self, path: str) -> int:
        r = self.session.get(path)
        return MODE_RANK.get(r.mode, 0) if r else 0

    def stop_scan(self) -> None:
        self.controller.stop()
        self.act_stop.setEnabled(False)
        self.status_left.setText("已停止，保留已完成结果")

    def _on_file_started(self, path: str, _mode: str) -> None:
        self._stages[path] = "probe"
        self._refresh_row_status(path)

    def _on_file_stage(self, path: str, stage: str) -> None:
        self._stages[path] = stage
        if path == self.model.path_at(self.table.currentIndex().row()):
            self.issues.load(None, "skipped" if stage == "skipped" else stage)

    def _on_file_done(self, r: FileResult) -> None:
        self._stages.pop(r.path, None)
        self.progress.setValue(self.progress.value() + 1)
        self.model.set_result(r)
        self._set_summary()
        if r.path == self.model.path_at(self.table.currentIndex().row()):
            self.issues.load(r)

    def _on_scan_finished(self, _mode: str, info: dict) -> None:
        self.act_stop.setEnabled(False)
        self.progress.hide()
        self._set_summary()
        self.status_left.setText(
            f"扫描完成：{info['done']}/{info['total']} 个文件 · {info['elapsed']:.1f}s"
        )

    def _refresh_row_status(self, path: str) -> None:
        row = self.model.row_of(path)
        if row >= 0:
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, 3))

    def _set_summary(self) -> None:
        s = self.session.summary()
        self.summary_label.setText(
            f"共 {s['total']} 个文件 · 通过 {s['passed']} · 警告 {s['warned']} · "
            f"失败 {s['failed']} · 未完成 {s['unfinished']}"
        )

    # ---- 播放联动 ----
    def _on_issue_activated(self, issue) -> None:
        if issue.start is not None:
            self.player.seek_to(max(0.0, issue.start - 1.0), autoplay=True)

    def _on_issue_focused(self, issue) -> None:
        if issue.start is not None:
            self.player.seek_to(max(0.0, issue.start - 1.0), autoplay=False)

    # ---- 导出 ----
    def _results_with_pending(self) -> list[FileResult]:
        out = []
        for p in self._paths:
            r = self.session.get(p)
            out.append(
                r if r is not None else FileResult(path=p, mode="none", status="pending")
            )
        return out or self.session.all()

    def export_report(self) -> None:
        results = self._results_with_pending()
        if not results:
            QMessageBox.information(self, "导出报告", "没有可导出的结果")
            return
        default = report_paths(ROOT / self.cfg.report_dir, datetime.now())[0]
        dest, _ = QFileDialog.getSaveFileName(self, "导出报告", str(default), "Markdown (*.md)")
        if not dest:
            return
        self._write(results, Path(dest))

    def _write(self, results: list[FileResult], dest: Path) -> None:
        ff_ver = ""
        try:
            ff_ver = probe.short_version(probe.ffmpeg_version())
        except Exception:
            ff_ver = "未知"
        md = render_markdown(
            results,
            root=self._dir or str(ROOT),
            cfg=self.cfg,
            when=datetime.now(),
            version=_version(),
            ffmpeg_ver=ff_ver,
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")
        json_path = dest.with_suffix(".json")
        json_path.write_text(
            dump_json(
                results,
                root=self._dir or str(ROOT),
                when=datetime.now(),
                version=_version(),
                ffmpeg_ver=ff_ver,
                cfg=self.cfg,
            ),
            encoding="utf-8",
        )
        self.status_left.setText(f"已导出 {dest.name}")

    # ---- 拖放与关闭 ----
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.is_dir():
                self._load_dir(str(p))
            elif p.is_file():
                self.player.load(str(p))
                self._show_info(str(p))  # 拖进来的文件没进列表，信息行照样要显示
                self.player.fps(None)  # 给逐帧步进一个默认步长（否则 Shift+→ 抛 AttributeError）
            break

    def closeEvent(self, e) -> None:
        # 脚本/测试里关窗不该被人机确认框拦住（offscreen 下它会让进程卡死）
        if self.ask_before_quit and self.controller.is_running():
            if QMessageBox.question(self, "退出", "扫描仍在进行，确定退出？") != QMessageBox.Yes:
                e.ignore()
                return
            self.controller.stop()
        self._dur_pool.shutdown(wait=False, cancel_futures=True)
        e.accept()


def _as_float(v) -> float:
    try:
        return float(v)  # ffprobe 的 "N/A" 之类走这里
    except (TypeError, ValueError):
        return 0.0


def _version() -> str:
    from videoqc.version import __version__

    return __version__
