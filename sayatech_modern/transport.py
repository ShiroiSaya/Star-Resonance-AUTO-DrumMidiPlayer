from __future__ import annotations

import time
from enum import Enum
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .backend import BackendPlaybackHandle, PlaybackBackend
from .crash_logging import append_runtime_log, write_crash_log
from .models import MidiAnalysisResult


class TransportState(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class TransportController(QObject):
    position_changed = Signal(float, float)
    state_changed = Signal(str)
    analysis_changed = Signal(object)
    log = Signal(str)

    def __init__(self, backend: Optional[PlaybackBackend] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.backend = backend or PlaybackBackend()
        self.state = TransportState.STOPPED
        self.analysis: Optional[MidiAnalysisResult] = None
        self.handle: Optional[BackendPlaybackHandle] = None
        self.position_sec = 0.0
        self.duration_sec = 0.0
        self._play_started_at: Optional[float] = None
        self._position_anchor = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self._last_play_request_at = 0.0
        self._play_debounce_sec = 0.25

    def set_backend(self, backend: PlaybackBackend) -> None:
        if self.backend is backend:
            return
        if self.state == TransportState.PLAYING:
            self.stop()
        self.backend = backend
        if self.analysis:
            self.handle = self.backend.prepare(self.analysis)
            self.position_sec = 0.0
            self.duration_sec = self.analysis.duration_sec
            self.position_changed.emit(self.position_sec, self.duration_sec)

    def set_analysis(self, analysis: MidiAnalysisResult) -> None:
        self.analysis = analysis
        self.handle = self.backend.prepare(analysis)
        self.position_sec = 0.0
        self.duration_sec = analysis.duration_sec
        self.state = TransportState.STOPPED
        self.analysis_changed.emit(analysis)
        self.position_changed.emit(self.position_sec, self.duration_sec)
        self.state_changed.emit(self.state.value)
        self.log.emit(f"已装载时间轴：{analysis.duration_sec:.2f}s")

    def play(self) -> None:
        if not self.analysis or not self.handle:
            self.log.emit("未装载 MIDI，无法开始。")
            return
        now = time.monotonic()
        if self.state == TransportState.PLAYING and getattr(self.handle, "is_running", False):
            self.log.emit("已在播放中，忽略重复开始。")
            return
        if now - self._last_play_request_at < self._play_debounce_sec:
            self.log.emit("检测到过快的重复开始请求，已忽略。")
            return
        self._last_play_request_at = now
        try:
            self.backend.start(self.handle, self.position_sec)
            self._position_anchor = self.position_sec
            self._play_started_at = time.perf_counter()
            self.state = TransportState.PLAYING
            self.timer.start()
            self.state_changed.emit(self.state.value)
            msg = f"开始 / 继续播放：{self.position_sec:.2f}s"
            append_runtime_log(msg)
            self.log.emit(msg)
        except BaseException as exc:
            path = write_crash_log('Transport play failed', exc, {'position_sec': self.position_sec, 'duration_sec': self.duration_sec, 'state': self.state.value})
            self.timer.stop()
            self._last_play_request_at = 0.0
            self.state = TransportState.PAUSED if self.position_sec > 0 else TransportState.STOPPED
            self.state_changed.emit(self.state.value)
            self.log.emit(f"开始播放失败，崩溃日志：{path}")
            append_runtime_log(f"Transport play failed; staying alive. log={path}")
            return

    def pause(self) -> None:
        if self.state != TransportState.PLAYING or not self.handle:
            return
        self._update_position_from_clock()
        try:
            self.backend.pause(self.handle, self.position_sec)
        except BaseException as exc:
            path = write_crash_log('Transport pause failed', exc, {'position_sec': self.position_sec, 'duration_sec': self.duration_sec, 'state': self.state.value})
            self.timer.stop()
            self.state = TransportState.PAUSED
            self.state_changed.emit(self.state.value)
            self.log.emit(f"暂停失败，崩溃日志：{path}")
            append_runtime_log(f"Transport pause failed; staying alive. log={path}")
            return
        self.state = TransportState.PAUSED
        self.timer.stop()
        self._last_play_request_at = 0.0
        self.state_changed.emit(self.state.value)
        self.log.emit(f"已暂停：{self.position_sec:.2f}s")

    def stop(self) -> None:
        try:
            if self.handle:
                self.backend.stop(self.handle)
        except BaseException as exc:
            path = write_crash_log('Transport stop failed', exc, {'position_sec': self.position_sec, 'duration_sec': self.duration_sec, 'state': self.state.value})
            self.timer.stop()
            self.state = TransportState.STOPPED
            self.state_changed.emit(self.state.value)
            self.log.emit(f"停止失败，崩溃日志：{path}")
            append_runtime_log(f"Transport stop failed; staying alive. log={path}")
            return
        self.position_sec = 0.0
        self._play_started_at = None
        self._last_play_request_at = 0.0
        self._position_anchor = 0.0
        self.state = TransportState.STOPPED
        self.timer.stop()
        self.position_changed.emit(self.position_sec, self.duration_sec)
        self.state_changed.emit(self.state.value)
        self.log.emit("已停止并回到起点。")

    def seek(self, position_sec: float) -> None:
        target = max(0.0, min(position_sec, self.duration_sec))
        self.position_sec = target
        try:
            if self.handle:
                self.backend.seek(self.handle, target)
        except BaseException as exc:
            path = write_crash_log('Transport seek failed', exc, {'position_sec': self.position_sec, 'duration_sec': self.duration_sec, 'state': self.state.value})
            self.log.emit(f"跳转失败，崩溃日志：{path}")
            append_runtime_log(f"Transport seek failed; staying alive. log={path}")
            return
        if self.state == TransportState.PLAYING:
            self._position_anchor = self.position_sec
            self._play_started_at = time.perf_counter()
            self._last_play_request_at = time.monotonic()
        self.position_changed.emit(self.position_sec, self.duration_sec)
        self.log.emit(f"已跳转到：{self.position_sec:.2f}s")

    def toggle_play_pause(self) -> None:
        if self.state == TransportState.PLAYING:
            self.pause()
        else:
            self.play()

    def _update_position_from_clock(self) -> None:
        if self.state != TransportState.PLAYING or self._play_started_at is None:
            return
        elapsed = time.perf_counter() - self._play_started_at
        self.position_sec = min(self.duration_sec, self._position_anchor + elapsed)

    def _tick(self) -> None:
        if self.state != TransportState.PLAYING:
            return
        self._update_position_from_clock()
        self.position_changed.emit(self.position_sec, self.duration_sec)
        if self.position_sec >= self.duration_sec:
            self.stop()
