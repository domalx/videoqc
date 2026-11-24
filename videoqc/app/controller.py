"""扫描调度：线程池 + Qt 信号（SPEC §6.2）。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from videoqc.app.config import Config
from videoqc.app.session import MODE_RANK, Session
from videoqc.core.model import FileResult
from videoqc.core.pipeline import analyze_file


class ScanController(QObject):
    file_started = Signal(str, str)  # path, mode
    file_stage = Signal(str, str)  # path, stage
    file_done = Signal(object)  # FileResult
    scan_finished = Signal(str, dict)  # mode, {"total","done","failed","elapsed"}
    scan_error = Signal(str)

    def __init__(self, cfg: Config, session: Session, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.session = session
        self._executor: ThreadPoolExecutor | None = None
        self._cancel = threading.Event()
        self._running: dict[str, str] = {}
        self._gen = 0
        self._playing = False
        self._lock = threading.Lock()
        self._mode = "fast"
        self._epoch = 0
        self._total = self._done = self._failed = 0
        self._t0 = 0.0

    # ---- 对外 ----
    def start(self, paths: list[str], mode: str) -> None:
        targets = []
        for p in paths:
            cur = self._running.get(p)
            if cur is not None and MODE_RANK.get(cur, 0) >= MODE_RANK.get(mode, 0):
                self.file_stage.emit(p, "skipped")  # 复用已有结果，不重扫
                continue
            targets.append(p)
        if not targets:
            self.scan_finished.emit(mode, {"total": 0, "done": 0, "failed": 0, "elapsed": 0.0})
            return

        # 每轮独立 Event：stop() 后旧任务仍带着已 set 的事件跑完，clear() 不能把它们复活
        self._cancel = threading.Event()
        cancel = self._cancel
        self._epoch += 1
        epoch = self._epoch
        self._total, self._done, self._failed = len(targets), 0, 0
        self._t0 = time.monotonic()
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._workers(), thread_name_prefix="scan"
            )
        for p in targets:
            with self._lock:
                self._gen += 1
                gen = self._gen
            self._running[p] = mode
            self._executor.submit(self._run_one, p, mode, gen, cancel, epoch)

    def stop(self) -> None:
        self._cancel.set()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        with self._lock:
            self._epoch += 1  # 让在飞的旧任务作废：停止后不能再冒出一轮“扫描完成”
            self._running.clear()  # 旧任务已在跑且会被 kill；不清会让 is_running() 永远为真

    def is_running(self) -> bool:
        return bool(self._running)

    def set_playing(self, playing: bool) -> None:
        """播放时把并发降到 1（不重建线程池，新任务自然排队）。"""
        self._playing = playing

    def _workers(self) -> int:
        return 1 if self._playing else self.cfg.jobs

    # ---- worker ----
    def _run_one(self, path: str, mode: str, gen: int, cancel: threading.Event, epoch: int) -> None:
        self.file_started.emit(path, mode)
        try:

            def _stage(stage: str, p: str = path) -> None:
                self.file_stage.emit(p, stage)

            prev = self.session.get(path)
            r = analyze_file(
                path,
                self.cfg,
                mode,
                gen,
                cancel,
                on_stage=_stage,
                warm_streams=prev.streams if prev is not None else None,
            )
        except Exception as e:  # pipeline 内部已兜底，这里只防漏
            r = FileResult(
                path=path,
                mode=mode,
                status="failed",
                generation=gen,
                error=f"{type(e).__name__}: {e}",
            )
        if self.session.put(r):
            self.file_done.emit(r)
        self._finish(r, mode, epoch)

    def _finish(self, r: FileResult, mode: str, epoch: int) -> None:
        with self._lock:
            if epoch != self._epoch:  # 上一轮遗留的迟到结果：不计入本轮的 total/done
                return
            self._running.pop(r.path, None)
            self._done += 1
            if r.status != "done":
                self._failed += 1
            left = len(self._running)
        if left == 0:
            self.scan_finished.emit(
                mode,
                {
                    "total": self._total,
                    "done": self._done,
                    "failed": self._failed,
                    "elapsed": round(time.monotonic() - self._t0, 1),
                },
            )
