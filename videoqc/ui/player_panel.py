"""播放器：QMediaPlayer + 控制条（SPEC §7.3）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def _clock(ms: int) -> str:
    """播放器时间用整秒：毫秒在这里没有意义，只让标签抖。"""
    s = max(0, ms) // 1000
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


SPEEDS = [0.5, 1.0, 1.5, 2.0]


class PlayerPanel(QWidget):
    error = Signal(str)
    playing_changed = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._origin = 0.0
        self._fps = 25.0
        self._frame_ms = 40  # 未探测到帧率前的默认步长（1/25s）
        self._has_source = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.player.setVideoOutput(self.video)
        self.video.setMinimumHeight(240)

        self.error_label = QLabel(self.video)
        self.error_label.setAlignment(Qt.AlignCenter)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#C0392B; background:#00000000;")
        self.error_label.hide()

        self.info_label = QLabel(self)  # 当前文件的基础信息（码流/分辨率/时长/体积）
        self.info_label.setStyleSheet("color:#666; padding:1px 2px;")
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info_label.hide()

        self.btn_play = QToolButton(self)
        self.btn_play.setText("▶")
        self.btn_play.setToolTip("播放/暂停 (Space)")
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 0)
        self.time_label = QLabel("00:00.000 / 00:00.000", self)
        self.btn_prev = QToolButton(self)
        self.btn_prev.setText("◀|")
        self.btn_prev.setToolTip("上一帧 (Shift+←)")
        self.btn_next = QToolButton(self)
        self.btn_next.setText("|▶")
        self.btn_next.setToolTip("下一帧 (Shift+→)")
        self.speed = QComboBox(self)
        self.speed.addItems([f"{s:g}×" for s in SPEEDS])
        self.speed.setCurrentIndex(1)
        self.volume = QSlider(Qt.Horizontal, self)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)

        bar = QHBoxLayout()
        for w in (self.btn_play, self.btn_prev, self.btn_next):
            bar.addWidget(w)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time_label)
        bar.addWidget(self.speed)
        bar.addWidget(QLabel("音量"))
        bar.addWidget(self.volume)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.video, 1)
        root.addWidget(self.info_label)
        root.addLayout(bar)

        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_prev.clicked.connect(lambda: self.step(-1))
        self.btn_next.clicked.connect(lambda: self.step(1))
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))
        self.speed.currentIndexChanged.connect(lambda i: self.player.setPlaybackRate(SPEEDS[i]))
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda ms: self.slider.setRange(0, ms))
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(
            lambda _e, msg: self._fail(msg or self.player.errorString())
        )
        self._set_enabled(False)

    # ---- 对外 ----
    def set_info(self, text: str) -> None:
        """播放器下方的文件信息行；空串即隐藏（未打开文件/探测失败时不留旧值）。"""
        self.info_label.setText(text)
        self.info_label.setVisible(bool(text))

    def load(self, path: str, origin_sec: float = 0.0) -> None:
        self._origin = origin_sec
        self.set_info("")  # 换文件先清空，探测结果回来再填
        self.error_label.hide()
        self.video.show()
        self._has_source = True
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.player.pause()
        self._set_enabled(True)

    def set_has_audio(self, has_audio: bool) -> None:
        self.volume.setEnabled(has_audio)

    def seek_to(self, sec: float, autoplay: bool = True) -> None:
        if not self._has_source:
            return
        self.player.setPosition(max(0, int((sec + self._origin) * 1000)))
        if autoplay:
            self.player.play()
        else:
            self.player.pause()

    def set_frame_step(self, ms: int) -> None:
        self._frame_ms = max(1, ms)

    def fps(self, fps: float | None) -> None:
        self._fps = fps if fps and fps > 0 else 25.0
        self.set_frame_step(round(1000 / self._fps))

    def toggle_play(self) -> None:
        if not self._has_source:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def step(self, direction: int) -> None:
        if not self._has_source:
            return
        self.player.pause()
        self.player.setPosition(max(0, self.player.position() + direction * self._frame_ms))

    # ---- 内部 ----
    def _set_enabled(self, on: bool) -> None:
        for w in (
            self.btn_play,
            self.btn_prev,
            self.btn_next,
            self.slider,
            self.speed,
            self.volume,
        ):
            w.setEnabled(on)

    def _fail(self, msg: str) -> None:
        self.video.hide()
        self.error_label.setText(f"无法播放该文件\n{msg}")
        self.error_label.resize(self.size())
        self.error_label.show()
        self._set_enabled(False)
        self.error.emit(msg)

    def _on_position(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.time_label.setText(f"{_clock(ms)} / {_clock(self.player.duration())}")

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.btn_play.setText("⏸" if playing else "▶")
        self.playing_changed.emit(playing)

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self.btn_play.setText("▶")
            self.player.setPosition(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.error_label.resize(self.video.size())
