from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Iterable, Optional

APP_DIR_NAME = "SayaTech MIDI Studio"


def bundled_root() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS")).resolve()
    return Path(__file__).resolve().parent.parent


def executable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return bundled_root()


def user_data_root() -> Path:
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    if not base:
        home = Path.home()
        base = str(home / "AppData" / "Local")
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    path = user_data_root() / "config"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    path = user_data_root() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = user_data_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_config_path() -> Path:
    return config_dir() / "config.txt"


def user_ui_settings_path() -> Path:
    return config_dir() / "ui_settings.json"


def _copy_first_existing(destination: Path, candidates: Iterable[Path]) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        try:
            if candidate.is_file():
                shutil.copy2(candidate, destination)
                return True
        except Exception:
            continue
    return False


def ensure_user_config_file(default_text: str = "") -> Path:
    dst = user_config_path()
    if dst.exists():
        return dst
    candidates = [
        executable_root() / "config.txt",
        bundled_root() / "config.txt",
        executable_root() / "config.example.txt",
        bundled_root() / "config.example.txt",
    ]
    if not _copy_first_existing(dst, candidates):
        dst.write_text(default_text or "", encoding="utf-8")
    return dst


def ensure_user_ui_settings_file(default_text: Optional[str] = None) -> Path:
    dst = user_ui_settings_path()
    if dst.exists():
        return dst
    candidates = [
        executable_root() / "ui_settings.json",
        bundled_root() / "ui_settings.json",
        executable_root() / "ui_settings.example.json",
        bundled_root() / "ui_settings.example.json",
    ]
    if not _copy_first_existing(dst, candidates) and default_text is not None:
        dst.write_text(default_text, encoding="utf-8")
    return dst
