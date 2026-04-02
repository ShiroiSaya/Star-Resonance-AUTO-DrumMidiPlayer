from __future__ import annotations

import os
from collections import defaultdict, deque
from heapq import merge
from typing import DefaultDict, Iterable, List, Tuple, Dict

import mido

from .gpu_accel import build_timeline_with_backend
from .models import MidiAnalysisResult, NoteSpan, PedalEvent, TimelineOverview, TrackInfo

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_to_note_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def extract_track_name(track, idx: int) -> str:
    for msg in track:
        if msg.type == "track_name":
            name = str(getattr(msg, "name", "")).strip()
            if name:
                return name
    return f"Track {idx + 1}"


def track_looks_like_drum(track, idx: int) -> bool:
    name = extract_track_name(track, idx).lower()
    if any(tag in name for tag in ("drum", "kit", "perc", "percussion", "鼓")):
        return True
    note_count = 0
    channel9_count = 0
    for msg in track:
        if msg.type in ("note_on", "note_off"):
            note_count += 1
            if getattr(msg, "channel", -1) == 9:
                channel9_count += 1
    return note_count > 0 and channel9_count / max(1, note_count) >= 0.5


def _empty_overview(duration: float, bins: int) -> TimelineOverview:
    bin_count = max(1, bins)
    return TimelineOverview(duration_sec=duration, bars=[0.0 for _ in range(bin_count)], active_sections=[False for _ in range(bin_count)])


def _normalize_raw_bars(raw_bars: Iterable[float], duration: float) -> TimelineOverview:
    bars = list(raw_bars)
    if not bars:
        return _empty_overview(duration, 1)
    peak = max(bars, default=0.0)
    if peak > 0.0:
        normalized = [v / peak for v in bars]
        active_sections = [v > 0.0 for v in bars]
    else:
        normalized = [0.0 for _ in bars]
        active_sections = [False for _ in bars]
    return TimelineOverview(duration_sec=duration, bars=normalized, active_sections=active_sections)


def _raw_bars_for_notes(notes: Iterable[NoteSpan], duration: float, bins: int = 96) -> List[float]:
    bin_count = max(1, bins)
    bars = [0.0 for _ in range(bin_count)]
    if duration <= 0:
        return bars
    inv_bin_width = bin_count / max(0.001, duration)
    last_idx = bin_count - 1
    for note in notes:
        start_idx = min(last_idx, int(note.start_sec * inv_bin_width))
        end_idx = min(last_idx, int(note.end_sec * inv_bin_width))
        add_value = note.velocity / 127.0
        for bar_idx in range(start_idx, end_idx + 1):
            bars[bar_idx] += add_value
    return bars


def _build_per_track_indexes(notes: List[NoteSpan], pedal_events: List[PedalEvent], duration: float, bins: int) -> tuple[dict[int, tuple[NoteSpan, ...]], dict[int, tuple[PedalEvent, ...]], dict[int, tuple[float, ...]]]:
    notes_by_track_list: DefaultDict[int, list[NoteSpan]] = defaultdict(list)
    pedals_by_track_list: DefaultDict[int, list[PedalEvent]] = defaultdict(list)
    for note in notes:
        notes_by_track_list[int(note.track_index)].append(note)
    for event in pedal_events:
        pedals_by_track_list[int(event.track_index)].append(event)
    raw_bars_by_track: dict[int, tuple[float, ...]] = {}
    for track_index, track_notes in notes_by_track_list.items():
        raw_bars_by_track[track_index] = tuple(_raw_bars_for_notes(track_notes, duration, bins=bins))
    notes_by_track = {track_index: tuple(track_notes) for track_index, track_notes in notes_by_track_list.items()}
    pedals_by_track = {track_index: tuple(track_events) for track_index, track_events in pedals_by_track_list.items()}
    return notes_by_track, pedals_by_track, raw_bars_by_track


def _build_timeline(notes: Iterable[NoteSpan], duration: float, bins: int = 96, *, use_gpu: bool = False) -> TimelineOverview:
    notes = list(notes)
    bin_count = max(1, bins)
    bars = [0.0 for _ in range(bin_count)]
    active_sections = [False for _ in range(bin_count)]
    if duration > 0 and notes:
        accelerated = build_timeline_with_backend([(note.start_sec, note.end_sec, note.velocity) for note in notes], duration, bin_count, use_gpu=use_gpu)
        if accelerated is not None:
            bars, active_sections, _backend = accelerated
            return TimelineOverview(duration_sec=duration, bars=bars, active_sections=active_sections)
        bin_width = duration / len(bars)
        for note in notes:
            start_idx = min(len(bars) - 1, int(note.start_sec / max(0.001, bin_width)))
            end_idx = min(len(bars) - 1, int(note.end_sec / max(0.001, bin_width)))
            for idx in range(start_idx, end_idx + 1):
                bars[idx] += note.velocity / 127.0
                active_sections[idx] = True
        peak = max(bars) if max(bars, default=0.0) > 0 else 1.0
        bars = [v / peak for v in bars]
    return TimelineOverview(duration_sec=duration, bars=bars, active_sections=active_sections)


def _note_has_effective_source_close(note: NoteSpan) -> bool:
    return bool(getattr(note, 'has_raw_note_off', False) or getattr(note, 'closed_by_next_same_note_on', False))


def _note_raw_duration(note: NoteSpan) -> float:
    if bool(getattr(note, 'has_raw_note_off', False)):
        return max(0.0, float(getattr(note, 'raw_duration_sec', 0.0)))
    if bool(getattr(note, 'closed_by_next_same_note_on', False)):
        return max(0.0, float(note.end_sec - note.start_sec))
    return max(0.0, float(note.end_sec - note.start_sec))


def _note_raw_end(note: NoteSpan) -> float:
    if bool(getattr(note, 'has_raw_note_off', False)):
        return float(getattr(note, 'raw_end_sec', 0.0))
    return float(note.start_sec) + max(0.0, float(note.end_sec - note.start_sec))


def _note_identity_key(note: NoteSpan) -> tuple[int, int, int]:
    return (int(note.track_index), int(getattr(note, 'channel', 0)), int(note.midi_note))


def _compute_note_stats(notes: Iterable[NoteSpan]) -> tuple[float, float]:
    shortest_note_sec = 0.0
    shortest_raw_same_key_gap_sec = 0.0
    last_raw_end_by_key: Dict[tuple[int, int, int], float] = {}
    has_prev_closed_by_key: Dict[tuple[int, int, int], bool] = {}
    for note in sorted(notes, key=lambda n: (float(n.start_sec), int(n.track_index), int(getattr(n, 'channel', 0)), int(n.midi_note))):
        if _note_has_effective_source_close(note):
            raw_duration = _note_raw_duration(note)
            shortest_note_sec = raw_duration if shortest_note_sec <= 0.0 else min(shortest_note_sec, raw_duration)
        key = _note_identity_key(note)
        last_raw_end = last_raw_end_by_key.get(key)
        if has_prev_closed_by_key.get(key, False) and last_raw_end is not None:
            gap = max(0.0, float(note.start_sec) - last_raw_end)
            shortest_raw_same_key_gap_sec = gap if shortest_raw_same_key_gap_sec <= 0.0 else min(shortest_raw_same_key_gap_sec, gap)
        if _note_has_effective_source_close(note):
            last_raw_end_by_key[key] = _note_raw_end(note)
            has_prev_closed_by_key[key] = True
        else:
            has_prev_closed_by_key[key] = False
    return shortest_note_sec, shortest_raw_same_key_gap_sec


def analyze_midi(file_path: str, bins: int = 96, pedal_threshold: int = 64, *, use_gpu: bool = False) -> MidiAnalysisResult:
    mid = mido.MidiFile(file_path)

    track_infos: List[TrackInfo] = []
    for idx, track in enumerate(mid.tracks):
        note_count = 0
        min_note = None
        max_note = None
        channels = set()
        has_pedal = False
        looks_like_drum = track_looks_like_drum(track, idx)
        for msg in track:
            if msg.type == "control_change" and getattr(msg, "control", -1) == 64:
                has_pedal = True
            if msg.type in ("note_on", "note_off"):
                channels.add(getattr(msg, "channel", 0) + 1)
                if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
                    note_count += 1
                    note = int(msg.note)
                    min_note = note if min_note is None else min(min_note, note)
                    max_note = note if max_note is None else max(max_note, note)
        track_infos.append(
            TrackInfo(
                index=idx,
                name=extract_track_name(track, idx),
                note_count=note_count,
                min_note=min_note,
                max_note=max_note,
                channels=sorted(channels),
                has_pedal=has_pedal,
                looks_like_drum=looks_like_drum,
            )
        )

    events: List[Tuple[int, int, int, object]] = []
    for track_idx, track in enumerate(mid.tracks):
        abs_tick = 0
        order = 0
        for msg in track:
            abs_tick += msg.time
            events.append((abs_tick, order, track_idx, msg))
            order += 1
    events.sort(key=lambda x: (x[0], x[1], x[2]))

    tempo = 500000
    first_tempo = 500000
    tempo_change_count = 0
    last_tick = 0
    abs_sec = 0.0
    active: DefaultDict[Tuple[int, int, int], deque[Tuple[float, int, int]]] = defaultdict(deque)
    notes: List[NoteSpan] = []
    pedal_events: List[PedalEvent] = []

    for abs_tick, _order, track_idx, msg in events:
        delta_tick = abs_tick - last_tick
        if delta_tick:
            abs_sec += mido.tick2second(delta_tick, mid.ticks_per_beat, tempo)
            last_tick = abs_tick

        if msg.type == "set_tempo":
            tempo = msg.tempo
            tempo_change_count += 1
            if tempo_change_count == 1:
                first_tempo = msg.tempo
            continue
        if msg.type == "control_change" and getattr(msg, "control", -1) == 64:
            pedal_events.append(
                PedalEvent(
                    track_index=track_idx,
                    time_sec=abs_sec,
                    is_down=int(getattr(msg, "value", 0)) >= pedal_threshold,
                )
            )
            continue
        if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
            key = (track_idx, getattr(msg, "channel", 0), int(msg.note))
            if active.get(key):
                while active[key]:
                    st_sec, prev_velocity, prev_channel = active[key].popleft()
                    notes.append(
                        NoteSpan(
                            track_index=track_idx,
                            start_sec=st_sec,
                            end_sec=abs_sec,
                            midi_note=int(msg.note),
                            velocity=prev_velocity,
                            channel=prev_channel,
                            raw_duration_sec=0.0,
                            raw_end_sec=0.0,
                            has_raw_note_off=False,
                            closed_by_next_same_note_on=True,
                        )
                    )
            active[key].append((abs_sec, int(getattr(msg, "velocity", 0)), int(getattr(msg, "channel", 0))))
        elif msg.type == "note_off" or (msg.type == "note_on" and getattr(msg, "velocity", 0) == 0):
            key = (track_idx, getattr(msg, "channel", 0), int(msg.note))
            if active.get(key):
                st_sec, velocity, channel = active[key].popleft()
                raw_duration_sec = max(0.0, abs_sec - st_sec)
                notes.append(
                    NoteSpan(
                        track_index=track_idx,
                        start_sec=st_sec,
                        end_sec=max(abs_sec, st_sec + 0.04),
                        midi_note=int(msg.note),
                        velocity=velocity,
                        channel=channel,
                        raw_duration_sec=raw_duration_sec,
                        raw_end_sec=abs_sec,
                        has_raw_note_off=True,
                        closed_by_next_same_note_on=False,
                    )
                )

    for (track_idx, _channel, note), stack in active.items():
        for st_sec, velocity, channel in stack:
            notes.append(
                NoteSpan(
                    track_index=track_idx,
                    start_sec=st_sec,
                    end_sec=st_sec + 0.2,
                    midi_note=int(note),
                    velocity=velocity,
                    channel=channel,
                    raw_duration_sec=0.0,
                    raw_end_sec=0.0,
                    has_raw_note_off=False,
                    closed_by_next_same_note_on=False,
                )
            )

    notes.sort(key=lambda n: (n.start_sec, n.track_index, n.midi_note))
    pedal_events.sort(key=lambda e: (e.time_sec, e.track_index, int(e.is_down)))
    duration = max((n.end_sec for n in notes), default=0.0)
    min_note = min((n.midi_note for n in notes), default=48)
    max_note = max((n.midi_note for n in notes), default=84)
    shortest_note_sec, shortest_raw_same_key_gap_sec = _compute_note_stats(notes)
    timeline = _build_timeline(notes, duration, bins=bins, use_gpu=use_gpu)
    notes_by_track, pedal_events_by_track, track_timeline_raw_bars = _build_per_track_indexes(notes, pedal_events, duration, bins)

    return MidiAnalysisResult(
        file_path=os.path.abspath(file_path),
        track_infos=track_infos,
        notes=notes,
        pedal_events=pedal_events,
        timeline=timeline,
        duration_sec=duration,
        min_note=min_note,
        max_note=max_note,
        shortest_note_sec=shortest_note_sec,
        shortest_raw_same_key_gap_sec=shortest_raw_same_key_gap_sec,
        shortest_retrigger_gap_sec=shortest_raw_same_key_gap_sec,
        primary_bpm=round(mido.tempo2bpm(first_tempo), 1),
        has_tempo_changes=tempo_change_count > 1,
        timeline_bins=bins,
        notes_by_track=notes_by_track,
        pedal_events_by_track=pedal_events_by_track,
        track_timeline_raw_bars=track_timeline_raw_bars,
        selected_track_indexes_key=tuple(sorted(t.index for t in track_infos if t.note_count > 0)),
    )


def filter_analysis(analysis: MidiAnalysisResult, selected_track_indexes: Iterable[int], *, bins: int = 96, use_gpu: bool = False) -> MidiAnalysisResult:
    del use_gpu
    selected_set = {int(idx) for idx in selected_track_indexes}
    if not selected_set:
        return analysis

    all_note_track_indexes = {t.index for t in analysis.track_infos if t.note_count > 0}
    ordered_indexes = tuple(sorted(selected_set))
    if ordered_indexes == tuple(sorted(all_note_track_indexes)):
        return analysis
    if analysis.selected_track_indexes_key and ordered_indexes == analysis.selected_track_indexes_key:
        return analysis

    filtered_tracks = [track for track in analysis.track_infos if track.index in selected_set]
    if not filtered_tracks:
        return MidiAnalysisResult(
            file_path=analysis.file_path,
            track_infos=[],
            notes=[],
            pedal_events=[],
            timeline=_empty_overview(analysis.duration_sec, bins),
            duration_sec=analysis.duration_sec,
            min_note=analysis.min_note,
            max_note=analysis.max_note,
            shortest_note_sec=0.0,
            shortest_raw_same_key_gap_sec=0.0,
            shortest_retrigger_gap_sec=0.0,
            primary_bpm=analysis.primary_bpm,
            has_tempo_changes=analysis.has_tempo_changes,
            timeline_bins=bins,
            source_analysis_id=analysis.source_analysis_id or id(analysis),
            selected_track_indexes_key=ordered_indexes,
        )

    notes_by_track = getattr(analysis, 'notes_by_track', None) or {}
    pedals_by_track = getattr(analysis, 'pedal_events_by_track', None) or {}
    raw_bars_by_track = getattr(analysis, 'track_timeline_raw_bars', None) or {}

    if notes_by_track:
        note_iters = [notes_by_track.get(track_index, ()) for track_index in ordered_indexes]
        if len(note_iters) == 1:
            filtered_notes = list(note_iters[0])
        else:
            filtered_notes = list(merge(*note_iters, key=lambda n: (n.start_sec, n.track_index, n.midi_note)))
        filtered_notes_by_track = {track_index: notes_by_track.get(track_index, ()) for track_index in ordered_indexes if track_index in notes_by_track}
    else:
        filtered_notes = [note for note in analysis.notes if note.track_index in selected_set]
        filtered_notes_by_track = {}

    if pedals_by_track:
        pedal_iters = [pedals_by_track.get(track_index, ()) for track_index in ordered_indexes]
        if len(pedal_iters) == 1:
            filtered_pedals = list(pedal_iters[0])
        else:
            filtered_pedals = list(merge(*pedal_iters, key=lambda e: (e.time_sec, e.track_index, int(e.is_down))))
        filtered_pedals_by_track = {track_index: pedals_by_track.get(track_index, ()) for track_index in ordered_indexes if track_index in pedals_by_track}
    else:
        filtered_pedals = [event for event in analysis.pedal_events if event.track_index in selected_set]
        filtered_pedals_by_track = {}

    min_note = min((track.min_note for track in filtered_tracks if track.min_note is not None), default=analysis.min_note)
    max_note = max((track.max_note for track in filtered_tracks if track.max_note is not None), default=analysis.max_note)
    shortest_note_sec, shortest_raw_same_key_gap_sec = _compute_note_stats(filtered_notes)

    if raw_bars_by_track and analysis.timeline_bins == bins:
        raw_bars = [0.0 for _ in range(max(1, bins))]
        filtered_raw_bars = {track_index: raw_bars_by_track.get(track_index, ()) for track_index in ordered_indexes if track_index in raw_bars_by_track}
        for track_bars in filtered_raw_bars.values():
            for bar_index, value in enumerate(track_bars):
                raw_bars[bar_index] += value
        timeline = _normalize_raw_bars(raw_bars, analysis.duration_sec)
    else:
        timeline = _build_timeline(filtered_notes, analysis.duration_sec, bins=bins, use_gpu=False)
        if filtered_notes_by_track or filtered_pedals_by_track:
            _, _, filtered_raw_bars = _build_per_track_indexes(filtered_notes, filtered_pedals, analysis.duration_sec, bins)
        else:
            filtered_notes_by_track, filtered_pedals_by_track, filtered_raw_bars = _build_per_track_indexes(filtered_notes, filtered_pedals, analysis.duration_sec, bins)

    if not filtered_notes_by_track or not filtered_pedals_by_track:
        fallback_notes_by_track, fallback_pedals_by_track, fallback_raw_bars = _build_per_track_indexes(filtered_notes, filtered_pedals, analysis.duration_sec, bins)
        if not filtered_notes_by_track:
            filtered_notes_by_track = fallback_notes_by_track
        if not filtered_pedals_by_track:
            filtered_pedals_by_track = fallback_pedals_by_track
        if 'filtered_raw_bars' not in locals() or not filtered_raw_bars:
            filtered_raw_bars = fallback_raw_bars

    return MidiAnalysisResult(
        file_path=analysis.file_path,
        track_infos=filtered_tracks,
        notes=filtered_notes,
        pedal_events=filtered_pedals,
        timeline=timeline,
        duration_sec=analysis.duration_sec,
        min_note=min_note,
        max_note=max_note,
        shortest_note_sec=shortest_note_sec,
        shortest_raw_same_key_gap_sec=shortest_raw_same_key_gap_sec,
        shortest_retrigger_gap_sec=shortest_raw_same_key_gap_sec,
        primary_bpm=analysis.primary_bpm,
        has_tempo_changes=analysis.has_tempo_changes,
        timeline_bins=bins,
        notes_by_track=filtered_notes_by_track,
        pedal_events_by_track=filtered_pedals_by_track,
        track_timeline_raw_bars=filtered_raw_bars,
        source_analysis_id=analysis.source_analysis_id or id(analysis),
        selected_track_indexes_key=ordered_indexes,
    )

