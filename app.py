from __future__ import annotations

import multiprocessing
import os
import sys

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt, qInstallMessageHandler
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from sayatech_modern.crash_logging import append_runtime_log, install_global_hooks, write_crash_log
from sayatech_modern.config_io import ensure_config_file, load_config
from sayatech_modern.main_window import APP_ICON_PATH, MainWindow, SPLASH_IMAGE_PATH
from sayatech_modern.system_utils import is_admin, relaunch_as_admin
from sayatech_modern.ui_settings import load_ui_settings


_QT_HANDLER = None


def _qt_message_filter(_mode, _context, message):
    if "QFont::setPointSize: Point size <= 0 (-1), must be greater than 0" in message:
        return
    sys.stderr.write(message + "\n")


def _maybe_auto_elevate() -> bool:
    project_root = os.path.abspath(os.path.dirname(__file__))
    config_path = ensure_config_file(os.path.join(project_root, "config.txt"))
    config = load_config(config_path)
    if not bool(config.get("AUTO_ELEVATE", False)):
        return False
    if is_admin():
        return False
    return relaunch_as_admin(sys.argv)


class StartupSplash(QWidget):
    def __init__(self, image_path: str, duration_ms: int = 3000):
        super().__init__(None, Qt.FramelessWindowHint | Qt.SplashScreen | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(12)
        card = QWidget()
        card.setObjectName("SplashCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet(
            "QWidget#SplashCard {"
            "background: rgba(8, 12, 18, 228);"
            "border: 1px solid rgba(255,255,255,36);"
            "border-radius: 28px;}"
            "QLabel { color: #f8fbff; background: transparent; }"
        )
        root.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        self.image_label = QLabel()
        pm = QPixmap(image_path)
        if not pm.isNull():
            self.image_label.setPixmap(pm.scaled(148, 148, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.image_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.image_label, 0, Qt.AlignHCenter)
        title = QLabel("SayaTech MIDI Studio")
        f = QFont("Microsoft YaHei UI")
        f.setBold(True)
        f.setPointSizeF(18)
        title.setFont(f)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        subtitle = QLabel("启动中…")
        sf = QFont("Microsoft YaHei UI")
        sf.setPointSizeF(10.5)
        subtitle.setFont(sf)
        subtitle.setStyleSheet("color: rgba(255,255,255,180);")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        self.resize(320, 320)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._duration_ms = max(1000, int(duration_ms))

    def play_then_show(self, window: MainWindow) -> None:
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)
        self.setWindowOpacity(0.0)
        self.show()
        self._fade.setDuration(420)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

        def fade_out():
            self._fade.stop()
            self._fade.setDuration(360)
            self._fade.setStartValue(self.windowOpacity())
            self._fade.setEndValue(0.0)
            def done():
                try:
                    self.close()
                finally:
                    window.show()
            self._fade.finished.connect(done)
            self._fade.start()
        hold_ms = max(1200, self._duration_ms - 780)
        QTimer.singleShot(hold_ms, fade_out)


def main() -> int:
    global _QT_HANDLER
    multiprocessing.freeze_support()
    install_global_hooks()
    append_runtime_log('Application starting.')
    try:
        if _maybe_auto_elevate():
            append_runtime_log('Auto elevate requested; relaunching as admin and exiting current process.')
            return 0
        _QT_HANDLER = _qt_message_filter
        qInstallMessageHandler(_QT_HANDLER)
        app = QApplication(sys.argv)
        base_font = app.font()
        if base_font.pointSizeF() <= 0:
            base_font = QFont("Microsoft YaHei UI", 10)
        app.setFont(base_font)
        if os.path.exists(APP_ICON_PATH):
            app.setWindowIcon(QIcon(APP_ICON_PATH))
        project_root = os.path.abspath(os.path.dirname(__file__))
        ui_settings = load_ui_settings(project_root)
        window = MainWindow()
        if os.path.exists(APP_ICON_PATH):
            window.setWindowIcon(QIcon(APP_ICON_PATH))
        if getattr(ui_settings, 'splash_enabled', True) and os.path.exists(SPLASH_IMAGE_PATH):
            splash = StartupSplash(SPLASH_IMAGE_PATH, getattr(ui_settings, 'splash_duration_ms', 3000))
            splash.play_then_show(window)
        else:
            window.show()
        append_runtime_log('Main window prepared; entering Qt event loop.')
        exit_code = app.exec()
        append_runtime_log(f'Qt event loop exited with code {exit_code}.')
        return exit_code
    except BaseException as exc:
        path = write_crash_log('Fatal exception during application bootstrap/event loop', exc)
        print(f'Crash log written to: {path}', file=sys.stderr)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
