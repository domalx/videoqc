"""运行日志与崩溃兜底（SPEC §1.2）。标准库 only，日志失败绝不阻断分析。

- 文件：ROOT/logs/videoqc.log，RotatingFileHandler 2MB × 3，级别 INFO。
- headless：同时输出到 stderr。
- 崩溃：sys.excepthook + Qt 消息处理器统一写日志，保持默认行为（不静默退出）。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from videoqc.media.paths import ROOT

LOGGER_NAME = "videoqc"
LOG_PATH = ROOT / "logs" / "videoqc.log"
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_configured = False


def configure(*, headless: bool = False, log_path: Path | None = None) -> logging.Logger:
    """幂等配置全局 logger。log_path 仅供测试覆盖；返回可直接使用的 logger。"""
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter(_FORMAT)
    path = log_path or LOG_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:                                  # 磁盘只读/目录不可写：降级为无文件日志
        pass
    if headless:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    _configured = True
    return logger


def get_logger() -> logging.Logger:
    """取 logger，不强制配置——单独调用 pipeline 时不应产生日志文件副作用。"""
    return logging.getLogger(LOGGER_NAME)


def stderr_tail(text: str, n: int = 50) -> str:
    """取 stderr 最后 n 行（SPEC §1.2：失败时记尾 50 行，不写完整帧数据）。"""
    lines = text.rstrip("\r\n").splitlines()
    return "\n".join(lines[-n:])


def install_excepthook() -> None:
    """未捕获异常写日志后仍交给原 hook（GUI 下 Qt 默认弹窗，保证不静默退出）。"""
    previous = sys.excepthook

    def _hook(exc_type, exc, tb) -> None:
        get_logger().critical("未捕获异常", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    sys.excepthook = _hook


def install_qt_message_handler() -> None:
    """Qt 消息（qWarning/qCritical）转写日志。延迟导入，headless 不依赖 PySide6。"""
    try:
        from PySide6.QtCore import qInstallMessageHandler
    except ImportError:
        return

    logger = get_logger()

    def _handler(mode, context, message) -> None:
        level = logging.WARNING if mode <= 2 else logging.INFO   # QtCritical/Fatal → WARNING
        logger.log(level, "Qt[%s]: %s", getattr(context, "file", "") or "?", message)

    qInstallMessageHandler(_handler)
