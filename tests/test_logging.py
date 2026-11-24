"""运行日志模块：轮转配置、幂等、stderr 尾截取（SPEC §1.2）。无 Qt、无 ffmpeg 依赖。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import videoqc.app.logging_setup as ls


def _reset(monkeypatch):
    """每个用例隔离全局 logger 状态。"""
    monkeypatch.setattr(ls, "_configured", False)
    logger = logging.getLogger(ls.LOGGER_NAME)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    return logger


def test_configure_creates_rotating_file(tmp_path, monkeypatch):
    logger = _reset(monkeypatch)
    log_path = tmp_path / "sub" / "videoqc.log"
    log = ls.configure(headless=False, log_path=log_path)

    assert log is logger
    assert log.level == logging.INFO
    assert log.propagate is False
    handlers = [h for h in log.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == 2 * 1024 * 1024 and handlers[0].backupCount == 3
    assert log_path.parent.is_dir()                    # 首次运行自动建目录

    log.info("hello-from-test")
    handlers[0].flush()
    assert "hello-from-test" in log_path.read_text(encoding="utf-8")


def test_configure_is_idempotent(tmp_path, monkeypatch):
    log = _reset(monkeypatch)
    ls.configure(headless=False, log_path=tmp_path / "a.log")
    before = list(log.handlers)
    ls.configure(headless=True, log_path=tmp_path / "b.log")
    assert list(log.handlers) == before                # 第二次不改变 handler（也不加 stderr）


def test_headless_adds_stderr_handler(tmp_path, monkeypatch):
    import sys
    log = _reset(monkeypatch)
    ls.configure(headless=True, log_path=tmp_path / "x.log")
    assert any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
               and h.stream is sys.stderr for h in log.handlers)


def test_stderr_tail_keeps_last_n_lines():
    text = "\n".join(f"line{i}" for i in range(80))
    lines = ls.stderr_tail(text, 50).splitlines()
    assert len(lines) == 50
    assert lines[0] == "line30" and lines[-1] == "line79"


def test_stderr_tail_short_text_unchanged():
    assert ls.stderr_tail("a\nb\n", 50) == "a\nb"


def test_decode_failure_stores_stderr_tail(monkeypatch):
    """解码失败时 DecodeEvents 保存 stderr 尾 50 行，供日志排障。"""
    import io
    import subprocess

    from videoqc.app.config import Config
    from videoqc.media import runners

    # 造一个一定失败且输出 >50 行 stderr 的假进程
    class _FakeProc:
        stderr = None
        stdout = None
        stdin = None
        returncode = 1

        def poll(self):
            return 1

        def wait(self):
            return 1

        def kill(self):
            pass

    fake = _FakeProc()
    fake.stderr = io.StringIO("\n".join(f"err{i}" for i in range(60)) + "\n")
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: fake)
    ev = runners.run_decode_pass("x.mp4", Config(), has_audio=False,
                                 origin_sec=0.0, duration_sec=1.0)
    assert ev.completed is False
    tail = ev.stderr_tail.splitlines()
    assert len(tail) == 50 and tail[0] == "err10" and tail[-1] == "err59"
