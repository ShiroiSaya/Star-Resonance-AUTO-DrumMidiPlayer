from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict

from .app_paths import ensure_user_ui_settings_file, user_ui_settings_path


@dataclass
class UISettings:
    play_hotkey: str = "F10"
    pause_hotkey: str = "F11"
    stop_hotkey: str = "F12"
    play_hotkey_enabled: bool = True
    pause_hotkey_enabled: bool = True
    stop_hotkey_enabled: bool = True
    dark_mode: bool = False
    theme_preset: str = "ocean"
    ui_scale: int = 100
    animations_enabled: bool = True
    animation_speed: int = 100
    debug_mode: bool = False
    glass_blur: int = 58
    splash_enabled: bool = True
    splash_duration_ms: int = 3000
    gpu_acceleration: bool = False
    performance_mode: bool = False


THEMES = {"ocean", "violet", "emerald", "sunset", "graphite"}


def _normalize(data: Dict[str, Any]) -> UISettings:
    base = UISettings()
    values = asdict(base)
    for key in list(values.keys()):
        if key in data:
            values[key] = data[key]
    values["ui_scale"] = max(80, min(140, int(values.get("ui_scale", 100))))
    values["animation_speed"] = max(40, min(220, int(values.get("animation_speed", 100))))
    values["glass_blur"] = max(0, min(100, int(values.get("glass_blur", 58))))
    values["splash_duration_ms"] = max(1000, min(5000, int(values.get("splash_duration_ms", 3000))))
    theme = str(values.get("theme_preset", "ocean") or "ocean").strip().lower()
    values["theme_preset"] = theme if theme in THEMES else "ocean"
    for key in ("play_hotkey", "pause_hotkey", "stop_hotkey"):
        text = str(values.get(key, "") or "").strip().upper()
        values[key] = text or getattr(base, key)
    for key in (
        "play_hotkey_enabled",
        "pause_hotkey_enabled",
        "stop_hotkey_enabled",
        "dark_mode",
        "animations_enabled",
        "debug_mode",
        "splash_enabled",
        "gpu_acceleration",
        "performance_mode",
    ):
        values[key] = bool(values.get(key, getattr(base, key)))
    return UISettings(**values)


def settings_path(project_root: str | None = None) -> str:
    return str(user_ui_settings_path())


def load_ui_settings(project_root: str | None = None) -> UISettings:
    path = str(ensure_user_ui_settings_file())
    if not os.path.exists(path):
        return UISettings()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return UISettings()
        return _normalize(data)
    except Exception:
        return UISettings()


def save_ui_settings(project_root: str | None, settings: UISettings) -> str:
    path = settings_path(project_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, ensure_ascii=False, indent=2)
    return path
