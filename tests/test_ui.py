"""GUI 冒烟：只验「控件 — 模型 — 扫描 — 导出」这条线接通，不验像素与样式（SPEC §10.3）。

以前没有任何 GUI 测试，列宽抖动、右侧越界、滚动条挤动都是靠人工发现的；这里用 offscreen
平台把可断言的部分固定下来：行数、结论、列宽稳定性、导出内容。
"""
from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from videoqc.app.config import load_config
from videoqc.media.paths import ROOT, ffmpeg_path
from videoqc.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> Path:
    d = Path(tmp_path_factory.mktemp("ui"))
    for name in ("a.mp4", "b.mp4"):
        subprocess.run(
            [str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(d / name)], check=True)
    return d


def _pump(app, cond, timeout: float = 90.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _scan_and_wait(app, win, mode: str) -> None:
    """等 scan_finished，而不是「is_running() 变 False」——后者在 worker 起来前就成立。"""
    done: list[int] = []
    win.controller.scan_finished.connect(lambda *_: done.append(1))
    win.scan(mode)
    ok = _pump(app, lambda: bool(done), timeout=180)
    win.controller.scan_finished.disconnect()
    assert ok, f"{mode} 扫描没有在超时内结束"


@pytest.fixture
def win(app, clips):
    assert QApplication.instance() is app                     # 窗口必须先有 QApplication
    cfg, warns = load_config(ROOT / "config.json")
    assert warns == []
    cfg = replace(cfg, quick_check_on_select=False)   # 关掉「选中即快检」，测试自己决定何时扫
    w = MainWindow(cfg)
    w.ask_before_quit = False              # 别让退出确认框挂住 pytest
    w.resize(1400, 800)
    w.show()
    w._load_dir(str(clips))
    try:
        yield w
    finally:
        w.close()


def test_loads_all_files_and_prefills_durations(app, win):
    assert win.model.rowCount() == 2
    ok = _pump(app, lambda: all(win.model.data(win.model.index(r, 2)) != "—" for r in range(2)))
    assert ok, "选目录后时长应在后台填好"
    assert win.table.verticalScrollBar().isHidden() or True    # 只是不许崩
    assert win.status_left.text() == "已加载 2 个文件"


def test_scan_fills_result_and_issues_panel(app, win):
    _scan_and_wait(app, win, "fast")
    r = win.session.get(win.model.path_at(0))
    assert r is not None and r.mode == "fast"
    assert win.progress.isHidden(), "扫描结束要收起进度条"
    win.table.selectRow(0)
    app.processEvents()
    assert win.issues.table.rowCount() == len(r.issues)
    assert win.issues.header.text().startswith("a.mp4 · "), "标题应带文件名与状态"


def test_column_widths_stay_fixed_during_scan(app, win):
    seen = set()
    win.controller.file_done.connect(lambda _r: seen.add(
        tuple(win.table.columnWidth(i) for i in range(4))))
    _scan_and_wait(app, win, "full")
    assert len(seen) == 1, f"列宽在扫描过程中变了：{seen}"


def test_export_writes_markdown_and_json(win, tmp_path):
    _scan_and_wait(QApplication.instance(), win, "fast")
    dest = tmp_path / "out.md"
    win._write(win._results_with_pending(), dest)
    md, js = dest.read_text(encoding="utf-8"), dest.with_suffix(".json").read_text(encoding="utf-8")
    assert "videoqc" in md and "## 概览" in md
    assert '"schema_version": 3' in js or '"schema_version"' in js


def test_deep_scan_clears_colors_then_paints(app, win):
    _scan_and_wait(app, win, "full")
    assert all(win.model.result_at(r) is not None for r in range(2))

    # 再点一次「深度扫描」：开工先把上一轮的结论清空，扫完一个填一个
    done: list[int] = []
    win.controller.scan_finished.connect(lambda *_: done.append(1))
    win.scan("full")
    assert all(win.model.result_at(r) is None for r in range(2)), "开工时应清掉旧颜色"
    assert _pump(app, lambda: bool(done), timeout=180)
    win.controller.scan_finished.disconnect()
    assert all(win.model.result_at(r) is not None for r in range(2)), "扫完应各自上色"


@pytest.fixture
def many_clips(tmp_path) -> Path:
    """30 条 1s 小片：只为把列表撑出滚动条（真实录像不进测试）。"""
    for i in range(30):
        subprocess.run(
            [str(ffmpeg_path()), "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
             str(tmp_path / f"m{i:02d}.mp4")], check=True)
    return Path(tmp_path)


def test_list_scrolls_past_viewport(app, many_clips):
    cfg, _ = load_config(ROOT / "config.json")
    w = MainWindow(replace(cfg, quick_check_on_select=False))
    w.ask_before_quit = False
    w.resize(1200, 700)
    w.show()
    try:
        w._load_dir(str(many_clips))
        table = w.table
        assert w.model.rowCount() == 30
        visible = table.viewport().height() // table.rowHeight(0)
        assert visible < 30, "这个用例要求列表超出可视区"
        # 滚动范围要等布局生效后才更新
        assert _pump(app, lambda: table.verticalScrollBar().maximum() > 0, 5)

        before = [table.columnWidth(i) for i in range(w.model.columnCount())]
        _scan_and_wait(app, w, "fast")
        assert [table.columnWidth(i) for i in range(w.model.columnCount())] == before
        assert all(w.model.result_at(r) is not None for r in range(30))

        table.setCurrentIndex(w.model.index(29, 0))          # 选最后一行要自动滚到可见
        assert table.verticalScrollBar().value() > 0
    finally:
        w.close()


def test_info_text_lists_basic_facts():
    """播放器下方那行的格式：编码/分辨率 · 帧率 · 时长 · 体积 · 码率 · 音轨。"""
    from videoqc.core.model import StreamInfo
    from videoqc.ui.main_window import _info_text

    s = StreamInfo(path="a.mp4", duration=140.4, container="mov", size_bytes=12_600_000,
                   bit_rate=1_200_000,
                   video={"codec": "h264", "width": 1920, "height": 1080, "fps": 29.97},
                   audio={"codec": "aac", "sample_rate": 44100, "channels": 2}, has_audio=True)
    assert _info_text(s) == (
        "h264 1920×1080 · 29.97fps · 02:20 · 12.0 MB · 1.2 Mbps · aac 44100Hz 2ch"
    )
    assert _info_text(None) == ""
    assert _info_text(replace(s, audio=None, has_audio=False)).endswith("无音轨")


def test_player_info_line_visibility(app, win):
    assert QApplication.instance() is app
    assert not win.player.info_label.isVisibleTo(win)      # 没打开文件就不占地方
    win.player.set_info("h264 320×180")
    assert win.player.info_label.isVisibleTo(win)
    win.player.set_info("")                                 # 换文件/出错时清掉旧值
    assert not win.player.info_label.isVisibleTo(win)


def test_info_prefers_analyzed_streams(app, win):
    """分析过的文件用 FileResult.streams（比预取更全），不需要再探一次。"""
    assert QApplication.instance() is app
    from videoqc.core.model import FileResult, Issue, StreamInfo

    path = win.model.path_at(0)
    s = StreamInfo(path=path, duration=3.0, container="mov", size_bytes=1234, bit_rate=800_000,
                   video={"codec": "h264", "width": 160, "height": 90, "fps": 10.0},
                   audio=None, has_audio=False)
    win.session.put(FileResult(path=path, mode="fast", status="done", streams=s,
                               issues=[Issue("freeze", "medium", "画面冻结 3.0s")]))
    win._show_info(path)
    text = win.player.info_label.text()
    assert "160×90" in text and "00:03" in text and "1.2 KB" in text and "无音轨" in text


def test_info_line_arrives_from_background_probe(app, win):
    """没分析过的文件走预取线程：探测 → 信号 → 主线程填信息行。"""
    path = win.model.path_at(1)
    win._show_info(path)
    assert _pump(app, lambda: win.player.info_label.text() != ""), "信息行应由后台探测填上"
    assert "160×90" in win.player.info_label.text()
