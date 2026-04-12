from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QRectF, QPoint, QSize, Signal, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QKeySequenceEdit,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .backend import ModernDrumBackend, ModernPianoBackend
from .cache_store import cache_size_bytes, clear_cache as clear_disk_cache, format_size as format_cache_size
from .app_paths import ensure_user_config_file, user_data_root
from .gpu_accel import resolve_compute_backend
from .crash_logging import append_runtime_log, runtime_log_path, set_runtime_debug_mode
from .ensemble import beijing_now, sync_beijing_clock
from .config_io import DEFAULT_TEMPLATE, SUPPORTED_FIELDS, ensure_config_file, load_config, midi_to_note_name as cfg_midi_to_note_name, note_name_to_midi, save_config
from .midi_analysis import analyze_midi, filter_analysis, midi_to_note_name
from .models import MidiAnalysisResult, NoteSpan, TrackInfo
from .theme import build_stylesheet
from .transport import TransportController
from .tuner import preview_lines, suggest_config
from .system_utils import is_admin
from .ui_settings import UISettings, load_ui_settings, save_ui_settings
from .widgets import AnimatedButton, AnimatedSwitch, FadeDialog, FadeStackedWidget

QPushButton = AnimatedButton
QCheckBox = AnimatedSwitch


def _ui_dark_mode() -> bool:
    app = QApplication.instance()
    return bool(app.property("uiDarkMode")) if app else False


def _ui_performance_mode() -> bool:
    app = QApplication.instance()
    return bool(app.property("uiPerformanceMode")) if app else False


def _qt_key_to_vk(key: int) -> Optional[int]:
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return ord(chr(key))
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return ord(chr(key))
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return 0x70 + (int(key) - int(Qt.Key.Key_F1))
    mapping = {
        int(Qt.Key.Key_Space): 0x20,
        int(Qt.Key.Key_Tab): 0x09,
        int(Qt.Key.Key_Backtab): 0x09,
        int(Qt.Key.Key_Escape): 0x1B,
        int(Qt.Key.Key_Backspace): 0x08,
        int(Qt.Key.Key_Return): 0x0D,
        int(Qt.Key.Key_Enter): 0x0D,
        int(Qt.Key.Key_Insert): 0x2D,
        int(Qt.Key.Key_Delete): 0x2E,
        int(Qt.Key.Key_Home): 0x24,
        int(Qt.Key.Key_End): 0x23,
        int(Qt.Key.Key_PageUp): 0x21,
        int(Qt.Key.Key_PageDown): 0x22,
        int(Qt.Key.Key_Left): 0x25,
        int(Qt.Key.Key_Up): 0x26,
        int(Qt.Key.Key_Right): 0x27,
        int(Qt.Key.Key_Down): 0x28,
        int(Qt.Key.Key_Comma): 0xBC,
        int(Qt.Key.Key_Period): 0xBE,
        int(Qt.Key.Key_Slash): 0xBF,
        int(Qt.Key.Key_Semicolon): 0xBA,
        int(Qt.Key.Key_Apostrophe): 0xDE,
        int(Qt.Key.Key_BracketLeft): 0xDB,
        int(Qt.Key.Key_BracketRight): 0xDD,
        int(Qt.Key.Key_Minus): 0xBD,
        int(Qt.Key.Key_Equal): 0xBB,
        int(Qt.Key.Key_Backslash): 0xDC,
        int(Qt.Key.Key_QuoteLeft): 0xC0,
    }
    return mapping.get(int(key))


def _parse_hotkey_binding(sequence_text: str) -> Optional[tuple[int, tuple[int, ...]]]:
    text = (sequence_text or '').strip()
    if not text:
        return None
    seq = QKeySequence.fromString(text, QKeySequence.PortableText)
    if seq.isEmpty():
        seq = QKeySequence.fromString(text, QKeySequence.NativeText)
    if seq.isEmpty():
        return None
    first = seq[0]
    if hasattr(first, 'key'):
        key = int(first.key())
        mods = first.keyboardModifiers()
    else:
        combo = int(first)
        modifier_mask = int(
            Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
            | Qt.KeyboardModifier.KeypadModifier
            | Qt.KeyboardModifier.GroupSwitchModifier
        )
        key = combo & ~modifier_mask
        mods = Qt.KeyboardModifiers(combo & modifier_mask)
    vk_code = _qt_key_to_vk(key)
    if vk_code is None:
        return None
    modifier_vks: List[int] = []
    if mods & Qt.KeyboardModifier.ControlModifier:
        modifier_vks.extend([0xA2, 0xA3])
    if mods & Qt.KeyboardModifier.ShiftModifier:
        modifier_vks.extend([0xA0, 0xA1])
    if mods & Qt.KeyboardModifier.AltModifier:
        modifier_vks.extend([0xA4, 0xA5])
    if mods & Qt.KeyboardModifier.MetaModifier:
        modifier_vks.extend([0x5B, 0x5C])
    return vk_code, tuple(modifier_vks)


def _pretty_hotkey(sequence_text: str) -> str:
    return (sequence_text or '').strip().upper() or '未设置'



ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
BACKGROUND_IMAGE_PATH = os.path.join(ASSETS_DIR, "background.png")
APP_ICON_PATH = os.path.join(ASSETS_DIR, "app.ico")
SPLASH_IMAGE_PATH = os.path.join(ASSETS_DIR, "splash.png")


class BackgroundSurface(QWidget):
    def __init__(self, image_path: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._source = QPixmap(image_path) if os.path.exists(image_path) else QPixmap()
        self._scaled = QPixmap()
        self._scaled_size = QSize()
        self.setAttribute(Qt.WA_StyledBackground, True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scaled_size = QSize()

    def _ensure_scaled(self) -> None:
        if self._source.isNull():
            self._scaled = QPixmap()
            self._scaled_size = self.size()
            return
        if self._scaled_size == self.size() and not self._scaled.isNull():
            return
        src = self._source
        target = self.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        scaled = src.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width() - target.width()) // 2)
        y = max(0, (scaled.height() - target.height()) // 2)
        self._scaled = scaled.copy(x, y, target.width(), target.height())
        self._scaled_size = target

    def fragment_for_widget(self, widget: QWidget, blur_strength: int = 58) -> QPixmap:
        self._ensure_scaled()
        if self._scaled.isNull() or widget is None or widget.width() <= 0 or widget.height() <= 0:
            return QPixmap()
        try:
            if widget.window() is not self.window():
                return QPixmap()
            top_left_global = widget.mapToGlobal(QPoint(0, 0))
            top_left = self.mapFromGlobal(top_left_global)
        except Exception:
            return QPixmap()
        rect = QRectF(float(top_left.x()), float(top_left.y()), float(widget.width()), float(widget.height())).toRect()
        rect = rect.intersected(self.rect())
        if rect.isEmpty():
            return QPixmap()
        fragment = self._scaled.copy(rect)
        strength = max(0, min(100, int(blur_strength)))
        if strength <= 0:
            return fragment
        down = max(1, int(7 - strength / 18))
        small_w = max(4, rect.width() // (down + 1))
        small_h = max(4, rect.height() // (down + 1))
        tiny = fragment.scaled(small_w, small_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return tiny.scaled(rect.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if _ui_dark_mode() or self._source.isNull():
            painter.fillRect(self.rect(), QColor("#03060b") if _ui_dark_mode() else QColor("#f6f3f5"))
            return
        if _ui_performance_mode():
            painter.fillRect(self.rect(), QColor("#f6f3f5"))
            return
        self._ensure_scaled()
        if self._scaled.isNull():
            painter.fillRect(self.rect(), QColor("#f6f3f5"))
            return
        painter.drawPixmap(self.rect(), self._scaled)
        painter.fillRect(self.rect(), QColor(255, 248, 251, 78))


class GlassFrame(QFrame):
    def __init__(self, object_name: str, radius: int = 18, sidebar: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName(object_name)
        self._radius = radius
        self._sidebar = sidebar
        self.setAttribute(Qt.WA_StyledBackground, False)

    def _paint_surface(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), self._radius, self._radius)
        painter.setClipPath(path)
        dark = _ui_dark_mode()
        perf = _ui_performance_mode()
        win = self.window()
        app = QApplication.instance()
        backdrop_enabled = bool(app.property("uiBackdropEnabled")) if app else (not dark and not perf)
        blur_strength = int(app.property("uiGlassBlur") or 0) if app else int(getattr(getattr(win, 'ui_settings', None), 'glass_blur', 58) or 0)
        if not dark and backdrop_enabled and hasattr(win, 'background_surface') and getattr(win, 'background_surface', None) is not None:
            frag = win.background_surface.fragment_for_widget(self, blur_strength)
            if not frag.isNull():
                painter.drawPixmap(self.rect(), frag)
        overlay = QColor(7, 12, 18, 220) if dark else (QColor(255, 255, 255, 236) if not backdrop_enabled else QColor(255, 255, 255, 150 + int(min(90, blur_strength * 0.5))))
        painter.fillPath(path, overlay)
        painter.setClipping(False)
        border = QColor("#1f2b3b") if dark else QColor("#e6d8e0")
        painter.setPen(QPen(border, 1))
        painter.drawPath(path)

    def paintEvent(self, event):
        painter = QPainter(self)
        self._paint_surface(painter)
        painter.end()
        super().paintEvent(event)


class Card(GlassFrame):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("Card", 18, False, parent)


class Sidebar(GlassFrame):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__("Sidebar", 20, True, parent)


class LightOverviewWidget(QWidget):
    def __init__(self, mode: str = "piano", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.mode = "drum" if mode == "drum" else "piano"
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self.min_note = 48
        self.max_note = 84
        self.bars: List[float] = []
        self.active_sections: List[bool] = []
        self.summary_text = "简览"
        self._static_cache = QPixmap()
        self._cache_key = None
        self.setMinimumHeight(156)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _invalidate_cache(self) -> None:
        self._cache_key = None
        self._static_cache = QPixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_cache()

    def set_analysis(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is None:
            self.duration_sec = 0.0
            self.position_sec = 0.0
            self.min_note = 48
            self.max_note = 84
            self.bars = []
            self.active_sections = []
            self.summary_text = "未载入 MIDI"
        else:
            self.duration_sec = float(getattr(analysis, 'duration_sec', 0.0) or 0.0)
            timeline = getattr(analysis, 'timeline', None)
            self.bars = list(getattr(timeline, 'bars', []) or [])
            self.active_sections = list(getattr(timeline, 'active_sections', []) or [])
            self.min_note = int(getattr(analysis, 'min_note', 48) or 48)
            self.max_note = int(getattr(analysis, 'max_note', 84) or 84)
            if self.mode == 'piano':
                self.summary_text = f"{midi_to_note_name(self.min_note)} ~ {midi_to_note_name(self.max_note)}"
            else:
                self.summary_text = f"鼓总览 · {len(getattr(analysis, 'notes', []) or [])} 个击打"
        self._invalidate_cache()
        self.update()

    def set_position(self, position_sec: float) -> None:
        self.position_sec = max(0.0, min(float(position_sec or 0.0), self.duration_sec if self.duration_sec > 0 else 0.0))
        self.update()

    def _ensure_static_cache(self) -> None:
        cache_key = (self.size().width(), self.size().height(), _ui_dark_mode(), self.mode, round(self.duration_sec, 4), self.min_note, self.max_note, tuple(round(v, 3) for v in self.bars[:256]), tuple(self.active_sections[:256]), self.summary_text)
        if self._cache_key == cache_key and not self._static_cache.isNull():
            return
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = _ui_dark_mode()
        bg = QColor("#0f172a") if dark else QColor("#f8fafc")
        grid = QColor("#334155") if dark else QColor("#e5e7eb")
        text_color = QColor("#cbd5e1") if dark else QColor("#64748b")
        active_color = QColor("#38bdf8") if dark else QColor("#60a5fa")
        idle_color = QColor("#475569") if dark else QColor("#cbd5e1")
        painter.fillRect(pm.rect(), bg)
        w = max(1, self.width())
        h = max(1, self.height())
        painter.setPen(QPen(grid, 1))
        for i in range(1, 8):
            y = int(h * i / 8)
            painter.drawLine(0, y, w, y)
        for i in range(1, 10):
            x = int(w * i / 10)
            painter.drawLine(x, 0, x, h)
        if self.mode == 'piano':
            painter.setPen(text_color)
            painter.drawText(8, 16, midi_to_note_name(self.max_note))
            painter.drawText(8, h - 8, midi_to_note_name(self.min_note))
        if not self.bars:
            painter.setPen(text_color)
            painter.drawText(self.rect(), Qt.AlignCenter, "简览：只显示有声 / 无声分布")
        else:
            usable_h = max(16, h - 30)
            count = max(1, len(self.bars))
            bin_w = max(2.0, w / count)
            baseline = h - 10
            for idx, value in enumerate(self.bars):
                x = idx * bin_w
                bar_h = max(2.0, min(float(usable_h), float(usable_h) * float(value)))
                is_active = True
                if self.active_sections and idx < len(self.active_sections):
                    is_active = bool(self.active_sections[idx])
                color = active_color if is_active else idle_color
                alpha = 90 + int(130 * float(max(0.0, min(1.0, value))))
                color.setAlpha(max(40, min(255, alpha)))
                painter.fillRect(QRectF(x, baseline - bar_h, max(1.4, bin_w - 1.0), bar_h), color)
            painter.setPen(text_color)
            painter.drawText(8, h - 8, self.summary_text)
            if self.duration_sec > 0:
                painter.drawText(w - 78, h - 8, f"{self.duration_sec:.1f}s")
        painter.end()
        self._static_cache = pm
        self._cache_key = cache_key

    def paintEvent(self, _event):
        self._ensure_static_cache()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static_cache)
        if self.duration_sec > 0:
            x_pos = (self.position_sec / max(0.001, self.duration_sec)) * self.width()
            painter.setPen(QPen(QColor("#e5e7eb") if _ui_dark_mode() else QColor("#111827"), 2))
            painter.drawLine(int(x_pos), 0, int(x_pos), self.height())


class LitePianoPreviewWidget(QWidget):
    BIN_COUNT = 160

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.notes: List[NoteSpan] = []
        self.duration_sec = 0.0
        self.min_note = 48
        self.max_note = 84
        self.view_min_note = 48
        self.view_max_note = 84
        self.position_sec = 0.0
        self._range_low: List[int] = []
        self._range_high: List[int] = []
        self._density: List[float] = []
        self._placeholder_text = "选择 MIDI 后，这里显示音域简览"
        self._static_cache = QPixmap()
        self._cache_key = None
        self._analysis_ref: Optional[MidiAnalysisResult] = None
        self._last_position_px = -10**9
        self.setMinimumHeight(176)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _invalidate_cache(self) -> None:
        self._cache_key = None
        self._static_cache = QPixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_cache()

    def show_placeholder(self, text: str) -> None:
        self.notes = []
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self._range_low = []
        self._range_high = []
        self._density = []
        self._analysis_ref = None
        self._last_position_px = -10**9
        self._placeholder_text = text or "正在刷新简览…"
        self._invalidate_cache()
        self.update()

    def _position_pixel(self, position_sec: float) -> int:
        if self.duration_sec <= 0 or self.width() <= 0:
            return -1
        return int((max(0.0, min(position_sec, self.duration_sec)) / max(0.001, self.duration_sec)) * self.width())

    def set_analysis(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is self._analysis_ref:
            self._last_position_px = -10**9
            self.set_position(0.0)
            return
        self._analysis_ref = analysis
        self.notes = analysis.notes if analysis else []
        self.duration_sec = analysis.duration_sec if analysis else 0.0
        self.min_note = analysis.min_note if analysis else 48
        self.max_note = analysis.max_note if analysis else 84
        self._placeholder_text = "选择 MIDI 后，这里显示音域简览"
        if self.notes:
            midi_notes = sorted(note.midi_note for note in self.notes)
            n = len(midi_notes)
            lo = midi_notes[min(n - 1, max(0, int(n * 0.10)))]
            hi = midi_notes[min(n - 1, max(0, int(n * 0.90)))]
            pad = max(1, min(2, int((hi - lo) / 12) + 1))
            self.view_min_note = max(self.min_note, lo - pad)
            self.view_max_note = min(self.max_note, hi + pad)
            if self.view_max_note - self.view_min_note < 12:
                mid = (self.view_max_note + self.view_min_note) / 2.0
                self.view_min_note = int(mid - 6)
                self.view_max_note = int(mid + 6)
            max_span = 18
            if self.view_max_note - self.view_min_note > max_span:
                mid = (self.view_max_note + self.view_min_note) / 2.0
                self.view_min_note = int(mid - max_span / 2)
                self.view_max_note = int(mid + max_span / 2)
            self.view_min_note = max(self.min_note, self.view_min_note)
            self.view_max_note = min(self.max_note, self.view_max_note)
        else:
            self.view_min_note = self.min_note
            self.view_max_note = self.max_note
        self.position_sec = 0.0
        self._rebuild_preview_bins()
        self._invalidate_cache()
        self._last_position_px = -10**9
        self.update()

    def set_position(self, position_sec: float) -> None:
        pixel = self._position_pixel(position_sec)
        if pixel == self._last_position_px and abs(position_sec - self.position_sec) < 0.02:
            return
        self.position_sec = position_sec
        self._last_position_px = pixel
        self.update()

    def _rebuild_preview_bins(self) -> None:
        self._range_low = [-1] * self.BIN_COUNT
        self._range_high = [-1] * self.BIN_COUNT
        self._density = [0.0] * self.BIN_COUNT
        if not self.notes or self.duration_sec <= 0:
            return
        denom = max(0.001, self.duration_sec)
        for note in self.notes:
            start_idx = min(self.BIN_COUNT - 1, max(0, int((note.start_sec / denom) * (self.BIN_COUNT - 1))))
            span_bins = min(4, max(1, int(((note.end_sec - note.start_sec) / denom) * self.BIN_COUNT) + 1))
            density_value = max(0.10, note.velocity / 127.0)
            for idx in range(start_idx, min(self.BIN_COUNT, start_idx + span_bins)):
                low = self._range_low[idx]
                high = self._range_high[idx]
                pitch = int(note.midi_note)
                self._range_low[idx] = pitch if low < 0 else min(low, pitch)
                self._range_high[idx] = pitch if high < 0 else max(high, pitch)
                self._density[idx] += density_value
        peak = max(self._density, default=0.0) or 1.0
        self._density = [min(1.0, v / peak) for v in self._density]

    def _ensure_static_cache(self) -> None:
        cache_key = (
            self.size().width(), self.size().height(), _ui_dark_mode(), round(self.duration_sec, 4),
            self.view_min_note, self.view_max_note, tuple(self._range_low), tuple(self._range_high),
            tuple(round(v, 3) for v in self._density), self._placeholder_text,
        )
        if self._cache_key == cache_key and not self._static_cache.isNull():
            return
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = _ui_dark_mode()
        bg = QColor("#0f172a") if dark else QColor("#f8fafc")
        grid = QColor("#334155") if dark else QColor("#e5e7eb")
        text_color = QColor("#cbd5e1") if dark else QColor("#64748b")
        accent = QColor("#60a5fa") if dark else QColor("#3b82f6")
        accent_soft = QColor("#a78bfa") if dark else QColor("#8b5cf6")
        painter.fillRect(pm.rect(), bg)
        if self.width() <= 0 or self.height() <= 0:
            painter.end()
            self._static_cache = pm
            self._cache_key = cache_key
            return
        painter.setPen(QPen(grid, 1))
        for i in range(1, 8):
            y = int(self.height() * i / 8)
            painter.drawLine(0, y, self.width(), y)
        for i in range(1, 10):
            x = int(self.width() * i / 10)
            painter.drawLine(x, 0, x, self.height())
        if not any(v >= 0 for v in self._range_low) or self.duration_sec <= 0:
            painter.setPen(text_color)
            painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder_text)
        else:
            usable_h = max(12, self.height() - 24)
            note_span = max(1, self.view_max_note - self.view_min_note)
            bin_w = max(2.0, self.width() / self.BIN_COUNT)
            density_y = self.height() - 10
            for idx, low in enumerate(self._range_low):
                high = self._range_high[idx]
                if low < 0 or high < 0:
                    continue
                x = idx * bin_w
                top_y = ((self.view_max_note - high) / note_span) * (usable_h - 6) + 4
                bottom_y = ((self.view_max_note - low) / note_span) * (usable_h - 6) + 8
                h = max(3.0, bottom_y - top_y)
                alpha = 110 + int(120 * self._density[idx])
                color = QColor(accent if idx % 2 == 0 else accent_soft)
                color.setAlpha(max(40, min(255, alpha)))
                painter.fillRect(QRectF(x, top_y, max(1.6, bin_w - 1.2), h), color)
                density_h = max(2.0, 8.0 * self._density[idx])
                painter.fillRect(QRectF(x, density_y - density_h, max(1.6, bin_w - 1.2), density_h), QColor(text_color.red(), text_color.green(), text_color.blue(), 160))
            painter.setPen(text_color)
            painter.drawText(8, 16, midi_to_note_name(self.view_max_note))
            painter.drawText(8, self.height() - 8, midi_to_note_name(self.view_min_note))
            painter.drawText(self.width() - 78, self.height() - 8, f"{self.duration_sec:.1f}s")
            painter.drawText(8, self.height() - 20, "音域简览")
        painter.end()
        self._static_cache = pm
        self._cache_key = cache_key

    def paintEvent(self, _event):
        self._ensure_static_cache()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static_cache)
        if self.duration_sec > 0:
            x_pos = (self.position_sec / max(0.001, self.duration_sec)) * self.width()
            painter.setPen(QPen(QColor("#e5e7eb") if _ui_dark_mode() else QColor("#111827"), 2))
            painter.drawLine(int(x_pos), 0, int(x_pos), self.height())


class LiteDrumPreviewWidget(QWidget):
    KEY_ROWS = [
        ("Q", "Snare Drum"), ("W", "Mid Tom"), ("E", "High Tom"),
        ("R", "Crash Cymbal 1"), ("T", "Hi-Hat"), ("Y", "Crash Cymbal 2"),
        ("S", "Pedal Hi-Hat"), ("F", "Bass Drum"), ("H", "Floor Tom"),
    ]
    KEY_INDEX = {k: i for i, (k, _name) in enumerate(KEY_ROWS)}
    BIN_COUNT = 160

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.notes: List[NoteSpan] = []
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self._row_bins: List[List[float]] = []
        self._placeholder_text = "这里显示鼓点简览"
        self._static_cache = QPixmap()
        self._cache_key = None
        self._analysis_ref: Optional[MidiAnalysisResult] = None
        self._last_position_px = -10**9
        self.setMinimumHeight(164)
        self.setMaximumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _invalidate_cache(self) -> None:
        self._cache_key = None
        self._static_cache = QPixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_cache()

    def show_placeholder(self, text: str) -> None:
        self.notes = []
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self._row_bins = []
        self._analysis_ref = None
        self._last_position_px = -10**9
        self._placeholder_text = text or "正在刷新鼓点简览…"
        self._invalidate_cache()
        self.update()

    def _position_pixel(self, position_sec: float) -> int:
        if self.duration_sec <= 0 or self.width() <= 0:
            return -1
        label_w = 140
        usable_w = max(10, self.width() - label_w - 10)
        return int(label_w + (max(0.0, min(position_sec, self.duration_sec)) / max(0.001, self.duration_sec)) * usable_w)

    def set_analysis(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is self._analysis_ref:
            self._last_position_px = -10**9
            self.set_position(0.0)
            return
        self._analysis_ref = analysis
        self.notes = analysis.notes if analysis else []
        self.duration_sec = analysis.duration_sec if analysis else 0.0
        self.position_sec = 0.0
        self._placeholder_text = "这里显示鼓点简览"
        self._rebuild_preview_bins()
        self._invalidate_cache()
        self._last_position_px = -10**9
        self.update()

    def set_position(self, position_sec: float) -> None:
        pixel = self._position_pixel(position_sec)
        if pixel == self._last_position_px and abs(position_sec - self.position_sec) < 0.02:
            return
        self.position_sec = position_sec
        self._last_position_px = pixel
        self.update()

    def _rebuild_preview_bins(self) -> None:
        self._row_bins = [[0.0 for _ in range(self.BIN_COUNT)] for _ in range(len(self.KEY_ROWS))]
        if not self.notes or self.duration_sec <= 0:
            return
        denom = max(0.001, self.duration_sec)
        for note in self.notes:
            key = ModernDrumBackend.drum_key_for_midi(note.midi_note)
            if not key:
                continue
            row = self.KEY_INDEX.get(key)
            if row is None:
                continue
            idx = min(self.BIN_COUNT - 1, max(0, int((note.start_sec / denom) * (self.BIN_COUNT - 1))))
            self._row_bins[row][idx] += max(0.15, note.velocity / 127.0)
        peak = max((max(row) for row in self._row_bins), default=0.0) or 1.0
        for row in range(len(self._row_bins)):
            self._row_bins[row] = [min(1.0, value / peak) for value in self._row_bins[row]]

    def _ensure_static_cache(self) -> None:
        cache_key = (self.size().width(), self.size().height(), _ui_dark_mode(), round(self.duration_sec, 4), tuple(tuple(round(v, 3) for v in row) for row in self._row_bins), self._placeholder_text)
        if self._cache_key == cache_key and not self._static_cache.isNull():
            return
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = _ui_dark_mode()
        bg = QColor("#0f172a") if dark else QColor("#f8fafc")
        grid = QColor("#334155") if dark else QColor("#e5e7eb")
        text_color = QColor("#cbd5e1") if dark else QColor("#334155")
        accent = QColor("#22c55e") if dark else QColor("#10b981")
        painter.fillRect(pm.rect(), bg)
        if self.width() <= 0 or self.height() <= 0:
            painter.end()
            self._static_cache = pm
            self._cache_key = cache_key
            return
        label_w = 140
        row_h = max(22, self.height() / len(self.KEY_ROWS))
        painter.setPen(QPen(grid, 1))
        for idx, (key, name) in enumerate(self.KEY_ROWS):
            y = int(idx * row_h)
            painter.drawLine(0, y, self.width(), y)
            painter.setPen(text_color)
            painter.drawText(12, y + int(row_h * 0.65), f"{key}  {name}")
            painter.setPen(QPen(grid, 1))
        painter.drawLine(label_w, 0, label_w, self.height())
        if not self._row_bins or self.duration_sec <= 0:
            painter.setPen(text_color)
            painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder_text)
        else:
            usable_w = max(10, self.width() - label_w - 10)
            bin_w = max(2.0, usable_w / self.BIN_COUNT)
            for row, values in enumerate(self._row_bins):
                y = row * row_h + row_h * 0.2
                h = row_h * 0.55
                for idx, value in enumerate(values):
                    if value <= 0:
                        continue
                    color = QColor(accent)
                    color.setAlpha(70 + int(160 * value))
                    x = label_w + idx * bin_w
                    painter.fillRect(QRectF(x, y, max(1.6, bin_w - 1.0), h), color)
            painter.setPen(text_color)
            painter.drawText(label_w + 8, self.height() - 8, "鼓点简览")
        painter.end()
        self._static_cache = pm
        self._cache_key = cache_key

    def paintEvent(self, _event):
        self._ensure_static_cache()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static_cache)
        if self.duration_sec > 0:
            label_w = 140
            usable_w = max(10, self.width() - label_w - 10)
            x_pos = label_w + (self.position_sec / max(0.001, self.duration_sec)) * usable_w
            painter.setPen(QPen(QColor("#e5e7eb") if _ui_dark_mode() else QColor("#111827"), 2))
            painter.drawLine(int(x_pos), 0, int(x_pos), self.height())


class PianoRollWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.notes: List[NoteSpan] = []
        self.duration_sec = 0.0
        self.min_note = 48
        self.max_note = 84
        self.view_min_note = 48
        self.view_max_note = 84
        self.position_sec = 0.0
        self._static_cache = QPixmap()
        self._cache_key = None
        self._analysis_ref: Optional[MidiAnalysisResult] = None
        self._last_position_px = -10**9
        self.setMinimumHeight(176)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _invalidate_cache(self) -> None:
        self._cache_key = None
        self._static_cache = QPixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_cache()

    def _position_pixel(self, position_sec: float) -> int:
        if self.duration_sec <= 0 or self.width() <= 0:
            return -1
        return int((max(0.0, min(position_sec, self.duration_sec)) / max(0.001, self.duration_sec)) * self.width())

    def set_analysis(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is self._analysis_ref:
            self._last_position_px = -10**9
            self.set_position(0.0)
            return
        self._analysis_ref = analysis
        self.notes = analysis.notes if analysis else []
        self.duration_sec = analysis.duration_sec if analysis else 0.0
        self.min_note = analysis.min_note if analysis else 48
        self.max_note = analysis.max_note if analysis else 84
        if self.notes:
            midi_notes = sorted(note.midi_note for note in self.notes)
            n = len(midi_notes)
            lo = midi_notes[min(n - 1, max(0, int(n * 0.10)))]
            hi = midi_notes[min(n - 1, max(0, int(n * 0.90)))]
            pad = max(1, min(2, int((hi - lo) / 12) + 1))
            self.view_min_note = max(self.min_note, lo - pad)
            self.view_max_note = min(self.max_note, hi + pad)
            if self.view_max_note - self.view_min_note < 12:
                mid = (self.view_max_note + self.view_min_note) / 2.0
                self.view_min_note = int(mid - 6)
                self.view_max_note = int(mid + 6)
            max_span = 18
            if self.view_max_note - self.view_min_note > max_span:
                mid = (self.view_max_note + self.view_min_note) / 2.0
                self.view_min_note = int(mid - max_span / 2)
                self.view_max_note = int(mid + max_span / 2)
            self.view_min_note = max(self.min_note, self.view_min_note)
            self.view_max_note = min(self.max_note, self.view_max_note)
        else:
            self.view_min_note = self.min_note
            self.view_max_note = self.max_note
        self.position_sec = 0.0
        self._invalidate_cache()
        self._last_position_px = -10**9
        self.update()

    def set_position(self, position_sec: float) -> None:
        pixel = self._position_pixel(position_sec)
        if pixel == self._last_position_px and abs(position_sec - self.position_sec) < 0.02:
            return
        self.position_sec = position_sec
        self._last_position_px = pixel
        self.update()

    def _ensure_static_cache(self) -> None:
        cache_key = (self.size().width(), self.size().height(), _ui_dark_mode(), id(self.notes), len(self.notes), round(self.duration_sec, 4), self.view_min_note, self.view_max_note)
        if self._cache_key == cache_key and not self._static_cache.isNull():
            return
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = _ui_dark_mode()
        bg = QColor("#0f172a") if dark else QColor("#f8fafc")
        grid = QColor("#334155") if dark else QColor("#e5e7eb")
        text_color = QColor("#cbd5e1") if dark else QColor("#64748b")
        painter.fillRect(pm.rect(), bg)
        if self.width() <= 0 or self.height() <= 0:
            painter.end()
            self._static_cache = pm
            self._cache_key = cache_key
            return
        painter.setPen(QPen(grid, 1))
        for i in range(1, 8):
            y = int(self.height() * i / 8)
            painter.drawLine(0, y, self.width(), y)
        for i in range(1, 10):
            x = int(self.width() * i / 10)
            painter.drawLine(x, 0, x, self.height())
        if not self.notes or self.duration_sec <= 0:
            painter.setPen(text_color)
            painter.drawText(self.rect(), Qt.AlignCenter, "选择 MIDI 后，这里显示钢琴 / 吉他 / 贝斯卷帘预览")
        else:
            note_span = max(1, self.view_max_note - self.view_min_note)
            palette = [QColor("#66C2A5"), QColor("#8DA0CB"), QColor("#FC8D62"), QColor("#E78AC3")] if not dark else [QColor("#34d399"), QColor("#818cf8"), QColor("#fb923c"), QColor("#f472b6")]
            for idx, note in enumerate(self.notes[:5000]):
                x1 = (note.start_sec / self.duration_sec) * self.width()
                x2 = max(x1 + 2.0, (note.end_sec / self.duration_sec) * self.width())
                y = ((self.view_max_note - note.midi_note) / note_span) * (self.height() - 14) + 4
                painter.fillRect(QRectF(x1, y, x2 - x1, 7), palette[idx % len(palette)])
            painter.setPen(text_color)
            painter.drawText(8, 16, midi_to_note_name(self.view_max_note))
            painter.drawText(8, self.height() - 8, midi_to_note_name(self.view_min_note))
            painter.drawText(self.width() - 56, self.height() - 8, f"{self.duration_sec:.1f}s")
        painter.end()
        self._static_cache = pm
        self._cache_key = cache_key

    def paintEvent(self, _event):
        self._ensure_static_cache()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static_cache)
        if self.duration_sec > 0:
            x_pos = (self.position_sec / max(0.001, self.duration_sec)) * self.width()
            painter.setPen(QPen(QColor("#e5e7eb") if _ui_dark_mode() else QColor("#111827"), 2))
            painter.drawLine(int(x_pos), 0, int(x_pos), self.height())


class WaveformTimelineWidget(QWidget):
    seek_requested = Signal(float)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.bars: List[float] = []
        self.active_sections: List[bool] = []
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self.drag_enabled = True
        self._static_cache = QPixmap()
        self._cache_key = None
        self._analysis_ref: Optional[MidiAnalysisResult] = None
        self._last_position_px = -10**9
        self.setMinimumHeight(96)
        self.setMaximumHeight(128)

    def _invalidate_cache(self) -> None:
        self._cache_key = None
        self._static_cache = QPixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_cache()

    def _position_pixel(self, position_sec: float) -> int:
        if self.duration_sec <= 0 or self.width() <= 0:
            return -1
        return int((max(0.0, min(position_sec, self.duration_sec)) / max(0.001, self.duration_sec)) * self.width())

    def set_analysis(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is self._analysis_ref:
            self._last_position_px = -10**9
            self.set_position(0.0)
            return
        self._analysis_ref = analysis
        if analysis:
            self.bars = analysis.timeline.bars
            self.active_sections = analysis.timeline.active_sections
            self.duration_sec = analysis.duration_sec
        else:
            self.bars = []
            self.active_sections = []
            self.duration_sec = 0.0
        self.position_sec = 0.0
        self._invalidate_cache()
        self._last_position_px = -10**9
        self.update()

    def set_position(self, position_sec: float) -> None:
        pixel = self._position_pixel(position_sec)
        if pixel == self._last_position_px and abs(position_sec - self.position_sec) < 0.02:
            return
        self.position_sec = position_sec
        self._last_position_px = pixel
        self.update()

    def mousePressEvent(self, event):
        if self.drag_enabled and self.duration_sec > 0:
            ratio = max(0.0, min(1.0, event.position().x() / max(1.0, self.width())))
            self.seek_requested.emit(ratio * self.duration_sec)

    def mouseMoveEvent(self, event):
        if self.drag_enabled and event.buttons() & Qt.LeftButton and self.duration_sec > 0:
            ratio = max(0.0, min(1.0, event.position().x() / max(1.0, self.width())))
            self.seek_requested.emit(ratio * self.duration_sec)

    def _ensure_static_cache(self) -> None:
        cache_key = (self.size().width(), self.size().height(), _ui_dark_mode(), len(self.bars), round(self.duration_sec, 4), tuple(self.active_sections[:32]))
        if self._cache_key == cache_key and not self._static_cache.isNull():
            return
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = _ui_dark_mode()
        bg = QColor("#0f172a") if dark else QColor("#f8fafc")
        muted = QColor("#94a3b8") if dark else QColor("#64748b")
        painter.fillRect(pm.rect(), bg)
        if not self.bars:
            painter.setPen(muted)
            painter.drawText(self.rect(), Qt.AlignCenter, "这里显示有声 / 无声时间轴")
        else:
            gap = 2
            count = len(self.bars)
            bar_w = max(3, int((self.width() - gap * (count - 1)) / max(1, count)))
            x = 0
            for idx, value in enumerate(self.bars):
                h = max(8, int(value * (self.height() - 24)))
                y = self.height() - h - 10
                if self.active_sections and idx < len(self.active_sections) and not self.active_sections[idx]:
                    color = QColor("#334155") if dark else QColor("#e5e7eb")
                else:
                    color = QColor("#60a5fa") if dark else QColor("#94a3b8")
                painter.fillRect(x, y, bar_w, h, color)
                x += bar_w + gap
        painter.end()
        self._static_cache = pm
        self._cache_key = cache_key

    def paintEvent(self, _event):
        self._ensure_static_cache()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static_cache)
        if self.bars and self.duration_sec > 0:
            played_ratio = self.position_sec / max(0.001, self.duration_sec)
            x_pos = played_ratio * self.width()
            painter.setPen(QPen(QColor("#e5e7eb") if _ui_dark_mode() else QColor("#111827"), 2))
            painter.drawLine(int(x_pos), 0, int(x_pos), self.height())


class DrumRollWidget(QWidget):
    KEY_ROWS = [
        ("Q", "Snare Drum"), ("W", "Mid Tom"), ("E", "High Tom"),
        ("R", "Crash Cymbal 1"), ("T", "Hi-Hat"), ("Y", "Crash Cymbal 2"),
        ("S", "Pedal Hi-Hat"), ("F", "Bass Drum"), ("H", "Floor Tom"),
    ]
    KEY_INDEX = {k: i for i, (k, _name) in enumerate(KEY_ROWS)}

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.notes: List[NoteSpan] = []
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self._static_cache = QPixmap()
        self._cache_key = None
        self._analysis_ref: Optional[MidiAnalysisResult] = None
        self._last_position_px = -10**9
        self.setMinimumHeight(164)
        self.setMaximumHeight(240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _invalidate_cache(self) -> None:
        self._cache_key = None
        self._static_cache = QPixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._invalidate_cache()

    def _position_pixel(self, position_sec: float) -> int:
        if self.duration_sec <= 0 or self.width() <= 0:
            return -1
        label_w = 140
        usable_w = max(10, self.width() - label_w - 10)
        return int(label_w + (max(0.0, min(position_sec, self.duration_sec)) / max(0.001, self.duration_sec)) * usable_w)

    def set_analysis(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is self._analysis_ref:
            self._last_position_px = -10**9
            self.set_position(0.0)
            return
        self._analysis_ref = analysis
        self.notes = analysis.notes if analysis else []
        self.duration_sec = analysis.duration_sec if analysis else 0.0
        self.position_sec = 0.0
        self._invalidate_cache()
        self._last_position_px = -10**9
        self.update()

    def set_position(self, position_sec: float) -> None:
        pixel = self._position_pixel(position_sec)
        if pixel == self._last_position_px and abs(position_sec - self.position_sec) < 0.02:
            return
        self.position_sec = position_sec
        self._last_position_px = pixel
        self.update()

    def _ensure_static_cache(self) -> None:
        cache_key = (self.size().width(), self.size().height(), _ui_dark_mode(), id(self.notes), len(self.notes), round(self.duration_sec, 4))
        if self._cache_key == cache_key and not self._static_cache.isNull():
            return
        pm = QPixmap(max(1, self.width()), max(1, self.height()))
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = _ui_dark_mode()
        bg = QColor("#0f172a") if dark else QColor("#f8fafc")
        grid = QColor("#334155") if dark else QColor("#e5e7eb")
        text_color = QColor("#cbd5e1") if dark else QColor("#334155")
        painter.fillRect(pm.rect(), bg)
        if self.width() <= 0 or self.height() <= 0:
            painter.end()
            self._static_cache = pm
            self._cache_key = cache_key
            return
        label_w = 140
        row_h = max(22, self.height() / len(self.KEY_ROWS))
        painter.setPen(QPen(grid, 1))
        for idx, (key, name) in enumerate(self.KEY_ROWS):
            y = int(idx * row_h)
            painter.drawLine(0, y, self.width(), y)
            painter.setPen(text_color)
            painter.drawText(12, y + int(row_h * 0.65), f"{key}  {name}")
            painter.setPen(QPen(QColor("#e5e7eb"), 1))
        painter.drawLine(label_w, 0, label_w, self.height())
        if not self.notes or self.duration_sec <= 0:
            painter.setPen(text_color)
            painter.drawText(self.rect(), Qt.AlignCenter, "这里显示鼓键行 + 对应 MIDI 实时预览")
        else:
            palette = [QColor("#66C2A5"), QColor("#8DA0CB"), QColor("#FC8D62"), QColor("#E78AC3"), QColor("#A6D854")] if not dark else [QColor("#34d399"), QColor("#818cf8"), QColor("#fb923c"), QColor("#f472b6"), QColor("#a3e635")]
            usable_w = max(10, self.width() - label_w - 10)
            for idx, note in enumerate(self.notes[:5000]):
                key = ModernDrumBackend.drum_key_for_midi(note.midi_note)
                if not key:
                    continue
                row = self.KEY_INDEX.get(key)
                if row is None:
                    continue
                x1 = label_w + (note.start_sec / self.duration_sec) * usable_w
                x2 = max(x1 + 3.0, label_w + (note.end_sec / self.duration_sec) * usable_w)
                y = row * row_h + row_h * 0.2
                h = row_h * 0.55
                painter.fillRect(QRectF(x1, y, x2 - x1, h), palette[idx % len(palette)])
        painter.end()
        self._static_cache = pm
        self._cache_key = cache_key

    def paintEvent(self, _event):
        self._ensure_static_cache()
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._static_cache)
        if self.duration_sec > 0:
            label_w = 140
            usable_w = max(10, self.width() - label_w - 10)
            x_pos = label_w + (self.position_sec / max(0.001, self.duration_sec)) * usable_w
            painter.setPen(QPen(QColor("#e5e7eb") if _ui_dark_mode() else QColor("#111827"), 2))
            painter.drawLine(int(x_pos), 0, int(x_pos), self.height())


class SettingsDialog(FadeDialog):
    def __init__(self, settings: UISettings, parent: Optional[QWidget] = None, *, cache_size_provider: Optional[Callable[[], int]] = None, clear_cache_callback: Optional[Callable[[], int]] = None):
        super().__init__(parent)
        self.setWindowTitle("外观、快捷键与性能")
        self.setModal(True)
        self.resize(720, 780)
        self.setMinimumSize(560, 520)
        self.setSizeGripEnabled(True)
        self.setObjectName("Surface")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._settings = UISettings(**settings.__dict__)
        self._cache_size_provider = cache_size_provider or cache_size_bytes
        self._clear_cache_callback = clear_cache_callback
        self.background_surface = BackgroundSurface(BACKGROUND_IMAGE_PATH, self)
        self.background_surface.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.background_surface.setGeometry(self.rect())
        self.background_surface.lower()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(10)

        title = QLabel("设置")
        title.setProperty("title", True)
        subtitle = QLabel("在这里调整快捷键、界面显示、性能和缓存。")
        subtitle.setProperty("muted", True)
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setObjectName("CenterSurface")
        self.scroll.viewport().setObjectName("CenterSurface")
        root.addWidget(self.scroll, 1)

        content = QWidget()
        content.setObjectName("Page")
        content.setAttribute(Qt.WA_StyledBackground, True)
        self.scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(10)

        hotkey_card = Card()
        hotkey_layout = QGridLayout(hotkey_card)
        hotkey_layout.setContentsMargins(16, 14, 16, 14)
        hotkey_layout.setHorizontalSpacing(12)
        hotkey_layout.setVerticalSpacing(10)
        hotkey_title = QLabel("播放热键")
        hotkey_title.setProperty("sectionTitle", True)
        hotkey_layout.addWidget(hotkey_title, 0, 0, 1, 3)
        rows = [("play", "播放 / 继续", self._settings.play_hotkey_enabled, self._settings.play_hotkey), ("pause", "暂停", self._settings.pause_hotkey_enabled, self._settings.pause_hotkey), ("stop", "停止并回零", self._settings.stop_hotkey_enabled, self._settings.stop_hotkey)]
        self.hotkey_switches: Dict[str, AnimatedSwitch] = {}
        self.hotkey_edits: Dict[str, QKeySequenceEdit] = {}
        for row, (name, label_text, enabled, sequence) in enumerate(rows, start=1):
            sw = AnimatedSwitch(label_text)
            sw.setChecked(bool(enabled))
            edit = QKeySequenceEdit(QKeySequence(sequence))
            self.hotkey_switches[name] = sw
            self.hotkey_edits[name] = edit
            hotkey_layout.addWidget(sw, row, 0)
            hotkey_layout.addWidget(edit, row, 1, 1, 2)
        hint = QLabel("全局热键更适合用单键，例如 F10 / F11 / F12。")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        hotkey_layout.addWidget(hint, 4, 0, 1, 3)
        content_layout.addWidget(hotkey_card)

        appearance_card = Card()
        appearance_layout = QGridLayout(appearance_card)
        appearance_layout.setContentsMargins(16, 14, 16, 14)
        appearance_layout.setHorizontalSpacing(12)
        appearance_layout.setVerticalSpacing(8)
        appearance_title = QLabel("外观与体验")
        appearance_title.setProperty("sectionTitle", True)
        appearance_layout.addWidget(appearance_title, 0, 0, 1, 2)
        self.dark_mode_switch = AnimatedSwitch("夜间模式")
        self.dark_mode_switch.setChecked(self._settings.dark_mode)
        appearance_layout.addWidget(self.dark_mode_switch, 1, 0, 1, 2)
        appearance_layout.addWidget(QLabel("主题颜色"), 2, 0)
        self.theme_combo = QComboBox()
        for label, value in [("海洋蓝", "ocean"), ("紫晶", "violet"), ("翡翠", "emerald"), ("落日橙", "sunset"), ("石墨灰", "graphite")]:
            self.theme_combo.addItem(label, value)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(getattr(self._settings, "theme_preset", "ocean"))))
        appearance_layout.addWidget(self.theme_combo, 2, 1)
        appearance_layout.addWidget(QLabel("窗口缩放"), 3, 0)
        self.scale_combo = QComboBox()
        for value in [80, 90, 100, 110, 125, 130]:
            self.scale_combo.addItem(f"{value}%", value)
        self.scale_combo.setCurrentIndex(max(0, self.scale_combo.findData(self._settings.ui_scale)))
        appearance_layout.addWidget(self.scale_combo, 3, 1)
        appearance_layout.addWidget(QLabel("毛玻璃厚度"), 4, 0)
        self.glass_blur_slider = QSlider(Qt.Horizontal)
        self.glass_blur_slider.setRange(0, 100)
        self.glass_blur_slider.setValue(int(getattr(self._settings, "glass_blur", 58)))
        appearance_layout.addWidget(self.glass_blur_slider, 4, 1)
        self.glass_blur_label = QLabel(f"{int(getattr(self._settings, 'glass_blur', 58))}%（夜间模式自动关闭背景）")
        self.glass_blur_label.setProperty("muted", True)
        appearance_layout.addWidget(self.glass_blur_label, 5, 1)
        self.splash_switch = AnimatedSwitch("启用启动画面")
        self.splash_switch.setChecked(bool(getattr(self._settings, "splash_enabled", True)))
        appearance_layout.addWidget(self.splash_switch, 6, 0, 1, 2)
        appearance_layout.addWidget(QLabel("启动动画时长"), 7, 0)
        self.splash_duration_combo = QComboBox()
        for value in [1000, 2000, 3000, 4000, 5000]:
            self.splash_duration_combo.addItem(f"{value // 1000} 秒", value)
        self.splash_duration_combo.setCurrentIndex(max(0, self.splash_duration_combo.findData(int(getattr(self._settings, "splash_duration_ms", 3000)))))
        appearance_layout.addWidget(self.splash_duration_combo, 7, 1)
        self.anim_switch = AnimatedSwitch("启用动画")
        self.anim_switch.setChecked(self._settings.animations_enabled)
        appearance_layout.addWidget(self.anim_switch, 8, 0, 1, 2)
        appearance_layout.addWidget(QLabel("动画速度"), 9, 0)
        self.anim_speed_slider = QSlider(Qt.Horizontal)
        self.anim_speed_slider.setRange(40, 220)
        self.anim_speed_slider.setValue(int(self._settings.animation_speed))
        appearance_layout.addWidget(self.anim_speed_slider, 9, 1)
        self.anim_speed_label = QLabel(f"{self._settings.animation_speed}%")
        self.anim_speed_label.setProperty("muted", True)
        appearance_layout.addWidget(self.anim_speed_label, 10, 1)
        self.debug_switch = AnimatedSwitch("Debug 模式（显示详细运行日志）")
        self.debug_switch.setChecked(self._settings.debug_mode)
        appearance_layout.addWidget(self.debug_switch, 11, 0, 1, 2)
        self.gpu_switch = AnimatedSwitch("GPU 加速（统计与调参粗筛）")
        self.gpu_switch.setChecked(bool(getattr(self._settings, "gpu_acceleration", False)))
        appearance_layout.addWidget(self.gpu_switch, 12, 0, 1, 2)
        gpu_hint = QLabel("用于统计、预览生成和自动调参粗筛，不会改变实时按键播放逻辑。")
        gpu_hint.setWordWrap(True)
        gpu_hint.setProperty("muted", True)
        appearance_layout.addWidget(gpu_hint, 13, 0, 1, 2)
        self.performance_switch = AnimatedSwitch("性能模式（为低配设备减少界面负担）")
        self.performance_switch.setChecked(bool(getattr(self._settings, "performance_mode", False)))
        appearance_layout.addWidget(self.performance_switch, 14, 0, 1, 2)
        perf_hint = QLabel("启用后会减少界面特效和刷新频率，低配设备会更流畅。")
        perf_hint.setWordWrap(True)
        perf_hint.setProperty("muted", True)
        appearance_layout.addWidget(perf_hint, 15, 0, 1, 2)
        self.anim_speed_slider.valueChanged.connect(lambda value: self.anim_speed_label.setText(f"{value}%"))
        self.glass_blur_slider.valueChanged.connect(lambda value: self.glass_blur_label.setText(f"{value}%（夜间模式自动关闭背景）"))
        self.anim_speed_slider.setEnabled(self._settings.animations_enabled)
        self.anim_switch.toggled.connect(self.anim_speed_slider.setEnabled)
        content_layout.addWidget(appearance_card)

        cache_card = Card()
        cache_layout = QGridLayout(cache_card)
        cache_layout.setContentsMargins(16, 14, 16, 14)
        cache_layout.setHorizontalSpacing(12)
        cache_layout.setVerticalSpacing(8)
        cache_title = QLabel("本地缓存")
        cache_title.setProperty("sectionTitle", True)
        cache_layout.addWidget(cache_title, 0, 0, 1, 2)
        cache_desc = QLabel("缓存会保存分析和自动调参结果。清除后，下次载入会重新计算。")
        cache_desc.setProperty("muted", True)
        cache_desc.setWordWrap(True)
        cache_layout.addWidget(cache_desc, 1, 0, 1, 2)
        cache_layout.addWidget(QLabel("当前缓存大小"), 2, 0)
        self.cache_size_label = QLabel("读取中…")
        self.cache_size_label.setProperty("muted", True)
        cache_layout.addWidget(self.cache_size_label, 2, 1)
        self.refresh_cache_btn = QPushButton("刷新缓存大小")
        self.refresh_cache_btn.clicked.connect(self._refresh_cache_info)
        self.clear_cache_btn = QPushButton("清除本地缓存")
        self.clear_cache_btn.clicked.connect(self._clear_cache)
        cache_layout.addWidget(self.refresh_cache_btn, 3, 0)
        cache_layout.addWidget(self.clear_cache_btn, 3, 1)
        content_layout.addWidget(cache_card)
        self._refresh_cache_info()

        watermark = QLabel("@SayaTech")
        watermark.setProperty("watermark", True)
        watermark.setAlignment(Qt.AlignRight)
        content_layout.addWidget(watermark)
        content_layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _refresh_cache_info(self) -> None:
        try:
            self.cache_size_label.setText(format_cache_size(int(self._cache_size_provider() or 0)))
        except Exception as exc:
            self.cache_size_label.setText(f"读取失败：{exc}")

    def _clear_cache(self) -> None:
        if QMessageBox.question(self, "清除缓存", "确定要清除本地缓存吗？这不会删除 MIDI 文件，但下次分析/调参/预热会重新构建。") != QMessageBox.StandardButton.Yes:
            return
        removed_bytes = 0
        try:
            removed_bytes = int(self._cache_size_provider() or 0)
        except Exception:
            removed_bytes = 0
        if self._clear_cache_callback is not None:
            try:
                self._clear_cache_callback()
            except Exception as exc:
                QMessageBox.warning(self, "清除缓存失败", str(exc))
                self._refresh_cache_info()
                return
        self._refresh_cache_info()
        QMessageBox.information(self, "清除完成", f"已清除本地缓存：{format_cache_size(removed_bytes)}")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "background_surface", None) is not None:
            self.background_surface.setGeometry(self.rect())
            self.background_surface.lower()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        dark = _ui_dark_mode()
        surface = getattr(self, "background_surface", None)
        if dark:
            painter.fillRect(self.rect(), QColor("#03060b"))
        else:
            painted = False
            if surface is not None:
                try:
                    surface._ensure_scaled()
                    if getattr(surface, "_scaled", QPixmap()).isNull() is False:
                        painter.drawPixmap(self.rect(), surface._scaled)
                        painter.fillRect(self.rect(), QColor(255, 248, 251, 92))
                        painted = True
                except Exception:
                    painted = False
            if not painted:
                painter.fillRect(self.rect(), QColor("#f6f3f5"))

    @property
    def settings(self) -> UISettings:
        return self._settings

    def _accept(self) -> None:
        keys = {}
        for name, edit in self.hotkey_edits.items():
            seq = edit.keySequence().toString(QKeySequence.PortableText).strip()
            keys[name] = seq.upper()
        enabled_names = [name for name, sw in self.hotkey_switches.items() if sw.isChecked()]
        active_values = [keys[name] for name in enabled_names if keys[name]]
        if len(active_values) != len(set(active_values)):
            QMessageBox.warning(self, "热键冲突", "播放、暂停和停止的热键不能重复。")
            return
        self._settings.play_hotkey_enabled = self.hotkey_switches['play'].isChecked()
        self._settings.pause_hotkey_enabled = self.hotkey_switches['pause'].isChecked()
        self._settings.stop_hotkey_enabled = self.hotkey_switches['stop'].isChecked()
        self._settings.play_hotkey = keys['play'] or 'F10'
        self._settings.pause_hotkey = keys['pause'] or 'F11'
        self._settings.stop_hotkey = keys['stop'] or 'F12'
        self._settings.dark_mode = self.dark_mode_switch.isChecked()
        self._settings.theme_preset = str(self.theme_combo.currentData() or "ocean")
        self._settings.ui_scale = int(self.scale_combo.currentData())
        self._settings.animations_enabled = self.anim_switch.isChecked()
        self._settings.animation_speed = int(self.anim_speed_slider.value())
        self._settings.debug_mode = self.debug_switch.isChecked()
        self._settings.glass_blur = int(self.glass_blur_slider.value())
        self._settings.splash_enabled = self.splash_switch.isChecked()
        self._settings.splash_duration_ms = int(self.splash_duration_combo.currentData() or 3000)
        self._settings.gpu_acceleration = self.gpu_switch.isChecked()
        self._settings.performance_mode = self.performance_switch.isChecked()
        self.accept()


class MainWindow(QMainWindow):
    clock_sync_finished = Signal(float, str, str)
    tuner_finished = Signal(object, str, str)
    tuner_failed = Signal(str)
    midi_loaded = Signal(object)
    midi_load_failed = Signal(object)
    backend_log_signal = Signal(str)
    drum_report_ready = Signal(object)

    def __init__(self):
        super().__init__()
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        self.config_path = ensure_config_file(str(ensure_user_config_file(DEFAULT_TEMPLATE)))
        self.runtime_config = load_config(self.config_path)
        self.ui_settings = load_ui_settings(None)
        set_runtime_debug_mode(bool(getattr(self.ui_settings, 'debug_mode', False)))
        self.config_widgets: Dict[str, QWidget] = {}
        self.drum_param_widgets: Dict[str, QWidget] = {}
        self.tuner_suggestions: Dict[str, object] = {}
        self._tuner_inflight = False
        self.clock_offset_sec = 0.0
        self.clock_source_text = "本地时间"
        self.clock_status_text = "尚未校时"
        self._clock_sync_inflight = False
        self.ensemble_active = False
        self.ensemble_fired = False
        self._ensemble_arm_debounce_until = 0.0
        self.ensemble_target: Optional[datetime] = None
        self._analysis_cache: Dict[tuple[str, tuple[int, ...]], MidiAnalysisResult] = {}
        self._analysis_cache_source_id: Optional[int] = None
        self._backend_config_signature: Optional[tuple] = None
        self._transport_refresh_key: Optional[tuple] = None
        self._drum_report_cache_key: Optional[tuple] = None
        self._drum_report_cache: Optional[DrumPlanReport] = None
        self._drum_report_task_token = 0
        self._track_tree_render_key: Optional[tuple] = None
        self._drum_report_render_key: Optional[tuple] = None
        self._last_visible_backend_kind: Optional[str] = None
        self._pending_log_lines: List[str] = []
        self._midi_load_inflight = False
        self._midi_load_token = 0
        self._track_refresh_pending = False
        self._analysis_refresh_inflight: Dict[str, Optional[object]] = {'piano': None, 'drum': None}
        self._runtime_config_dirty = True
        self._last_position_slider_value = -1
        self._last_position_time_key = (None, None, None)
        self._last_visual_update_at = 0.0
        self._last_clock_ui_update_at = 0.0
        self._last_clock_render_payload: tuple[str, str] = ("", "")
        self._global_hotkey_bindings: List[tuple[Optional[tuple[int, tuple[int, ...]]], Callable[[], None], bool]] = []

        self.setWindowTitle(str(self.runtime_config.get("GUI_TITLE", "SayaTech MIDI 自动弹奏")) + " · Modern")
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))
        self.resize(1500, 940)
        append_runtime_log(f"MainWindow init | config={self.config_path} | runtime_log={runtime_log_path()}", debug=True)
        self.backend_log_signal.connect(self._log)
        self.piano_backend = ModernPianoBackend(self._threadsafe_backend_log)
        self.drum_backend = ModernDrumBackend(self._threadsafe_backend_log)
        self._apply_runtime_config_to_backends()
        self.transport = TransportController(self.piano_backend)
        self.current_analysis: Optional[MidiAnalysisResult] = None
        self.current_mode = "piano"
        self.selected_piano_tracks: set[int] = set()
        self.selected_drum_tracks: set[int] = set()
        self._hotkey_last_trigger: Dict[str, float] = {"play": 0.0, "pause": 0.0, "stop": 0.0}
        self._global_hotkey_state: Dict[tuple[int, tuple[int, ...]], bool] = {}
        self._last_applied_stylesheet = ""
        self._piano_preview_mode = "lite"
        self._drum_preview_mode = "lite"

        self._apply_ui_settings(initial=True)
        self._build_ui()
        self._apply_performance_mode_widget_visibility()
        self._apply_page_update_policy()
        self._load_config_into_form()
        self._load_drum_config_widgets()
        self._wire_transport()
        self._setup_shortcuts()
        self._setup_global_hotkeys()
        self._refresh_hotkey_labels()
        self._sync_mode_cards()
        self.clock_sync_finished.connect(self._apply_clock_sync)
        self.drum_report_ready.connect(self._on_drum_report_ready)
        self.tuner_finished.connect(self._on_tuner_finished)
        self.tuner_failed.connect(self._on_tuner_failed)
        self.midi_loaded.connect(self._on_midi_loaded)
        self.midi_load_failed.connect(self._on_midi_load_failed)
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(100)
        self.clock_timer.timeout.connect(self._tick_clock)
        self.clock_timer.start()
        self.auto_sync_timer = QTimer(self)
        self.auto_sync_timer.setInterval(5 * 60 * 1000)
        self.auto_sync_timer.timeout.connect(self._maybe_auto_sync)
        self.auto_sync_timer.start()
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setSingleShot(True)
        self._log_flush_timer.setInterval(24)
        self._log_flush_timer.timeout.connect(self._flush_pending_logs)
        self._track_refresh_timer = QTimer(self)
        self._track_refresh_timer.setSingleShot(True)
        self._track_refresh_timer.setInterval(20)
        self._track_refresh_timer.timeout.connect(self._flush_pending_transport_refresh)
        self._playback_prewarm_timer = QTimer(self)
        self._playback_prewarm_timer.setSingleShot(True)
        self._playback_prewarm_timer.setInterval(180)
        self._playback_prewarm_timer.timeout.connect(self._run_playback_prewarm)
        self._playback_ready_timer = QTimer(self)
        self._playback_ready_timer.setInterval(120)
        self._playback_ready_timer.timeout.connect(self._refresh_playback_controls)
        self._playback_ready_timer.start()
        self._set_ensemble_target(beijing_now(self.clock_offset_sec).replace(tzinfo=None) + timedelta(seconds=30))
        self._log(f"[DEBUG] 用户数据目录：{user_data_root()}")
        self._log(f"[DEBUG] 运行日志：{runtime_log_path()}")
        self._start_clock_sync(reason="启动校时")

    def _build_ui(self) -> None:
        root = BackgroundSurface(BACKGROUND_IMAGE_PATH)
        root.setObjectName("Surface")
        root.setAttribute(Qt.WA_StyledBackground, True)
        self.background_surface = root
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        header = Card()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 12)
        header_layout.setSpacing(10)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("SayaTech MIDI Studio")
        title.setProperty("title", True)
        subtitle = QLabel("界面更紧凑，适合直接上手。")
        subtitle.setProperty("muted", True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch(1)
        self.settings_btn = QToolButton()
        self.settings_btn.setText("⚙")
        self.settings_btn.setToolTip("设置")
        self.settings_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.settings_btn.clicked.connect(self._open_settings)
        header_layout.addWidget(self.settings_btn)
        root_layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.main_splitter = splitter
        root_layout.addWidget(splitter, 1)

        self.left_sidebar = self._build_left_sidebar()
        splitter.addWidget(self.left_sidebar)

        center_scroll = QScrollArea()
        center_scroll.setWidgetResizable(True)
        center_scroll.setFrameShape(QFrame.NoFrame)
        self.center_scroll = center_scroll
        self.pages = FadeStackedWidget()
        self.pages.setObjectName("CenterSurface")
        center_scroll.setObjectName("CenterSurface")
        center_scroll.viewport().setObjectName("CenterSurface")
        center_scroll.setWidget(self.pages)
        self.pages.addWidget(self._build_piano_page())
        self.pages.addWidget(self._build_config_page())
        self.pages.addWidget(self._build_tuner_page())
        self.pages.addWidget(self._build_drum_page())
        splitter.addWidget(center_scroll)

        self.right_sidebar = self._build_right_sidebar()
        splitter.addWidget(self.right_sidebar)
        self.right_sidebar.setMinimumWidth(350)
        splitter.setSizes([298, 1100, 360])

    def _build_left_sidebar(self) -> QWidget:
        sidebar = Sidebar()
        sidebar.setMinimumWidth(286)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        nav_title = QLabel("工作台")
        nav_title.setStyleSheet("font-weight: 700; font-size: 16px;")
        layout.addWidget(nav_title)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("NavList")
        self.nav_list.setFocusPolicy(Qt.NoFocus)
        self.nav_list.setSelectionRectVisible(False)
        self.nav_list.setSpacing(2)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav_list.setMinimumHeight(286)
        for text, mode in [("钢琴 / 吉他 / 贝斯", "piano"), ("架子鼓", "drum"), ("参数配置", "config"), ("自动调参", "tuner")]:
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, mode)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentItemChanged.connect(self._switch_mode)
        layout.addWidget(self.nav_list, 1)

        files_card = Card()
        files_layout = QVBoxLayout(files_card)
        files_layout.setContentsMargins(12, 12, 12, 12)
        files_layout.addWidget(QLabel("当前 MIDI 文件"))
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("选择 MIDI 文件后显示路径")
        files_layout.addWidget(self.file_path_edit)
        self.open_btn = QPushButton("选择 MIDI")
        self.open_btn.setProperty("primary", True)
        self.open_btn.clicked.connect(self._open_file)
        files_layout.addWidget(self.open_btn)
        layout.addWidget(files_card, 0)

        track_card = Card()
        track_layout = QVBoxLayout(track_card)
        track_layout.setContentsMargins(12, 12, 12, 12)
        track_layout.addWidget(QLabel("轨道选择"))
        button_grid = QGridLayout()
        self.recommend_btn = QPushButton("智能选轨")
        self.select_all_btn = QPushButton("全选")
        self.clear_btn = QPushButton("清空")
        self.refresh_btn = QPushButton("刷新")
        for btn, slot in [
            (self.recommend_btn, self._select_recommended_tracks),
            (self.select_all_btn, self._select_all_tracks),
            (self.clear_btn, self._clear_tracks),
            (self.refresh_btn, self._refresh_transport_for_mode),
        ]:
            btn.clicked.connect(slot)
        button_grid.addWidget(self.recommend_btn, 0, 0)
        button_grid.addWidget(self.select_all_btn, 0, 1)
        button_grid.addWidget(self.clear_btn, 1, 0)
        button_grid.addWidget(self.refresh_btn, 1, 1)
        track_layout.addLayout(button_grid)
        self.track_tree = QTreeWidget()
        self.track_tree.setHeaderLabels(["轨道", "音符", "范围"])
        self.track_tree.setRootIsDecorated(False)
        self.track_tree.setAlternatingRowColors(False)
        self.track_tree.itemChanged.connect(self._on_track_item_changed)
        self.track_tree.header().setStretchLastSection(False)
        self.track_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.track_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.track_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        track_layout.addWidget(self.track_tree)
        layout.addWidget(track_card, 1)
        layout.setStretch(1, 1)
        layout.setStretch(3, 1)
        return sidebar

    def _build_status_card(self, title: str, value: str, muted: Optional[str] = None) -> Card:
        card = Card()
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        card.setMinimumHeight(88)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        t = QLabel(title)
        t.setProperty("muted", True)
        val = QLabel(value)
        val.setObjectName("StatusValue")
        v.addWidget(t)
        v.addWidget(val)
        if muted:
            m = QLabel(muted)
            m.setProperty("muted", True)
            v.addWidget(m)
        return card

    def _build_piano_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        page.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top_cards = QHBoxLayout()
        top_cards.setSpacing(10)
        self.piano_mode_card = self._build_status_card("当前模式", "钢琴 / 吉他 / 贝斯")
        self.piano_bpm_card = self._build_status_card("当前 MIDI BPM", "-", "未载入")
        self.piano_state_card = self._build_status_card("播放状态", "stopped", "等待播放")
        for c in [self.piano_mode_card, self.piano_bpm_card, self.piano_state_card]:
            top_cards.addWidget(c, 1)
        layout.addLayout(top_cards)

        vertical_split = QSplitter(Qt.Vertical)
        vertical_split.setChildrenCollapsible(False)
        self.piano_splitter = vertical_split

        preview_card = Card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(14, 10, 14, 10)
        preview_layout.setSpacing(6)
        preview_layout.setAlignment(Qt.AlignTop)
        title_row = QHBoxLayout()
        title = QLabel("钢琴 / 吉他 / 贝斯预览")
        title.setProperty("sectionTitle", True)
        title_row.addWidget(title)
        title_row.addStretch(1)
        self.piano_lite_btn = QToolButton()
        self.piano_lite_btn.setText("简览")
        self.piano_lite_btn.setCheckable(True)
        self.piano_lite_btn.setChecked(True)
        self.piano_lite_btn.clicked.connect(lambda: self._set_piano_preview_mode("lite"))
        self.piano_detail_btn = QToolButton()
        self.piano_detail_btn.setText("详细预览")
        self.piano_detail_btn.setCheckable(True)
        self.piano_detail_btn.clicked.connect(lambda: self._set_piano_preview_mode("detail"))
        title_row.addWidget(self.piano_lite_btn)
        title_row.addWidget(self.piano_detail_btn)
        self.piano_range_label = QLabel("未载入")
        self.piano_range_label.setProperty("muted", True)
        title_row.addWidget(self.piano_range_label)
        preview_layout.addLayout(title_row)
        preview_hint = QLabel("默认先显示简览；点“详细预览”后再加载卷帘图。")
        preview_hint.setProperty("muted", True)
        preview_hint.setWordWrap(True)
        preview_layout.addWidget(preview_hint)
        self.piano_preview_stack = FadeStackedWidget()
        self.piano_preview_card = preview_card
        self.piano_roll = LightOverviewWidget("piano")
        self.piano_roll_detail = None
        self._piano_detail_placeholder = QWidget()
        _piano_detail_ph_layout = QVBoxLayout(self._piano_detail_placeholder)
        _piano_detail_ph_layout.setContentsMargins(0, 0, 0, 0)
        _piano_detail_label = QLabel("点击上方“详细预览”后加载卷帘图。")
        _piano_detail_label.setAlignment(Qt.AlignCenter)
        _piano_detail_label.setProperty("muted", True)
        _piano_detail_ph_layout.addWidget(_piano_detail_label)
        self.piano_preview_stack.addWidget(self.piano_roll)
        self.piano_preview_stack.addWidget(self._piano_detail_placeholder)
        preview_layout.addWidget(self.piano_preview_stack, 1)
        vertical_split.addWidget(preview_card)

        timeline_card = Card()
        timeline_layout = QVBoxLayout(timeline_card)
        timeline_layout.setContentsMargins(14, 10, 14, 10)
        timeline_layout.setSpacing(6)
        title_row = QHBoxLayout()
        timeline_title = QLabel("播放进度 / 时间轴")
        timeline_title.setProperty("sectionTitle", True)
        title_row.addWidget(timeline_title)
        title_row.addStretch(1)
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setProperty("muted", True)
        title_row.addWidget(self.time_label)
        timeline_layout.addLayout(title_row)
        self.waveform = WaveformTimelineWidget()
        timeline_layout.addWidget(self.waveform, 0, Qt.AlignTop)
        self.progress_slider = QSlider(Qt.Horizontal)
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.sliderMoved.connect(self._on_slider_moved)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        timeline_layout.addWidget(self.progress_slider)
        buttons = QHBoxLayout()
        self.play_btn = QPushButton("F10 播放 / 继续")
        self.play_btn.setProperty("primary", True)
        self.pause_btn = QPushButton("F11 暂停")
        self.stop_btn = QPushButton("F12 停止并回零")
        self.play_btn.clicked.connect(self._play_transport)
        self.pause_btn.clicked.connect(self.transport.pause)
        self.stop_btn.clicked.connect(self.transport.stop)
        for btn in [self.play_btn, self.pause_btn, self.stop_btn]:
            buttons.addWidget(btn)
        buttons.addStretch(1)
        timeline_layout.addLayout(buttons)
        vertical_split.addWidget(timeline_card)
        vertical_split.setSizes([290, 185])

        layout.addWidget(vertical_split, 1)
        return page

    def _build_drum_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        page.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top_cards = QHBoxLayout()
        self.drum_mode_card = self._build_status_card("当前模式", "架子鼓")
        self.drum_bpm_card = self._build_status_card("当前 MIDI BPM", "-", "未载入")
        self.drum_state_card = self._build_status_card("播放状态", "stopped", "等待播放")
        for c in [self.drum_mode_card, self.drum_bpm_card, self.drum_state_card]:
            top_cards.addWidget(c, 1)
        layout.addLayout(top_cards)

        content_split = QSplitter(Qt.Horizontal)
        content_split.setChildrenCollapsible(False)
        self.drum_content_split = content_split

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        drum_card = Card()
        drum_layout = QVBoxLayout(drum_card)
        drum_layout.setContentsMargins(14, 10, 14, 10)
        drum_layout.setSpacing(6)
        drum_layout.setAlignment(Qt.AlignTop)
        title_row = QHBoxLayout()
        drum_title = QLabel("鼓预览")
        drum_title.setProperty("sectionTitle", True)
        title_row.addWidget(drum_title)
        title_row.addStretch(1)
        self.drum_lite_btn = QToolButton()
        self.drum_lite_btn.setText("简览")
        self.drum_lite_btn.setCheckable(True)
        self.drum_lite_btn.setChecked(True)
        self.drum_lite_btn.clicked.connect(lambda: self._set_drum_preview_mode("lite"))
        self.drum_detail_btn = QToolButton()
        self.drum_detail_btn.setText("详细预览")
        self.drum_detail_btn.setCheckable(True)
        self.drum_detail_btn.clicked.connect(lambda: self._set_drum_preview_mode("detail"))
        title_row.addWidget(self.drum_lite_btn)
        title_row.addWidget(self.drum_detail_btn)
        self.drum_hint_label = QLabel("默认显示简览")
        self.drum_hint_label.setProperty("muted", True)
        title_row.addWidget(self.drum_hint_label)
        drum_layout.addLayout(title_row)
        drum_hint = QLabel("默认先显示简览；点“详细预览”后再加载鼓卷帘。")
        drum_hint.setProperty("muted", True)
        drum_hint.setWordWrap(True)
        drum_layout.addWidget(drum_hint)
        self.drum_preview_stack = FadeStackedWidget()
        self.drum_preview_card = drum_card
        self.drum_roll = LightOverviewWidget("drum")
        self.drum_roll_detail = None
        self._drum_detail_placeholder = QWidget()
        _drum_detail_ph_layout = QVBoxLayout(self._drum_detail_placeholder)
        _drum_detail_ph_layout.setContentsMargins(0, 0, 0, 0)
        _drum_detail_label = QLabel("点击上方“详细预览”后加载鼓卷帘。")
        _drum_detail_label.setAlignment(Qt.AlignCenter)
        _drum_detail_label.setProperty("muted", True)
        _drum_detail_ph_layout.addWidget(_drum_detail_label)
        self.drum_preview_stack.addWidget(self.drum_roll)
        self.drum_preview_stack.addWidget(self._drum_detail_placeholder)
        drum_layout.addWidget(self.drum_preview_stack, 1)
        left_layout.addWidget(drum_card, 2)

        drum_timeline_card = Card()
        drum_timeline_layout = QVBoxLayout(drum_timeline_card)
        drum_timeline_layout.setContentsMargins(14, 10, 14, 10)
        drum_timeline_layout.setSpacing(6)
        title_row = QHBoxLayout()
        drum_time_title = QLabel("鼓时间轴 / 拖动定位")
        drum_time_title.setProperty("sectionTitle", True)
        title_row.addWidget(drum_time_title)
        title_row.addStretch(1)
        self.drum_time_label = QLabel("00:00 / 00:00")
        self.drum_time_label.setProperty("muted", True)
        title_row.addWidget(self.drum_time_label)
        drum_timeline_layout.addLayout(title_row)
        self.drum_waveform = WaveformTimelineWidget()
        self.drum_waveform.seek_requested.connect(self.transport.seek)
        drum_timeline_layout.addWidget(self.drum_waveform, 0, Qt.AlignTop)
        self.drum_progress_slider = QSlider(Qt.Horizontal)
        self.drum_progress_slider.setRange(0, 1000)
        self.drum_progress_slider.sliderMoved.connect(self._on_drum_slider_moved)
        self.drum_progress_slider.sliderReleased.connect(self._on_drum_slider_released)
        drum_timeline_layout.addWidget(self.drum_progress_slider)
        buttons = QHBoxLayout()
        self.drum_play_btn = QPushButton("F10 播放 / 继续")
        self.drum_play_btn.setProperty("primary", True)
        self.drum_pause_btn = QPushButton("F11 暂停")
        self.drum_stop_btn = QPushButton("F12 停止并回零")
        self.drum_play_btn.clicked.connect(self._play_transport)
        self.drum_pause_btn.clicked.connect(self.transport.pause)
        self.drum_stop_btn.clicked.connect(self.transport.stop)
        for btn in [self.drum_play_btn, self.drum_pause_btn, self.drum_stop_btn]:
            buttons.addWidget(btn)
        buttons.addStretch(1)
        drum_timeline_layout.addLayout(buttons)
        left_layout.addWidget(drum_timeline_card, 1)

        content_split.addWidget(left_panel)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setFrameShape(QFrame.NoFrame)
        side_scroll.setMinimumWidth(0)
        side_scroll.setMaximumWidth(16777215)
        side_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        side_scroll.setObjectName("CenterSurface")
        side_scroll.viewport().setObjectName("CenterSurface")
        side = QWidget()
        side.setObjectName("Page")
        side.setAttribute(Qt.WA_StyledBackground, True)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(10)

        param_card = Card()
        param_layout = QGridLayout(param_card)
        param_layout.setContentsMargins(12, 10, 12, 10)
        param_layout.setHorizontalSpacing(8)
        param_layout.setVerticalSpacing(4)
        param_layout.setColumnStretch(0, 1)
        param_layout.setColumnStretch(1, 1)
        title = QLabel("鼓模式参数")
        title.setProperty("sectionTitle", True)
        param_layout.addWidget(title, 0, 0, 1, 2)
        desc = QLabel("这些参数会影响架子鼓映射和演奏稳定性。")
        desc.setProperty("sectionDesc", True)
        desc.setWordWrap(True)
        param_layout.addWidget(desc, 1, 0, 1, 2)
        self._drum_param_specs = [
            ("BASE_TAP_HOLD", "基础点按时长", "float", "每个鼓键最短按住多久。太短会发虚，太长会糊成一片。"),
            ("SAME_TIME_WINDOW", "同时击打判定窗口", "float", "起音非常接近的鼓击会被视为同一拍内同时出现。"),
            ("COARSE_GROUP_WINDOW", "上下文分组窗口", "float", "给上下文替代和智能保留做粗分组时使用的时间窗口。"),
            ("DENSITY_LIMIT_HZ", "同键频率上限", "float", "同一个键每秒最多允许触发多少次，避免超高速连打。"),
            ("ACCENT_VELOCITY", "重音阈值", "int", "力度高于这个值的鼓击会更偏向保留成重音。"),
            ("GHOST_VELOCITY", "弱音阈值", "int", "力度低于这个值的鼓击会更偏向弱音处理。"),
            ("USE_CONTEXT_REPLACE", "启用上下文替代", "bool", "缺少对应鼓键时，结合上下文选择更自然的替代乐器。"),
            ("USE_VELOCITY_RULES", "启用力度规则", "bool", "根据力度区分重音、弱音和普通击打。"),
            ("USE_SMART_KEEP", "启用智能保留", "bool", "同一拍鼓击太多时，优先保留更重要的乐器和节奏骨架。"),
            ("PREFER_CHANNEL_10", "优先读取鼓轨", "bool", "分析时优先把第 10 通道当成标准鼓轨来处理。"),
        ]
        row = 2
        for key, label, kind, help_text in self._drum_param_specs:
            lab = QLabel(label)
            lab.setToolTip(help_text)
            param_layout.addWidget(lab, row, 0)
            widget = self._create_drum_param_widget(key, kind)
            widget.setToolTip(help_text)
            self.drum_param_widgets[key] = widget
            param_layout.addWidget(widget, row, 1)
            row += 1
        param_layout.setRowStretch(row + 1, 1)
        btn_row = QHBoxLayout()
        btn_apply = QPushButton("应用鼓参数")
        btn_apply.setProperty("primary", True)
        btn_apply.clicked.connect(self._apply_drum_params_panel)
        btn_save = QPushButton("保存鼓参数")
        btn_save.clicked.connect(self._save_drum_params_panel)
        btn_reload = QPushButton("重读鼓参数")
        btn_reload.clicked.connect(self._reload_drum_params_panel)
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_reload)
        param_layout.addLayout(btn_row, row, 0, 1, 2)
        side_layout.addWidget(param_card)

        summary_card = Card()
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("分析摘要")
        title.setProperty("sectionTitle", True)
        summary_layout.addWidget(title)
        self.drum_summary_mode = QLabel("模式：未分析")
        self.drum_summary_hits = QLabel("原始击打：0 / 映射后击打：0")
        self.drum_summary_keys = QLabel("映射键统计：-")
        self.drum_summary_fallback = QLabel("上下文替代 / 回退：-")
        self.drum_summary_ignored = QLabel("忽略 / 裁剪：-")
        for w in [self.drum_summary_mode, self.drum_summary_hits, self.drum_summary_keys, self.drum_summary_fallback, self.drum_summary_ignored]:
            w.setProperty("muted", True)
            w.setWordWrap(True)
            summary_layout.addWidget(w)
        side_layout.addWidget(summary_card)

        preview_card = Card()
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        title = QLabel("映射预览")
        title.setProperty("sectionTitle", True)
        preview_layout.addWidget(title)
        self.drum_mapping_preview_card = preview_card
        self.drum_preview_tree = QTreeWidget()
        self.drum_preview_tree.setMinimumHeight(360)
        self.drum_preview_tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.drum_preview_tree.setColumnCount(4)
        self.drum_preview_tree.setHeaderLabels(["原始鼓音", "次数", "映射", "备注"])
        self.drum_preview_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.drum_preview_tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.drum_preview_tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.drum_preview_tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        preview_layout.addWidget(self.drum_preview_tree, 1)
        preview_card.setMinimumHeight(400)
        side_layout.addWidget(preview_card, 2)
        side_layout.addStretch(1)

        side_scroll.setWidget(side)
        content_split.addWidget(side_scroll)
        content_split.setStretchFactor(0, 1)
        content_split.setStretchFactor(1, 1)
        content_split.setSizes([760, 760])
        layout.addWidget(content_split, 1)
        return page

    def _create_drum_param_widget(self, key: str, kind: str) -> QWidget:
        value = self.runtime_config.get(key)
        if kind == "bool":
            widget = QCheckBox()
            widget.setText("")
            widget.setChecked(bool(value))
            widget.setMinimumWidth(48)
            return widget
        if kind == "int":
            widget = QSpinBox()
            widget.setRange(1, 127)
            widget.setValue(int(value))
            widget.setMinimumWidth(96)
            return widget
        widget = QDoubleSpinBox()
        widget.setDecimals(3)
        widget.setSingleStep(0.001)
        widget.setRange(0.001, 999.0)
        widget.setValue(float(value))
        widget.setMinimumWidth(112)
        return widget

    def _build_config_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        page.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        top = Card()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(14, 12, 14, 12)
        title_box = QVBoxLayout()
        title = QLabel("参数配置")
        title.setProperty("sectionTitle", True)
        title_box.addWidget(title)
        desc = QLabel("这里保留会直接影响演奏结果的核心参数。")
        desc.setProperty("muted", True)
        desc.setWordWrap(True)
        title_box.addWidget(desc)
        top_layout.addLayout(title_box)
        top_layout.addStretch(1)
        for text_btn, slot in [("从文件重读", self._reload_config_from_disk), ("应用到当前运行", self._apply_config_from_form), ("保存 config.txt", self._save_config_to_disk)]:
            btn = QPushButton(text_btn)
            if text_btn == "应用到当前运行":
                btn.setProperty("primary", True)
            btn.clicked.connect(slot)
            top_layout.addWidget(btn)
        layout.addWidget(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("CenterSurface")
        scroll.viewport().setObjectName("CenterSurface")
        container = QWidget()
        container.setObjectName("Page")
        container.setAttribute(Qt.WA_StyledBackground, True)
        hbox = QHBoxLayout(container)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(10)
        left_col = QVBoxLayout()
        right_col = QVBoxLayout()
        left_col.setSpacing(10)
        right_col.setSpacing(10)
        section_desc = {
            "音域与键位": "",
            "演奏与切区": "",
            "重触发与踏板": "",
            "和弦与折返": "",
            "局部移八度": "",
            "旋律优先": "",
            "低音层保留": "",
            "高级模式": "不确定时保持默认即可。",
        }
        hidden_sections = {"界面与热键"}
        grouped = {}
        for spec in SUPPORTED_FIELDS:
            if spec.section in hidden_sections:
                continue
            grouped.setdefault(spec.section, []).append(spec)
        columns = [(left_col, 0), (right_col, 0)]
        for section_name, specs in grouped.items():
            card = Card()
            card_layout = QGridLayout(card)
            card_layout.setContentsMargins(18, 16, 18, 16)
            card_layout.setHorizontalSpacing(14)
            card_layout.setVerticalSpacing(10)
            title = QLabel(section_name)
            title.setProperty("sectionTitle", True)
            card_layout.addWidget(title, 0, 0, 1, 2)
            desc_text = section_desc.get(section_name, "")
            row = 1
            if desc_text:
                desc_label = QLabel(desc_text)
                desc_label.setProperty("sectionDesc", True)
                desc_label.setWordWrap(True)
                card_layout.addWidget(desc_label, 1, 0, 1, 2)
                row = 2
            for spec in specs:
                label = QLabel(spec.label)
                label.setProperty("fieldLabel", True)
                label.setToolTip(spec.help_text)
                card_layout.addWidget(label, row, 0)
                widget = self._create_config_widget(spec)
                widget.setToolTip(spec.help_text)
                self.config_widgets[spec.key] = widget
                card_layout.addWidget(widget, row, 1)
                row += 1
            card_layout.setRowStretch(row, 1)
            target_index = 0 if columns[0][1] <= columns[1][1] else 1
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            columns[target_index][0].addWidget(card, 0, Qt.AlignTop)
            columns[target_index] = (columns[target_index][0], columns[target_index][1] + len(specs) + (1 if desc_text else 0))
        left_col.addStretch(1)
        right_col.addStretch(1)
        hbox.addLayout(left_col, 1)
        hbox.addLayout(right_col, 1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        return page

    def _create_config_widget(self, spec):
        if spec.kind == "bool":
            widget = QCheckBox()
            return widget
        if spec.kind == "choice":
            widget = QComboBox()
            widget.addItems(spec.options or [])
            return widget
        if spec.kind == "int":
            widget = QSpinBox()
            widget.setRange(-999999, 999999)
            return widget
        if spec.kind == "float":
            widget = QDoubleSpinBox()
            widget.setDecimals(4)
            widget.setSingleStep(0.01)
            widget.setRange(-999999.0, 999999.0)
            return widget
        widget = QLineEdit()
        return widget

    def _build_tuner_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Page")
        page.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        left = Card()
        left.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 12, 14, 12)
        left_layout.addWidget(QLabel("自动调参"))
        intro = QLabel("按当前 MIDI 和可弹区间生成建议参数。")
        intro.setProperty("muted", True)
        intro.setWordWrap(True)
        left_layout.addWidget(intro)

        range_card = Card()
        range_layout = QGridLayout(range_card)
        range_layout.setContentsMargins(12, 10, 12, 10)
        range_layout.setHorizontalSpacing(10)
        range_layout.setVerticalSpacing(8)
        range_layout.addWidget(QLabel("可弹奏区间"), 0, 0, 1, 2)
        range_hint = QLabel("填写你在游戏里实际能弹到的最低音和最高音。")
        range_hint.setWordWrap(True)
        range_hint.setProperty("muted", True)
        range_layout.addWidget(range_hint, 1, 0, 1, 2)
        range_layout.addWidget(QLabel("乐器模式"), 2, 0)
        self.tuner_instrument_combo = QComboBox()
        self.tuner_instrument_combo.addItems(["钢琴", "吉他", "贝斯"])
        _instrument_text = str(self.runtime_config.get("INSTRUMENT_MODE", "钢琴"))
        _instrument_index = max(0, self.tuner_instrument_combo.findText(_instrument_text))
        self.tuner_instrument_combo.setCurrentIndex(_instrument_index)
        range_layout.addWidget(self.tuner_instrument_combo, 2, 1)
        range_layout.addWidget(QLabel("最低音"), 3, 0)
        self.tuner_min_note_edit = QLineEdit(cfg_midi_to_note_name(int(self.runtime_config.get("UNLOCKED_MIN_NOTE", 48))))
        self.tuner_min_note_edit.setPlaceholderText("例如 C2")
        range_layout.addWidget(self.tuner_min_note_edit, 4, 0)
        range_layout.addWidget(QLabel("最高音"), 3, 1)
        self.tuner_max_note_edit = QLineEdit(cfg_midi_to_note_name(int(self.runtime_config.get("UNLOCKED_MAX_NOTE", 83))))
        self.tuner_max_note_edit.setPlaceholderText("例如 B5")
        range_layout.addWidget(self.tuner_max_note_edit, 4, 1)
        left_layout.addWidget(range_card)

        btn_row = QHBoxLayout()
        self.tuner_generate_btn = QPushButton("生成建议")
        self.tuner_generate_btn.setProperty("primary", True)
        self.tuner_apply_btn = QPushButton("回填到配置页")
        self.tuner_generate_btn.clicked.connect(self._generate_tuner_suggestions)
        self.tuner_apply_btn.clicked.connect(self._apply_tuner_suggestions)
        btn_row.addWidget(self.tuner_generate_btn)
        btn_row.addWidget(self.tuner_apply_btn)
        btn_row.addStretch(1)
        left_layout.addLayout(btn_row)
        self.tuner_output = QTextEdit()
        self.tuner_output.setReadOnly(True)
        self.tuner_output.setPlainText("载入 MIDI 并填写可弹区间后，这里会生成建议参数。")
        left_layout.addWidget(self.tuner_output, 1)
        layout.addWidget(left, 1)

        right = Card()
        right.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 12, 14, 12)
        right_layout.addWidget(QLabel("建议预览"))
        self.tuner_preview = QTextEdit()
        self.tuner_preview.setReadOnly(True)
        self.tuner_preview.setPlainText(
            "生成建议后，这里会预览将要写回的主要内容：\n\n"
            "- 基础窗口起点\n"
            "- 可弹最低音 / 可弹最高音\n"
            "- 自动适配音域 / 自动切区 / 踏板识别\n"
            "- 重按与折返相关参数\n"
            "- 局部移八度 / 旋律保留 / 低音层保留"
        )
        right_layout.addWidget(self.tuner_preview)
        layout.addWidget(right, 1)
        return page

    def _build_right_sidebar(self) -> QWidget:
        sidebar = Sidebar()
        sidebar.setMinimumWidth(350)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        transport_card = Card()
        transport_layout = QVBoxLayout(transport_card)
        transport_layout.setContentsMargins(14, 14, 14, 14)
        transport_layout.setSpacing(8)
        transport_title = QLabel("播放状态")
        transport_title.setProperty("sectionTitle", True)
        transport_layout.addWidget(transport_title)
        self.state_badge = QLabel("stopped")
        self.state_badge.setProperty("badge", True)
        transport_layout.addWidget(self.state_badge, 0, Qt.AlignLeft)
        self.summary_label = QLabel("还没有载入 MIDI 文件")
        self.summary_label.setWordWrap(True)
        self.summary_label.setProperty("muted", True)
        transport_layout.addWidget(self.summary_label)
        layout.addWidget(transport_card)

        ensemble_card = Card()
        ensemble_layout = QVBoxLayout(ensemble_card)
        ensemble_layout.setContentsMargins(14, 14, 14, 14)
        ensemble_layout.setSpacing(8)
        ensemble_title = QLabel("合奏定时")
        ensemble_title.setProperty("sectionTitle", True)
        ensemble_layout.addWidget(ensemble_title)
        self.ensemble_status_badge = QLabel("未启用")
        self.ensemble_status_badge.setProperty("badge", True)
        ensemble_layout.addWidget(self.ensemble_status_badge, 0, Qt.AlignLeft)
        self.beijing_time_label = QLabel("北京时间：同步中...")
        self.beijing_time_label.setProperty("kpiValue", True)
        self.beijing_time_label.setWordWrap(True)
        self.clock_source_label = QLabel("校时来源：本地时间")
        self.clock_source_label.setWordWrap(True)
        self.clock_source_label.setProperty("muted", True)
        self.ensemble_status_label = QLabel("合奏状态：未启用")
        self.ensemble_status_label.setWordWrap(True)
        self.ensemble_status_label.setProperty("muted", True)
        target_label = QLabel("目标北京时间")
        target_label.setProperty("muted", True)
        self.ensemble_target_edit = QLineEdit()
        self.ensemble_target_edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS")
        quick_row = QHBoxLayout()
        self.ensemble_now_btn = QPushButton("现在")
        self.ensemble_plus10_btn = QPushButton("+10s")
        self.ensemble_plus30_btn = QPushButton("+30s")
        self.ensemble_plus60_btn = QPushButton("+1m")
        self.ensemble_now_btn.clicked.connect(lambda: self._set_ensemble_target(beijing_now(self.clock_offset_sec).replace(tzinfo=None)))
        self.ensemble_plus10_btn.clicked.connect(lambda: self._set_ensemble_target(beijing_now(self.clock_offset_sec).replace(tzinfo=None) + timedelta(seconds=10)))
        self.ensemble_plus30_btn.clicked.connect(lambda: self._set_ensemble_target(beijing_now(self.clock_offset_sec).replace(tzinfo=None) + timedelta(seconds=30)))
        self.ensemble_plus60_btn.clicked.connect(lambda: self._set_ensemble_target(beijing_now(self.clock_offset_sec).replace(tzinfo=None) + timedelta(minutes=1)))
        for btn in [self.ensemble_now_btn, self.ensemble_plus10_btn, self.ensemble_plus30_btn, self.ensemble_plus60_btn]:
            quick_row.addWidget(btn)
        self.ensemble_direct_start = QCheckBox("到点直接开播")
        self.ensemble_direct_start.setChecked(True)
        self.ensemble_auto_sync = QCheckBox("自动校时")
        self.ensemble_auto_sync.setChecked(True)
        opt_row = QHBoxLayout()
        opt_row.addWidget(self.ensemble_direct_start)
        opt_row.addWidget(self.ensemble_auto_sync)
        action_row = QHBoxLayout()
        self.ensemble_sync_btn = QPushButton("立即校时")
        self.ensemble_arm_btn = QPushButton("开始合奏")
        self.ensemble_arm_btn.setProperty("primary", True)
        self.ensemble_cancel_btn = QPushButton("取消")
        self.ensemble_sync_btn.clicked.connect(lambda: self._start_clock_sync(reason="手动校时"))
        self.ensemble_arm_btn.clicked.connect(self._arm_ensemble)
        self.ensemble_cancel_btn.clicked.connect(self._cancel_ensemble)
        action_row.addWidget(self.ensemble_sync_btn)
        action_row.addWidget(self.ensemble_arm_btn)
        action_row.addWidget(self.ensemble_cancel_btn)
        ensemble_layout.addWidget(self.beijing_time_label)
        ensemble_layout.addWidget(self.clock_source_label)
        ensemble_layout.addWidget(self.ensemble_status_label)
        ensemble_layout.addWidget(target_label)
        ensemble_layout.addWidget(self.ensemble_target_edit)
        ensemble_layout.addLayout(quick_row)
        ensemble_layout.addLayout(opt_row)
        ensemble_layout.addLayout(action_row)
        layout.addWidget(ensemble_card)

        self.log_card = Card()
        log_layout = QVBoxLayout(self.log_card)
        log_layout.setContentsMargins(12, 12, 12, 12)
        title_row = QHBoxLayout()
        log_title = QLabel("运行日志")
        log_title.setProperty("sectionTitle", True)
        title_row.addWidget(log_title)
        title_row.addStretch(1)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(lambda: self.log_output.setPlainText(""))
        title_row.addWidget(clear_btn)
        log_layout.addLayout(title_row)
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(1600)
        self.log_output.setMinimumHeight(170)
        log_layout.addWidget(self.log_output)
        layout.addWidget(self.log_card, 1)
        return sidebar

    def _apply_ui_settings(self, *, initial: bool = False) -> None:
        app = QApplication.instance()
        if app is None:
            return
        performance_mode = bool(getattr(self.ui_settings, "performance_mode", False))
        effective_animations = bool(self.ui_settings.animations_enabled) and not performance_mode
        effective_backdrop = (not bool(self.ui_settings.dark_mode)) and not performance_mode
        effective_glass_blur = 0 if performance_mode else int(getattr(self.ui_settings, 'glass_blur', 58))
        app.setProperty('uiDarkMode', bool(self.ui_settings.dark_mode))
        app.setProperty('uiThemePreset', getattr(self.ui_settings, "theme_preset", "ocean"))
        app.setProperty('uiPerformanceMode', performance_mode)
        app.setProperty('uiAnimationsEnabled', effective_animations)
        app.setProperty('uiAnimationSpeed', int(self.ui_settings.animation_speed))
        app.setProperty('uiDebugMode', bool(self.ui_settings.debug_mode))
        set_runtime_debug_mode(bool(self.ui_settings.debug_mode))
        app.setProperty('uiBackdropEnabled', effective_backdrop)
        app.setProperty('uiGlassBlur', effective_glass_blur)
        stylesheet = build_stylesheet(
            self.ui_settings.dark_mode,
            self.ui_settings.ui_scale,
            getattr(self.ui_settings, "theme_preset", "ocean"),
            backdrop_enabled=effective_backdrop,
        )
        self._last_applied_stylesheet = stylesheet
        app.setStyleSheet(stylesheet)
        font = app.font()
        if font.pointSizeF() <= 0:
            font = QFont('Microsoft YaHei UI')
        font.setFamilies(['Microsoft YaHei UI', 'Segoe UI Variable Text', 'PingFang SC', 'Noto Sans SC'])
        font.setPointSizeF(max(9.0, 10.2 * max(0.8, min(1.4, self.ui_settings.ui_scale / 100.0))))
        app.setFont(font)
        scale = max(0.8, min(1.4, self.ui_settings.ui_scale / 100.0))
        if hasattr(self, 'main_splitter'):
            self.main_splitter.setSizes([int(340 * scale), max(860, int(1040 * scale)), int(360 * scale)])
        if hasattr(self, 'piano_splitter'):
            self.piano_splitter.setSizes([int(290 * scale), int(185 * scale)])
        if hasattr(self, 'drum_content_split'):
            if performance_mode:
                self.drum_content_split.setSizes([max(700, int(760 * scale)), max(700, int(760 * scale))])
            else:
                self.drum_content_split.setSizes([max(900, int(980 * scale)), max(420, int(460 * scale))])
        if hasattr(self, 'left_sidebar'):
            self.left_sidebar.setMinimumWidth(int(310 * scale))
        if hasattr(self, 'right_sidebar'):
            self.right_sidebar.setMinimumWidth(int(340 * scale))
        if hasattr(self, '_log_flush_timer') and self._log_flush_timer is not None:
            self._log_flush_timer.setInterval(140 if performance_mode else 24)
        if hasattr(self, 'clock_timer') and self.clock_timer is not None:
            self.clock_timer.setInterval(120 if performance_mode else 100)
        if hasattr(self, 'background_surface') and self.background_surface is not None:
            self.background_surface.update()
        if hasattr(self, "log_output") and self.log_output is not None:
            if bool(self.ui_settings.debug_mode):
                self.log_output.setMaximumBlockCount(1800 if performance_mode else 2400)
            else:
                self.log_output.setMaximumBlockCount(700 if performance_mode else 1200)
        if hasattr(self, '_apply_performance_mode_widget_visibility'):
            self._apply_performance_mode_widget_visibility()
        if hasattr(self, '_apply_page_update_policy'):
            self._apply_page_update_policy()
        self._last_visual_update_at = 0.0
        self._last_clock_ui_update_at = 0.0
        if not initial:
            self.update()
            for w in [getattr(self, 'piano_roll', None), getattr(self, 'waveform', None), getattr(self, 'drum_roll', None), getattr(self, 'drum_waveform', None), getattr(self, 'log_output', None), getattr(self, 'background_surface', None)]:
                if w is not None:
                    w.update()

    def _performance_mode_enabled(self) -> bool:
        return bool(getattr(self.ui_settings, "performance_mode", False))

    def _apply_performance_mode_widget_visibility(self) -> None:
        performance_mode = self._performance_mode_enabled()
        scale = max(0.8, min(1.4, self.ui_settings.ui_scale / 100.0)) if hasattr(self, 'ui_settings') else 1.0
        if hasattr(self, 'piano_preview_card') and self.piano_preview_card is not None:
            self.piano_preview_card.setVisible(not performance_mode)
            self.piano_preview_card.setUpdatesEnabled(not performance_mode)
            self.piano_preview_card.setMaximumHeight(0 if performance_mode else 16777215)
            if hasattr(self, 'piano_lite_btn'):
                self.piano_lite_btn.setEnabled(not performance_mode)
            if hasattr(self, 'piano_detail_btn'):
                self.piano_detail_btn.setEnabled(not performance_mode)
        if hasattr(self, 'drum_preview_card') and self.drum_preview_card is not None:
            self.drum_preview_card.setVisible(not performance_mode)
            self.drum_preview_card.setUpdatesEnabled(not performance_mode)
            self.drum_preview_card.setMaximumHeight(0 if performance_mode else 16777215)
            if hasattr(self, 'drum_lite_btn'):
                self.drum_lite_btn.setEnabled(not performance_mode)
            if hasattr(self, 'drum_detail_btn'):
                self.drum_detail_btn.setEnabled(not performance_mode)
        if hasattr(self, 'piano_splitter') and self.piano_splitter is not None:
            if performance_mode:
                self.piano_splitter.setSizes([0, max(1, int(520 * scale))])
            else:
                self.piano_splitter.setSizes([int(290 * scale), int(185 * scale)])
        if hasattr(self, 'drum_mapping_preview_card') and self.drum_mapping_preview_card is not None:
            self.drum_mapping_preview_card.setMinimumHeight(int(400 * scale))
        if hasattr(self, 'drum_preview_tree') and self.drum_preview_tree is not None:
            self.drum_preview_tree.setMinimumHeight(int(360 * scale))

    def _apply_page_update_policy(self) -> None:
        performance_mode = self._performance_mode_enabled()
        page_index = {"piano": 0, "config": 1, "tuner": 2, "drum": 3}.get(self.current_mode, 0)
        if hasattr(self, 'pages') and self.pages is not None:
            for idx in range(self.pages.count()):
                page = self.pages.widget(idx)
                if page is not None:
                    page.setUpdatesEnabled((not performance_mode) or idx == page_index)
        visible_piano_widget = None
        visible_drum_widget = None
        if hasattr(self, 'piano_roll'):
            visible_piano_widget = self.piano_roll_detail if getattr(self, '_piano_preview_mode', 'lite') == 'detail' else self.piano_roll
            for widget in [self.piano_roll, getattr(self, 'piano_roll_detail', None), getattr(self, 'waveform', None), getattr(self, 'progress_slider', None), getattr(self, 'time_label', None)]:
                if widget is not None:
                    widget.setUpdatesEnabled((not performance_mode) or (self.current_mode == 'piano' and widget in {visible_piano_widget, getattr(self, 'waveform', None), getattr(self, 'progress_slider', None), getattr(self, 'time_label', None)}))
        if hasattr(self, 'drum_roll'):
            visible_drum_widget = self.drum_roll_detail if getattr(self, '_drum_preview_mode', 'lite') == 'detail' else self.drum_roll
            for widget in [self.drum_roll, getattr(self, 'drum_roll_detail', None), getattr(self, 'drum_waveform', None), getattr(self, 'drum_progress_slider', None), getattr(self, 'drum_time_label', None), getattr(self, 'drum_preview_tree', None)]:
                if widget is not None:
                    widget.setUpdatesEnabled((not performance_mode) or (self.current_mode == 'drum' and widget in {visible_drum_widget, getattr(self, 'drum_waveform', None), getattr(self, 'drum_progress_slider', None), getattr(self, 'drum_time_label', None), getattr(self, 'drum_preview_tree', None)}))

    def _sync_visible_playback_widgets(self) -> None:
        if not hasattr(self, 'transport'):
            return
        current_backend_kind = self._current_transport_backend_kind() if hasattr(self, 'transport') else 'piano'
        position_sec = float(getattr(self.transport, 'position_sec', 0.0) or 0.0)
        duration_sec = float(getattr(self.transport, 'duration_sec', 0.0) or 0.0)
        performance_mode = self._performance_mode_enabled()
        if self.current_mode == 'piano' and hasattr(self, 'waveform'):
            analysis = self.transport.analysis if current_backend_kind == 'piano' else self._analysis_for_backend_kind('piano')
            if not performance_mode:
                self.piano_roll.set_analysis(analysis)
                if getattr(self, '_piano_preview_mode', 'lite') == 'detail' and self.piano_roll_detail is not None:
                    self.piano_roll_detail.set_analysis(analysis)
                    self.piano_roll_detail.set_position(position_sec if current_backend_kind == 'piano' else 0.0)
            self.waveform.set_analysis(analysis)
            if current_backend_kind == 'piano' and duration_sec > 0:
                slider_value = int((position_sec / max(0.001, duration_sec)) * 1000)
                self.progress_slider.blockSignals(True)
                self.progress_slider.setValue(slider_value)
                self.progress_slider.blockSignals(False)
                self.time_label.setText(f"{self._format_time(position_sec)} / {self._format_time(duration_sec)}")
                if not performance_mode:
                    self.piano_roll.set_position(position_sec)
                self.waveform.set_position(position_sec)
            else:
                self.progress_slider.blockSignals(True)
                self.progress_slider.setValue(0)
                self.progress_slider.blockSignals(False)
                self.time_label.setText(f"00:00 / {self._format_time(analysis.duration_sec if analysis else 0.0)}")
        elif self.current_mode == 'drum' and hasattr(self, 'drum_waveform'):
            analysis = self.transport.analysis if current_backend_kind == 'drum' else self._analysis_for_backend_kind('drum')
            if not performance_mode:
                self.drum_roll.set_analysis(analysis)
                if getattr(self, '_drum_preview_mode', 'lite') == 'detail' and self.drum_roll_detail is not None:
                    self.drum_roll_detail.set_analysis(analysis)
                    self.drum_roll_detail.set_position(position_sec if current_backend_kind == 'drum' else 0.0)
            self.drum_waveform.set_analysis(analysis)
            if current_backend_kind == 'drum' and duration_sec > 0:
                slider_value = int((position_sec / max(0.001, duration_sec)) * 1000)
                self.drum_progress_slider.blockSignals(True)
                self.drum_progress_slider.setValue(slider_value)
                self.drum_progress_slider.blockSignals(False)
                self.drum_time_label.setText(f"{self._format_time(position_sec)} / {self._format_time(duration_sec)}")
                if not performance_mode:
                    self.drum_roll.set_position(position_sec)
                self.drum_waveform.set_position(position_sec)
            else:
                self.drum_progress_slider.blockSignals(True)
                self.drum_progress_slider.setValue(0)
                self.drum_progress_slider.blockSignals(False)
                self.drum_time_label.setText(f"00:00 / {self._format_time(analysis.duration_sec if analysis else 0.0)}")

    def _refresh_hotkey_labels(self) -> None:
        if hasattr(self, 'play_btn'):
            self.play_btn.setText(f"{_pretty_hotkey(self.ui_settings.play_hotkey)} 播放 / 继续")
        if hasattr(self, 'pause_btn'):
            self.pause_btn.setText(f"{_pretty_hotkey(self.ui_settings.pause_hotkey)} 暂停")
        if hasattr(self, 'stop_btn'):
            self.stop_btn.setText(f"{_pretty_hotkey(self.ui_settings.stop_hotkey)} 停止并回零")
        if hasattr(self, 'drum_play_btn'):
            self.drum_play_btn.setText(f"{_pretty_hotkey(self.ui_settings.play_hotkey)} 播放 / 继续")
        if hasattr(self, 'drum_pause_btn'):
            self.drum_pause_btn.setText(f"{_pretty_hotkey(self.ui_settings.pause_hotkey)} 暂停")
        if hasattr(self, 'drum_stop_btn'):
            self.drum_stop_btn.setText(f"{_pretty_hotkey(self.ui_settings.stop_hotkey)} 停止并回零")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.ui_settings, self, cache_size_provider=cache_size_bytes, clear_cache_callback=self._clear_local_cache)
        dialog.setStyleSheet(self._last_applied_stylesheet)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.ui_settings = dialog.settings
        save_path = save_ui_settings(None, self.ui_settings)
        self._apply_ui_settings()
        self._rebuild_shortcuts()
        self._refresh_hotkey_labels()
        self._refresh_global_hotkey_bindings()
        self._sync_visible_playback_widgets()
        self._log(f"界面设置已保存")

    def _current_transport_backend_kind(self) -> str:
        return "drum" if self.transport.backend is self.drum_backend else "piano"

    def _set_piano_preview_mode(self, mode: str) -> None:
        self._piano_preview_mode = "detail" if mode == "detail" else "lite"
        self.piano_lite_btn.setChecked(self._piano_preview_mode == "lite")
        self.piano_detail_btn.setChecked(self._piano_preview_mode == "detail")
        if self._piano_preview_mode == "detail":
            self._ensure_piano_detail_preview()
        target_index = 1 if self._piano_preview_mode == "detail" else 0
        self.piano_preview_stack.fade_to_index(target_index)
        self._apply_page_update_policy()
        if self._piano_preview_mode == "detail" and self.piano_roll_detail is not None:
            analysis = self.transport.analysis if self._current_transport_backend_kind() == "piano" else self._analysis_for_backend_kind("piano")
            self.piano_roll_detail.set_analysis(analysis)
            self.piano_roll_detail.set_position(self.transport.position_sec if analysis is not None else 0.0)
        self._sync_visible_playback_widgets()

    def _set_drum_preview_mode(self, mode: str) -> None:
        self._drum_preview_mode = "detail" if mode == "detail" else "lite"
        self.drum_lite_btn.setChecked(self._drum_preview_mode == "lite")
        self.drum_detail_btn.setChecked(self._drum_preview_mode == "detail")
        if self._drum_preview_mode == "detail":
            self._ensure_drum_detail_preview()
        target_index = 1 if self._drum_preview_mode == "detail" else 0
        self.drum_preview_stack.fade_to_index(target_index)
        self._apply_page_update_policy()
        if self._drum_preview_mode == "detail" and self.drum_roll_detail is not None:
            analysis = self.transport.analysis if self._current_transport_backend_kind() == "drum" else self._analysis_for_backend_kind("drum")
            self.drum_roll_detail.set_analysis(analysis)
            self.drum_roll_detail.set_position(self.transport.position_sec if analysis is not None else 0.0)
        self._sync_visible_playback_widgets()

    def _ensure_piano_detail_preview(self) -> None:
        if self.piano_roll_detail is not None:
            return
        detail = PianoRollWidget()
        self.piano_roll_detail = detail
        if getattr(self, '_piano_detail_placeholder', None) is not None:
            self.piano_preview_stack.removeWidget(self._piano_detail_placeholder)
            self._piano_detail_placeholder.setParent(None)
            self._piano_detail_placeholder = None
        self.piano_preview_stack.insertWidget(1, detail)

    def _ensure_drum_detail_preview(self) -> None:
        if self.drum_roll_detail is not None:
            return
        detail = DrumRollWidget()
        self.drum_roll_detail = detail
        if getattr(self, '_drum_detail_placeholder', None) is not None:
            self.drum_preview_stack.removeWidget(self._drum_detail_placeholder)
            self._drum_detail_placeholder.setParent(None)
            self._drum_detail_placeholder = None
        self.drum_preview_stack.insertWidget(1, detail)

    def _wire_transport(self) -> None:
        self.transport.position_changed.connect(self._update_position_ui)
        self.transport.state_changed.connect(self._update_state_ui)
        self.transport.analysis_changed.connect(self._apply_analysis_to_widgets)
        self.transport.log.connect(self._log)
        self.waveform.seek_requested.connect(self.transport.seek)

    def _setup_shortcuts(self) -> None:
        self.shortcut_objects: List[QShortcut] = []
        self._rebuild_shortcuts()

    def _rebuild_shortcuts(self) -> None:
        for sc in getattr(self, 'shortcut_objects', []):
            try:
                sc.setParent(None)
                sc.deleteLater()
            except Exception:
                pass
        self.shortcut_objects = []
        bindings = [
            (self.ui_settings.play_hotkey_enabled, self.ui_settings.play_hotkey, self._request_play_hotkey),
            (self.ui_settings.pause_hotkey_enabled, self.ui_settings.pause_hotkey, self._request_pause_hotkey),
            (self.ui_settings.stop_hotkey_enabled, self.ui_settings.stop_hotkey, self._request_stop_hotkey),
        ]
        for enabled, key, callback in bindings:
            if not enabled or not key:
                continue
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(callback)
            self.shortcut_objects.append(sc)

    def _setup_global_hotkeys(self) -> None:
        self.global_hotkey_timer = QTimer(self)
        self.global_hotkey_timer.setInterval(30)
        self.global_hotkey_timer.timeout.connect(self._poll_global_hotkeys)
        self._refresh_global_hotkey_bindings()
        self.global_hotkey_timer.start()

    def _refresh_global_hotkey_bindings(self) -> None:
        self._global_hotkey_bindings = [
            (_parse_hotkey_binding(self.ui_settings.play_hotkey), self._request_play_hotkey, self.ui_settings.play_hotkey_enabled),
            (_parse_hotkey_binding(self.ui_settings.pause_hotkey), self._request_pause_hotkey, self.ui_settings.pause_hotkey_enabled),
            (_parse_hotkey_binding(self.ui_settings.stop_hotkey), self._request_stop_hotkey, self.ui_settings.stop_hotkey_enabled),
        ]
        self._global_hotkey_state.clear()

    def _poll_global_hotkeys(self) -> None:
        if os.name != 'nt':
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
        except Exception:
            return
        for binding, callback, enabled in self._global_hotkey_bindings:
            if binding is None:
                continue
            vk_code, modifier_vks = binding
            pressed = False
            if enabled:
                main_pressed = bool(user32.GetAsyncKeyState(vk_code) & 0x8000)
                modifiers_pressed = True
                if modifier_vks:
                    modifiers_pressed = all(bool(user32.GetAsyncKeyState(mod_vk) & 0x8000) for mod_vk in modifier_vks)
                pressed = main_pressed and modifiers_pressed
            prev = self._global_hotkey_state.get(binding, False)
            if pressed and not prev:
                callback()
            self._global_hotkey_state[binding] = pressed

    def _hotkey_ready(self, channel: str) -> bool:
        now = time.monotonic()
        if now - self._hotkey_last_trigger.get(channel, 0.0) < 0.12:
            return False
        self._hotkey_last_trigger[channel] = now
        return True

    def _request_play_hotkey(self) -> None:
        if not self.ui_settings.play_hotkey_enabled:
            return
        if not self._hotkey_ready('play'):
            return
        self._play_transport()

    def _request_pause_hotkey(self) -> None:
        if not self.ui_settings.pause_hotkey_enabled:
            return
        if not self._hotkey_ready('pause'):
            return
        self.transport.pause()

    def _request_stop_hotkey(self) -> None:
        if not self.ui_settings.stop_hotkey_enabled:
            return
        if not self._hotkey_ready('stop'):
            return
        self.transport.stop()

    @staticmethod
    def _freeze_config_value(value):
        if isinstance(value, dict):
            return tuple(sorted((str(k), MainWindow._freeze_config_value(v)) for k, v in value.items()))
        if isinstance(value, (list, tuple, set)):
            return tuple(MainWindow._freeze_config_value(v) for v in value)
        return value

    def _current_config_signature(self) -> tuple:
        return tuple(sorted((str(k), self._freeze_config_value(v)) for k, v in self.runtime_config.items()))

    def _backend_kind_for_mode(self, mode: Optional[str] = None) -> str:
        return "drum" if (mode or self.current_mode) == "drum" else "piano"

    def _invalidate_analysis_cache(self) -> None:
        self._analysis_cache.clear()
        self._analysis_cache_source_id = id(self.current_analysis) if self.current_analysis is not None else None
        self._transport_refresh_key = None
        self._track_tree_render_key = None
        self._drum_report_render_key = None

    def _clear_local_cache(self) -> int:
        removed_bytes = int(cache_size_bytes() or 0)
        clear_disk_cache()
        self._analysis_cache.clear()
        self._analysis_cache_source_id = id(self.current_analysis) if self.current_analysis is not None else None
        self._transport_refresh_key = None
        self._drum_report_cache = None
        self._drum_report_cache_key = None
        self._drum_report_render_key = None
        for backend in (getattr(self, 'piano_backend', None), getattr(self, 'drum_backend', None)):
            if backend is not None and hasattr(backend, 'clear_runtime_caches'):
                try:
                    backend.clear_runtime_caches()
                except Exception:
                    pass
        if getattr(self, 'transport', None) is not None and getattr(self.transport, 'handle', None) is not None:
            try:
                self.transport.backend.invalidate_handle_snapshot(self.transport.handle)
            except Exception:
                pass
        self._refresh_playback_controls()
        self._log(f"缓存已清除：{format_cache_size(removed_bytes)}")
        return removed_bytes

    def _analysis_for_backend_kind(self, backend_kind: str, allow_build: bool = True) -> Optional[MidiAnalysisResult]:
        if self.current_analysis is None:
            return None
        source_id = id(self.current_analysis)
        if self._analysis_cache_source_id != source_id:
            self._analysis_cache.clear()
            self._analysis_cache_source_id = source_id
        if backend_kind == "drum":
            selected = set(self.selected_drum_tracks or set(self.current_analysis.recommended_drum_indexes))
        else:
            selected = set(self.selected_piano_tracks or set(self.current_analysis.recommended_track_indexes))
        if not selected:
            return self.current_analysis
        cache_key = (backend_kind, tuple(sorted(selected)))
        cached = self._analysis_cache.get(cache_key)
        if cached is None:
            if not allow_build:
                return None
            cached = filter_analysis(self.current_analysis, selected, use_gpu=bool(getattr(self.ui_settings, "gpu_acceleration", False)))
            self._analysis_cache[cache_key] = cached
        return cached

    def _schedule_drum_report_refresh(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is None or not getattr(analysis, 'notes', None):
            self._drum_report_cache_key = None
            self._drum_report_cache = None
            self._drum_report_render_key = None
            if hasattr(self, 'drum_summary_mode'):
                self.drum_summary_mode.setText("模式：未分析")
                self.drum_summary_hits.setText("原始击打：0 / 映射后击打：0")
                self.drum_summary_keys.setText("映射键统计：-")
                self.drum_summary_fallback.setText("上下文替代 / 回退：-")
                self.drum_summary_ignored.setText("忽略 / 裁剪：-")
                self.drum_preview_tree.clear()
            return
        report_key = (str(getattr(analysis, "analysis_cache_key", "") or getattr(analysis, "source_sha256", "") or id(analysis)), self._backend_config_signature)
        if report_key == self._drum_report_cache_key and self._drum_report_cache is not None:
            self._refresh_drum_report(analysis)
            return
        if hasattr(self, 'drum_summary_mode'):
            self.drum_summary_mode.setText("模式：分析中…")
            self.drum_summary_hits.setText("原始击打：正在后台计算 / 映射后击打：正在后台计算")
            self.drum_summary_keys.setText("映射键统计：后台计算中…")
            self.drum_summary_fallback.setText("上下文替代 / 回退：后台计算中…")
            self.drum_summary_ignored.setText("忽略 / 裁剪：后台计算中…")
        self._drum_report_task_token += 1
        token = self._drum_report_task_token
        config_snapshot = dict(self.runtime_config)
        config_signature = self._backend_config_signature
        def worker() -> None:
            try:
                temp_drum_backend = ModernDrumBackend()
                temp_drum_backend.update_config(config_snapshot)
                report = temp_drum_backend.build_plan_report(analysis)
                self.drum_report_ready.emit({
                    'token': token,
                    'analysis_id': id(analysis),
                    'analysis_cache_key': str(getattr(analysis, "analysis_cache_key", "") or getattr(analysis, "source_sha256", "") or ""),
                    'config_signature': config_signature,
                    'report': report,
                })
            except Exception as exc:
                self._threadsafe_backend_log(f"[DEBUG] 鼓报告后台计算失败：{exc}")
        threading.Thread(target=worker, daemon=True, name='DrumReportWorker').start()

    def _on_drum_report_ready(self, payload: object) -> None:
        data = dict(payload or {})
        if int(data.get('token', -1)) != int(getattr(self, '_drum_report_task_token', 0)):
            return
        analysis = self._analysis_for_backend_kind('drum', allow_build=False)
        if analysis is None:
            return
        payload_analysis_key = str(data.get('analysis_cache_key') or '')
        current_analysis_key = str(getattr(analysis, "analysis_cache_key", "") or getattr(analysis, "source_sha256", "") or '')
        if payload_analysis_key:
            if payload_analysis_key != current_analysis_key:
                return
        elif int(data.get('analysis_id', -1)) != id(analysis):
            return
        if data.get('config_signature') != self._backend_config_signature:
            return
        report = data.get('report')
        if report is None:
            return
        self._drum_report_cache = report
        self._drum_report_cache_key = (current_analysis_key or id(analysis), self._backend_config_signature)
        self._drum_report_render_key = None
        if self.current_mode == 'drum':
            self._refresh_drum_report(analysis)

    def _mode_tracks(self) -> List[TrackInfo]:
        if not self.current_analysis:
            return []
        if self.current_mode == "drum":
            result = [t for t in self.current_analysis.track_infos if t.note_count > 0 and (t.looks_like_drum or True)]
            drum_only = [t for t in result if t.looks_like_drum]
            return drum_only or result
        return [t for t in self.current_analysis.track_infos if t.note_count > 0 and not t.looks_like_drum] or [t for t in self.current_analysis.track_infos if t.note_count > 0]

    def _selected_track_set_for_mode(self) -> set[int]:
        return self.selected_drum_tracks if self.current_mode == "drum" else self.selected_piano_tracks

    def _recommended_track_set_for_mode(self) -> set[int]:
        if not self.current_analysis:
            return set()
        return set(self.current_analysis.recommended_drum_indexes if self.current_mode == "drum" else self.current_analysis.recommended_track_indexes)

    def _switch_mode(self, current: QListWidgetItem, _previous: Optional[QListWidgetItem]) -> None:
        mode = current.data(Qt.UserRole) if current else "piano"
        self.current_mode = mode
        if hasattr(self, 'pages'):
            self.pages.fade_to_index({"piano": 0, "config": 1, "tuner": 2, "drum": 3}.get(mode, 0))
        mode_text = current.text() if current else mode
        self._finish_switch_mode(mode_text)

    def _finish_switch_mode(self, mode_text: str) -> None:
        self._populate_track_tree(self._mode_tracks())
        self._schedule_transport_refresh(immediate=True)
        self._apply_page_update_policy()
        self._sync_visible_playback_widgets()
        self._sync_mode_cards()

    def _sync_mode_cards(self) -> None:
        state = self.transport.state.value if hasattr(self.transport.state, 'value') else str(self.transport.state)
        self._set_status_card(self.piano_mode_card, "当前模式", "钢琴 / 吉他 / 贝斯")
        self._set_status_card(self.drum_mode_card, "当前模式", "架子鼓")
        pretty, hint = self._display_state_meta(state)
        self._set_status_card(self.piano_state_card, "播放状态", pretty, hint)
        self._set_status_card(self.drum_state_card, "播放状态", pretty, hint)

    def _current_playback_visuals(self) -> tuple[str, str, str, str, bool]:
        if self._midi_load_inflight:
            return "读取中", "正在读取 MIDI", "MIDI 读取中…", "正在读取 MIDI", False
        if self.current_analysis is None:
            return "未载入", "请选择 MIDI", "请选择 MIDI", "尚未载入 MIDI", False
        backend_kind = self._backend_kind_for_mode()
        if self._track_refresh_pending:
            return "切换中", "轨道切换中", "等待轨道切换完成", "等待应用最新轨道选择", False
        if self._analysis_refresh_inflight.get(backend_kind) is not None:
            return "切换中", "轨道切换中", "等待轨道切换完成", "轨道切换中", False
        analysis = self._analysis_for_backend_kind(backend_kind, allow_build=False)
        if analysis is None:
            return "准备中", "准备中", "等待基础数据", "等待生成可播放分析结果", False
        if not getattr(analysis, 'notes', None):
            return "无音符", "无可播放音符", "当前轨道无音符", "当前轨道选择没有可播放音符", False
        backend = self.drum_backend if backend_kind == 'drum' else self.piano_backend
        stage = getattr(backend, 'playback_stage', lambda _a=None: 'full')(analysis)
        if backend.is_playback_ready(analysis):
            if stage == 'fast':
                return "快速就绪", "快速就绪", "快速播放", backend.playback_readiness_text(analysis), True
            return "完整就绪", "完整就绪", "播放 / 继续", backend.playback_readiness_text(analysis), True
        if stage == 'prewarm_full':
            return "完整预热", "完整预热中", "完整预热中…", backend.playback_readiness_text(analysis), False
        if stage == 'prewarm_fast':
            return "快速预热", "快速预热中", "快速预热中…", backend.playback_readiness_text(analysis), False
        if stage == 'pending':
            return "等待预热", "等待预热", "等待预热…", backend.playback_readiness_text(analysis), False
        if hasattr(self, '_playback_prewarm_timer') and self._playback_prewarm_timer.isActive():
            return "等待预热", "等待预热", "等待预热…", "等待预热启动", False
        return "准备中", "准备中", "等待就绪…", backend.playback_readiness_text(analysis), False

    def _active_plan_label(self) -> str:
        backend = self.transport.backend if getattr(self, 'transport', None) is not None else None
        handle = getattr(self.transport, 'handle', None) if getattr(self, 'transport', None) is not None else None
        analysis = getattr(self.transport, 'analysis', None) if getattr(self, 'transport', None) is not None else None
        if backend is None:
            return ""
        label = getattr(backend, 'active_plan_label', lambda *_args, **_kwargs: '')(handle, analysis)
        return str(label or "")

    def _display_state_meta(self, state: str) -> tuple[str, str]:
        badge, status_text, _button_text, reason, ready = self._current_playback_visuals()
        plan_label = self._active_plan_label()
        if state == "stopped":
            hint = reason or ("可立即播放" if ready else "等待就绪")
            return status_text, hint
        mapping = {
            "playing": (f"播放中（{plan_label}）" if plan_label else "播放中", "演奏进行中"),
            "paused": (f"已暂停（{plan_label}）" if plan_label else "已暂停", "可继续或回到起点"),
        }
        return mapping.get(state, (state, ""))

    def _set_status_card(self, card: Card, title: str, value: str, muted: str = "") -> None:
        labels = card.findChildren(QLabel)
        if len(labels) >= 2:
            labels[0].setText(title)
            labels[1].setText(value)
        if len(labels) >= 3:
            labels[2].setText(muted)

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 MIDI 文件", "", "MIDI Files (*.mid *.midi)")
        if not path:
            return
        self.file_path_edit.setText(path)
        self._load_midi(path)

    def _set_midi_loading(self, loading: bool, path: str = "") -> None:
        self._midi_load_inflight = bool(loading)
        if loading:
            self.state_badge.setText("正在后台分析 MIDI…")
        else:
            self._update_state_ui(self.transport.state.value if hasattr(self.transport.state, "value") else str(self.transport.state))
        widgets = [
            getattr(self, 'open_btn', None),
            getattr(self, 'play_btn', None), getattr(self, 'pause_btn', None), getattr(self, 'stop_btn', None),
            getattr(self, 'drum_play_btn', None), getattr(self, 'drum_pause_btn', None), getattr(self, 'drum_stop_btn', None),
            getattr(self, 'track_tree', None),
        ]
        for widget in widgets:
            if widget is not None:
                widget.setEnabled(not loading)
        if loading and path:
            self.summary_label.setText(f"{os.path.basename(path)}\n正在读取 MIDI 与生成基础数据…")
        self._refresh_playback_controls()

    def _load_midi(self, path: str) -> None:
        path = (path or '').strip()
        if not path:
            return
        if self.transport.state.value != "stopped":
            self.transport.stop()
        self._midi_load_token += 1
        token = self._midi_load_token
        pedal_threshold = int(self.runtime_config.get("PEDAL_ON_VALUE", 64))
        use_gpu = bool(getattr(self.ui_settings, 'gpu_acceleration', False))
        config_snapshot = dict(self.runtime_config)
        backend_info = resolve_compute_backend(use_gpu)
        backend_text = backend_info.summary_text
        backend_note = backend_info.detail_text
        self._set_midi_loading(True, path)
        self._log(f"开始后台分析 MIDI：{os.path.basename(path)} | 计算后端：{backend_text}")

        def worker() -> None:
            try:
                analysis = analyze_midi(path, pedal_threshold=pedal_threshold, use_gpu=use_gpu)
                piano_selected = set(analysis.recommended_track_indexes)
                drum_selected = set(analysis.recommended_drum_indexes)
                self.midi_loaded.emit({
                    'token': token,
                    'path': path,
                    'analysis': analysis,
                    'piano_selected': piano_selected,
                    'drum_selected': drum_selected,
                    'backend_text': backend_text,
                    'backend_note': backend_note,
                })
            except Exception as exc:
                self.midi_load_failed.emit({'token': token, 'path': path, 'message': str(exc)})

        threading.Thread(target=worker, daemon=True, name='MidiAnalysisWorker').start()

    def _on_midi_loaded(self, payload: object) -> None:
        data = dict(payload or {})
        if int(data.get('token', -1)) != self._midi_load_token:
            return
        path = str(data.get('path') or '')
        analysis = data.get('analysis')
        if analysis is None:
            self._set_midi_loading(False)
            return
        self.current_analysis = analysis
        self._invalidate_analysis_cache()
        self.selected_piano_tracks = set(data.get('piano_selected') or set())
        self.selected_drum_tracks = set(data.get('drum_selected') or set())
        self._populate_track_tree(self._mode_tracks())
        self._schedule_transport_refresh(immediate=True)
        bpm_text = f"{analysis.primary_bpm:.1f} BPM" + (" · 多段" if analysis.has_tempo_changes else "")
        self._set_status_card(self.piano_bpm_card, "当前 MIDI BPM", bpm_text, os.path.basename(path))
        self._set_status_card(self.drum_bpm_card, "当前 MIDI BPM", bpm_text, os.path.basename(path))
        backend_text = str(data.get('backend_text') or 'CPU')
        backend_note = str(data.get('backend_note') or '')
        summary_text = (
            f"{os.path.basename(path)}\n总时长 {analysis.duration_sec:.2f}s\n"
            f"可见音符 {len(analysis.notes)}\n钢琴 / 吉他 / 贝斯推荐 {len(analysis.recommended_track_indexes)} 轨 / 鼓推荐 {len(analysis.recommended_drum_indexes)} 轨\n"
            f"计算后端：{backend_text}"
        )
        if backend_note and backend_note not in backend_text and ("GPU" in backend_note or "CPU" in backend_note):
            summary_text += f"\n{backend_note}"
        self.summary_label.setText(summary_text)
        self._set_midi_loading(False)
        self._sync_mode_cards()
        self._refresh_playback_controls()
        self._log(f"已分析 MIDI：{os.path.basename(path)} | 计算后端：{data.get('backend_text', 'CPU')}")

    def _on_midi_load_failed(self, payload: object) -> None:
        data = dict(payload or {})
        if int(data.get('token', -1)) != self._midi_load_token:
            return
        self._set_midi_loading(False)
        message = str(data.get('message') or '未知错误')
        QMessageBox.critical(self, "MIDI 读取失败", message)
        self._log(f"读取失败：{message}")

    def _schedule_playback_prewarm(self, immediate: bool = False) -> None:
        if self.current_analysis is None or self.transport.state.value == 'playing':
            return
        if immediate:
            self._playback_prewarm_timer.stop()
            self._run_playback_prewarm()
            return
        self._playback_prewarm_timer.start()
        self._refresh_playback_controls()

    def _run_playback_prewarm(self) -> None:
        if self.current_analysis is None or self.transport.state.value == 'playing':
            return
        backend_kind = self._backend_kind_for_mode()
        analysis = self._analysis_for_backend_kind(backend_kind, allow_build=False)
        if analysis is None or not getattr(analysis, 'notes', None):
            self._refresh_playback_controls()
            return
        self.transport.ensure_prepared()
        if backend_kind == 'drum':
            self._schedule_drum_report_refresh(analysis)
        self._refresh_playback_controls()

    def _schedule_transport_refresh(self, immediate: bool = False) -> None:
        if immediate:
            self._flush_pending_transport_refresh(force=True)
            return
        self._track_refresh_pending = True
        if hasattr(self, '_track_refresh_timer'):
            self._track_refresh_timer.start()
        else:
            self._refresh_transport_for_mode()

    def _flush_pending_transport_refresh(self, force: bool = False) -> None:
        if hasattr(self, '_track_refresh_timer') and self._track_refresh_timer.isActive():
            self._track_refresh_timer.stop()
        was_pending = self._track_refresh_pending
        self._track_refresh_pending = False
        self._analysis_refresh_inflight: Dict[str, Optional[object]] = {'piano': None, 'drum': None}
        if force or was_pending or self.transport.analysis is None or self.transport.state.value == 'playing':
            self._refresh_transport_for_mode()

    def _play_transport(self) -> None:
        if getattr(self, '_track_refresh_pending', False):
            self._flush_pending_transport_refresh()
        ready, reason = self._current_playback_ready_state()
        if not ready:
            self._schedule_playback_prewarm(immediate=True)
            self._refresh_playback_controls()
            self._log(f"播放暂不可用：{reason}")
            return
        self.transport.play()

    def _populate_track_tree(self, tracks: List[TrackInfo]) -> None:
        selected = self._selected_track_set_for_mode()
        if not selected:
            selected.update(self._recommended_track_set_for_mode())
        render_key = (
            self._backend_kind_for_mode(),
            tuple((t.index, t.note_count, t.min_note, t.max_note, bool(t.looks_like_drum), t.name) for t in tracks),
            tuple(sorted(selected)),
        )
        if render_key == self._track_tree_render_key:
            return
        self.track_tree.blockSignals(True)
        self.track_tree.setUpdatesEnabled(False)
        self.track_tree.clear()
        for track in tracks:
            if track.note_count <= 0:
                note_range = "-"
            else:
                note_range = f"{midi_to_note_name(track.min_note)} ~ {midi_to_note_name(track.max_note)}"
            text = track.name + (" [鼓]" if track.looks_like_drum else "")
            item = QTreeWidgetItem([text, str(track.note_count), note_range])
            item.setData(0, Qt.UserRole, track.index)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if track.index in selected else Qt.Unchecked)
            self.track_tree.addTopLevelItem(item)
        self.track_tree.blockSignals(False)
        self.track_tree.setUpdatesEnabled(True)
        self._track_tree_render_key = render_key

    def _on_track_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        self._track_tree_render_key = None
        selected = self._selected_track_set_for_mode()
        track_index = int(item.data(0, Qt.UserRole))
        if item.checkState(0) == Qt.Checked:
            selected.add(track_index)
        else:
            selected.discard(track_index)
        if self.current_mode == "drum":
            drum_tracks = self.selected_drum_tracks or set(self.current_analysis.recommended_drum_indexes if self.current_analysis else [])
            self.drum_hint_label.setText(f"已选 {len(drum_tracks)} 轨")
        self._schedule_transport_refresh(immediate=False)

    def _select_recommended_tracks(self) -> None:
        self._track_tree_render_key = None
        selected = self._selected_track_set_for_mode()
        selected.clear()
        selected.update(self._recommended_track_set_for_mode())
        self._populate_track_tree(self._mode_tracks())
        self._schedule_transport_refresh(immediate=True)

    def _select_all_tracks(self) -> None:
        self._track_tree_render_key = None
        selected = self._selected_track_set_for_mode()
        selected.clear()
        selected.update({t.index for t in self._mode_tracks()})
        self._populate_track_tree(self._mode_tracks())
        self._schedule_transport_refresh(immediate=True)

    def _clear_tracks(self) -> None:
        self._track_tree_render_key = None
        self._selected_track_set_for_mode().clear()
        self._populate_track_tree(self._mode_tracks())
        self._schedule_transport_refresh(immediate=True)

    def _analysis_for_current_mode(self) -> Optional[MidiAnalysisResult]:
        return self._analysis_for_backend_kind(self._backend_kind_for_mode())

    def _refresh_transport_for_mode(self) -> None:
        self._apply_runtime_config_to_backends()
        backend_kind = self._backend_kind_for_mode()
        backend = self.drum_backend if backend_kind == "drum" else self.piano_backend
        self.transport.set_backend(backend)
        analysis = self._analysis_for_backend_kind(backend_kind)
        refresh_key = (backend_kind, id(analysis) if analysis is not None else None, self._backend_config_signature)
        if analysis is not None and refresh_key != self._transport_refresh_key:
            self.transport.set_analysis(analysis, prepare=False)
            self._transport_refresh_key = refresh_key
        if analysis is not None:
            if backend_kind == "piano":
                self.piano_range_label.setText(f"{midi_to_note_name(analysis.min_note)} ~ {midi_to_note_name(analysis.max_note)}")
                if (not self._performance_mode_enabled()) and self._piano_preview_mode == "detail" and self.piano_roll_detail is not None:
                    self.piano_roll_detail.set_analysis(analysis)
                    self.piano_roll_detail.set_position(self.transport.position_sec)
            else:
                drum_tracks = self.selected_drum_tracks or set(self.current_analysis.recommended_drum_indexes if self.current_analysis else [])
                self.drum_hint_label.setText(f"已选 {len(drum_tracks)} 轨")
                if (not self._performance_mode_enabled()) and self._drum_preview_mode == "detail" and self.drum_roll_detail is not None:
                    self.drum_roll_detail.set_analysis(analysis)
                    self.drum_roll_detail.set_position(self.transport.position_sec)
        if self.current_mode == "drum":
            if self.current_analysis is not None:
                drum_analysis = self._analysis_for_backend_kind("drum", allow_build=False)
                self._refresh_drum_report(drum_analysis)
            else:
                self._refresh_drum_report(None)
        self._schedule_playback_prewarm(immediate=False)

    def _current_playback_ready_state(self) -> tuple[bool, str]:
        _badge, _status_text, _button_text, reason, ready = self._current_playback_visuals()
        return ready, reason

    def _refresh_playback_controls(self) -> None:
        badge, status_text, button_core_text, reason, ready = self._current_playback_visuals()
        hotkey = _pretty_hotkey(self.ui_settings.play_hotkey)
        plan_label = self._active_plan_label()
        if self.transport.state.value == 'playing':
            play_text = f"{hotkey} 播放中（{plan_label}）" if plan_label else f"{hotkey} 播放中"
        elif self.transport.state.value == 'paused':
            play_text = f"{hotkey} 继续播放（{plan_label}）" if plan_label else f"{hotkey} 继续播放"
        elif ready:
            if button_core_text == '快速播放':
                play_text = f"{hotkey} 快速播放"
            else:
                play_text = f"{hotkey} 播放 / 继续"
        else:
            play_text = f"{hotkey} {button_core_text}"
        for btn in [getattr(self, 'play_btn', None), getattr(self, 'drum_play_btn', None)]:
            if btn is None:
                continue
            btn.setText(play_text)
            btn.setToolTip(reason)
            btn.setEnabled((not self._midi_load_inflight) and ready)
        for btn in [getattr(self, 'pause_btn', None), getattr(self, 'stop_btn', None), getattr(self, 'drum_pause_btn', None), getattr(self, 'drum_stop_btn', None)]:
            if btn is not None:
                btn.setEnabled(not self._midi_load_inflight)
        if self.transport.state.value == 'stopped' and not self._midi_load_inflight:
            self.state_badge.setText(badge)
            pretty, hint = self._display_state_meta('stopped')
            self._set_status_card(self.piano_state_card, "播放状态", pretty, hint)
            self._set_status_card(self.drum_state_card, "播放状态", pretty, hint)

    def _apply_analysis_to_widgets(self, analysis: MidiAnalysisResult) -> None:
        backend_kind = self._current_transport_backend_kind()
        self._last_position_slider_value = -1
        self._last_position_time_key = (None, None, None)
        self._last_visual_update_at = 0.0
        end_text = self._format_time(analysis.duration_sec)
        if self._performance_mode_enabled() and self.current_mode != backend_kind:
            return
        if backend_kind == "piano":
            if not self._performance_mode_enabled():
                self.piano_roll.set_analysis(analysis)
                if self._piano_preview_mode == "detail" and self.piano_roll_detail is not None:
                    self.piano_roll_detail.set_analysis(analysis)
                    self.piano_roll_detail.set_position(self.transport.position_sec)
            self.waveform.set_analysis(analysis)
            self.progress_slider.setValue(0)
            self.time_label.setText(f"00:00 / {end_text}")
            self.piano_range_label.setText(f"{midi_to_note_name(analysis.min_note)} ~ {midi_to_note_name(analysis.max_note)}")
        else:
            if not self._performance_mode_enabled():
                self.drum_roll.set_analysis(analysis)
                if self._drum_preview_mode == "detail" and self.drum_roll_detail is not None:
                    self.drum_roll_detail.set_analysis(analysis)
                    self.drum_roll_detail.set_position(self.transport.position_sec)
            self.drum_waveform.set_analysis(analysis)
            self.drum_progress_slider.setValue(0)
            self.drum_time_label.setText(f"00:00 / {end_text}")
            self.drum_hint_label.setText(f"已选 {len(self.selected_drum_tracks or set(analysis.recommended_drum_indexes))} 轨")
        self._refresh_playback_controls()

    def _update_position_ui(self, position_sec: float, duration_sec: float) -> None:
        slider_value = 0 if duration_sec <= 0 else int((position_sec / duration_sec) * 1000)
        whole_current = max(0, int(position_sec))
        whole_end = max(0, int(duration_sec))
        backend_kind = self._current_transport_backend_kind()
        performance_mode = self._performance_mode_enabled()
        if performance_mode and self.transport.state.value == 'playing':
            if self.current_mode != backend_kind:
                return
            now_perf = time.perf_counter()
            if position_sec < duration_sec and (now_perf - self._last_visual_update_at) < 0.05:
                return
            self._last_visual_update_at = now_perf
        if backend_kind == "piano":
            if slider_value != self._last_position_slider_value:
                self.progress_slider.blockSignals(True)
                self.progress_slider.setValue(slider_value)
                self.progress_slider.blockSignals(False)
            time_key = (backend_kind, whole_current, whole_end)
            if time_key != self._last_position_time_key:
                self.time_label.setText(f"{self._format_time(position_sec)} / {self._format_time(duration_sec)}")
                self._last_position_time_key = time_key
            if not performance_mode:
                self.piano_roll.set_position(position_sec)
            self.waveform.set_position(position_sec)
            if (not performance_mode) and self._piano_preview_mode == "detail":
                if self.piano_roll_detail is not None:
                    self.piano_roll_detail.set_position(position_sec)
        else:
            if slider_value != self._last_position_slider_value:
                self.drum_progress_slider.blockSignals(True)
                self.drum_progress_slider.setValue(slider_value)
                self.drum_progress_slider.blockSignals(False)
            time_key = (backend_kind, whole_current, whole_end)
            if time_key != self._last_position_time_key:
                self.drum_time_label.setText(f"{self._format_time(position_sec)} / {self._format_time(duration_sec)}")
                self._last_position_time_key = time_key
            if not performance_mode:
                self.drum_roll.set_position(position_sec)
            self.drum_waveform.set_position(position_sec)
            if (not performance_mode) and self._drum_preview_mode == "detail":
                if self.drum_roll_detail is not None:
                    self.drum_roll_detail.set_position(position_sec)
        self._last_position_slider_value = slider_value

    def _update_state_ui(self, state: str) -> None:
        if state == 'stopped':
            badge, pretty, _button_text, hint, _ready = self._current_playback_visuals()
            self.state_badge.setText(badge)
            self._set_status_card(self.piano_state_card, "播放状态", pretty, hint)
            self._set_status_card(self.drum_state_card, "播放状态", pretty, hint)
        else:
            plan_label = self._active_plan_label()
            if state == 'playing':
                self.state_badge.setText('播放中')
                pretty = f"播放中（{plan_label}）" if plan_label else '播放中'
                hint = '演奏进行中'
            elif state == 'paused':
                self.state_badge.setText('已暂停')
                pretty = f"已暂停（{plan_label}）" if plan_label else '已暂停'
                hint = '可继续或回到起点'
            else:
                self.state_badge.setText(state)
                pretty, hint = self._display_state_meta(state)
            self._set_status_card(self.piano_state_card, "播放状态", pretty, hint)
            self._set_status_card(self.drum_state_card, "播放状态", pretty, hint)
        self._refresh_playback_controls()

    def _on_slider_moved(self, value: int) -> None:
        if self.transport.duration_sec <= 0:
            return
        target = (value / 1000.0) * self.transport.duration_sec
        self.time_label.setText(f"{self._format_time(target)} / {self._format_time(self.transport.duration_sec)}")
        if not self._performance_mode_enabled():
            self.piano_roll.set_position(target)
        self.waveform.set_position(target)
        if (not self._performance_mode_enabled()) and self._piano_preview_mode == "detail":
            if self.piano_roll_detail is not None:
                self.piano_roll_detail.set_position(target)

    def _on_slider_released(self) -> None:
        if self.transport.duration_sec <= 0:
            return
        self.transport.seek((self.progress_slider.value() / 1000.0) * self.transport.duration_sec)

    def _on_drum_slider_moved(self, value: int) -> None:
        if self.transport.duration_sec <= 0:
            return
        target = (value / 1000.0) * self.transport.duration_sec
        self.drum_time_label.setText(f"{self._format_time(target)} / {self._format_time(self.transport.duration_sec)}")
        if not self._performance_mode_enabled():
            self.drum_roll.set_position(target)
        self.drum_waveform.set_position(target)
        if (not self._performance_mode_enabled()) and self._drum_preview_mode == "detail":
            if self.drum_roll_detail is not None:
                self.drum_roll_detail.set_position(target)

    def _on_drum_slider_released(self) -> None:
        if self.transport.duration_sec <= 0:
            return
        self.transport.seek((self.drum_progress_slider.value() / 1000.0) * self.transport.duration_sec)

    @staticmethod
    def _format_time(sec: float) -> str:
        whole = max(0, int(sec))
        minutes = whole // 60
        seconds = whole % 60
        return f"{minutes:02d}:{seconds:02d}"

    def _mark_runtime_config_dirty(self) -> None:
        self._runtime_config_dirty = True

    def _apply_runtime_config_to_backends(self) -> None:
        if not self._runtime_config_dirty and self._backend_config_signature is not None:
            return
        signature = self._current_config_signature()
        changed = signature != self._backend_config_signature
        if changed:
            self.piano_backend.update_config(self.runtime_config)
            self.drum_backend.update_config(self.runtime_config)
            self._backend_config_signature = signature
            self._transport_refresh_key = None
            self._drum_report_render_key = None
            self._drum_report_cache_key = None
            self._drum_report_cache = None
            self._log(f"[DEBUG] 按键注入后端：钢琴/吉他/贝斯={self.piano_backend.input_backend_label()} | 架子鼓={self.drum_backend.input_backend_label()}")
        self._runtime_config_dirty = False
        title = str(self.runtime_config.get("GUI_TITLE", "SayaTech MIDI 自动弹奏")) + " · Modern"
        self.setWindowTitle(title)

    def _load_drum_config_widgets(self) -> None:
        for key, widget in self.drum_param_widgets.items():
            value = self.runtime_config.get(key)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))

    def _collect_drum_config_from_panel(self) -> Dict[str, object]:
        config = dict(self.runtime_config)
        for key, widget in self.drum_param_widgets.items():
            if isinstance(widget, QCheckBox):
                config[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                config[key] = int(widget.value())
            elif isinstance(widget, QDoubleSpinBox):
                config[key] = float(widget.value())
        return config

    def _apply_drum_params_panel(self) -> None:
        self.runtime_config = self._collect_drum_config_from_panel()
        self._mark_runtime_config_dirty()
        self._apply_runtime_config_to_backends()
        self._schedule_transport_refresh(immediate=True)
        self._log("已应用鼓模式独立参数。")

    def _reload_drum_params_panel(self) -> None:
        self.runtime_config = load_config(self.config_path)
        self._mark_runtime_config_dirty()
        self._load_config_into_form()
        self._load_drum_config_widgets()
        self._apply_runtime_config_to_backends()
        self._schedule_transport_refresh(immediate=True)
        self._log("已重读鼓模式参数。")

    def _save_drum_params_panel(self) -> None:
        self.runtime_config = self._collect_drum_config_from_panel()
        self._mark_runtime_config_dirty()
        save_config(self.config_path, self.runtime_config)
        self._apply_runtime_config_to_backends()
        self._schedule_transport_refresh(immediate=True)
        self._log(f"已保存鼓模式参数到 {self.config_path}")

    def _refresh_drum_report(self, analysis: Optional[MidiAnalysisResult]) -> None:
        if analysis is None or not analysis.notes:
            self._drum_report_cache_key = None
            self._drum_report_cache = None
            self._drum_report_render_key = None
            self.drum_summary_mode.setText("模式：未分析")
            self.drum_summary_hits.setText("原始击打：0 / 映射后击打：0")
            self.drum_summary_keys.setText("映射键统计：-")
            self.drum_summary_fallback.setText("上下文替代 / 回退：-")
            self.drum_summary_ignored.setText("忽略 / 裁剪：-")
            self.drum_preview_tree.clear()
            return
        report_key = (str(getattr(analysis, "analysis_cache_key", "") or getattr(analysis, "source_sha256", "") or id(analysis)), self._backend_config_signature)
        if report_key != self._drum_report_cache_key or self._drum_report_cache is None:
            self._schedule_drum_report_refresh(analysis)
            return
        if report_key == self._drum_report_render_key and self._drum_report_cache is not None:
            return
        report = self._drum_report_cache
        self.drum_summary_mode.setText(f"模式：{report.selected_mode}")
        self.drum_summary_hits.setText(f"原始击打：{report.total_source_hits} / 映射后击打：{report.total_mapped_hits}")
        mapped = "、".join(f"{self.drum_backend.KEY_NAMES.get(k, k)}×{v}" for k, v in report.mapped_counter[:6]) or "-"
        fallback = "、".join(f"{k}×{v}" for k, v in report.fallback_counter[:4]) or "-"
        ignored = "、".join(f"{k}×{v}" for k, v in report.ignored_counter[:4]) or "-"
        self.drum_summary_keys.setText(f"映射键统计：{mapped}")
        self.drum_summary_fallback.setText(f"上下文替代 / 回退：{fallback}")
        self.drum_summary_ignored.setText(f"忽略 / 裁剪：{ignored}")
        self.drum_preview_tree.clear()
        for note_name, count, mapped_name, remark in report.preview_rows:
            self.drum_preview_tree.addTopLevelItem(QTreeWidgetItem([note_name, str(count), mapped_name, remark]))
        self._drum_report_render_key = report_key

    def _load_config_into_form(self) -> None:
        for spec in SUPPORTED_FIELDS:
            widget = self.config_widgets.get(spec.key)
            if widget is None:
                continue
            value = self.runtime_config.get(spec.key)
            if isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                index = widget.findText(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QLineEdit):
                if spec.kind == "note":
                    widget.setText(cfg_midi_to_note_name(int(value)))
                elif spec.key == "KEYMAP" and isinstance(value, list):
                    widget.setText(",".join(value))
                else:
                    widget.setText(str(value))
        self._load_drum_config_widgets()

    def _collect_config_from_form(self) -> Dict[str, object]:
        config = dict(self.runtime_config)
        for spec in SUPPORTED_FIELDS:
            widget = self.config_widgets.get(spec.key)
            if widget is None:
                continue
            if isinstance(widget, QCheckBox):
                config[spec.key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                config[spec.key] = widget.currentText().strip()
            elif isinstance(widget, QSpinBox):
                config[spec.key] = int(widget.value())
            elif isinstance(widget, QDoubleSpinBox):
                config[spec.key] = float(widget.value())
            elif isinstance(widget, QLineEdit):
                raw = widget.text().strip()
                if spec.kind == "note":
                    from .config_io import note_name_to_midi
                    config[spec.key] = note_name_to_midi(raw or "C3")
                elif spec.key == "KEYMAP":
                    config[spec.key] = [x.strip() for x in raw.split(",") if x.strip()]
                else:
                    config[spec.key] = raw
        config["VISIBLE_OCTAVES"] = 3
        if str(config.get("INSTRUMENT_MODE", "钢琴")).strip() in {"贝斯", "bass", "BASS"}:
            config["LEFTMOST_NOTE"] = 12
        return config

    def _apply_config_from_form(self) -> None:
        try:
            new_config = self._collect_config_from_form()
        except Exception as exc:
            QMessageBox.critical(self, "参数解析失败", str(exc))
            return
        old_pedal = int(self.runtime_config.get("PEDAL_ON_VALUE", 64))
        new_pedal = int(new_config.get("PEDAL_ON_VALUE", 64))
        self.runtime_config = new_config
        self._mark_runtime_config_dirty()
        if hasattr(self, "tuner_instrument_combo"):
            instrument_text = str(self.runtime_config.get("INSTRUMENT_MODE", "钢琴"))
            idx = max(0, self.tuner_instrument_combo.findText(instrument_text))
            self.tuner_instrument_combo.setCurrentIndex(idx)
        self._apply_runtime_config_to_backends()
        if self.current_analysis and self.file_path_edit.text().strip() and new_pedal != old_pedal:
            self._load_midi(self.file_path_edit.text().strip())
        else:
            self._schedule_transport_refresh(immediate=True)
        self._log("已应用当前配置到运行中。")

    def _reload_config_from_disk(self) -> None:
        old_pedal = int(self.runtime_config.get("PEDAL_ON_VALUE", 64))
        self.runtime_config = load_config(self.config_path)
        self._mark_runtime_config_dirty()
        self._load_config_into_form()
        if hasattr(self, "tuner_instrument_combo"):
            idx = max(0, self.tuner_instrument_combo.findText(str(self.runtime_config.get("INSTRUMENT_MODE", "钢琴"))))
            self.tuner_instrument_combo.setCurrentIndex(idx)
        self._apply_runtime_config_to_backends()
        new_pedal = int(self.runtime_config.get("PEDAL_ON_VALUE", 64))
        if self.current_analysis and self.file_path_edit.text().strip() and new_pedal != old_pedal:
            self._load_midi(self.file_path_edit.text().strip())
        else:
            self._schedule_transport_refresh(immediate=True)
        self._log("已从 config.txt 重新读取。")

    def _save_config_to_disk(self) -> None:
        try:
            self.runtime_config = self._collect_config_from_form()
            self._mark_runtime_config_dirty()
            save_config(self.config_path, self.runtime_config)
            self._apply_runtime_config_to_backends()
            self._log(f"已保存到 {self.config_path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def _generate_tuner_suggestions(self) -> None:
        if self._tuner_inflight:
            self._log("自动调参仍在运行，请等当前任务完成。")
            return
        if not self.current_analysis:
            QMessageBox.information(self, "提示", "请先载入 MIDI 再自动调参。")
            return
        try:
            playable_min = note_name_to_midi(self.tuner_min_note_edit.text().strip())
            playable_max = note_name_to_midi(self.tuner_max_note_edit.text().strip())
        except Exception as exc:
            QMessageBox.warning(self, "区间输入无效", f"可弹奏区间格式不正确：{exc}")
            return
        if playable_min > playable_max:
            QMessageBox.warning(self, "区间输入无效", "可弹奏最低音不能高于最高音。")
            return
        analysis = self._analysis_for_current_mode() or self.current_analysis
        current_config = dict(self.runtime_config)
        if hasattr(self, "tuner_instrument_combo"):
            current_config["INSTRUMENT_MODE"] = self.tuner_instrument_combo.currentText()
        self._tuner_inflight = True
        self.tuner_generate_btn.setEnabled(False)
        self.tuner_apply_btn.setEnabled(False)
        self.tuner_output.setPlainText("正在后台自动调参…\n你可以继续切换页面或查看预览。")
        self.tuner_preview.setPlainText("正在计算建议参数，请稍候…")
        self._log("自动调参已转到后台线程：先快速预筛，再做少量全量评分。")

        def worker() -> None:
            try:
                suggestions, report = suggest_config(
                    analysis,
                    current_config,
                    (playable_min, playable_max),
                    use_gpu=bool(getattr(self.ui_settings, "gpu_acceleration", False)),
                )
                lines = preview_lines(suggestions)
                preview_text = "<br>".join(lines) if lines else "本次没有生成可预览参数。"
                self.tuner_finished.emit(suggestions, report, preview_text)
            except Exception as exc:
                self.tuner_failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_tuner_finished(self, suggestions: object, report: str, preview_text: str) -> None:
        self._tuner_inflight = False
        self.tuner_generate_btn.setEnabled(True)
        self.tuner_apply_btn.setEnabled(True)
        self.tuner_suggestions = dict(suggestions or {})
        self.tuner_output.setHtml(report)
        self.tuner_preview.setHtml("<div style='white-space:pre-wrap; line-height:1.55;'>" + preview_text + "</div>")
        self._log("已完成后台自动调参并生成建议。")

    def _on_tuner_failed(self, message: str) -> None:
        self._tuner_inflight = False
        self.tuner_generate_btn.setEnabled(True)
        self.tuner_apply_btn.setEnabled(True)
        self.tuner_output.setPlainText(f"自动调参失败：{message}")
        self.tuner_preview.setPlainText("本次没有生成可预览参数。")
        QMessageBox.warning(self, "自动调参失败", message)

    def _apply_tuner_suggestions(self) -> None:
        if not self.tuner_suggestions:
            QMessageBox.information(self, "提示", "请先生成建议参数。")
            return
        self.runtime_config.update(self.tuner_suggestions)
        self._mark_runtime_config_dirty()
        if hasattr(self, "tuner_instrument_combo"):
            instrument_text = str(self.runtime_config.get("INSTRUMENT_MODE", self.tuner_instrument_combo.currentText()))
            idx = max(0, self.tuner_instrument_combo.findText(instrument_text))
            self.tuner_instrument_combo.setCurrentIndex(idx)
        if "UNLOCKED_MIN_NOTE" in self.tuner_suggestions:
            self.tuner_min_note_edit.setText(cfg_midi_to_note_name(int(self.tuner_suggestions["UNLOCKED_MIN_NOTE"])))
        if "UNLOCKED_MAX_NOTE" in self.tuner_suggestions:
            self.tuner_max_note_edit.setText(cfg_midi_to_note_name(int(self.tuner_suggestions["UNLOCKED_MAX_NOTE"])))
        self._load_config_into_form()
        if hasattr(self, 'nav_list') and self.nav_list is not None:
            row = -1
            for i in range(self.nav_list.count()):
                item = self.nav_list.item(i)
                if item and item.data(Qt.UserRole) == "config":
                    row = i
                    break
            if row >= 0:
                self.nav_list.blockSignals(True)
                self.nav_list.setCurrentRow(row)
                self.nav_list.blockSignals(False)
        if hasattr(self, 'pages') and self.pages is not None:
            fade_to = getattr(self.pages, 'fade_to_index', None)
            if callable(fade_to):
                fade_to(1)
            else:
                self.pages.setCurrentIndex(1)
        self.current_mode = "config"
        self._sync_mode_cards()
        self._apply_runtime_config_to_backends()
        self._log("已将自动调参建议回填到配置页。")

    def _set_ensemble_target(self, dt: datetime) -> None:
        self.ensemble_target = dt.replace(microsecond=0)
        self.ensemble_target_edit.setText(self.ensemble_target.strftime("%Y-%m-%d %H:%M:%S"))
        if not self.ensemble_active:
            self.ensemble_status_label.setText("合奏状态：未启用")
            self.ensemble_status_badge.setText("未启用")

    def _parse_ensemble_target(self) -> Optional[datetime]:
        text = self.ensemble_target_edit.text().strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError("目标时间格式应为 YYYY-MM-DD HH:MM:SS")

    def _arm_ensemble(self) -> None:
        now_mono = time.monotonic()
        if now_mono < self._ensemble_arm_debounce_until:
            return
        self._ensemble_arm_debounce_until = now_mono + 0.5

        if self.ensemble_active:
            self._log("合奏已处于准备状态，无需重复开始。")
            return
        if not self.current_analysis:
            QMessageBox.information(self, "提示", "请先载入 MIDI 再开始合奏。")
            return
        try:
            target = self._parse_ensemble_target()
        except Exception as exc:
            QMessageBox.critical(self, "时间格式错误", str(exc))
            return
        if target is None:
            QMessageBox.information(self, "提示", "请先填写目标北京时间。")
            return
        now = beijing_now(self.clock_offset_sec).replace(tzinfo=None)
        if target <= now:
            self.ensemble_active = False
            self.ensemble_fired = False
            self.ensemble_status_label.setText("合奏状态：目标时间已过，请重新设置")
            self.ensemble_status_badge.setText("时间已过")
            self._log("合奏未启用：目标时间已经过去，请重新设置。")
            return
        self.ensemble_target = target
        self.ensemble_active = True
        self.ensemble_fired = False
        self.ensemble_status_label.setText(f"合奏状态：已准备，目标 {target.strftime('%Y-%m-%d %H:%M:%S')}")
        self.ensemble_status_badge.setText("已准备")
        self._log(f"合奏已准备：{target.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）")

    def _cancel_ensemble(self) -> None:
        self.ensemble_active = False
        self.ensemble_fired = False
        self.ensemble_status_label.setText("合奏状态：已取消")
        self.ensemble_status_badge.setText("已取消")
        self._log("已取消单次合奏。")

    def _start_clock_sync(self, *, reason: str) -> None:
        if self._clock_sync_inflight:
            return
        self._clock_sync_inflight = True
        self.clock_status_text = f"{reason}中..."
        self.clock_source_label.setText(f"校时来源：{self.clock_source_text} | {self.clock_status_text}")

        def worker() -> None:
            offset, source, status = sync_beijing_clock()
            self.clock_sync_finished.emit(offset, source, status)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_clock_sync(self, offset: float, source: str, status: str) -> None:
        self.clock_offset_sec = offset
        self.clock_source_text = source
        self.clock_status_text = status
        self._clock_sync_inflight = False
        self.clock_source_label.setText(f"校时来源：{self.clock_source_text} | {self.clock_status_text}")
        self._log(f"北京时间校时：{source} | {status}")

    def _maybe_auto_sync(self) -> None:
        if getattr(self, 'ensemble_auto_sync', None) is not None and self.ensemble_auto_sync.isChecked():
            self._start_clock_sync(reason="自动校时")

    def _trigger_ensemble_start(self) -> None:
        if self.ensemble_fired:
            return
        self.ensemble_fired = True
        self.ensemble_active = False
        delay_sec = 0.0 if self.ensemble_direct_start.isChecked() else float(self.runtime_config.get("START_DELAY", 3.0))
        if self.transport.state.value != "stopped":
            self.transport.stop()
        self.transport.seek(0.0)
        if delay_sec <= 0:
            self._play_transport()
            self.ensemble_status_label.setText("合奏状态：已触发，已开始播放")
            self.ensemble_status_badge.setText("已触发")
            self._log("合奏已触发：到点立即开播。")
        else:
            self.ensemble_status_label.setText(f"合奏状态：已触发，将在 {delay_sec:.1f}s 后开始")
            self.ensemble_status_badge.setText("即将开始")
            self._log(f"合奏已触发：将在 {delay_sec:.1f}s 后开始播放。")
            QTimer.singleShot(int(delay_sec * 1000), self._play_transport)

    def _tick_clock(self) -> None:
        now = beijing_now(self.clock_offset_sec).replace(tzinfo=None)
        performance_mode = self._performance_mode_enabled()
        should_render = True
        if performance_mode and self.transport.state.value == 'playing':
            now_perf = time.perf_counter()
            should_render = (now_perf - self._last_clock_ui_update_at) >= 0.25
            if should_render:
                self._last_clock_ui_update_at = now_perf
        if should_render:
            clock_text = "北京时间：" + now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            source_text = f"校时来源：{self.clock_source_text} | {self.clock_status_text}"
            if (clock_text, source_text) != self._last_clock_render_payload:
                self.beijing_time_label.setText(clock_text)
                self.clock_source_label.setText(source_text)
                self._last_clock_render_payload = (clock_text, source_text)
        if self.ensemble_active and self.ensemble_target:
            delta = (self.ensemble_target - now).total_seconds()
            if delta > 0:
                if should_render:
                    minutes = int(delta) // 60
                    seconds = int(delta) % 60
                    millis = int((delta - int(delta)) * 1000)
                    self.ensemble_status_label.setText(
                        f"合奏状态：已准备 | 目标 {self.ensemble_target.strftime('%Y-%m-%d %H:%M:%S')} | 剩余 {minutes:02d}:{seconds:02d}.{millis:03d}"
                    )
                    self.ensemble_status_badge.setText("倒计时中")
            else:
                self._trigger_ensemble_start()
        elif not self.ensemble_fired and not self.ensemble_active and should_render:
            self.ensemble_status_label.setText("合奏状态：未启用")
            self.ensemble_status_badge.setText("未启用")
        if self.transport.state.value != 'playing' or should_render:
            self._refresh_playback_controls()

    def _is_debug_enabled(self) -> bool:
        return bool(getattr(self.ui_settings, "debug_mode", False))

    @staticmethod
    def _split_runtime_line(text: str) -> tuple[str, bool]:
        prefix = "[DEBUG] "
        if text.startswith(prefix):
            return text[len(prefix):], True
        return text, False

    def _is_verbose_runtime_line(self, text: str) -> bool:
        return text.startswith((
            "可弹区间",
            "当前按键注入后端：",
            "按键注入后端：",
            "钢琴/吉他/贝斯开始播放：",
            "鼓开始播放：",
            "鼓命中：",
            "切换可弹区间到",
            "启动播放线程",
            "请求停止旧播放线程",
            "旧播放线程已退出",
            "钢琴/吉他/贝斯播放线程结束",
            "鼓播放线程结束",
            "已切换工作台",
            "已装载时间轴",
            "用户数据目录：",
            "运行日志：",
            "界面设置已保存",
            "缓存已清除：",
            "北京时间校时：",
            "自动调参已转到后台线程：",
            "开始后台分析 MIDI：",
            "已分析 MIDI：",
        ))

    def _threadsafe_backend_log(self, text: str) -> None:
        clean_text, is_debug_line = self._split_runtime_line(text)
        if is_debug_line and not self._is_debug_enabled():
            return
        if self._is_verbose_runtime_line(clean_text) and not self._is_debug_enabled():
            return
        self.backend_log_signal.emit(clean_text)

    def _flush_pending_logs(self) -> None:
        if not self._pending_log_lines:
            return
        lines = self._pending_log_lines
        self._pending_log_lines = []
        try:
            with runtime_log_path().open('a', encoding='utf-8') as f:
                stamp = datetime.now
                for line in lines:
                    f.write(f"[{stamp().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}] {line}\n")
        except Exception:
            pass
        if hasattr(self, 'log_output') and self.log_output is not None:
            self.log_output.appendPlainText("\n".join(lines))
            self.log_output.verticalScrollBar().setValue(self.log_output.verticalScrollBar().maximum())

    def _log(self, text: str) -> None:
        clean_text, is_debug_line = self._split_runtime_line(text)
        if is_debug_line and not self._is_debug_enabled():
            return
        if self._is_verbose_runtime_line(clean_text) and not self._is_debug_enabled():
            return
        self._pending_log_lines.append(clean_text)
        if hasattr(self, '_log_flush_timer') and self._log_flush_timer is not None:
            if not self._log_flush_timer.isActive():
                self._log_flush_timer.start()
        else:
            self._flush_pending_logs()
