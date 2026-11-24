"""入口：无参 → GUI；--headless → CLI（见 SPEC §7 / §6）。"""
from __future__ import annotations

import sys

from videoqc.app import cli


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--headless":
        return cli.main(args[1:])
    if args:
        return cli.main(args)
    return _run_gui()


def _run_gui() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("videoqc")

    from videoqc.app.config import load_config
    from videoqc.app.logging_setup import configure, install_excepthook, install_qt_message_handler
    from videoqc.media.paths import ROOT, binaries_present
    from videoqc.ui.main_window import MainWindow

    log = configure(headless=False)
    install_excepthook()
    install_qt_message_handler()
    cfg, warnings = load_config(ROOT / "config.json")
    if warnings:
        print("\n".join(warnings), file=sys.stderr)
    log.info("GUI 启动自检：工具齐备=%s, 配置告警=%d 条", binaries_present(), len(warnings))
    win = MainWindow(cfg)
    win.show()
    if not binaries_present():
        QMessageBox.critical(win, "缺少外部程序",
                             "bin/ 下未找到 ffmpeg.exe 或 ffprobe.exe，无法分析视频。")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
