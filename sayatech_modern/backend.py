from __future__ import annotations

import time
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from .crash_logging import append_runtime_log, write_crash_log
from .models import DrumPlanReport, MidiAnalysisResult, NoteSpan, PedalEvent

try:  # pragma: no cover - runtime dependency on Windows host
    import pydirectinput as _keylib

    _keylib.FAILSAFE = False
    _keylib.PAUSE = 0.0
except Exception:  # pragma: no cover - CI / non-Windows fallback
    _keylib = None


DEFAULT_KEYMAP = [
    "z", "1", "x", "2", "c", "v", "3", "b", "4", "n", "5", "m",
    "a", "6", "s", "7", "d", "f", "8", "g", "9", "h", "0", "j",
    "q", "i", "w", "o", "e", "r", "p", "t", "[", "y", "]", "u",
]
DEFAULT_LEFTMOST = 48  # C3
WINDOW_SIZE = len(DEFAULT_KEYMAP)
DEFAULT_RIGHTMOST = DEFAULT_LEFTMOST + WINDOW_SIZE - 1  # B5
DEFAULT_OVERALL_MIN = 21  # A0
DEFAULT_OVERALL_MAX = 108  # C8
MIN_WINDOW_OFFSET = -4
MAX_WINDOW_OFFSET = 4

FINE_MODE_TO_OFFSET = {"ctrl": -1, "base": 0, "shift": 1}
OFFSET_TO_FINE_LABEL = {-1: "左移1八度", 0: "默认", 1: "右移1八度"}


@dataclass(slots=True)
class BackendPlaybackHandle:
    duration_sec: float
    current_sec: float = 0.0
    worker: Optional[threading.Thread] = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    is_running: bool = False
    pressed_keys: set[str] = field(default_factory=set)
    nav_offset: int = 0
    fine_mode: str = "base"
    coarse_steps: int = 0
    pedal_on: bool = False


class PlaybackBackend:
    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self.log_callback = log_callback

    def _log(self, text: str) -> None:
        if self.log_callback:
            self.log_callback(text)
        else:
            append_runtime_log(text)

    def prepare(self, analysis: MidiAnalysisResult) -> BackendPlaybackHandle:
        return BackendPlaybackHandle(duration_sec=analysis.duration_sec, current_sec=0.0)

    def start(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        handle.current_sec = max(0.0, min(position_sec, handle.duration_sec))

    def pause(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        handle.current_sec = max(0.0, min(position_sec, handle.duration_sec))

    def stop(self, handle: BackendPlaybackHandle) -> None:
        handle.current_sec = 0.0
        handle.nav_offset = 0
        handle.fine_mode = "base"
        handle.coarse_steps = 0
        handle.pedal_on = False

    def seek(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        handle.current_sec = max(0.0, min(position_sec, handle.duration_sec))


class KeyboardMixin:
    def _key_down(self, key: str) -> None:
        if _keylib is None:
            return
        _keylib.keyDown(key.lower())

    def _key_up(self, key: str) -> None:
        if _keylib is None:
            return
        _keylib.keyUp(key.lower())

    def _tap(self, key: str, hold: float) -> None:
        self._key_down(key)
        time.sleep(max(0.003, hold))
        self._key_up(key)

    def _release_keys(self, handle: BackendPlaybackHandle, keys: Sequence[str]) -> None:
        for key in list(keys):
            if key not in handle.pressed_keys:
                continue
            try:
                self._key_up(key)
            except Exception:
                pass
            handle.pressed_keys.discard(key)

    def _release_all(self, handle: BackendPlaybackHandle) -> None:
        self._release_keys(handle, list(handle.pressed_keys))


class LiveBackendBase(PlaybackBackend, KeyboardMixin):
    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        super().__init__(log_callback=log_callback)
        self.analysis: Optional[MidiAnalysisResult] = None
        self._warned_no_keylib = False

    def prepare(self, analysis: MidiAnalysisResult) -> BackendPlaybackHandle:
        self.analysis = analysis
        return BackendPlaybackHandle(duration_sec=analysis.duration_sec, current_sec=0.0)

    def _ensure_runtime_warning(self) -> None:
        if _keylib is None and not self._warned_no_keylib:
            self._warned_no_keylib = True
            self._log("未检测到 pydirectinput，当前环境只会模拟播放进度，不会真实按键注入。")

    def _interrupt_worker(self, handle: BackendPlaybackHandle) -> None:
        if handle.worker and handle.worker.is_alive():
            self._log(f'请求停止旧播放线程: {handle.worker.name}')
            handle.stop_event.set()
            handle.worker.join(timeout=1.2)
            if handle.worker.is_alive():
                self._log(f'警告：旧播放线程在 1.2s 内未完全退出: {handle.worker.name}')
            else:
                self._log(f'旧播放线程已退出: {handle.worker.name}')
        handle.is_running = False
        handle.worker = None
        handle.stop_event = threading.Event()
        self._release_all(handle)
        self._set_pedal_state(handle, False)

    def start(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        self._ensure_runtime_warning()
        handle.current_sec = max(0.0, min(position_sec, handle.duration_sec))
        self._interrupt_worker(handle)
        handle.stop_event = threading.Event()
        handle.is_running = True
        worker = threading.Thread(target=self._run_from_position, args=(handle, handle.current_sec), daemon=True, name=f"{self.__class__.__name__}-worker")
        handle.worker = worker
        self._log(f'启动播放线程 {worker.name} | 起点 {handle.current_sec:.3f}s')
        worker.start()

    def pause(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        handle.current_sec = max(0.0, min(position_sec, handle.duration_sec))
        self._interrupt_worker(handle)

    def stop(self, handle: BackendPlaybackHandle) -> None:
        self._interrupt_worker(handle)
        self._reset_to_default_window(handle)
        handle.current_sec = 0.0

    def seek(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        handle.current_sec = max(0.0, min(position_sec, handle.duration_sec))
        if handle.is_running:
            self.start(handle, handle.current_sec)

    def update_config(self, config: dict) -> None:
        self.keymap = list(config.get("KEYMAP", DEFAULT_KEYMAP)) or list(DEFAULT_KEYMAP)
        self.base_leftmost = int(config.get("LEFTMOST_NOTE", DEFAULT_LEFTMOST))
        self.visible_octaves = max(1, int(config.get("VISIBLE_OCTAVES", 3)))
        self.window_size = max(1, min(len(self.keymap), self.visible_octaves * 12))
        self.window_rightmost = self.base_leftmost + self.window_size - 1
        self.overall_min_note = int(config.get("UNLOCKED_MIN_NOTE", DEFAULT_OVERALL_MIN))
        self.overall_max_note = int(config.get("UNLOCKED_MAX_NOTE", DEFAULT_OVERALL_MAX))
        self.auto_transpose = bool(config.get("AUTO_TRANSPOSE", True))
        self.use_pedal = bool(config.get("USE_PEDAL", True))
        self.min_note_len = max(0.01, float(config.get("MIN_NOTE_LEN", self.min_note_len)))
        self.use_shift_octave = bool(config.get("USE_SHIFT_OCTAVE", True))
        self.auto_shift_from_range = bool(config.get("AUTO_SHIFT_FROM_RANGE", True))
        self.shift_key = str(config.get("SHIFT_KEY", self.shift_key)).strip().lower() or "shift"
        self.switch_margin = max(0, int(config.get("SWITCH_MARGIN", self.switch_margin)))
        self.min_notes_between_switches = max(0, int(config.get("MIN_NOTES_BETWEEN_SWITCHES", self.min_notes_between_switches)))
        self.shift_weight = max(0.1, float(config.get("SHIFT_WEIGHT", self.shift_weight)))
        self.fixed_window_mode = False
        range_fits_window = self.overall_min_note >= self.base_leftmost and self.overall_max_note <= self.window_rightmost
        if self.auto_shift_from_range and self.overall_max_note <= self.window_rightmost:
            self.use_shift_octave = False
        if range_fits_window and (self.auto_shift_from_range or not self.use_shift_octave):
            self.fixed_window_mode = True
            self.use_shift_octave = False
        self.retrigger_gap = float(config.get("RETRIGGER_GAP", self.retrigger_gap))
        self.retrigger_mode = bool(config.get("RETRIGGER_MODE", True))
        self.retrigger_priority = str(config.get("RETRIGGER_PRIORITY", "latest")).strip().lower()
        if self.retrigger_priority not in {"latest", "first"}:
            self.retrigger_priority = "latest"
        self.lookahead_groups = max(1, int(round(int(config.get("LOOKAHEAD_NOTES", 24)) / 3)))
        self.pedal_tap_time = float(config.get("PEDAL_TAP_TIME", 0.08))
        self.chord_priority = bool(config.get("CHORD_PRIORITY", False))
        self.chord_split_threshold = max(0.0, float(config.get("CHORD_SPLIT_THRESHOLD", 0.035)))
        self.octave_fold_priority = bool(config.get("OCTAVE_FOLD_PRIORITY", True))
        self.octave_fold_weight = max(0.0, float(config.get("OCTAVE_FOLD_WEIGHT", 0.55)))
        self.max_melodic_jump_after_fold = max(0, int(config.get("MAX_MELODIC_JUMP_AFTER_FOLD", 12)))
        self.bar_aware_transpose = bool(config.get("BAR_AWARE_TRANSPOSE", True))
        self.bar_transpose_scope = str(config.get("BAR_TRANSPOSE_SCOPE", "phrase")).strip().lower()
        if self.bar_transpose_scope not in {"phrase", "halfbar", "bar"}:
            self.bar_transpose_scope = "phrase"
        self.bar_transpose_threshold = max(1, int(config.get("BAR_TRANSPOSE_THRESHOLD", 1)))
        self.shift_hold_bass = bool(config.get("SHIFT_HOLD_BASS", True))
        self.shift_hold_max_note = int(config.get("SHIFT_HOLD_MAX_NOTE", 59))
        self.shift_hold_max_chord_rank = max(0, int(config.get("SHIFT_HOLD_MAX_CHORD_RANK", 1)))
        self.shift_hold_conflict_clear = bool(config.get("SHIFT_HOLD_CONFLICT_CLEAR", True))
        self.shift_hold_release_delay = max(0.0, float(config.get("SHIFT_HOLD_RELEASE_DELAY", 0.03)))
        self.octave_avoid_collision = bool(config.get("OCTAVE_AVOID_COLLISION", False))
        self.octave_preview_neighbors = max(0, int(config.get("OCTAVE_PREVIEW_NEIGHBORS", 0)))
        self.melody_priority = bool(config.get("MELODY_PRIORITY", True))
        self.melody_pitch_weight = float(config.get("MELODY_PITCH_WEIGHT", 1.0))
        self.melody_duration_weight = float(config.get("MELODY_DURATION_WEIGHT", 0.7))
        self.melody_continuity_weight = float(config.get("MELODY_CONTINUITY_WEIGHT", 1.2))
        self.melody_keep_top = max(1, int(config.get("MELODY_KEEP_TOP", 2)))
        if self.analysis is not None:
            self._prime_action_cache()

    def _run_from_position(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        raise NotImplementedError

    @staticmethod
    def _sleep_until(target_perf: float, stop_event: threading.Event, coarse_margin: float = 0.002) -> bool:
        while True:
            if stop_event.is_set():
                return False
            remain = target_perf - time.perf_counter()
            if remain <= 0:
                return True
            if remain > coarse_margin:
                time.sleep(remain - coarse_margin)
            else:
                time.sleep(min(0.0008, max(0.0001, remain)))

    def _set_handle_window(self, handle: BackendPlaybackHandle, fine_mode: str, coarse_steps: int) -> None:
        handle.fine_mode = fine_mode
        handle.coarse_steps = coarse_steps
        handle.nav_offset = FINE_MODE_TO_OFFSET[fine_mode] + coarse_steps * 3

    @staticmethod
    def _offset_to_state(offset: int) -> Tuple[str, int]:
        rem = offset % 3
        if rem == 0:
            return "base", offset // 3
        if rem == 1:
            return "shift", (offset - 1) // 3
        return "ctrl", (offset + 1) // 3

    @staticmethod
    def _state_to_offset(fine_mode: str, coarse_steps: int) -> int:
        return FINE_MODE_TO_OFFSET[fine_mode] + coarse_steps * 3

    def _state_to_nav_path(self, current_fine: str, current_coarse: int, target_fine: str, target_coarse: int) -> List[Tuple[str, str, int]]:
        path: List[Tuple[str, str, int]] = []
        fine = current_fine
        coarse = current_coarse
        if fine != target_fine:
            if target_fine == "base":
                nav_key = self.shift_key if fine == "shift" else "ctrlleft"
            elif target_fine == "shift":
                nav_key = self.shift_key
            else:
                nav_key = "ctrlleft"
            fine = target_fine
            path.append((nav_key, fine, coarse))
        delta = target_coarse - coarse
        while delta > 0:
            coarse += 1
            delta -= 1
            path.append(("period", fine, coarse))
        while delta < 0:
            coarse -= 1
            delta += 1
            path.append(("comma", fine, coarse))
        return path

    def _move_handle_to_offset(self, handle: BackendPlaybackHandle, target_offset: int, tap_hold: float = 0.010) -> None:
        target_fine, target_coarse = self._offset_to_state(target_offset)
        path = self._state_to_nav_path(handle.fine_mode, handle.coarse_steps, target_fine, target_coarse)
        for key_name, fine_mode, coarse_steps in path:
            self._tap(key_name, tap_hold)
            self._set_handle_window(handle, fine_mode, coarse_steps)

    def _set_pedal_state(self, handle: BackendPlaybackHandle, is_on: bool, tap_hold: float = 0.010) -> None:
        if handle.pedal_on == bool(is_on):
            return
        self._tap("space", tap_hold)
        handle.pedal_on = bool(is_on)

    def _reset_to_default_window(self, handle: BackendPlaybackHandle) -> None:
        if handle.fine_mode != "base" or handle.coarse_steps != 0:
            path = self._state_to_nav_path(handle.fine_mode, handle.coarse_steps, "base", 0)
            for key_name, fine_mode, coarse_steps in path:
                self._tap(key_name, 0.010)
                self._set_handle_window(handle, fine_mode, coarse_steps)
            self._log("可弹区间已回到默认 C3-B5")


def _note_name(midi_note: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi_note % 12]}{midi_note // 12 - 1}"


@dataclass(slots=True)
class PianoAction:
    t: float
    kind: str
    key: str
    target_offset: int = 0
    label: str = ""
    pedal_state: Optional[bool] = None
    note_token: int = -1
    midi_note: int = 0
    chord_rank: int = 0


class ModernPianoBackend(LiveBackendBase):
    GROUP_THRESHOLD_SEC = 0.035

    def __init__(
        self,
        log_callback: Optional[Callable[[str], None]] = None,
        retrigger_gap: float = 0.003,
        lookahead_groups: int = 8,
        nav_step_gap: float = 0.014,
        nav_settle_sec: float = 0.026,
        nav_tap_hold: float = 0.010,
    ):
        super().__init__(log_callback=log_callback)
        self.retrigger_gap = retrigger_gap
        self.lookahead_groups = max(1, lookahead_groups)
        self.nav_step_gap = nav_step_gap
        self.nav_settle_sec = nav_settle_sec
        self.nav_tap_hold = nav_tap_hold
        self.keymap = list(DEFAULT_KEYMAP)
        self.base_leftmost = DEFAULT_LEFTMOST
        self.visible_octaves = 3
        self.window_size = len(DEFAULT_KEYMAP)
        self.window_rightmost = self.base_leftmost + self.window_size - 1
        self.overall_min_note = DEFAULT_OVERALL_MIN
        self.overall_max_note = DEFAULT_OVERALL_MAX
        self.auto_transpose = True
        self.use_pedal = True
        self.pedal_tap_time = 0.08
        self.min_note_len = 0.10
        self.use_shift_octave = True
        self.auto_shift_from_range = True
        self.shift_key = "shift"
        self.switch_margin = 2
        self.min_notes_between_switches = 12
        self.shift_weight = 1.6
        self.retrigger_mode = True
        self.retrigger_priority = "latest"
        self.chord_priority = False
        self.chord_split_threshold = 0.035
        self.octave_fold_priority = True
        self.octave_fold_weight = 0.55
        self.max_melodic_jump_after_fold = 12
        self.bar_aware_transpose = True
        self.bar_transpose_scope = "phrase"
        self.bar_transpose_threshold = 1
        self.shift_hold_bass = True
        self.shift_hold_max_note = 59
        self.shift_hold_max_chord_rank = 1
        self.shift_hold_conflict_clear = True
        self.shift_hold_release_delay = 0.03
        self.octave_avoid_collision = False
        self.octave_preview_neighbors = 0
        self.melody_priority = True
        self.melody_pitch_weight = 1.0
        self.melody_duration_weight = 0.7
        self.melody_continuity_weight = 1.2
        self.melody_keep_top = 2
        self.fixed_window_mode = False
        self._actions_cache: Optional[List[PianoAction]] = None
        self._action_cache_signature: Optional[tuple] = None

    def prepare(self, analysis: MidiAnalysisResult) -> BackendPlaybackHandle:
        handle = super().prepare(analysis)
        self._prime_action_cache()
        return handle

    def _current_action_cache_signature(self) -> Optional[tuple]:
        if not self.analysis:
            return None
        notes = self.analysis.notes
        pedals = self.analysis.pedal_events
        note_head = tuple((n.start_sec, n.end_sec, n.midi_note, n.velocity) for n in notes[:3])
        note_tail = tuple((n.start_sec, n.end_sec, n.midi_note, n.velocity) for n in notes[-3:]) if len(notes) > 3 else ()
        pedal_head = tuple((p.time_sec, p.is_down, p.track_index) for p in pedals[:3])
        pedal_tail = tuple((p.time_sec, p.is_down, p.track_index) for p in pedals[-3:]) if len(pedals) > 3 else ()
        return (
            len(notes), note_head, note_tail,
            len(pedals), pedal_head, pedal_tail,
            tuple(self.keymap), self.base_leftmost, self.visible_octaves, self.window_size, self.window_rightmost,
            self.overall_min_note, self.overall_max_note, self.auto_transpose, self.use_pedal, round(self.min_note_len, 4),
            self.use_shift_octave, self.auto_shift_from_range, self.shift_key, self.switch_margin, self.min_notes_between_switches,
            round(self.shift_weight, 4), round(self.retrigger_gap, 4), self.retrigger_mode, self.retrigger_priority,
            self.lookahead_groups, round(self.pedal_tap_time, 4), self.chord_priority, round(self.chord_split_threshold, 4),
            self.octave_fold_priority, round(self.octave_fold_weight, 4), self.max_melodic_jump_after_fold,
            self.bar_aware_transpose, self.bar_transpose_scope, self.bar_transpose_threshold,
            self.shift_hold_bass, self.shift_hold_max_note, self.shift_hold_max_chord_rank,
            self.shift_hold_conflict_clear, round(self.shift_hold_release_delay, 4),
            self.octave_avoid_collision, self.octave_preview_neighbors,
            self.melody_priority, round(self.melody_pitch_weight, 4), round(self.melody_duration_weight, 4),
            round(self.melody_continuity_weight, 4), self.melody_keep_top, self.fixed_window_mode,
        )

    def _prime_action_cache(self) -> None:
        if not self.analysis:
            self._actions_cache = None
            self._action_cache_signature = None
            return
        signature = self._current_action_cache_signature()
        if signature is not None and signature == self._action_cache_signature and self._actions_cache is not None:
            return
        try:
            self._actions_cache = self._build_actions(self.analysis.notes, self.analysis.pedal_events)
            self._action_cache_signature = signature
        except Exception:
            self._actions_cache = None
            self._action_cache_signature = None

    def _run_from_position(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        if not self.analysis:
            return
        actions = list(self._actions_cache) if self._actions_cache is not None else self._build_actions(self.analysis.notes, self.analysis.pedal_events)
        if not actions:
            return

        start_offset = self._offset_at_position(actions, position_sec)
        start_pedal = self._pedal_at_position(actions, position_sec)
        if handle.nav_offset != start_offset:
            self._release_all(handle)
            self._move_handle_to_offset(handle, start_offset, self.nav_tap_hold)
            self._log(f"切换可弹区间到 {self._offset_label(handle.nav_offset)}，从 {position_sec:.2f}s 开始。")
        if self.use_pedal:
            self._set_pedal_state(handle, start_pedal, self.pedal_tap_time)

        actions = [a for a in actions if a.t >= max(0.0, position_sec - 0.01)]
        if not actions:
            return

        start_perf = time.perf_counter()
        anchor = position_sec
        key_state: dict[str, int] = {}
        key_primary_token: dict[str, int] = {}
        key_active_tokens: dict[str, set[int]] = {}
        token_meta: dict[int, PianoAction] = {}
        held_keys: dict[str, float] = {}
        last_nav_log_at = 0.0

        def release_expired_holds(force: bool = False, conflict_key: Optional[str] = None) -> None:
            now = time.perf_counter()
            to_release = []
            for key, until in list(held_keys.items()):
                if force or (conflict_key is not None and key == conflict_key) or now >= until:
                    to_release.append(key)
            if to_release:
                self._release_keys(handle, to_release)
                for key in to_release:
                    held_keys.pop(key, None)

        try:
            self._log(
                f"钢琴播放开始 | 起点={position_sec:.3f}s | 音符={len(self.analysis.notes) if self.analysis else 0} | 动作={len(actions)} | "
                f"keymap={len(self.keymap)} | 窗口={self.base_leftmost}-{self.window_rightmost} | "
                f"整体范围={self.overall_min_note}-{self.overall_max_note} | shift={self.use_shift_octave} | "
                f"auto_shift={self.auto_shift_from_range} | fixed_window={self.fixed_window_mode} | pedal={self.use_pedal}"
            )
            for action in actions:
                if handle.stop_event.is_set():
                    break
                delay = max(0.0, action.t - anchor)
                target_perf = start_perf + delay
                if not self._sleep_until(target_perf, handle.stop_event):
                    break
                release_expired_holds()

                if action.kind == "nav":
                    holdable_keys: set[str] = set()
                    if self.shift_hold_bass and action.target_offset > handle.nav_offset:
                        for token, meta in list(token_meta.items()):
                            if meta.key not in handle.pressed_keys:
                                continue
                            if meta.midi_note <= self.shift_hold_max_note and meta.chord_rank <= self.shift_hold_max_chord_rank:
                                holdable_keys.add(meta.key)
                    self._release_keys(handle, [k for k in list(handle.pressed_keys) if k not in holdable_keys])
                    key_state.clear()
                    key_primary_token.clear()
                    key_active_tokens.clear()
                    token_meta.clear()
                    target_fine, target_coarse = self._offset_to_state(action.target_offset)
                    path = self._state_to_nav_path(handle.fine_mode, handle.coarse_steps, target_fine, target_coarse)
                    for key_name, fine_mode, coarse_steps in path:
                        self._tap(key_name, self.nav_tap_hold)
                        self._set_handle_window(handle, fine_mode, coarse_steps)
                    if holdable_keys:
                        expire_at = time.perf_counter() + max(0.0, self.shift_hold_release_delay)
                        for key in holdable_keys:
                            held_keys[key] = max(held_keys.get(key, 0.0), expire_at)
                    now = time.perf_counter()
                    if now - last_nav_log_at > 0.05:
                        last_nav_log_at = now
                        if holdable_keys:
                            self._log(f"可弹区间 -> {action.label} | 保留低音 {', '.join(sorted(holdable_keys))}")
                        else:
                            self._log(f"可弹区间 -> {action.label}")
                    continue
                if action.kind == "pedal":
                    if self.use_pedal:
                        self._set_pedal_state(handle, bool(action.pedal_state), self.pedal_tap_time)
                    continue
                active_tokens = key_active_tokens.setdefault(action.key, set())
                if action.kind == "down":
                    if self.shift_hold_conflict_clear and action.key in held_keys:
                        release_expired_holds(conflict_key=action.key)
                    token_meta[action.note_token] = action
                    active_count = key_state.get(action.key, 0)
                    active_tokens.add(action.note_token)
                    if action.key not in key_primary_token:
                        key_primary_token[action.key] = action.note_token
                    if active_count > 0:
                        if self.retrigger_mode:
                            self._key_up(action.key)
                            handle.pressed_keys.discard(action.key)
                            if self.retrigger_gap > 0:
                                time.sleep(self.retrigger_gap)
                            self._key_down(action.key)
                            handle.pressed_keys.add(action.key)
                    else:
                        self._key_down(action.key)
                        handle.pressed_keys.add(action.key)
                    key_state[action.key] = active_count + 1
                else:
                    token_meta.pop(action.note_token, None)
                    if action.note_token in active_tokens:
                        active_tokens.remove(action.note_token)
                    active_count = key_state.get(action.key, 0)
                    if active_count <= 0:
                        continue
                    if self.retrigger_priority == "first":
                        primary = key_primary_token.get(action.key)
                        if primary == action.note_token:
                            self._release_keys(handle, [action.key])
                            key_state[action.key] = 0
                            key_primary_token.pop(action.key, None)
                            active_tokens.clear()
                        else:
                            key_state[action.key] = max(0, active_count - 1)
                    else:
                        next_count = max(0, active_count - 1)
                        key_state[action.key] = next_count
                        if next_count == 0:
                            self._release_keys(handle, [action.key])
                            key_primary_token.pop(action.key, None)
        except BaseException as exc:
            context = {
                'position_sec': position_sec,
                'handle_current_sec': handle.current_sec,
                'handle_nav_offset': handle.nav_offset,
                'handle_fine_mode': handle.fine_mode,
                'handle_coarse_steps': handle.coarse_steps,
                'pressed_keys': sorted(handle.pressed_keys),
                'pedal_on': handle.pedal_on,
                'analysis_note_count': len(self.analysis.notes) if self.analysis else None,
                'analysis_duration_sec': self.analysis.duration_sec if self.analysis else None,
                'action_count': len(actions),
                'last_action': {
                    't': action.t if 'action' in locals() else None,
                    'kind': action.kind if 'action' in locals() else None,
                    'key': action.key if 'action' in locals() else None,
                    'target_offset': action.target_offset if 'action' in locals() else None,
                    'note_token': action.note_token if 'action' in locals() else None,
                    'midi_note': action.midi_note if 'action' in locals() else None,
                },
                'config_snapshot': {
                    'base_leftmost': self.base_leftmost,
                    'visible_octaves': self.visible_octaves,
                    'window_size': self.window_size,
                    'overall_min_note': self.overall_min_note,
                    'overall_max_note': self.overall_max_note,
                    'auto_transpose': self.auto_transpose,
                    'use_pedal': self.use_pedal,
                    'pedal_tap_time': self.pedal_tap_time,
                    'min_note_len': self.min_note_len,
                    'use_shift_octave': self.use_shift_octave,
                    'auto_shift_from_range': self.auto_shift_from_range,
                    'shift_key': self.shift_key,
                    'switch_margin': self.switch_margin,
                    'min_notes_between_switches': self.min_notes_between_switches,
                    'shift_weight': self.shift_weight,
                    'fixed_window_mode': self.fixed_window_mode,
                    'retrigger_gap': self.retrigger_gap,
                    'retrigger_mode': self.retrigger_mode,
                    'retrigger_priority': self.retrigger_priority,
                    'octave_avoid_collision': self.octave_avoid_collision,
                    'octave_preview_neighbors': self.octave_preview_neighbors,
                },
            }
            path = write_crash_log('Piano playback thread crashed', exc, context)
            self._log(f'钢琴播放线程异常，已写入崩溃日志: {path}')
            raise
        finally:
            release_expired_holds(force=True)
            self._release_all(handle)
            self._set_pedal_state(handle, False, self.pedal_tap_time)
            handle.is_running = False
            self._log('钢琴播放线程结束。')

    def _build_actions(self, notes: Sequence[NoteSpan], pedal_events: Sequence[PedalEvent]) -> List[PianoAction]:
        if not notes:
            return []
        grouped = self._group_notes(notes)
        actions: List[PianoAction] = []
        allowed_offsets = self._allowed_offsets()
        current_offset = 0 if 0 in allowed_offsets else allowed_offsets[0]
        last_switch_note_index = 0
        processed_note_count = 0
        note_token = 0
        prev_melody_note: Optional[int] = None
        for group_index, group in enumerate(grouped):
            group_start = group[0].start_sec
            target_offset = self._choose_best_offset(
                grouped,
                group_index,
                current_offset,
                prev_melody_note,
                notes_since_switch=max(0, processed_note_count - last_switch_note_index),
            )
            cur_fine, cur_coarse = self._offset_to_state(current_offset)
            tar_fine, tar_coarse = self._offset_to_state(target_offset)
            nav_path = self._state_to_nav_path(cur_fine, cur_coarse, tar_fine, tar_coarse)
            if nav_path:
                nav_start = max(0.0, group_start - self.nav_settle_sec - self.nav_step_gap * max(0, len(nav_path) - 1))
                for nav_index, (_nav_key, fine_mode, coarse_steps) in enumerate(nav_path):
                    next_offset = self._state_to_offset(fine_mode, coarse_steps)
                    nav_t = nav_start + nav_index * self.nav_step_gap
                    actions.append(
                        PianoAction(
                            t=nav_t,
                            kind="nav",
                            key="",
                            target_offset=next_offset,
                            label=self._offset_label(next_offset),
                        )
                    )
                current_offset = target_offset
                last_switch_note_index = processed_note_count

            ordered_group, melody_note, _melody_rank, low_rank_map = self._ordered_group_notes(group, prev_melody_note)
            mapped_melody: Optional[int] = None
            for note in ordered_group:
                prev_hint = prev_melody_note if note is melody_note else None
                mapped_note = self._map_note_to_window(note.midi_note, current_offset, prev_hint)
                if mapped_note is None:
                    continue
                if note is melody_note:
                    mapped_melody = mapped_note
                key_index = mapped_note - self._window_left(current_offset)
                if not (0 <= key_index < len(self.keymap)):
                    continue
                key = self.keymap[key_index]
                chord_rank = low_rank_map.get(id(note), 0)
                actions.append(PianoAction(t=note.start_sec, kind="down", key=key, target_offset=current_offset, note_token=note_token, midi_note=note.midi_note, chord_rank=chord_rank))
                actions.append(PianoAction(t=max(note.end_sec, note.start_sec + self.min_note_len), kind="up", key=key, target_offset=current_offset, note_token=note_token, midi_note=note.midi_note, chord_rank=chord_rank))
                note_token += 1
            if mapped_melody is not None:
                prev_melody_note = mapped_melody
            processed_note_count += len(group)

        if self.use_pedal:
            last_pedal_state: Optional[bool] = None
            for pedal in sorted(pedal_events, key=lambda p: (p.time_sec, p.track_index)):
                if last_pedal_state is None or last_pedal_state != bool(pedal.is_down):
                    actions.append(PianoAction(t=pedal.time_sec, kind="pedal", key="", pedal_state=bool(pedal.is_down), label="踏板"))
                    last_pedal_state = bool(pedal.is_down)

        priority = {"up": 0, "pedal": 1, "nav": 2, "down": 3}
        actions.sort(key=lambda a: (a.t, priority.get(a.kind, 9), a.key))
        return actions

    def _group_notes(self, notes: Sequence[NoteSpan]) -> List[List[NoteSpan]]:
        ordered = sorted(notes, key=lambda n: (n.start_sec, n.midi_note, n.track_index))
        groups: List[List[NoteSpan]] = []
        threshold = max(0.0, self.chord_split_threshold)
        for note in ordered:
            if not groups or note.start_sec - groups[-1][0].start_sec > threshold:
                groups.append([note])
            else:
                groups[-1].append(note)
        return groups

    def _melody_rankings(self, group: Sequence[NoteSpan], prev_melody_note: Optional[int]) -> Tuple[dict[int, int], Optional[NoteSpan]]:
        if not group:
            return {}, None
        scored = []
        for note in group:
            duration = max(self.min_note_len, note.end_sec - note.start_sec)
            pitch_score = note.midi_note * self.melody_pitch_weight
            duration_score = duration * 12.0 * self.melody_duration_weight
            continuity = 0.0
            if prev_melody_note is not None:
                continuity = max(0.0, 12.0 - min(12.0, abs(note.midi_note - prev_melody_note))) * self.melody_continuity_weight
            velocity_bonus = note.velocity / 127.0 * 0.25
            score = pitch_score + duration_score + continuity + velocity_bonus
            scored.append((score, note))
        scored.sort(key=lambda item: (item[0], item[1].midi_note, item[1].velocity), reverse=True)
        ranks = {id(note): rank for rank, (_score, note) in enumerate(scored)}
        return ranks, scored[0][1]

    def _ordered_group_notes(self, group: Sequence[NoteSpan], prev_melody_note: Optional[int]) -> Tuple[List[NoteSpan], Optional[NoteSpan], dict[int, int], dict[int, int]]:
        ordered = sorted(group, key=lambda n: (n.midi_note, n.velocity, n.start_sec))
        low_rank_map = {id(note): rank for rank, note in enumerate(sorted(group, key=lambda n: (n.midi_note, n.start_sec, n.velocity)))}
        melody_rank_map, melody_note = self._melody_rankings(group, prev_melody_note) if self.melody_priority else ({}, None)
        if melody_note is None and group:
            melody_note = max(group, key=lambda n: (n.midi_note, n.velocity, -(n.end_sec - n.start_sec)))
        if self.chord_priority and len(ordered) > 1:
            result: List[NoteSpan] = []
            l, r = 0, len(ordered) - 1
            take_low = True
            while l <= r:
                if take_low:
                    result.append(ordered[l])
                    l += 1
                else:
                    result.append(ordered[r])
                    r -= 1
                take_low = not take_low
            ordered = result
        if self.melody_priority and melody_rank_map:
            ordered = sorted(
                ordered,
                key=lambda n: (
                    0 if melody_rank_map.get(id(n), 999) < self.melody_keep_top else 1,
                    melody_rank_map.get(id(n), 999),
                    -n.midi_note,
                ),
            )
        return ordered, melody_note, melody_rank_map, low_rank_map

    def _scope_group_count(self) -> int:
        if self.bar_transpose_scope == "bar":
            return 4
        if self.bar_transpose_scope == "halfbar":
            return 2
        return 1

    def _allowed_offsets(self) -> List[int]:
        if getattr(self, "fixed_window_mode", False):
            return [0]
        offsets: List[int] = []
        for offset in range(MIN_WINDOW_OFFSET, MAX_WINDOW_OFFSET + 1):
            fine_mode, _coarse = self._offset_to_state(offset)
            if fine_mode == "shift" and not self.use_shift_octave:
                continue
            offsets.append(offset)
        return offsets or [0]

    def _choose_best_offset(
        self,
        groups: Sequence[Sequence[NoteSpan]],
        group_index: int,
        current_offset: int,
        prev_melody_note: Optional[int],
        notes_since_switch: int = 10 ** 9,
    ) -> int:
        if getattr(self, "fixed_window_mode", False):
            return 0
        search_offsets = self._allowed_offsets()
        best_offset = current_offset if current_offset in search_offsets else search_offsets[0]
        best_score: Optional[Tuple[float, float, float, float, float, float]] = None
        preview_groups = max(self.lookahead_groups, self.octave_preview_neighbors) if self.octave_preview_neighbors > 0 else self.lookahead_groups
        future_groups = groups[group_index : min(len(groups), group_index + preview_groups)]
        segment_groups = future_groups[: self._scope_group_count()]
        for offset in search_offsets:
            total_direct = 0.0
            total_penalty = 0.0
            future_prev = prev_melody_note
            for future_index, future_group in enumerate(future_groups):
                group_weight = max(1.0, 3.2 - future_index * 0.35)
                direct, penalty, future_prev = self._evaluate_group_window(future_group, offset, future_prev)
                total_direct += direct * group_weight
                total_penalty += penalty * group_weight
            local_bonus = self._local_transpose_bonus(segment_groups, current_offset, offset)
            nav_cost = abs(offset - current_offset)
            center_cost = abs((self._window_left(offset) + self._window_right(offset)) / 2.0 - self._group_center(groups[group_index]))
            target_fine, _target_coarse = self._offset_to_state(offset)
            shift_multiplier = self.shift_weight if target_fine == "shift" else 1.0
            total_value = (total_direct - total_penalty + local_bonus) * shift_multiplier
            score = (total_value, total_direct * shift_multiplier, local_bonus, -total_penalty, -nav_cost, -center_cost)
            if best_score is None or score > best_score:
                best_score = score
                best_offset = offset

        cur_direct, cur_penalty, _ = self._evaluate_group_window(groups[group_index], current_offset, prev_melody_note)
        current_total = cur_direct - cur_penalty
        if current_offset in search_offsets:
            current_fine, _current_coarse = self._offset_to_state(current_offset)
            if current_fine == "shift":
                current_total *= self.shift_weight

        if best_offset != current_offset and best_score is not None:
            required_gain = 0.15 + self.switch_margin * 0.35
            if notes_since_switch < self.min_notes_between_switches:
                cooldown_ratio = 1.0 - (notes_since_switch / max(1, self.min_notes_between_switches))
                required_gain += 0.60 * cooldown_ratio
            if best_score[0] <= current_total + required_gain:
                best_offset = current_offset
        return best_offset

    def _local_transpose_bonus(self, segment_groups: Sequence[Sequence[NoteSpan]], current_offset: int, offset: int) -> float:
        if not self.bar_aware_transpose or not segment_groups:
            return 0.0
        current_hi = sum(1 for group in segment_groups for note in group if note.midi_note > self._window_right(current_offset))
        current_lo = sum(1 for group in segment_groups for note in group if note.midi_note < self._window_left(current_offset))
        candidate_hi = sum(1 for group in segment_groups for note in group if note.midi_note > self._window_right(offset))
        candidate_lo = sum(1 for group in segment_groups for note in group if note.midi_note < self._window_left(offset))
        bonus = 0.0
        if current_hi >= self.bar_transpose_threshold:
            bonus += max(0, current_hi - candidate_hi) * 1.6
        if current_lo >= self.bar_transpose_threshold:
            bonus += max(0, current_lo - candidate_lo) * 1.2
        return bonus

    def _voice_weight(
        self,
        note: NoteSpan,
        ordered_group: Sequence[NoteSpan],
        index: int,
        melody_rank_map: dict[int, int],
    ) -> float:
        highest = max(n.midi_note for n in ordered_group)
        lowest = min(n.midi_note for n in ordered_group)
        weight = 1.0
        if note.midi_note == highest:
            weight += 2.8
        if note.midi_note == lowest and len(ordered_group) > 1:
            weight += 1.6
        if self.chord_priority:
            weight += max(0.0, 1.15 - index * 0.18)
        if self.melody_priority:
            rank = melody_rank_map.get(id(note), 999)
            if rank < self.melody_keep_top:
                weight += 2.2 - rank * 0.45
        if note.velocity >= 100:
            weight += 0.3
        return weight

    def _evaluate_group_window(
        self,
        group: Sequence[NoteSpan],
        offset: int,
        prev_melody_note: Optional[int],
    ) -> Tuple[float, float, Optional[int]]:
        ordered, melody_note, melody_rank_map, _low_rank_map = self._ordered_group_notes(group, prev_melody_note)
        direct_score = 0.0
        penalty = 0.0
        mapped_melody = prev_melody_note
        used_key_indexes: set[int] = set()
        for index, note in enumerate(ordered):
            weight = self._voice_weight(note, ordered, index, melody_rank_map)
            mapped, fold_distance, jump_excess = self._map_note_with_meta(
                note.midi_note,
                offset,
                prev_melody_note if note is melody_note else None,
            )
            if mapped is None:
                penalty += 9.0 * weight
                continue
            if note is melody_note:
                mapped_melody = mapped
            key_index = mapped - self._window_left(offset)
            if self.octave_avoid_collision and key_index in used_key_indexes:
                penalty += 0.9 * weight
            used_key_indexes.add(key_index)
            if self._note_in_window(note.midi_note, offset):
                direct_score += weight * 3.0
            else:
                direct_score += weight * 0.35
                penalty += fold_distance * max(0.2, self.octave_fold_weight) * weight
            if jump_excess > 0:
                penalty += jump_excess * 0.45 * weight
        return direct_score, penalty, mapped_melody

    def _group_center(self, group: Sequence[NoteSpan]) -> float:
        if not group:
            return float(self.base_leftmost)
        return sum(note.midi_note for note in group) / len(group)

    def _window_left(self, offset: int) -> int:
        return self.base_leftmost + offset * 12

    def _window_right(self, offset: int) -> int:
        return self.window_rightmost + offset * 12

    def _note_in_window(self, note: int, offset: int) -> bool:
        left = max(self._window_left(offset), self.overall_min_note)
        right = min(self._window_right(offset), self.overall_max_note)
        return left <= note <= right

    def _map_note_with_meta(self, note: int, offset: int, prev_note: Optional[int] = None) -> Tuple[Optional[int], float, float]:
        left = max(self._window_left(offset), self.overall_min_note)
        right = min(self._window_right(offset), self.overall_max_note)
        if left > right:
            return None, 99.0, 0.0
        if left <= note <= right:
            jump_excess = 0.0
            if prev_note is not None and self.max_melodic_jump_after_fold > 0:
                jump_excess = max(0.0, abs(note - prev_note) - self.max_melodic_jump_after_fold)
            return note, 0.0, jump_excess
        if not self.auto_transpose and not self.octave_fold_priority:
            return None, 99.0, 0.0
        if not self.octave_fold_priority:
            return None, 99.0, 0.0
        candidates: List[int] = []
        for k in range(-6, 7):
            candidate = note + 12 * k
            if left <= candidate <= right:
                candidates.append(candidate)
        if not candidates:
            return None, 99.0, 0.0
        best: Optional[Tuple[float, float, int, float]] = None
        for candidate in candidates:
            fold_distance = abs(candidate - note) / 12.0
            jump_excess = 0.0
            if prev_note is not None and self.max_melodic_jump_after_fold > 0:
                jump_excess = max(0.0, abs(candidate - prev_note) - self.max_melodic_jump_after_fold)
            score = (
                fold_distance * max(0.2, self.octave_fold_weight) + jump_excess * 0.6,
                abs(candidate - note),
                abs(candidate - (prev_note if prev_note is not None else note)),
                jump_excess,
            )
            if best is None or score < best:
                best = (score[0], score[1], candidate, jump_excess)
        if best is None:
            return None, 99.0, 0.0
        return int(best[2]), abs(int(best[2]) - note) / 12.0, float(best[3])

    def _map_note_to_window(self, note: int, offset: int, prev_note: Optional[int] = None) -> Optional[int]:
        mapped, _fold_distance, _jump_excess = self._map_note_with_meta(note, offset, prev_note)
        return mapped

    def _offset_label(self, offset: int) -> str:
        left = max(self._window_left(offset), self.overall_min_note)
        right = min(self._window_right(offset), self.overall_max_note)
        return f"{_note_name(left)}-{_note_name(right)}"

    @staticmethod
    def _offset_at_position(actions: Sequence[PianoAction], position_sec: float) -> int:
        offset = 0
        for action in actions:
            if action.kind == "nav" and action.t <= position_sec:
                offset = action.target_offset
            if action.t > position_sec:
                break
        return offset

    @staticmethod
    def _pedal_at_position(actions: Sequence[PianoAction], position_sec: float) -> bool:
        state = False
        for action in actions:
            if action.kind == "pedal" and action.t <= position_sec:
                state = bool(action.pedal_state)
            if action.t > position_sec:
                break
        return state


@dataclass(slots=True)
class DrumHit:
    t: float
    key: str
    velocity: int
    hold: float
    midi_note: int = 0
    original_name: str = ""
    mapped_name: str = ""
    reason: str = ""
    mapping_kind: str = "direct"


class ModernDrumBackend(LiveBackendBase):
    KEY_NAMES = {
        "F": "Bass Drum",
        "Q": "Snare Drum",
        "W": "Mid Tom",
        "E": "High Tom",
        "R": "Crash Cymbal 1",
        "T": "Hi-Hat",
        "Y": "Crash Cymbal 2",
        "S": "Pedal Hi-Hat",
        "H": "Floor Tom",
    }
    KEY_PRIORITY = {"F": 0, "Q": 1, "T": 2, "S": 3, "R": 4, "Y": 5, "E": 6, "W": 7, "H": 8}
    PRIMARY_MAP = {
        35: "F", 36: "F",
        37: "Q", 38: "Q", 39: "Q", 40: "Q",
        41: "H", 43: "H",
        45: "W", 47: "W", 48: "W",
        50: "E", 58: "E",
        44: "S",
        42: "T", 46: "T",
        49: "R", 52: "R", 55: "R", 57: "R",
        51: "Y", 53: "Y", 59: "Y",
    }
    EXTENDED_MAP = {
        27: "F", 28: "Q", 29: "Q", 30: "Q", 31: "Q", 32: "Q", 33: "Q", 34: "Q",
        54: "Y", 56: "R", 60: "Y", 61: "Y", 62: "W", 63: "E", 64: "E", 65: "W", 66: "H",
        67: "S", 68: "Q", 69: "Y", 70: "Y", 71: "R", 72: "Q", 73: "Q", 74: "T", 75: "Y",
        76: "Y", 77: "Q", 78: "Q", 79: "Q", 80: "Q", 81: "Q",
    }
    NOTE_NAMES = {
        35: "原声底鼓2", 36: "底鼓", 37: "军鼓边击", 38: "原声军鼓", 39: "拍手", 40: "电军鼓",
        41: "低音落地桶鼓", 42: "闭合踩镲", 43: "高音落地桶鼓", 44: "脚踩踩镲", 45: "低音桶鼓",
        46: "开放踩镲", 47: "低中音桶鼓", 48: "高音中桶鼓", 49: "强音镲1", 50: "高音桶鼓",
        51: "叮叮镲1", 52: "中国镲", 53: "叮叮镲帽", 54: "铃鼓", 55: "溅镲", 56: "牛铃",
        57: "强音镲2", 58: "震音掌", 59: "叮叮镲2", 60: "高音邦戈鼓", 61: "低音邦戈鼓",
        62: "静音高康加鼓", 63: "高康加鼓", 64: "低康加鼓", 65: "高音定音鼓", 66: "低音定音鼓",
        67: "高音阿哥哥", 68: "低音阿哥哥", 69: "沙锤", 70: "沙槌", 71: "短口哨", 72: "长口哨",
        73: "短刮瓜", 74: "长刮瓜", 75: "响棒", 76: "高木鱼", 77: "低木鱼", 78: "静音高木块",
        79: "开放高木块", 80: "静音低木块", 81: "开放低木块", 82: "静音三角铁", 83: "开放三角铁",
    }
    MAX_SIMULTANEOUS_DEFAULT = 4

    @classmethod
    def drum_key_for_midi(cls, note: int) -> Optional[str]:
        return cls.PRIMARY_MAP.get(note) or cls.EXTENDED_MAP.get(note) or cls._fallback_key_for_note(note)

    @classmethod
    def note_name_for_midi(cls, note: int) -> str:
        return cls.NOTE_NAMES.get(note, f"未知鼓音({note})")

    @classmethod
    def _fallback_key_for_note(cls, note: int) -> Optional[str]:
        if note < 0:
            return None
        if note <= 36:
            return "F"
        if note <= 40:
            return "Q"
        if note == 44:
            return "S"
        if note in {42, 46, 74}:
            return "T"
        if 41 <= note <= 43:
            return "H"
        if 45 <= note <= 49:
            return "W"
        if 50 <= note <= 58:
            return "E" if note in {50, 58} else ("Y" if note >= 53 else "R")
        if note <= 66:
            return "Y" if note >= 60 else "W"
        if note <= 81:
            return "T" if note in {67, 74} else "Y"
        return None

    def __init__(self, log_callback: Optional[Callable[[str], None]] = None, density_limit_hz: float = 42.0, retrigger_gap: float = 0.004):
        super().__init__(log_callback=log_callback)
        self.density_limit_hz = density_limit_hz
        self.retrigger_gap = retrigger_gap
        self.max_simultaneous = self.MAX_SIMULTANEOUS_DEFAULT
        self.base_tap_hold = 0.010
        self.same_time_window = 0.008
        self.coarse_group_window = 0.065
        self.accent_velocity = 108
        self.ghost_velocity = 42
        self.use_context_replace = True
        self.use_velocity_rules = True
        self.use_smart_keep = True
        self.prefer_channel_10 = True

    def update_config(self, config: dict) -> None:
        self.retrigger_gap = float(config.get("RETRIGGER_GAP", self.retrigger_gap))
        max_sim = config.get("MAX_SIMULTANEOUS", "none")
        try:
            self.max_simultaneous = self.MAX_SIMULTANEOUS_DEFAULT if str(max_sim).strip().lower() in {"", "none", "null"} else max(1, int(max_sim))
        except Exception:
            self.max_simultaneous = self.MAX_SIMULTANEOUS_DEFAULT
        self.base_tap_hold = max(0.002, float(config.get("BASE_TAP_HOLD", self.base_tap_hold)))
        self.same_time_window = max(0.001, float(config.get("SAME_TIME_WINDOW", self.same_time_window)))
        self.density_limit_hz = max(1.0, float(config.get("DENSITY_LIMIT_HZ", self.density_limit_hz)))
        self.coarse_group_window = max(self.same_time_window, float(config.get("COARSE_GROUP_WINDOW", self.coarse_group_window)))
        self.accent_velocity = max(1, min(127, int(config.get("ACCENT_VELOCITY", self.accent_velocity))))
        self.ghost_velocity = max(1, min(127, int(config.get("GHOST_VELOCITY", self.ghost_velocity))))
        self.use_context_replace = bool(config.get("USE_CONTEXT_REPLACE", True))
        self.use_velocity_rules = bool(config.get("USE_VELOCITY_RULES", True))
        self.use_smart_keep = bool(config.get("USE_SMART_KEEP", True))
        self.prefer_channel_10 = bool(config.get("PREFER_CHANNEL_10", True))

    def build_plan_report(self, analysis: Optional[MidiAnalysisResult]) -> DrumPlanReport:
        if analysis is None or not analysis.notes:
            return DrumPlanReport(selected_mode="未载入", total_source_hits=0, total_mapped_hits=0)
        ordered = sorted(analysis.notes, key=lambda n: (n.start_sec, -n.velocity, n.midi_note))
        note_counter: Counter[int] = Counter()
        mapped_counter: Counter[str] = Counter()
        fallback_counter: Counter[str] = Counter()
        ignored_counter: Counter[str] = Counter()
        preview_map: dict[int, list] = {}
        total_source_hits = len(ordered)
        total_mapped_hits = 0
        history: list[str] = []
        groups: list[list[NoteSpan]] = []
        current: list[NoteSpan] = []
        anchor = None
        for note in ordered:
            if anchor is None or note.start_sec - anchor <= self.same_time_window:
                current.append(note)
                anchor = note.start_sec if anchor is None else anchor
            else:
                groups.append(current)
                current = [note]
                anchor = note.start_sec
        if current:
            groups.append(current)
        for group in groups:
            present_keys: set[str] = set()
            candidate_hits: list[DrumHit] = []
            cluster_notes = [n.midi_note for n in group]
            for note in sorted(group, key=lambda n: (-n.velocity, n.midi_note)):
                note_counter[note.midi_note] += 1
                key, reason, kind = self._map_note_with_context_verbose(note.midi_note, cluster_notes, present_keys, history)
                if key:
                    hit = DrumHit(t=note.start_sec, key=key, velocity=note.velocity, hold=self._hold_for_velocity(key, note.velocity), midi_note=note.midi_note, original_name=self.note_name_for_midi(note.midi_note), mapped_name=self.KEY_NAMES.get(key, key), reason=reason, mapping_kind=kind)
                    candidate_hits.append(hit)
                    present_keys.add(key)
                    info = preview_map.setdefault(note.midi_note, [0, key, reason or self.KEY_NAMES.get(key, key)])
                    info[0] += 1
                else:
                    ignored_counter[self.note_name_for_midi(note.midi_note)] += 1
                    info = preview_map.setdefault(note.midi_note, [0, "—", reason or "未映射"])
                    info[0] += 1
            kept_hits = self._smart_keep(candidate_hits)
            if len(kept_hits) < len(candidate_hits):
                for dropped in candidate_hits:
                    if dropped not in kept_hits:
                        ignored_counter[f"智能裁剪:{dropped.mapped_name}"] += 1
            for hit in kept_hits:
                total_mapped_hits += 1
                mapped_counter[hit.key] += 1
                if hit.mapping_kind != "direct":
                    fallback_counter[hit.reason or hit.mapping_kind] += 1
                history.append(hit.key)
            history[:] = history[-12:]
        preview_rows = []
        for midi_note, (count, key, reason) in sorted(preview_map.items(), key=lambda item: (-item[1][0], item[0]))[:18]:
            mapped = self.KEY_NAMES.get(key, key) if key not in {"—", ""} else "未映射"
            preview_rows.append((self.note_name_for_midi(midi_note), count, mapped, reason or "直接映射"))
        mode_parts = ["上下文替代" if self.use_context_replace else "基础映射", "智能保留" if self.use_smart_keep else "全保留", "力度规则" if self.use_velocity_rules else "固定时长"]
        if self.prefer_channel_10:
            mode_parts.append("鼓轨优先")
        return DrumPlanReport(
            selected_mode=" / ".join(mode_parts),
            total_source_hits=total_source_hits,
            total_mapped_hits=total_mapped_hits,
            note_counter=sorted(note_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:12],
            mapped_counter=sorted(mapped_counter.items(), key=lambda kv: (-kv[1], self.KEY_PRIORITY.get(kv[0], 99))),
            fallback_counter=sorted(fallback_counter.items(), key=lambda kv: -kv[1]),
            ignored_counter=sorted(ignored_counter.items(), key=lambda kv: -kv[1])[:8],
            preview_rows=preview_rows,
        )

    def _run_from_position(self, handle: BackendPlaybackHandle, position_sec: float) -> None:
        if not self.analysis:
            return
        hits = self._build_hits(self.analysis.notes)
        hits = [h for h in hits if h.t >= max(0.0, position_sec - 0.01)]
        if not hits:
            return
        start_perf = time.perf_counter()
        anchor = position_sec
        last_status_at = 0.0
        try:
            for hit in hits:
                if handle.stop_event.is_set():
                    break
                target_perf = start_perf + max(0.0, hit.t - anchor)
                if not self._sleep_until(target_perf, handle.stop_event):
                    break
                if hit.key in handle.pressed_keys:
                    self._key_up(hit.key)
                    handle.pressed_keys.discard(hit.key)
                    if self.retrigger_gap > 0:
                        time.sleep(self.retrigger_gap)
                self._key_down(hit.key)
                handle.pressed_keys.add(hit.key)
                time.sleep(hit.hold)
                self._key_up(hit.key)
                handle.pressed_keys.discard(hit.key)
                now = time.perf_counter()
                if now - last_status_at > 0.18:
                    last_status_at = now
                    name = self.KEY_NAMES.get(hit.key, hit.key)
                    suffix = f" | {hit.reason}" if hit.reason and hit.reason != "直接映射" else ""
                    self._log(f"鼓命中：{hit.key}  {name}  力度={hit.velocity}{suffix}")
        finally:
            self._release_all(handle)
            handle.is_running = False

    def _build_hits(self, notes: Sequence[NoteSpan]) -> List[DrumHit]:
        if not notes:
            return []
        ordered = sorted(notes, key=lambda n: (n.start_sec, -n.velocity, n.midi_note))
        groups: List[List[NoteSpan]] = []
        current: List[NoteSpan] = []
        anchor = None
        for note in ordered:
            if anchor is None or note.start_sec - anchor <= self.same_time_window:
                current.append(note)
                anchor = note.start_sec if anchor is None else anchor
            else:
                groups.append(current)
                current = [note]
                anchor = note.start_sec
        if current:
            groups.append(current)

        hits: List[DrumHit] = []
        history: List[str] = []
        for group in groups:
            cluster_hits = self._map_group_to_hits(group, history)
            if cluster_hits:
                history.extend(hit.key for hit in cluster_hits)
                history[:] = history[-12:]
                hits.extend(cluster_hits)
        hits.sort(key=lambda h: (h.t, self.KEY_PRIORITY.get(h.key, 99), -h.velocity))
        return self._density_limit(hits)

    def _map_group_to_hits(self, group: Sequence[NoteSpan], history: Sequence[str]) -> List[DrumHit]:
        mapped: List[DrumHit] = []
        cluster_notes = [n.midi_note for n in group]
        present_keys: set[str] = set()
        for note in sorted(group, key=lambda n: (-n.velocity, n.midi_note)):
            key, reason, kind = self._map_note_with_context_verbose(note.midi_note, cluster_notes, present_keys, history)
            if not key:
                continue
            hold = self._hold_for_velocity(key, note.velocity)
            mapped.append(DrumHit(t=note.start_sec, key=key, velocity=note.velocity, hold=hold, midi_note=note.midi_note, original_name=self.note_name_for_midi(note.midi_note), mapped_name=self.KEY_NAMES.get(key, key), reason=reason, mapping_kind=kind))
            present_keys.add(key)
        return self._smart_keep(mapped)

    def _map_note_with_context_verbose(self, note: int, cluster_notes: Sequence[int], present_keys: set[str], history: Sequence[str]) -> tuple[Optional[str], str, str]:
        key = self.PRIMARY_MAP.get(note)
        if key:
            return key, "直接映射", "direct"
        key = self.EXTENDED_MAP.get(note)
        if key:
            return key, "扩展映射", "extended"
        if not self.use_context_replace:
            key = self._fallback_key_for_note(note)
            return (key, "基础回退", "fallback") if key else (None, "未映射", "ignored")

        if note in {54, 56, 71, 75, 76}:
            return self._choose_cymbal_variant(note, present_keys, history), "上下文替代：镲类", "context"
        if 60 <= note <= 66:
            if any(n in {41, 43, 45, 47, 48, 50, 58} for n in cluster_notes):
                return ("E" if note >= 63 else "W"), "上下文替代：手鼓/定音鼓", "context"
            return ("Y" if note >= 64 else "W"), "上下文替代：辅打击", "context"
        if note in {67, 74}:
            return ("S" if "S" not in present_keys else "T"), "上下文替代：踩镲系", "context"
        key = self._fallback_key_for_note(note)
        return (key, "区间回退", "fallback") if key else (None, "未映射", "ignored")

    def _choose_cymbal_variant(self, note: int, present_keys: set[str], history: Sequence[str]) -> str:
        preferred = "Y" if note in {54, 75, 76} else "R"
        alternate = "R" if preferred == "Y" else "Y"
        if preferred not in present_keys:
            return preferred
        if alternate not in present_keys:
            return alternate
        recent_pref = sum(1 for key in history[-6:] if key == preferred)
        recent_alt = sum(1 for key in history[-6:] if key == alternate)
        return alternate if recent_pref > recent_alt else preferred

    def _smart_keep(self, hits: Sequence[DrumHit]) -> List[DrumHit]:
        if not hits:
            return []
        best_by_key: dict[str, DrumHit] = {}
        for hit in hits:
            prev = best_by_key.get(hit.key)
            if prev is None or hit.velocity > prev.velocity:
                best_by_key[hit.key] = hit
        deduped = list(best_by_key.values())
        if not self.use_smart_keep or len(deduped) <= self.max_simultaneous:
            return sorted(deduped, key=lambda h: (self.KEY_PRIORITY.get(h.key, 99), -h.velocity))

        keep: List[DrumHit] = []
        chosen_keys: set[str] = set()
        for essential in ["F", "Q", "T", "S"]:
            hit = best_by_key.get(essential)
            if hit is not None and essential not in chosen_keys:
                keep.append(hit)
                chosen_keys.add(essential)
            if len(keep) >= self.max_simultaneous:
                return sorted(keep[:self.max_simultaneous], key=lambda h: (self.KEY_PRIORITY.get(h.key, 99), -h.velocity))

        cymbals = sorted((h for h in deduped if h.key in {"R", "Y"} and h.key not in chosen_keys), key=lambda h: (-h.velocity, self.KEY_PRIORITY.get(h.key, 99)))
        if cymbals and len(keep) < self.max_simultaneous:
            keep.append(cymbals[0])
            chosen_keys.add(cymbals[0].key)

        remaining = sorted((h for h in deduped if h.key not in chosen_keys), key=lambda h: (self.KEY_PRIORITY.get(h.key, 99), -h.velocity))
        for hit in remaining:
            if len(keep) >= self.max_simultaneous:
                break
            keep.append(hit)
            chosen_keys.add(hit.key)
        return sorted(keep[:self.max_simultaneous], key=lambda h: (self.KEY_PRIORITY.get(h.key, 99), -h.velocity))

    def _hold_for_velocity(self, key: str, velocity: int) -> float:
        hold = self.base_tap_hold
        if not self.use_velocity_rules:
            return max(0.004, min(0.040, hold))
        if key in {"Q", "F", "T", "S"}:
            if velocity <= self.ghost_velocity:
                hold *= 0.72
            elif velocity >= self.accent_velocity:
                hold *= 1.20
        elif key in {"R", "Y"}:
            if velocity <= self.ghost_velocity:
                hold *= 0.82
            elif velocity >= self.accent_velocity:
                hold *= 1.28
        else:
            if velocity <= self.ghost_velocity:
                hold *= 0.78
            elif velocity >= self.accent_velocity:
                hold *= 1.18
        return max(0.004, min(0.040, hold))

    def _density_limit(self, hits: Sequence[DrumHit]) -> List[DrumHit]:
        if not hits:
            return []
        min_gap = 1.0 / max(1.0, self.density_limit_hz)
        last_time: dict[str, float] = {}
        limited: List[DrumHit] = []
        for hit in hits:
            prev = last_time.get(hit.key)
            if prev is not None and hit.t - prev < min_gap:
                continue
            last_time[hit.key] = hit.t
            limited.append(hit)
        return limited

