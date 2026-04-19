from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from .theme import _palette


class CircularStatusIndicator(QWidget):
    """Status badge with rounded button-like appearance."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.text_value = text
        self._pulse_scale = 1.0
        self.status_color = QColor("#5b9eff")
        self.setMinimumHeight(32)
        self.setMaximumHeight(32)

        self._pulse_anim = QPropertyAnimation(self, b"pulse_scale", self)
        self._pulse_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_anim.setDuration(2000)
        self._pulse_anim.setStartValue(1.0)
        self._pulse_anim.setEndValue(1.08)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._start_pulse)
        self._pulse_timer.setInterval(2500)

    def _get_pulse_scale(self) -> float:
        return self._pulse_scale

    def _set_pulse_scale(self, value: float) -> None:
        self._pulse_scale = float(value)
        self.update()

    pulse_scale = Property(float, _get_pulse_scale, _set_pulse_scale)

    def setText(self, text: str) -> None:
        self.text_value = text
        self.update()

    def setStatusColor(self, color: QColor | str) -> None:
        if isinstance(color, str):
            self.status_color = QColor(color)
        else:
            self.status_color = color
        self.update()

    def _start_pulse(self) -> None:
        if self._pulse_anim.state() == QPropertyAnimation.Stopped:
            self._pulse_anim.start()

    def start_pulse(self) -> None:
        self._pulse_timer.start()
        self._start_pulse()

    def stop_pulse(self) -> None:
        self._pulse_timer.stop()
        self._pulse_anim.stop()
        self._pulse_scale = 1.0
        self.update()

    def sizeHint(self):
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        text_width = fm.horizontalAdvance(self.text_value) if self.text_value else 0
        return self.minimumSizeHint() if text_width == 0 else self.size()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        app = QApplication.instance()
        is_dark = bool(app.property("uiDarkMode")) if app else False
        preset = str(app.property("uiThemePreset") or "ocean") if app else "ocean"
        palette = _palette(is_dark, preset)

        rect = self.rect()
        padding = 8
        radius = 6

        # Draw rounded button background
        bg_color = QColor(self.status_color)
        bg_color.setAlpha(40)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(rect.adjusted(0, 2, 0, -2), radius, radius)

        # Draw border
        border_color = QColor(self.status_color)
        border_color.setAlpha(120)
        painter.setPen(QPen(border_color, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0, 2, 0, -2), radius, radius)

        # Draw text
        if self.text_value:
            text_color = QColor(palette["text"])
            painter.setPen(text_color)
            painter.setFont(self.font())
            painter.drawText(rect, Qt.AlignVCenter | Qt.AlignHCenter, self.text_value)


class StatusBadgeRow(QWidget):
    """Horizontal row of circular status indicators."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.indicators = {}

    def add_indicator(self, key: str, text: str, color: str = "#5b9eff") -> CircularStatusIndicator:
        from PySide6.QtWidgets import QHBoxLayout

        indicator = CircularStatusIndicator(text, self)
        indicator.setStatusColor(color)

        layout = self.layout()
        layout.addWidget(indicator)
        self.indicators[key] = indicator

        return indicator

    def update_indicator(self, key: str, text: str, color: Optional[str] = None) -> None:
        if key in self.indicators:
            self.indicators[key].setText(text)
            if color:
                self.indicators[key].setStatusColor(color)

    def start_pulse(self, key: str) -> None:
        if key in self.indicators:
            self.indicators[key].start_pulse()

    def stop_pulse(self, key: str) -> None:
        if key in self.indicators:
            self.indicators[key].stop_pulse()


class EnhancedStatusLabel(QLabel):
    """Enhanced status label with better visual hierarchy."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setProperty("badge", True)
        self._glow_effect = None
        self._setup_glow()

    def _setup_glow(self) -> None:
        from PySide6.QtWidgets import QGraphicsDropShadowEffect

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(8)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 40))
        self.setGraphicsEffect(shadow)

    def set_variant(self, variant: str) -> None:
        """Set status variant: success, error, warning, info."""
        self.setProperty("variant", variant)
        self.style().polish(self)
