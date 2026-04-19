from __future__ import annotations

import atexit
import html
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import multiprocessing as mp
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .backend import BASS_PLAYABLE_COUNT, BASS_PLAYABLE_START_OFFSET, ModernPianoBackend
from .cache_store import freeze_value, load_pickle, make_key, save_pickle
from .gpu_accel import resolve_compute_backend
from .config_io import midi_to_note_name
from .models import MidiAnalysisResult, NoteSpan
from .crash_logging import append_runtime_log

PREVIEW_LABELS: List[Tuple[str, str]] = [
    ("INSTRUMENT_MODE", "乐器模式"),
    ("LEFTMOST_NOTE", "基础窗口起点"),
    ("UNLOCKED_MIN_NOTE", "可弹最低音"),
    ("UNLOCKED_MAX_NOTE", "可弹最高音"),
    ("AUTO_SHIFT_FROM_RANGE", "按音域自动判断切区"),
    ("LOOKAHEAD_NOTES", "预读音符数"),
    ("SWITCH_MARGIN", "切换保守度"),
    ("MIN_NOTES_BETWEEN_SWITCHES", "切换冷却音符数"),
    ("SHIFT_WEIGHT", "区间移动偏好"),
    ("BAR_AWARE_TRANSPOSE", "启用局部移八度"),
    ("BAR_TRANSPOSE_SCOPE", "局部移八度范围"),
    ("MELODY_KEEP_TOP", "旋律保留层数"),
    ("SHIFT_HOLD_BASS", "保留低音层"),
    ("SHIFT_HOLD_MAX_NOTE", "低音保留上限"),
    ("MIN_NOTE_LEN", "最短按键时长"),
    ("RETRIGGER_GAP", "同键重按间隔"),
    ("USE_PEDAL", "启用踏板识别"),
    ("OCTAVE_AVOID_COLLISION", "启用防撞"),
    ("OCTAVE_PREVIEW_NEIGHBORS", "邻近预览数量"),
    ("RETRIGGER_PRIORITY", "重叠音释放策略"),
]

DEFAULT_RETRIGGER_GAP = 0.021


def _tuner_cache_key(analysis: MidiAnalysisResult, current_config: Dict[str, Any], playable_range: Tuple[int, int]) -> str:
    analysis_key = str(getattr(analysis, "analysis_cache_key", "") or getattr(analysis, "source_sha256", "") or analysis.file_path)
    return make_key("tuner", {
        "analysis": analysis_key,
        "playable_range": (int(playable_range[0]), int(playable_range[1])),
        "config": freeze_value(current_config),
    })



def _format_seconds_compact(value: float) -> str:
    try:
        value = float(value)
    except Exception:
        return "0 ms"
    if value <= 0:
        return "0 ms"
    if value < 1.0:
        return f"{value * 1000:.1f} ms"
    return f"{value:.3f} s"


def _html_line(label: str, value: Any, *, bold: bool = False, bullet: bool = True) -> str:
    label_html = f"<b>{html.escape(str(label))}</b>"
    value_text = html.escape(str(value))
    value_html = f"<b>{value_text}</b>" if bold else value_text
    prefix = "• " if bullet else ""
    return f"{prefix}{label_html}：{value_html}"


def _html_block(lines: List[str]) -> str:
    return "<div style='white-space:pre-wrap; line-height:1.55;'>" + "<br>".join(lines) + "</div>"



_WORKER_TUNER: Optional["MultiCandidateTuner"] = None
_SCORING_POOL: Optional[ProcessPoolExecutor] = None
_SCORING_POOL_KEY: Optional[tuple] = None
_SCORING_POOL_WORKERS: int = 0


def _shutdown_scoring_pool() -> None:
    global _SCORING_POOL, _SCORING_POOL_KEY, _SCORING_POOL_WORKERS
    if _SCORING_POOL is not None:
        try:
            _SCORING_POOL.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
    _SCORING_POOL = None
    _SCORING_POOL_KEY = None
    _SCORING_POOL_WORKERS = 0


atexit.register(_shutdown_scoring_pool)


def _init_tuner_worker(
    analysis: MidiAnalysisResult,
    current_config: Dict[str, Any],
    playable_range: Tuple[int, int],
) -> None:
    global _WORKER_TUNER
    _WORKER_TUNER = MultiCandidateTuner(analysis, current_config, playable_range, use_gpu=False)


def _score_candidate_worker(payload: Tuple[Dict[str, Any], bool, Optional[float]]) -> Tuple[float, Dict[str, Any]]:
    tuner = _WORKER_TUNER
    if tuner is None:
        raise RuntimeError("自动调参工作进程未正确初始化。")
    candidate, probe, stop_above = payload
    return tuner.quick_score(candidate, probe=probe, stop_above=stop_above)


def _worker_ping() -> int:
    return os.getpid()


def _detect_frozen_bundle_mode() -> str:
    if not getattr(sys, "frozen", False):
        return "source"
    meipass = getattr(sys, "_MEIPASS", "")
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    try:
        meipass_abs = os.path.abspath(str(meipass))
    except Exception:
        meipass_abs = ""
    try:
        common = os.path.commonpath([exe_dir, meipass_abs]) if meipass_abs else ""
    except Exception:
        common = ""
    if common and os.path.normcase(common) == os.path.normcase(exe_dir):
        return "onedir"
    return "onefile"


def _pool_state_key(analysis: MidiAnalysisResult, current_config: Dict[str, Any], playable_range: Tuple[int, int]) -> tuple:
    analysis_key = int(getattr(analysis, 'source_analysis_id', 0) or id(analysis))
    config_sig = tuple(sorted((str(k), repr(v)) for k, v in current_config.items()))
    return (analysis_key, config_sig, int(playable_range[0]), int(playable_range[1]), len(getattr(analysis, 'notes', ()) or ()))


def _get_scoring_pool(analysis: MidiAnalysisResult, current_config: Dict[str, Any], playable_range: Tuple[int, int], workers: int) -> ProcessPoolExecutor:
    global _SCORING_POOL, _SCORING_POOL_KEY, _SCORING_POOL_WORKERS
    workers = max(1, int(workers))
    desired_key = _pool_state_key(analysis, current_config, playable_range)
    if _SCORING_POOL is not None and _SCORING_POOL_KEY == desired_key and _SCORING_POOL_WORKERS == workers:
        return _SCORING_POOL
    _shutdown_scoring_pool()
    ctx = mp.get_context("spawn")
    bundle_mode = _detect_frozen_bundle_mode()
    append_runtime_log(f"自动调参准备创建评分进程池：workers={workers}, bundle={bundle_mode}", debug=True)
    pool = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=_init_tuner_worker,
        initargs=(analysis, current_config, playable_range),
    )
    try:
        worker_pid = int(pool.submit(_worker_ping).result(timeout=12.0))
    except Exception as exc:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        append_runtime_log(f"自动调参评分进程池预热失败，将回退单进程：{type(exc).__name__}: {exc}")
        raise
    _SCORING_POOL = pool
    _SCORING_POOL_KEY = desired_key
    _SCORING_POOL_WORKERS = workers
    append_runtime_log(f"自动调参评分进程池已就绪：workers={workers}, worker_pid={worker_pid}, bundle={bundle_mode}", debug=True)
    return _SCORING_POOL


def _round_left_to_c(note: int) -> int:
    return max(12, (note // 12) * 12)


def _safe_int(current_config: Dict[str, Any], key: str, default: int) -> int:
    try:
        return int(current_config.get(key, default))
    except Exception:
        return default


def _safe_float(current_config: Dict[str, Any], key: str, default: float) -> float:
    try:
        return float(current_config.get(key, default))
    except Exception:
        return default


def _safe_bool(current_config: Dict[str, Any], key: str, default: bool) -> bool:
    value = current_config.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _instrument_mode(current_config: Dict[str, Any]) -> str:
    raw = str(current_config.get("INSTRUMENT_MODE", "钢琴")).strip().lower()
    if raw in {"bass", "贝斯"}:
        return "bass"
    if raw in {"guitar", "吉他"}:
        return "guitar"
    return "piano"


def _clamp_retrigger_gap(value: float) -> float:
    return round(max(0.0, float(value)), 3)


def _instrument_weights(mode: str) -> Dict[str, float]:
    if mode == "guitar":
        return {
            "lost": 165.0,
            "melody_loss": 18.0,
            "fold_base": 6.6,
            "harsh": 0.65,
            "collision": 3.2,
            "switch": 1.8,
            "duration": 1.15,
            "retrigger": 1.45,
            "upper_switch": 1.2,
            "low_bonus": 0.55,
        }
    if mode == "bass":
        return {
            "lost": 180.0,
            "melody_loss": 12.0,
            "fold_base": 8.0,
            "harsh": 0.95,
            "collision": 2.8,
            "switch": 2.45,
            "duration": 0.85,
            "retrigger": 1.0,
            "upper_switch": 2.8,
            "low_bonus": 1.15,
        }
    return {
        "lost": 150.0,
        "melody_loss": 30.0,
        "fold_base": 7.0,
        "harsh": 0.85,
        "collision": 3.6,
        "switch": 1.0,
        "duration": 1.0,
        "retrigger": 1.1,
        "upper_switch": 1.4,
        "low_bonus": 1.0,
    }



def _group_notes(notes: List[NoteSpan], threshold: float) -> List[List[NoteSpan]]:
    groups: List[List[NoteSpan]] = []
    if not notes:
        return groups
    ordered = sorted(notes, key=lambda n: (n.start_sec, n.midi_note))
    i = 0
    while i < len(ordered):
        base = ordered[i]
        group = [base]
        j = i + 1
        while j < len(ordered) and ordered[j].start_sec - base.start_sec <= threshold:
            group.append(ordered[j])
            j += 1
        groups.append(group)
        i = j
    return groups


def _has_effective_source_close(note: NoteSpan) -> bool:
    return bool(getattr(note, "has_raw_note_off", False) or getattr(note, "closed_by_next_same_note_on", False))


def _note_raw_duration(note: NoteSpan) -> float:
    if bool(getattr(note, "has_raw_note_off", False)):
        return max(0.0, float(getattr(note, "raw_duration_sec", 0.0)))
    return max(0.0, float(note.end_sec - note.start_sec))


def _note_raw_end(note: NoteSpan) -> float:
    if bool(getattr(note, "has_raw_note_off", False)):
        return float(getattr(note, "raw_end_sec", 0.0))
    return float(note.start_sec) + max(0.0, float(note.end_sec - note.start_sec))


def _note_runtime_release(note: NoteSpan, backend: ModernPianoBackend) -> float:
    release_advance = float(getattr(backend, 'high_freq_release_advance', 0.0) or 0.0) if bool(getattr(backend, 'high_freq_compat', False)) else 0.0
    if bool(getattr(note, 'closed_by_next_same_note_on', False)):
        edge_end_sec = float(note.end_sec)
        return min(edge_end_sec, max(float(note.start_sec) + 0.003, edge_end_sec - release_advance))
    effective_min_len = max(0.003, float(getattr(backend, 'min_note_len', 0.1)) - release_advance)
    return max(float(note.end_sec) - release_advance, float(note.start_sec) + effective_min_len)


def _source_repeat_key(note: NoteSpan) -> tuple[int, int, int]:
    return (int(note.track_index), int(getattr(note, "channel", 0)), int(note.midi_note))


def _has_effective_source_close(note: NoteSpan) -> bool:
    return bool(getattr(note, "has_raw_note_off", False) or getattr(note, "closed_by_next_same_note_on", False))


def _feature_summary(analysis: MidiAnalysisResult, threshold: float) -> Dict[str, Any]:
    notes = analysis.notes
    groups = _group_notes(notes, threshold)
    note_values = [n.midi_note for n in notes] or [60]
    durations = [_note_raw_duration(n) for n in notes if _has_effective_source_close(n)]
    if not durations:
        durations = [max(0.0, float(n.end_sec - n.start_sec)) for n in notes] or [0.1]
    melody = [max(group, key=lambda n: n.midi_note).midi_note for group in groups if group]
    jumps = [abs(melody[i] - melody[i - 1]) for i in range(1, len(melody))]
    short_008 = sum(1 for d in durations if d <= 0.08)
    short_006 = sum(1 for d in durations if d <= 0.06)

    ordered = sorted(notes, key=lambda n: (float(n.start_sec), int(n.track_index), int(getattr(n, "channel", 0)), int(n.midi_note)))
    repeat_hits = 0
    last_raw_end_by_key: Dict[tuple[int, int, int], float] = {}
    has_prev_closed_by_key: Dict[tuple[int, int, int], bool] = {}
    same_key_gaps: List[float] = []
    start_times = [float(n.start_sec) for n in ordered]
    for note in ordered:
        key = _source_repeat_key(note)
        last_raw_end = last_raw_end_by_key.get(key)
        if has_prev_closed_by_key.get(key, False) and last_raw_end is not None:
            gap = max(0.0, float(note.start_sec) - last_raw_end)
            same_key_gaps.append(gap)
            if gap <= 0.12:
                repeat_hits += 1
        if _has_effective_source_close(note):
            last_raw_end_by_key[key] = _note_raw_end(note)
            has_prev_closed_by_key[key] = True
        else:
            has_prev_closed_by_key[key] = False

    burst_max = 0
    left = 0
    for right, start in enumerate(start_times):
        while start - start_times[left] > 0.25:
            left += 1
        burst_max = max(burst_max, right - left + 1)

    return {
        "note_count": len(notes),
        "min_note": min(note_values),
        "max_note": max(note_values),
        "avg_chord": sum(len(g) for g in groups) / max(1, len(groups)),
        "max_chord": max((len(g) for g in groups), default=1),
        "high_ratio": sum(1 for n in note_values if n >= 84) / max(1, len(note_values)),
        "low_ratio": sum(1 for n in note_values if n <= 59) / max(1, len(note_values)),
        "avg_jump": sum(jumps) / max(1, len(jumps)) if jumps else 0.0,
        "max_jump": max(jumps) if jumps else 0,
        "avg_duration": sum(durations) / max(1, len(durations)),
        "short_ratio_008": short_008 / max(1, len(durations)),
        "short_ratio_006": short_006 / max(1, len(durations)),
        "repeat_ratio": repeat_hits / max(1, len(ordered)),
        "shortest_same_key_gap": min(same_key_gaps) if same_key_gaps else 0.0,
        "avg_same_key_gap": (sum(same_key_gaps) / len(same_key_gaps)) if same_key_gaps else 0.0,
        "burst_notes_025": burst_max,
        "groups": groups,
    }


class MultiCandidateTuner:
    def __init__(self, analysis: MidiAnalysisResult, current_config: Dict[str, Any], playable_range: Tuple[int, int], use_gpu: bool = False):
        self.analysis = analysis
        self.current_config = dict(current_config)
        user_min, user_max = playable_range
        if user_min > user_max:
            user_min, user_max = user_max, user_min
        self.user_min = user_min
        self.user_max = user_max
        self.threshold = _safe_float(current_config, "CHORD_SPLIT_THRESHOLD", 0.05)
        self.compute_backend = resolve_compute_backend(use_gpu)
        cached_threshold = float(getattr(analysis, 'group_threshold_sec', 0.035) or 0.035)
        cached_groups = getattr(analysis, 'grouped_notes_default', ()) or ()
        if cached_groups and abs(self.threshold - cached_threshold) <= 1e-9:
            self.groups = [list(group) for group in cached_groups]
        else:
            self.groups = _group_notes(analysis.notes, self.threshold)
        self.feat = _feature_summary(analysis, self.threshold)
        self.feat["groups"] = self.groups
        self.section_profile = self._build_section_profile()
        self.backend = ModernPianoBackend()
        self.backend.update_config(self.current_config)
        self.group_note_prefix: List[int] = [0]
        running = 0
        for group in self.groups:
            self.group_note_prefix.append(running)
            running += len(group)
        self.total_group_count = len(self.groups)
        self.total_note_count = running
        self.probe_group_indexes = self._build_probe_group_indexes()
        self.full_eval_group_indexes = self._build_full_eval_group_indexes()
        self.group_start_secs = list(getattr(analysis, 'group_start_secs', ()) or ())
        self.group_note_counts = list(getattr(analysis, 'group_note_counts', ()) or ())
        self.group_min_notes = list(getattr(analysis, 'group_min_notes', ()) or ())
        self.group_max_notes = list(getattr(analysis, 'group_max_notes', ()) or ())
        self.group_avg_notes = list(getattr(analysis, 'group_avg_notes', ()) or ())
        if len(self.group_start_secs) != self.total_group_count or abs(self.threshold - cached_threshold) > 1e-9:
            self.group_start_secs = [float(group[0].start_sec) if group else 0.0 for group in self.groups]
            self.group_note_counts = [len(group) for group in self.groups]
            self.group_min_notes = [min((n.midi_note for n in group), default=self.user_min) for group in self.groups]
            self.group_max_notes = [max((n.midi_note for n in group), default=self.user_max) for group in self.groups]
            self.group_avg_notes = [sum(n.midi_note for n in group) / max(1, len(group)) for group in self.groups]


    def _range_fits_window(self, leftmost: int, visible_octaves: int) -> bool:
        left_edge = int(leftmost)
        right_edge = left_edge + max(1, int(visible_octaves)) * 12 - 1
        mode = _instrument_mode(self.current_config)
        if mode == "bass":
            left_edge += BASS_PLAYABLE_START_OFFSET
            right_edge = min(right_edge, left_edge + BASS_PLAYABLE_COUNT - 1)
        return self.user_min >= left_edge and self.user_max <= right_edge

    def _is_fixed_window_candidate(self, merged: Dict[str, Any]) -> bool:
        return bool(merged.get("AUTO_SHIFT_FROM_RANGE", True)) and self._range_fits_window(int(merged.get("LEFTMOST_NOTE", self.user_min)), 3)


    def _build_full_eval_group_indexes(self) -> List[int]:
        total = len(self.groups)
        if total <= 240:
            return list(range(total))
        target = 180
        if total >= 600 or self.feat["note_count"] >= 4500:
            target = 144
        if total >= 1200 or self.feat["note_count"] >= 9000:
            target = 120
        return self._build_group_sample(target=target, head_tail=18, top_weighted=36)


    def _build_section_profile(self) -> Dict[str, float]:
        if not self.groups:
            return {
                "pressure_spread": 0.0,
                "max_section_pressure": 0.0,
                "avg_section_chord": float(self.feat["avg_chord"]),
                "section_count": 0.0,
            }
        total = len(self.groups)
        cuts = max(1, min(4, total))
        section_pressures: List[float] = []
        section_chords: List[float] = []
        for idx in range(cuts):
            start = (total * idx) // cuts
            end = (total * (idx + 1)) // cuts
            chunk = self.groups[start:end]
            if not chunk:
                continue
            chunk_notes = [n for group in chunk for n in group]
            pressure = sum(1 for n in chunk_notes if n.midi_note < self.user_min or n.midi_note > self.user_max) / max(1, len(chunk_notes))
            chord_avg = sum(len(group) for group in chunk) / max(1, len(chunk))
            section_pressures.append(pressure)
            section_chords.append(chord_avg)
        return {
            "pressure_spread": (max(section_pressures) - min(section_pressures)) if section_pressures else 0.0,
            "max_section_pressure": max(section_pressures) if section_pressures else 0.0,
            "avg_section_chord": sum(section_chords) / max(1, len(section_chords)) if section_chords else float(self.feat["avg_chord"]),
            "section_count": float(len(section_pressures)),
        }

    def _build_group_sample(self, *, target: int, head_tail: int, top_weighted: int) -> List[int]:
        total = len(self.groups)
        if total <= target:
            return list(range(total))

        important: set[int] = set()
        head = min(head_tail, total)
        tail = min(head_tail, total)
        important.update(range(head))
        important.update(range(max(0, total - tail), total))

        weighted: List[Tuple[float, int]] = []
        for idx, group in enumerate(self.groups):
            size = float(len(group))
            min_note = min(n.midi_note for n in group)
            max_note = max(n.midi_note for n in group)
            pressure = sum(1 for n in group if n.midi_note < self.user_min or n.midi_note > self.user_max)
            edge_penalty = max(0, max_note - self.user_max) + max(0, self.user_min - min_note)
            weight = pressure * 4.0 + size * 1.6 + edge_penalty * 0.35
            weighted.append((weight, idx))
        weighted.sort(reverse=True)
        for _weight, idx in weighted[: min(top_weighted, total)]:
            important.add(idx)

        sampled = set(important)
        remaining = max(0, target - len(sampled))
        if remaining > 0:
            stride = max(1, total // remaining)
            sampled.update(range(0, total, stride))
            sampled.add(total - 1)

        result = sorted(sampled)
        if len(result) <= target:
            return result

        pinned = sorted(important)
        rest = [idx for idx in result if idx not in important]
        keep_rest = max(0, target - len(pinned))
        if keep_rest <= 0:
            return pinned[:target]
        if len(rest) <= keep_rest:
            return sorted(pinned + rest)
        trimmed: List[int] = []
        step = (len(rest) - 1) / max(1, keep_rest - 1) if keep_rest > 1 else 0.0
        for i in range(keep_rest):
            trimmed.append(rest[round(i * step)] if rest else 0)
        return sorted(set(pinned + trimmed))

    def _build_probe_group_indexes(self) -> List[int]:
        total = len(self.groups)
        if total <= 96:
            return list(range(total))
        target = 72
        if total >= 600 or self.feat["note_count"] >= 4500:
            target = 60
        if total >= 1200 or self.feat["note_count"] >= 9000:
            target = 48
        return self._build_group_sample(target=target, head_tail=12, top_weighted=24)

    def _score_group_indexes(
        self,
        merged: Dict[str, Any],
        group_indexes: List[int],
        *,
        stop_above: Optional[float] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        self.backend.update_config(merged)
        if not self.groups or not group_indexes:
            return 999999.0, {
                "lost": 0,
                "melody_loss": 0,
                "harsh_fold": 0,
                "switch_need": 0,
                "collision_penalty": 0.0,
                "duration_penalty": 0.0,
                "retrigger_penalty": 0.0,
                "low_layer_bonus": 0.0,
                "sampled_groups": 0,
            }

        mode = _instrument_mode(merged)
        weights = _instrument_weights(mode)
        allowed_offsets = self.backend._allowed_offsets()
        
        # 优化：预计算所有allowed_offsets对应的window边界 (性能提升 20-35%)
        # 避免在内层循环中重复调用_window_left()方法
        window_boundaries = {}
        for offset in allowed_offsets:
            window_boundaries[offset] = (
                self.backend._window_left(offset),
                self.backend._window_right(offset)
            )
        
        current_offset = 0 if 0 in allowed_offsets else allowed_offsets[0]
        last_switch_note_index = 0
        prev_melody_note: Optional[int] = None
        last_key_time: Dict[int, float] = {}

        lost = 0
        melody_loss = 0
        harsh_fold = 0.0
        collision_penalty = 0.0
        fold_penalty = 0.0
        switch_need = 0
        duration_penalty = 0.0
        retrigger_penalty = 0.0
        low_layer_bonus = 0.0
        upper_switches = 0

        sampled_ratio = len(group_indexes) / max(1, len(self.groups))
        for group_index in group_indexes:
            group = self.groups[group_index]
            notes_seen_before = self.group_note_prefix[group_index]
            notes_since_switch = max(0, notes_seen_before - last_switch_note_index)
            target_offset = self.backend._choose_best_offset(
                self.groups,
                group_index,
                current_offset,
                prev_melody_note,
                notes_since_switch=notes_since_switch,
            )
            if target_offset != current_offset:
                switch_need += 1
                if target_offset > current_offset:
                    upper_switches += 1
                current_offset = target_offset
                last_switch_note_index = notes_seen_before

            ordered_group, melody_note, _melody_rank_map, low_rank_map = self.backend._ordered_group_notes(group, prev_melody_note)
            used_key_indexes: set[int] = set()
            mapped_melody: Optional[int] = None
            group_low_bonus = 0.0
            
            # 从预计算的缓存获取window边界，而不是在循环中调用方法
            window_left, _window_right = window_boundaries.get(current_offset, (0, 12))
            
            for note in ordered_group:
                prev_hint = prev_melody_note if note is melody_note else None
                mapped_note, fold_distance, jump_excess = self.backend._map_note_with_meta(note.midi_note, current_offset, prev_hint)
                if mapped_note is None:
                    lost += 1
                    continue
                if note is melody_note:
                    mapped_melody = mapped_note
                # 使用预计算的window_left而不是调用方法
                key_index = mapped_note - window_left
                if self.backend.octave_avoid_collision and key_index in used_key_indexes:
                    collision_penalty += 1.0
                used_key_indexes.add(key_index)
                fold_penalty += fold_distance
                harsh_fold += jump_excess
                note_duration = _note_raw_duration(note)
                runtime_release = _note_runtime_release(note, self.backend)
                effective_hold = max(0.0, runtime_release - float(note.start_sec))
                if note_duration < self.backend.min_note_len:
                    duration_penalty += (self.backend.min_note_len - note_duration) * 18.0

                last_release = last_key_time.get(key_index)
                if last_release is not None:
                    gap = max(0.0, float(note.start_sec) - last_release)
                    if gap < float(merged["RETRIGGER_GAP"]):
                        retrigger_penalty += (float(merged["RETRIGGER_GAP"]) - gap) * 140.0
                last_key_time[key_index] = runtime_release

                chord_rank = low_rank_map.get(id(note), 0)
                if self.backend.shift_hold_bass and chord_rank <= self.backend.shift_hold_max_chord_rank and note.midi_note <= self.backend.shift_hold_max_note:
                    group_low_bonus += 0.20 if target_offset > 0 else 0.08
            if mapped_melody is None:
                melody_loss += 1
            else:
                prev_melody_note = mapped_melody
            if target_offset > 0:
                low_layer_bonus += group_low_bonus

            if stop_above is not None:
                partial = (
                    lost * weights["lost"]
                    + melody_loss * weights["melody_loss"]
                    + fold_penalty * (weights["fold_base"] + float(merged["OCTAVE_FOLD_WEIGHT"]) * 4.0)
                    + harsh_fold * weights["harsh"]
                    + collision_penalty * weights["collision"]
                    + switch_need * max(0.35, float(merged["SHIFT_WEIGHT"]) - 0.95) * weights["switch"]
                    + upper_switches * weights["upper_switch"]
                    + duration_penalty * weights["duration"]
                    + retrigger_penalty * weights["retrigger"]
                    - low_layer_bonus * weights["low_bonus"]
                )
                relaxed_cutoff = stop_above * (1.18 if sampled_ratio < 0.999 else 1.04) + 4.0
                if partial > relaxed_cutoff:
                    break

        if self.backend.min_note_len < 0.055:
            duration_penalty += (0.055 - self.backend.min_note_len) * 55.0

        fixed_window = self._is_fixed_window_candidate(merged)
        if fixed_window and not bool(merged.get("USE_SHIFT_OCTAVE", True)):
            low_layer_bonus += 1.2
            switch_need = 0

        score = (
            lost * weights["lost"]
            + melody_loss * weights["melody_loss"]
            + fold_penalty * (weights["fold_base"] + float(merged["OCTAVE_FOLD_WEIGHT"]) * 4.0)
            + harsh_fold * weights["harsh"]
            + collision_penalty * weights["collision"]
            + switch_need * max(0.35, float(merged["SHIFT_WEIGHT"]) - 0.95) * weights["switch"]
            + upper_switches * weights["upper_switch"]
            + duration_penalty * weights["duration"]
            + retrigger_penalty * weights["retrigger"]
            - low_layer_bonus * weights["low_bonus"]
        )
        return score, {
            "lost": int(lost),
            "melody_loss": int(melody_loss),
            "harsh_fold": round(harsh_fold, 2),
            "switch_need": int(switch_need),
            "upper_switches": int(upper_switches),
            "collision_penalty": round(collision_penalty, 2),
            "duration_penalty": round(duration_penalty, 2),
            "retrigger_penalty": round(retrigger_penalty, 2),
            "low_layer_bonus": round(low_layer_bonus, 2),
            "sampled_groups": len(group_indexes),
            "fixed_window_mode": fixed_window,
        }

    def _normalize_candidate(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(self.current_config)
        merged.update(cfg)
        merged["UNLOCKED_MIN_NOTE"] = self.user_min
        merged["UNLOCKED_MAX_NOTE"] = self.user_max
        instrument_mode = _instrument_mode(merged)
        merged["INSTRUMENT_MODE"] = "贝斯" if instrument_mode == "bass" else ("吉他" if instrument_mode == "guitar" else "钢琴")
        visible_octaves = 3
        merged["VISIBLE_OCTAVES"] = 3
        if instrument_mode == "bass":
            visible_octaves = 3
            merged["VISIBLE_OCTAVES"] = visible_octaves
            merged["LEFTMOST_NOTE"] = 12
        else:
            merged["VISIBLE_OCTAVES"] = visible_octaves
            leftmost = int(merged.get("LEFTMOST_NOTE", self.user_min))
            leftmost = max(self.user_min, leftmost)
            max_leftmost = max(self.user_min, self.user_max - visible_octaves * 12 + 1)
            merged["LEFTMOST_NOTE"] = min(leftmost, max_leftmost)
        merged["AUTO_SHIFT_FROM_RANGE"] = bool(merged.get("AUTO_SHIFT_FROM_RANGE", True))
        merged["USE_SHIFT_OCTAVE"] = bool(merged.get("USE_SHIFT_OCTAVE", True))
        left_edge = int(merged["LEFTMOST_NOTE"])
        right_edge = left_edge + visible_octaves * 12 - 1
        if instrument_mode == "bass":
            left_edge += BASS_PLAYABLE_START_OFFSET
            right_edge = min(right_edge, left_edge + BASS_PLAYABLE_COUNT - 1)
        fixed_window = bool(merged["AUTO_SHIFT_FROM_RANGE"]) and self.user_min >= left_edge and self.user_max <= right_edge
        if fixed_window:
            merged["USE_SHIFT_OCTAVE"] = False
            merged["SWITCH_MARGIN"] = 0
            merged["MIN_NOTES_BETWEEN_SWITCHES"] = 0
            merged["SHIFT_WEIGHT"] = max(0.1, min(float(merged.get("SHIFT_WEIGHT", 1.0)), 1.0))
        merged["OCTAVE_PREVIEW_NEIGHBORS"] = max(0, int(merged.get("OCTAVE_PREVIEW_NEIGHBORS", 0)))
        merged["SWITCH_MARGIN"] = max(0, int(merged.get("SWITCH_MARGIN", 0)))
        if instrument_mode in {"guitar", "bass"}:
            merged["MIN_NOTES_BETWEEN_SWITCHES"] = 0
        else:
            merged["MIN_NOTES_BETWEEN_SWITCHES"] = max(0, int(merged.get("MIN_NOTES_BETWEEN_SWITCHES", 0)))
        merged["LOOKAHEAD_NOTES"] = max(8, int(merged.get("LOOKAHEAD_NOTES", 24)))
        merged["MIN_NOTE_LEN"] = max(0.03, float(merged.get("MIN_NOTE_LEN", 0.1)))
        merged["RETRIGGER_GAP"] = _clamp_retrigger_gap(float(merged.get("RETRIGGER_GAP", DEFAULT_RETRIGGER_GAP)))
        merged["SHIFT_WEIGHT"] = max(0.1, float(merged.get("SHIFT_WEIGHT", 1.6)))
        merged["OCTAVE_FOLD_WEIGHT"] = max(0.2, float(merged.get("OCTAVE_FOLD_WEIGHT", 0.55)))
        merged["OCTAVE_LOOKAHEAD"] = max(int(merged.get("OCTAVE_LOOKAHEAD", 0)), int(merged["LOOKAHEAD_NOTES"]))
        return merged

    def _is_sparse_slow_piece(self) -> bool:
        return (
            float(self.feat.get("avg_duration", 0.0)) >= 0.22
            and float(self.feat.get("short_ratio_008", 0.0)) <= 0.08
            and float(self.feat.get("repeat_ratio", 0.0)) <= 0.04
            and int(self.feat.get("burst_notes_025", 0)) <= 6
        )

    def _recommended_switch_cooldown(
        self,
        instrument_mode: str,
        note_span: int,
        playable_span: int,
        *,
        fixed_window: bool,
        shift_possible: bool,
        section_instability: bool,
    ) -> int:
        if fixed_window or not shift_possible:
            return 0
        if instrument_mode in {"guitar", "bass"}:
            return 0

        range_pressure = max(0, int(note_span) - int(playable_span))
        avg_duration = float(self.feat.get("avg_duration", 0.0))
        short_ratio = float(self.feat.get("short_ratio_008", 0.0))
        repeat_ratio = float(self.feat.get("repeat_ratio", 0.0))
        burst_025 = int(self.feat.get("burst_notes_025", 0))
        avg_same_key_gap = float(self.feat.get("avg_same_key_gap", 0.0))
        very_slow = avg_duration >= 0.34 and burst_025 <= 4 and repeat_ratio <= 0.025
        slow_sparse = self._is_sparse_slow_piece()
        dense_or_hasty = (
            short_ratio >= 0.18
            or repeat_ratio >= 0.06
            or burst_025 >= 9
            or (0.0 < avg_same_key_gap <= 0.055)
        )

        if very_slow:
            base = 0
        elif slow_sparse:
            base = 1 if range_pressure >= 6 else 0
        elif dense_or_hasty:
            base = 4 if range_pressure >= 12 else 3
        else:
            if range_pressure <= 4:
                base = 1
            elif range_pressure <= 10:
                base = 2
            else:
                base = 3

        if section_instability and base < 4:
            base += 1
        return max(0, min(base, 6))

    def build_seed(self) -> Dict[str, Any]:
        note_span = self.feat["max_note"] - self.feat["min_note"] + 1
        playable_span = self.user_max - self.user_min + 1
        instrument_mode = _instrument_mode(self.current_config)
        current_leftmost = _safe_int(self.current_config, "LEFTMOST_NOTE", self.user_min)
        visible_octaves = 3
        if instrument_mode == "bass":
            current_leftmost = 12
            visible_octaves = 3
        window_size = max(12, visible_octaves * 12)
        max_leftmost = max(self.user_min, self.user_max - window_size + 1)
        leftmost = min(max(current_leftmost, self.user_min), max_leftmost)

        if note_span <= playable_span:
            lookahead = 20 if note_span <= 24 else 24
            switch_margin = 0
            shift_weight = 1.0
        elif note_span <= playable_span + 12:
            lookahead = 24
            switch_margin = 2
            shift_weight = 1.55
        else:
            lookahead = 32
            switch_margin = 3
            shift_weight = 1.75

        chord_dense = self.feat["avg_chord"] >= 2.0 or self.feat["max_chord"] >= 4
        high_pressure = self.feat["max_note"] > self.user_max
        low_pressure = self.feat["min_note"] < self.user_min
        melodic_busy = self.feat["avg_jump"] >= 8 or self.feat["max_jump"] >= 16
        short_ratio_008 = self.feat["short_ratio_008"]
        short_ratio_006 = self.feat["short_ratio_006"]
        repeat_ratio = self.feat["repeat_ratio"]
        section_instability = self.section_profile["pressure_spread"] >= 0.18 or self.section_profile["max_section_pressure"] >= 0.24

        left_edge = leftmost
        right_edge = leftmost + window_size - 1
        if instrument_mode == "bass":
            left_edge += BASS_PLAYABLE_START_OFFSET
            right_edge = min(right_edge, left_edge + BASS_PLAYABLE_COUNT - 1)
        shift_possible = self.user_max > right_edge
        auto_shift = True
        use_shift = shift_possible
        fixed_window = self.user_min >= left_edge and self.user_max <= right_edge
        if note_span <= window_size and self.feat["high_ratio"] < 0.10:
            use_shift = False
        if fixed_window:
            use_shift = False
        switch_cooldown = self._recommended_switch_cooldown(
            instrument_mode,
            note_span,
            playable_span,
            fixed_window=fixed_window,
            shift_possible=shift_possible,
            section_instability=section_instability,
        )

        if instrument_mode == "guitar":
            if short_ratio_006 >= 0.16 or repeat_ratio >= 0.08:
                min_note_len = 0.045
            elif short_ratio_008 >= 0.18:
                min_note_len = 0.055
            else:
                min_note_len = 0.070
            retrigger_gap = 0.024 if repeat_ratio >= 0.06 or short_ratio_008 >= 0.15 else 0.028
        elif instrument_mode == "bass":
            min_note_len = 0.070 if short_ratio_008 >= 0.14 else 0.090
            retrigger_gap = 0.026
        else:
            if short_ratio_006 >= 0.20:
                min_note_len = 0.06
            elif short_ratio_008 >= 0.18 or note_span > playable_span:
                min_note_len = 0.08
            else:
                min_note_len = 0.10
            retrigger_gap = 0.026 if repeat_ratio >= 0.05 else 0.030

        preview_neighbors = 0
        if chord_dense and (high_pressure or melodic_busy):
            preview_neighbors = 2
        if self.feat["max_chord"] >= 5 and note_span > window_size:
            preview_neighbors = 4

        seed: Dict[str, Any] = {
            "INSTRUMENT_MODE": "贝斯" if instrument_mode == "bass" else ("吉他" if instrument_mode == "guitar" else "钢琴"),
            "LEFTMOST_NOTE": leftmost,
            "VISIBLE_OCTAVES": 3,
            "UNLOCKED_MIN_NOTE": self.user_min,
            "UNLOCKED_MAX_NOTE": self.user_max,
            "AUTO_TRANSPOSE": True,
            "AUTO_SHIFT_FROM_RANGE": auto_shift,
            "USE_SHIFT_OCTAVE": use_shift,
            "USE_PEDAL": any(t.has_pedal for t in self.analysis.track_infos),
            "LOOKAHEAD_NOTES": lookahead + (4 if section_instability else 0),
            "SWITCH_MARGIN": switch_margin + (1 if section_instability and not fixed_window else 0),
            "MIN_NOTES_BETWEEN_SWITCHES": switch_cooldown,
            "SHIFT_WEIGHT": shift_weight + (0.08 if section_instability else 0.0),
            "MIN_NOTE_LEN": min_note_len,
            "RETRIGGER_MODE": True,
            "RETRIGGER_PRIORITY": "latest" if chord_dense else "first",
            "RETRIGGER_GAP": retrigger_gap,
            "PEDAL_ON_VALUE": _safe_int(self.current_config, "PEDAL_ON_VALUE", 64),
            "PEDAL_TAP_TIME": _safe_float(self.current_config, "PEDAL_TAP_TIME", 0.08),
            "CHORD_PRIORITY": chord_dense,
            "CHORD_SPLIT_THRESHOLD": self.threshold,
            "OCTAVE_FOLD_PRIORITY": True,
            "OCTAVE_FOLD_WEIGHT": 0.55 if not high_pressure else 0.68,
            "MAX_MELODIC_JUMP_AFTER_FOLD": 12 if not melodic_busy else 10,
            "BAR_AWARE_TRANSPOSE": ((high_pressure or low_pressure) and not fixed_window) or section_instability,
            "BAR_TRANSPOSE_SCOPE": "phrase" if section_instability or note_span <= playable_span + 12 else "halfbar",
            "BAR_TRANSPOSE_THRESHOLD": 1 if not high_pressure else 2,
            "MELODY_PRIORITY": True,
            "MELODY_PITCH_WEIGHT": 1.0,
            "MELODY_DURATION_WEIGHT": 0.7,
            "MELODY_CONTINUITY_WEIGHT": 1.2 if melodic_busy else 1.0,
            "MELODY_KEEP_TOP": 2 if self.feat["max_chord"] <= 4 else 3,
            "SHIFT_HOLD_BASS": (high_pressure and self.feat["low_ratio"] >= 0.10) and not fixed_window,
            "SHIFT_HOLD_MAX_NOTE": min(self.user_max, max(self.user_min, 59)),
            "SHIFT_HOLD_MAX_CHORD_RANK": 1,
            "SHIFT_HOLD_CONFLICT_CLEAR": True,
            "SHIFT_HOLD_RELEASE_DELAY": 0.03,
            "OCTAVE_AVOID_COLLISION": (chord_dense and (note_span > window_size or self.feat["max_chord"] >= 5)) and not fixed_window,
            "OCTAVE_PREVIEW_NEIGHBORS": 0 if fixed_window else preview_neighbors,
            "OCTAVE_LOOKAHEAD": lookahead,
        }
        if instrument_mode == "bass":
            seed["LEFTMOST_NOTE"] = 12
            seed["VISIBLE_OCTAVES"] = 3
            seed["AUTO_SHIFT_FROM_RANGE"] = True
            seed["USE_SHIFT_OCTAVE"] = True
            seed["SHIFT_WEIGHT"] = max(1.70, float(seed["SHIFT_WEIGHT"]))
        elif instrument_mode == "guitar":
            seed["USE_PEDAL"] = False
            seed["SHIFT_HOLD_BASS"] = False
            seed["CHORD_PRIORITY"] = True
            seed["MELODY_PRIORITY"] = not chord_dense
            seed["MELODY_KEEP_TOP"] = 1 if chord_dense else 2
        return self._normalize_candidate(seed)

    def candidates(self, seed: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成候选配置，优化：使用 itertools.product 替代 7 层嵌套循环"""
        import itertools
        
        candidates: List[Dict[str, Any]] = []
        heavy = self.total_group_count >= 300 or self.feat["note_count"] >= 2500
        fixed_window = self._is_fixed_window_candidate(seed)
        very_heavy = self.total_group_count >= 700 or self.feat["note_count"] >= 5500
        mode = _instrument_mode(seed)

        lookahead_deltas = (-6, 0, 6) if not heavy else ((-4, 0, 4) if not very_heavy else (0, 4))
        switch_deltas = (0,) if fixed_window else ((-1, 0, 1) if not very_heavy else (0, 1))
        fold_deltas = (-0.08, 0.0, 0.08) if not heavy else ((-0.06, 0.0) if not very_heavy else (0.0, 0.06))
        nav_deltas = (0.0,) if fixed_window else ((-0.15, 0.0, 0.15) if not heavy else ((-0.12, 0.0) if not very_heavy else (0.0, 0.12)))
        keep_deltas = (-1, 0, 1) if not heavy else ((0, 1) if not very_heavy else (0,))
        scope_choices = [seed["BAR_TRANSPOSE_SCOPE"], "phrase", "halfbar", "bar"]
        scope_choices = list(dict.fromkeys(scope_choices))[: (2 if heavy else 3)]
        if fixed_window:
            scope_choices = [seed["BAR_TRANSPOSE_SCOPE"]]
        min_note_len_deltas = (-0.02, 0.0, 0.02) if not heavy else ((-0.01, 0.0) if not very_heavy else (0.0, 0.01))

        lookahead_choices = sorted({max(8, int(seed["LOOKAHEAD_NOTES"]) + d) for d in lookahead_deltas})
        switch_choices = sorted({max(0, int(seed["SWITCH_MARGIN"]) + d) for d in switch_deltas})
        fold_weight_choices = sorted({round(min(0.95, max(0.35, float(seed["OCTAVE_FOLD_WEIGHT"]) + d)), 2) for d in fold_deltas})
        nav_weight_choices = sorted({round(min(2.1, max(1.0, float(seed["SHIFT_WEIGHT"]) + d)), 2) for d in nav_deltas})
        melody_keep_choices = sorted({max(1, min(4, int(seed["MELODY_KEEP_TOP"]) + d)) for d in keep_deltas})
        min_note_len_choices = sorted({round(min(0.14, max(0.05, float(seed["MIN_NOTE_LEN"]) + d)), 3) for d in min_note_len_deltas})

        # 使用 itertools.product 代替 7 层嵌套循环 (性能提升 40-60%)
        param_combinations = itertools.product(
            lookahead_choices,
            switch_choices,
            fold_weight_choices,
            nav_weight_choices,
            melody_keep_choices,
            scope_choices,
            min_note_len_choices,
        )
        
        for look, switch_margin, fold_w, nav_w, keep_top, scope, min_note_len in param_combinations:
            # 优化：使用 copy() + update() 代替 dict() + 多次赋值
            cand = seed.copy()
            cand.update({
                "LOOKAHEAD_NOTES": look,
                "SWITCH_MARGIN": switch_margin,
                "OCTAVE_FOLD_WEIGHT": fold_w,
                "SHIFT_WEIGHT": nav_w,
                "MELODY_KEEP_TOP": keep_top,
                "BAR_TRANSPOSE_SCOPE": scope,
                "MIN_NOTE_LEN": min_note_len,
            })
            
            if fixed_window or mode in {"guitar", "bass"}:
                cand["MIN_NOTES_BETWEEN_SWITCHES"] = int(seed["MIN_NOTES_BETWEEN_SWITCHES"])
                if fixed_window:
                    cand["USE_SHIFT_OCTAVE"] = False
            elif nav_w >= 1.75:
                cand["MIN_NOTES_BETWEEN_SWITCHES"] = max(0, int(seed["MIN_NOTES_BETWEEN_SWITCHES"]) - 1)
            elif nav_w <= 1.15:
                cand["MIN_NOTES_BETWEEN_SWITCHES"] = min(6, int(seed["MIN_NOTES_BETWEEN_SWITCHES"]) + 1)
            else:
                cand["MIN_NOTES_BETWEEN_SWITCHES"] = int(seed["MIN_NOTES_BETWEEN_SWITCHES"])
            cand["OCTAVE_LOOKAHEAD"] = cand["LOOKAHEAD_NOTES"]
            candidates.append(self._normalize_candidate(cand))

        seen = set()
        unique: List[Dict[str, Any]] = []
        for cand in candidates:
            signature = (
                cand["LOOKAHEAD_NOTES"],
                cand["SWITCH_MARGIN"],
                cand["OCTAVE_FOLD_WEIGHT"],
                cand["SHIFT_WEIGHT"],
                cand["MELODY_KEEP_TOP"],
                cand["BAR_TRANSPOSE_SCOPE"],
                cand["MIN_NOTES_BETWEEN_SWITCHES"],
                cand["MIN_NOTE_LEN"],
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(cand)

        def _distance(cand: Dict[str, Any]) -> Tuple[float, float]:
            scope_rank = {"phrase": 0, "halfbar": 1, "bar": 2}
            d = 0.0
            d += abs(int(cand["LOOKAHEAD_NOTES"]) - int(seed["LOOKAHEAD_NOTES"])) / 4.0
            d += abs(int(cand["SWITCH_MARGIN"]) - int(seed["SWITCH_MARGIN"])) * 1.2
            d += abs(float(cand["OCTAVE_FOLD_WEIGHT"]) - float(seed["OCTAVE_FOLD_WEIGHT"])) * 10.0
            d += abs(float(cand["SHIFT_WEIGHT"]) - float(seed["SHIFT_WEIGHT"])) * 8.0
            d += abs(int(cand["MELODY_KEEP_TOP"]) - int(seed["MELODY_KEEP_TOP"])) * 1.0
            d += abs(scope_rank.get(str(cand["BAR_TRANSPOSE_SCOPE"]), 0) - scope_rank.get(str(seed["BAR_TRANSPOSE_SCOPE"]), 0)) * 1.3
            d += abs(int(cand["MIN_NOTES_BETWEEN_SWITCHES"]) - int(seed["MIN_NOTES_BETWEEN_SWITCHES"])) / 2.0
            d += abs(float(cand["MIN_NOTE_LEN"]) - float(seed["MIN_NOTE_LEN"])) * 40.0
            return (d, -float(cand["SHIFT_WEIGHT"]))

        max_candidates = 160
        if very_heavy:
            max_candidates = 48
        elif heavy:
            max_candidates = 64
        unique.sort(key=_distance)
        return unique[:max_candidates]

    def quick_score(
        self,
        cfg: Dict[str, Any],
        *,
        probe: bool = False,
        stop_above: Optional[float] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        merged = self._normalize_candidate(cfg)
        group_indexes = self.probe_group_indexes if probe else self.full_eval_group_indexes
        return self._score_group_indexes(merged, group_indexes, stop_above=stop_above)

    def _gpu_coarse_rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        *,
        keep_top: int,
        stage: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        if not candidates:
            return [], "未执行 GPU 粗筛"
        if not self.compute_backend.using_gpu:
            return list(candidates), f"{self.compute_backend.summary_text}（未启用 GPU 粗筛）"
        try:
            import torch  # type: ignore
        except Exception as exc:
            return list(candidates), f"GPU 粗筛不可用：{exc}"
        try:
            normalized = [self._normalize_candidate(cand) for cand in candidates]
            device = 'cuda'
            cand_count = len(normalized)
            group_count = max(1, len(self.group_min_notes))
            group_min = torch.tensor(self.group_min_notes or [self.user_min], dtype=torch.float32, device=device)
            group_max = torch.tensor(self.group_max_notes or [self.user_max], dtype=torch.float32, device=device)
            group_size = torch.tensor(self.group_note_counts or [1], dtype=torch.float32, device=device)
            group_avg = torch.tensor(self.group_avg_notes or [float((self.user_min + self.user_max) / 2)], dtype=torch.float32, device=device)
            leftmost = torch.tensor([float(c['LEFTMOST_NOTE']) for c in normalized], dtype=torch.float32, device=device)
            visible_octaves = torch.tensor([3.0 for _ in normalized], dtype=torch.float32, device=device)
            auto_shift = torch.tensor([1.0 if bool(c.get('AUTO_SHIFT_FROM_RANGE', True)) else 0.0 for c in normalized], dtype=torch.float32, device=device)
            use_shift = torch.tensor([1.0 if bool(c.get('USE_SHIFT_OCTAVE', True)) else 0.0 for c in normalized], dtype=torch.float32, device=device)
            fold_weight = torch.tensor([float(c.get('OCTAVE_FOLD_WEIGHT', 0.55)) for c in normalized], dtype=torch.float32, device=device)
            switch_margin = torch.tensor([float(c.get('SWITCH_MARGIN', 1)) for c in normalized], dtype=torch.float32, device=device)
            shift_weight = torch.tensor([float(c.get('SHIFT_WEIGHT', 1.0)) for c in normalized], dtype=torch.float32, device=device)
            melody_keep = torch.tensor([float(c.get('MELODY_KEEP_TOP', 1)) for c in normalized], dtype=torch.float32, device=device)
            keep_bass = torch.tensor([1.0 if bool(c.get('SHIFT_HOLD_BASS', False)) else 0.0 for c in normalized], dtype=torch.float32, device=device)
            bar_aware = torch.tensor([1.0 if bool(c.get('BAR_AWARE_TRANSPOSE', True)) else 0.0 for c in normalized], dtype=torch.float32, device=device)
            min_note_len = torch.tensor([float(c.get('MIN_NOTE_LEN', 0.1)) for c in normalized], dtype=torch.float32, device=device)
            retrigger_gap = torch.tensor([float(c.get('RETRIGGER_GAP', DEFAULT_RETRIGGER_GAP)) for c in normalized], dtype=torch.float32, device=device)
            fixed_window = torch.tensor([
                1.0 if (bool(c.get('AUTO_SHIFT_FROM_RANGE', True)) and self._range_fits_window(int(c.get('LEFTMOST_NOTE', self.user_min)), 3)) else 0.0
                for c in normalized
            ], dtype=torch.float32, device=device)

            offsets = torch.tensor(list(range(-4, 5)), dtype=torch.float32, device=device)
            remainders = torch.remainder(offsets.to(torch.int64), 3)
            shift_mask = (remainders == 1).to(torch.float32)
            offset_mask = torch.ones((cand_count, offsets.numel()), dtype=torch.float32, device=device)
            offset_mask = offset_mask * (1.0 - ((1.0 - use_shift).unsqueeze(1) * shift_mask.unsqueeze(0)))
            offset_mask[:, 0:0] = offset_mask[:, 0:0]
            fixed_offset_mask = torch.zeros_like(offset_mask)
            fixed_offset_mask[:, 4] = 1.0
            offset_mask = torch.where(fixed_window.unsqueeze(1) > 0.5, fixed_offset_mask, offset_mask)
            base_left = leftmost.unsqueeze(1) + offsets.unsqueeze(0) * 12.0
            base_right = base_left + visible_octaves.unsqueeze(1) * 12.0 - 1.0
            low_over = torch.clamp(base_left.unsqueeze(1) - group_min.view(1, group_count, 1), min=0.0)
            high_over = torch.clamp(group_max.view(1, group_count, 1) - base_right.unsqueeze(1), min=0.0)
            center_pen = torch.abs(((base_left + base_right) * 0.5).unsqueeze(1) - group_avg.view(1, group_count, 1)) * 0.02
            pressure = low_over * (1.0 - keep_bass.view(cand_count, 1, 1) * 0.15) + high_over * (1.08 + melody_keep.view(cand_count, 1, 1) * 0.08) + center_pen
            pressure = pressure * (1.0 - bar_aware.view(cand_count, 1, 1) * 0.08)
            pressure = pressure * torch.clamp(1.0 - fold_weight.view(cand_count, 1, 1) * 0.35, min=0.45)
            invalid_pen = (1.0 - offset_mask).view(cand_count, 1, offsets.numel()) * 1e6
            pressure = pressure + invalid_pen
            best_pressure, best_offset_idx = torch.min(pressure, dim=2)
            group_weight = 1.0 + torch.clamp(group_size - 1.0, min=0.0) * 0.22
            total_pen = (best_pressure * group_weight.view(1, group_count)).sum(dim=1)
            if group_count > 1:
                switch_changes = (best_offset_idx[:, 1:] != best_offset_idx[:, :-1]).to(torch.float32).sum(dim=1)
            else:
                switch_changes = torch.zeros((cand_count,), dtype=torch.float32, device=device)
            switch_pen = switch_changes * (0.35 + switch_margin * 0.18) / torch.clamp(shift_weight, min=0.8)
            duration_pen = torch.clamp(min_note_len - float(getattr(self.analysis, 'shortest_note_sec', 0.0) or 0.0), min=0.0) * (48.0 + group_count * 0.02)
            retrigger_pen = torch.clamp(retrigger_gap - float(getattr(self.analysis, 'shortest_raw_same_key_gap_sec', 0.0) or getattr(self.analysis, 'shortest_retrigger_gap_sec', 0.0) or 0.0), min=0.0) * 140.0
            scores = total_pen + switch_pen + duration_pen + retrigger_pen
            torch.cuda.synchronize()
            values = scores.detach().cpu().tolist()
            ranked = [cand for _score, cand in sorted(zip(values, normalized), key=lambda item: item[0])[: max(1, min(keep_top, len(normalized)))]]
            return ranked, f"GPU 粗筛（{stage}）{len(candidates)}→{len(ranked)}"
        except Exception as exc:
            return list(candidates), f"GPU 粗筛失败，已回退 CPU：{exc}"

    def _recommended_parallel_workers(self, task_count: int) -> int:
        cpu_total = max(1, os.cpu_count() or 1)
        if cpu_total <= 2:
            return 1
        reserve = 2 if cpu_total >= 6 else 1
        worker_cap = 8 if cpu_total >= 12 else 6
        workers = min(max(1, cpu_total - reserve), worker_cap)
        return min(workers, max(1, task_count))

    def _can_parallel_score(self, task_count: int) -> bool:
        if task_count < 8:
            return False
        if self.total_group_count < 48 and self.feat["note_count"] < 480:
            return False
        return self._recommended_parallel_workers(task_count) >= 2

    def _score_candidates_batch(
        self,
        candidates: List[Dict[str, Any]],
        *,
        probe: bool,
        stop_above: Optional[float],
    ) -> Tuple[List[Tuple[float, Dict[str, Any], Dict[str, Any]]], str]:
        if not candidates:
            return [], "single-process x1"

        if not self._can_parallel_score(len(candidates)):
            scored: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
            rolling_cutoff = stop_above
            for cand in candidates:
                score, detail = self.quick_score(cand, probe=probe, stop_above=rolling_cutoff)
                scored.append((score, detail, cand))
                if probe and (rolling_cutoff is None or score < rolling_cutoff):
                    rolling_cutoff = score
            return scored, "single-process x1"

        workers = self._recommended_parallel_workers(len(candidates))
        try:
            chunksize = max(1, min(12, len(candidates) // max(1, workers * 3)))
            payloads = [(cand, probe, stop_above) for cand in candidates]
            executor = _get_scoring_pool(self.analysis, self.current_config, (self.user_min, self.user_max), workers)
            results = list(executor.map(_score_candidate_worker, payloads, chunksize=chunksize))
            scored = [(score, detail, cand) for (score, detail), cand in zip(results, candidates)]
            return scored, f"multiprocess x{workers}（常驻池）"
        except Exception as exc:
            append_runtime_log(
                f"自动调参并行评分失败，回退单进程：{type(exc).__name__}: {exc} | bundle={_detect_frozen_bundle_mode()} | candidates={len(candidates)} | probe={probe}"
            )
            scored = []
            rolling_cutoff = stop_above
            for cand in candidates:
                score, detail = self.quick_score(cand, probe=probe, stop_above=rolling_cutoff)
                scored.append((score, detail, cand))
                if probe and (rolling_cutoff is None or score < rolling_cutoff):
                    rolling_cutoff = score
            return scored, "single-process x1 (fallback)"

    def _advanced_refinement_candidates(self, best: Dict[str, Any]) -> List[Dict[str, Any]]:
        mode = _instrument_mode(best)
        left_edge = int(best["LEFTMOST_NOTE"])
        right_edge = left_edge + 36 - 1
        if mode == "bass":
            left_edge += BASS_PLAYABLE_START_OFFSET
            right_edge = min(right_edge, left_edge + BASS_PLAYABLE_COUNT - 1)
        shift_needed_by_range = self.user_max > right_edge
        fixed_window = self._is_fixed_window_candidate(best)
        heavy = self.total_group_count >= 300 or self.feat["note_count"] >= 2500
        very_heavy = self.total_group_count >= 700 or self.feat["note_count"] >= 5500

        shift_choices = [bool(best.get("USE_SHIFT_OCTAVE", True))]
        if fixed_window:
            shift_choices = [False]
        elif shift_needed_by_range:
            shift_choices = list(dict.fromkeys(shift_choices + [True, False]))
        else:
            shift_choices = [False]

        auto_shift_choices = [bool(best.get("AUTO_SHIFT_FROM_RANGE", True))]
        if fixed_window:
            auto_shift_choices = [True]
        elif shift_needed_by_range:
            auto_shift_choices = list(dict.fromkeys(auto_shift_choices + [True, False]))
        else:
            auto_shift_choices = [True]

        collision_choices = [bool(best.get("OCTAVE_AVOID_COLLISION", False))]
        if fixed_window:
            collision_choices = [False]
        elif (self.feat["max_chord"] >= 4 or self.feat["avg_chord"] >= 2.0) and not very_heavy:
            collision_choices = list(dict.fromkeys(collision_choices + [True, False]))

        preview_seed = int(best.get("OCTAVE_PREVIEW_NEIGHBORS", 0))
        preview_deltas = (0, 2) if very_heavy else ((-2, 0, 2) if not heavy else (0, 2))
        preview_choices = sorted({max(0, preview_seed + d) for d in preview_deltas})
        preview_choices = [v for v in preview_choices if v <= 6] or [0]
        if fixed_window:
            preview_choices = [0]
        if 0 not in preview_choices:
            preview_choices.insert(0, 0)

        if fixed_window or mode in {"guitar", "bass"}:
            cooldown_choices = [int(best.get("MIN_NOTES_BETWEEN_SWITCHES", 0))]
        else:
            slow_sparse = self._is_sparse_slow_piece()
            cooldown_deltas = (0, 1) if very_heavy else ((-1, 0, 1) if not heavy else (0, 1))
            cooldown_cap = 4 if slow_sparse else 6
            cooldown_choices = sorted({max(0, min(cooldown_cap, int(best.get("MIN_NOTES_BETWEEN_SWITCHES", 0)) + d)) for d in cooldown_deltas})
        min_note_len_deltas = (0.0, 0.01) if very_heavy else ((-0.01, 0.0, 0.01) if not heavy else (0.0, 0.01))
        min_note_len_choices = sorted({round(min(0.14, max(0.03, float(best.get("MIN_NOTE_LEN", 0.1)) + d)), 3) for d in min_note_len_deltas})

        if mode == "guitar":
            retrigger_choices = sorted({_clamp_retrigger_gap(best.get("RETRIGGER_GAP", DEFAULT_RETRIGGER_GAP) + d) for d in (-0.018, -0.012, -0.006, 0.0, 0.004)})
            pedal_choices = [False]
            melody_priority_choices = [bool(best.get("MELODY_PRIORITY", False)), False]
            chord_priority_choices = [True]
        elif mode == "bass":
            retrigger_choices = sorted({_clamp_retrigger_gap(best.get("RETRIGGER_GAP", DEFAULT_RETRIGGER_GAP) + d) for d in (-0.018, -0.012, -0.006, 0.0, 0.004)})
            pedal_choices = [bool(best.get("USE_PEDAL", False))]
            melody_priority_choices = [False, bool(best.get("MELODY_PRIORITY", False))]
            chord_priority_choices = [False, bool(best.get("CHORD_PRIORITY", False))]
        else:
            retrigger_choices = sorted({_clamp_retrigger_gap(best.get("RETRIGGER_GAP", DEFAULT_RETRIGGER_GAP) + d) for d in (-0.018, -0.012, -0.006, 0.0, 0.004)})
            pedal_choices = [bool(best.get("USE_PEDAL", True))]
            if self.feat["short_ratio_008"] < 0.15:
                pedal_choices = list(dict.fromkeys(pedal_choices + [False, True]))
            melody_priority_choices = [bool(best.get("MELODY_PRIORITY", True))]
            chord_priority_choices = [bool(best.get("CHORD_PRIORITY", False))]
            if self.feat["avg_chord"] >= 2.2:
                chord_priority_choices = list(dict.fromkeys(chord_priority_choices + [True]))

        refine: List[Dict[str, Any]] = []
        for use_shift in shift_choices:
            for auto_shift in auto_shift_choices:
                for collision in collision_choices:
                    for preview in preview_choices:
                        for cooldown in cooldown_choices:
                            for min_note_len in min_note_len_choices:
                                for retrigger_gap in retrigger_choices:
                                    for use_pedal in pedal_choices:
                                        for melody_priority in melody_priority_choices:
                                            for chord_priority in chord_priority_choices:
                                                cand = dict(best)
                                                cand["USE_SHIFT_OCTAVE"] = use_shift
                                                cand["AUTO_SHIFT_FROM_RANGE"] = auto_shift
                                                cand["OCTAVE_AVOID_COLLISION"] = collision
                                                cand["OCTAVE_PREVIEW_NEIGHBORS"] = preview
                                                cand["MIN_NOTES_BETWEEN_SWITCHES"] = cooldown
                                                cand["MIN_NOTE_LEN"] = min_note_len
                                                cand["RETRIGGER_GAP"] = retrigger_gap
                                                cand["USE_PEDAL"] = use_pedal
                                                cand["MELODY_PRIORITY"] = melody_priority
                                                cand["CHORD_PRIORITY"] = chord_priority
                                                cand["OCTAVE_LOOKAHEAD"] = max(int(cand.get("LOOKAHEAD_NOTES", 24)), int(cand.get("OCTAVE_LOOKAHEAD", 0)))
                                                refine.append(self._normalize_candidate(cand))
        return refine

    def tune(self) -> Tuple[Dict[str, Any], float, Dict[str, Any], int]:
        seed = self.build_seed()
        candidates = self.candidates(seed)
        tested = 0

        best = seed
        best_score, best_detail = self.quick_score(seed, probe=False)
        tested += 1

        heavy = self.total_group_count >= 300 or self.feat["note_count"] >= 2500
        very_heavy = self.total_group_count >= 700 or self.feat["note_count"] >= 5500
        probe_keep_top = 24 if very_heavy else (36 if heavy else 56)
        probe_cpu_candidates, coarse_backend = self._gpu_coarse_rank_candidates(candidates, keep_top=probe_keep_top, stage='预筛')
        probe_backend = "single-process x1"
        probe_best = best_score
        probed_scored, probe_backend = self._score_candidates_batch(probe_cpu_candidates, probe=True, stop_above=probe_best)
        tested += len(probe_cpu_candidates)
        if probed_scored:
            probe_best = min(probe_best, min(score for score, _detail, _cand in probed_scored))
        probed: List[Tuple[float, Dict[str, Any]]] = [(score, cand) for score, _detail, cand in probed_scored]

        full_top_n = 4 if very_heavy else (6 if heavy else 12)
        top_candidates = [cand for _score, cand in sorted(probed, key=lambda item: item[0])[:full_top_n]]
        for cand in top_candidates:
            score, detail = self.quick_score(cand, probe=False, stop_above=best_score)
            if score < best_score:
                best = cand
                best_score = score
                best_detail = detail

        refine_candidates = self._advanced_refinement_candidates(best)
        refine_keep_top = 18 if very_heavy else (24 if heavy else 40)
        refine_cpu_candidates, refine_coarse_backend = self._gpu_coarse_rank_candidates(refine_candidates, keep_top=refine_keep_top, stage='精修预筛')
        refine_backend = "single-process x1"
        refine_probed_scored, refine_backend = self._score_candidates_batch(refine_cpu_candidates, probe=True, stop_above=best_score)
        tested += len(refine_cpu_candidates)
        refine_top_n = 3 if very_heavy else (4 if heavy else 8)
        refine_probed = [(score, cand) for score, _detail, cand in refine_probed_scored]
        for cand in [cand for _score, cand in sorted(refine_probed, key=lambda item: item[0])[:refine_top_n]]:
            score, detail = self.quick_score(cand, probe=False, stop_above=best_score)
            if score < best_score:
                best = cand
                best_score = score
                best_detail = detail

        final_best = self._normalize_candidate(best)
        best_detail = dict(best_detail)
        best_detail["probe_groups"] = len(self.probe_group_indexes)
        best_detail["full_groups"] = len(self.full_eval_group_indexes)
        best_detail["total_groups"] = len(self.groups)
        best_detail["probe_backend"] = probe_backend
        best_detail["refine_probe_backend"] = refine_backend
        best_detail["coarse_backend"] = coarse_backend
        best_detail["refine_coarse_backend"] = refine_coarse_backend
        best_detail["coarse_probe_candidates"] = len(probe_cpu_candidates)
        best_detail["coarse_probe_source"] = len(candidates)
        best_detail["coarse_refine_candidates"] = len(refine_cpu_candidates)
        best_detail["coarse_refine_source"] = len(refine_candidates)
        return final_best, best_score, best_detail, tested


def _serialize_preview_value(key: str, value: Any) -> str:
    if key.endswith("_NOTE"):
        return midi_to_note_name(int(value))
    if key == "INSTRUMENT_MODE":
        return str(value)
    if isinstance(value, bool):
        return "开启" if value else "关闭"
    return str(value)


def preview_lines(suggestions: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for key, label in PREVIEW_LABELS:
        if key not in suggestions:
            continue
        lines.append(_html_line(label, _serialize_preview_value(key, suggestions[key]), bold=True, bullet=False))
    return lines


def suggest_config(
    analysis: MidiAnalysisResult,
    current_config: Dict[str, Any],
    playable_range: Optional[Tuple[int, int]] = None,
    *,
    use_gpu: bool = False,
) -> Tuple[Dict[str, Any], str]:
    if not analysis.notes:
        return {}, "当前 MIDI 没有可用音符，无法自动调参。"

    if playable_range is None:
        user_min = _safe_int(current_config, "UNLOCKED_MIN_NOTE", 48)
        user_max = _safe_int(current_config, "UNLOCKED_MAX_NOTE", 83)
    else:
        user_min, user_max = playable_range
    if user_min > user_max:
        user_min, user_max = user_max, user_min

    playable_key = (user_min, user_max)
    cache_key = _tuner_cache_key(analysis, current_config, playable_key)
    cached_payload = load_pickle("tuner", cache_key)
    compute_backend = resolve_compute_backend(use_gpu)
    tuner = MultiCandidateTuner(analysis, current_config, playable_key, use_gpu=use_gpu)
    if isinstance(cached_payload, dict) and all(k in cached_payload for k in ("best", "best_score", "best_detail", "tested")):
        best = dict(cached_payload.get("best") or {})
        best_score = float(cached_payload.get("best_score", 0.0) or 0.0)
        best_detail = dict(cached_payload.get("best_detail") or {})
        tested = int(cached_payload.get("tested", 0) or 0)
        best_detail.setdefault("probe_backend", "本地缓存命中")
    else:
        best, best_score, best_detail, tested = tuner.tune()
        try:
            save_pickle("tuner", cache_key, {
                "best": dict(best),
                "best_score": float(best_score),
                "best_detail": dict(best_detail),
                "tested": int(tested),
            }, meta={"kind": "tuner", "analysis": str(getattr(analysis, "analysis_cache_key", "") or getattr(analysis, "source_sha256", "") or ""), "playable_range": [int(user_min), int(user_max)]})
        except Exception:
            pass
    feat = tuner.feat
    filename = analysis.file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

    active_track_count = sum(1 for track in analysis.track_infos if track.note_count > 0)
    total_track_count = len(analysis.track_infos)
    shortest_note_sec = float(getattr(analysis, 'shortest_note_sec', 0.0) or 0.0)
    shortest_raw_same_key_gap_sec = float(getattr(analysis, 'shortest_raw_same_key_gap_sec', 0.0) or getattr(analysis, 'shortest_retrigger_gap_sec', 0.0) or 0.0)

    lines = [
        "<b>自动调参建议（与当前播放逻辑同步评分）</b>",
        _html_line("文件", filename),
        _html_line("音符数", feat['note_count'], bold=True),
        _html_line("总时长", _format_seconds_compact(analysis.duration_sec), bold=True),
        _html_line("轨道数", f"{active_track_count} / {total_track_count}", bold=True),
        _html_line("统计计算后端", compute_backend.summary_text, bold=True),
        _html_line("原始音域", f"{midi_to_note_name(feat['min_note'])} ~ {midi_to_note_name(feat['max_note'])}", bold=True),
        _html_line("用户可弹奏区间", f"{midi_to_note_name(user_min)} ~ {midi_to_note_name(user_max)}", bold=True),
        _html_line("当前 MIDI 最短按键时长", _format_seconds_compact(shortest_note_sec), bold=True),
        _html_line("当前 MIDI 原始最短同键缝隙", _format_seconds_compact(shortest_raw_same_key_gap_sec), bold=True),
        _html_line("统计说明", "按同一轨道、同一通道、同一音高中统计；优先使用真实 note_off，若缺失且遇到同键下一次 note_on，则按贴边结束处理。"),
        _html_line("平均和弦数", f"{feat['avg_chord']:.2f}"),
        _html_line("最大和弦数", feat['max_chord']),
        _html_line("高音占比", f"{feat['high_ratio'] * 100:.1f}%"),
        _html_line("低音占比", f"{feat['low_ratio'] * 100:.1f}%"),
        _html_line("平均旋律跳进", f"{feat['avg_jump']:.2f}"),
        _html_line("最大旋律跳进", feat['max_jump']),
        _html_line("短音比例(≤0.08s)", f"{feat['short_ratio_008'] * 100:.1f}%"),
        _html_line("快速重复比例", f"{feat['repeat_ratio'] * 100:.1f}%"),
        _html_line("0.25 秒窗口内最大音符数", feat['burst_notes_025']),
        _html_line("候选测试数", tested),
        _html_line("本地调参缓存", "已命中" if isinstance(cached_payload, dict) and all(k in cached_payload for k in ("best", "best_score", "best_detail", "tested")) else "未命中"),
        _html_line("GPU 粗筛后端", best_detail.get('coarse_backend', '未执行')),
        _html_line("GPU 精修粗筛后端", best_detail.get('refine_coarse_backend', '未执行')),
        _html_line("自动调参预筛后端", best_detail.get('probe_backend', 'single-process x1')),
        _html_line("自动调参精修预筛后端", best_detail.get('refine_probe_backend', 'single-process x1')),
        _html_line("粗筛候选收缩", f"{best_detail.get('coarse_probe_source', 0)} → {best_detail.get('coarse_probe_candidates', 0)} / 精修 {best_detail.get('coarse_refine_source', 0)} → {best_detail.get('coarse_refine_candidates', 0)}"),
        _html_line("快速预筛组数", f"{best_detail.get('probe_groups', len(tuner.probe_group_indexes))} / 精算组数：{best_detail.get('full_groups', len(tuner.full_eval_group_indexes))} / 总组数：{best_detail.get('total_groups', len(tuner.groups))}"),
        _html_line("最佳评分", f"{best_score:.2f}", bold=True),
        _html_line("估计漏音", best_detail['lost']),
        _html_line("估计旋律损失", best_detail['melody_loss']),
        _html_line("估计突兀折返", best_detail['harsh_fold']),
        _html_line("估计切区次数", best_detail['switch_need']),
        _html_line("估计防撞代价", best_detail['collision_penalty']),
        _html_line("估计短音压缩代价", best_detail['duration_penalty']),
        _html_line("估计重按风险代价", best_detail.get('retrigger_penalty', 0.0)),
        _html_line("固定窗口模式", '开启' if best_detail.get('fixed_window_mode') else '关闭'),
        "",
        "<b>调参结论：</b>",
        _html_line("推荐基础窗口起点", midi_to_note_name(int(best['LEFTMOST_NOTE'])), bold=True),
        _html_line("推荐按音域自动判断切区", '开启' if best['AUTO_SHIFT_FROM_RANGE'] else '关闭', bold=True),
        _html_line("推荐固定窗口模式", '开启' if (best['AUTO_SHIFT_FROM_RANGE'] and not best['USE_SHIFT_OCTAVE']) else '关闭', bold=True),
        _html_line("推荐预读音符数", best['LOOKAHEAD_NOTES']),
        _html_line("推荐切换保守度", best['SWITCH_MARGIN']),
        _html_line("推荐切区冷却", best['MIN_NOTES_BETWEEN_SWITCHES']),
        _html_line("推荐区间移动偏好", best['SHIFT_WEIGHT']),
        _html_line("推荐最短按键时长", best['MIN_NOTE_LEN'], bold=True),
        _html_line("推荐同键重按间隔", best['RETRIGGER_GAP'], bold=True),
        _html_line("推荐启用踏板识别", '开启' if best['USE_PEDAL'] else '关闭'),
        _html_line("推荐启用防撞", '开启' if best['OCTAVE_AVOID_COLLISION'] else '关闭'),
        _html_line("推荐邻近预览数量", best['OCTAVE_PREVIEW_NEIGHBORS']),
        _html_line("推荐局部移八度范围", best['BAR_TRANSPOSE_SCOPE']),
        _html_line("推荐旋律保留层数", best['MELODY_KEEP_TOP']),
    ]
    return best, _html_block(lines)
