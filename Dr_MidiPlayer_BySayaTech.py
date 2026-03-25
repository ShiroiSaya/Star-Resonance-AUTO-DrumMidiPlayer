
import ctypes
import os
import sys
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import mido
import pydirectinput as keylib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B

keylib.FAILSAFE = False
keylib.PAUSE = 0.0


# 基础配置

APP_TITLE = "SayaTech Drum MIDI Player"

# 9键固定布局
KEY_TO_NAME = {
    "Q": "Snare Drum",
    "W": "Mid Tom",
    "E": "High Tom",
    "R": "Crash Cymbal 1",
    "T": "Hi-Hat",
    "Y": "Crash Cymbal 2",
    "S": "Pedal Hi-Hat",
    "F": "Bass Drum",
    "H": "Floor Tom",
}

# 越小越重要
KEY_PRIORITY = {
    "F": 0,
    "Q": 1,
    "T": 2,
    "S": 3,
    "R": 4,
    "Y": 5,
    "E": 6,
    "W": 7,
    "H": 8,
}

GM_DRUM_NAMES: Dict[int, str] = {
    35: "原声底鼓2",
    36: "底鼓",
    37: "军鼓边击",
    38: "原声军鼓",
    39: "拍手",
    40: "电军鼓",
    41: "低音落地桶鼓",
    42: "闭合踩镲",
    43: "高音落地桶鼓",
    44: "脚踩踩镲",
    45: "低音桶鼓",
    46: "开放踩镲",
    47: "低中音桶鼓",
    48: "高音中桶鼓",
    49: "强音镲1",
    50: "高音桶鼓",
    51: "叮叮镲1",
    52: "中国镲",
    53: "叮叮镲帽",
    54: "铃鼓",
    55: "溅镲",
    56: "牛铃",
    57: "强音镲2",
    58: "震音掌/颤音拍",
    59: "叮叮镲2",
    60: "高音邦戈鼓",
    61: "低音邦戈鼓",
    62: "静音高康加鼓",
    63: "高康加鼓",
    64: "低康加鼓",
    65: "高音定音鼓",
    66: "低音定音鼓",
    67: "高音阿哥哥",
    68: "低音阿哥哥",
    69: "沙锤",
    70: "沙槌",
    71: "短口哨",
    72: "长口哨",
    73: "短刮瓜",
    74: "长刮瓜",
    75: "响棒",
    76: "高木鱼",
    77: "低木鱼",
    78: "静音高木块",
    79: "开放高木块",
    80: "静音低木块",
    81: "开放低木块",
    82: "静音三角铁",
    83: "开放三角铁",
}

DIRECT_NOTE_TO_KEY = {
    35: "F", 36: "F",
    37: "Q", 38: "Q", 39: "Q", 40: "Q",
    41: "H", 43: "H", 45: "H",
    47: "W", 48: "W",
    50: "E",
    42: "T", 44: "S", 46: "T",
    49: "R", 57: "Y",
    51: "T", 53: "Y", 59: "T",
    52: "Y", 55: "Y", 54: "T", 56: "T",
    58: "Q",
    60: "E", 61: "W",
    62: "E", 63: "W", 64: "H",
    65: "E", 66: "H",
    67: "T", 68: "T",
    69: "T", 70: "T",
    71: "T", 72: "T",
    73: "T", 74: "T",
    75: "T",
    76: "E", 77: "H",
    78: "E", 79: "E", 80: "H", 81: "H",
    82: "T", 83: "T",
}


@dataclass
class AppConfig:
    start_delay: float = 3.0
    base_tap_hold: float = 0.010
    retrigger_gap: float = 0.004
    same_time_window: float = 0.008
    live_wait_coarse_threshold: float = 0.004
    live_wait_fine_sleep: float = 0.0005
    status_update_interval: float = 0.20
    density_limit_hz: float = 42.0
    coarse_group_window: float = 0.065
    accent_velocity: int = 108
    ghost_velocity: int = 42
    use_context_replace: bool = True
    use_velocity_rules: bool = True
    use_smart_keep: bool = True
    prefer_channel_10: bool = True


@dataclass
class DrumHit:
    t: float
    midi_note: int
    velocity: int
    channel: int
    source_track: str = ""
    original_name: str = ""
    mapped_key: str = ""
    mapped_name: str = ""
    fallback_from: str = ""
    priority: int = 99
    tap_hold: float = 0.010
    group_index: int = 0


@dataclass
class PlaybackPlan:
    hits: List[DrumHit]
    selected_mode: str
    total_source_hits: int
    total_mapped_hits: int
    note_counter: Counter
    mapped_counter: Counter
    fallback_counter: Counter
    ignored_counter: Counter
    preview_rows: List[Tuple[str, int, str, str]]


class Controller:
    def __init__(self):
        self.stop_requested = threading.Event()
        self.exit_requested = threading.Event()
        self.is_playing = threading.Event()
        self.start_requested = threading.Event()


def gm_name(note: int) -> str:
    return GM_DRUM_NAMES.get(note, f"未知鼓音({note})")


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def shell_quote_windows(arg: str) -> str:
    return f'"{arg}"'


def relaunch_as_admin_current_process() -> bool:
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(shell_quote_windows(a) for a in sys.argv[1:])
        else:
            exe = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            params = " ".join([shell_quote_windows(script_path)] + [shell_quote_windows(a) for a in sys.argv[1:]])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32
    except Exception:
        return False


def normalize_path(text: str) -> str:
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return os.path.normpath(text)


def press_key(key: str):
    keylib.keyDown(key.lower())


def release_key(key: str):
    keylib.keyUp(key.lower())


def wait_key_release(vk_code: int):
    while ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000:
        time.sleep(0.03)


def hotkey_worker(controller: Controller):
    while not controller.exit_requested.is_set():
        if ctypes.windll.user32.GetAsyncKeyState(VK_F10) & 0x8000:
            if not controller.is_playing.is_set():
                controller.start_requested.set()
            wait_key_release(VK_F10)
        if ctypes.windll.user32.GetAsyncKeyState(VK_F11) & 0x8000:
            if controller.is_playing.is_set():
                controller.stop_requested.set()
            wait_key_release(VK_F11)
        if ctypes.windll.user32.GetAsyncKeyState(VK_F12) & 0x8000:
            controller.stop_requested.set()
            controller.exit_requested.set()
            wait_key_release(VK_F12)
            break
        time.sleep(0.03)


def extract_track_name(track) -> str:
    for msg in track:
        if msg.type == "track_name":
            name = str(getattr(msg, "name", "")).strip()
            if name:
                return name
    return ""


def track_looks_like_drum(track) -> bool:
    name = extract_track_name(track).lower()
    if any(tag in name for tag in ("drum", "kit", "perc", "percussion", "鼓")):
        return True
    channel9_count = 0
    note_count = 0
    for msg in track:
        if msg.type in ("note_on", "note_off"):
            note_count += 1
            if getattr(msg, "channel", -1) == 9:
                channel9_count += 1
    return note_count > 0 and channel9_count / max(1, note_count) >= 0.6


def merge_tracks_fallback(mid: mido.MidiFile):
    merged = []
    for track_idx, track in enumerate(mid.tracks):
        track_name = extract_track_name(track) or f"Track {track_idx + 1}"
        for msg in track:
            merged.append((track_name, msg))
    return merged


def collect_hits(mid: mido.MidiFile, config: AppConfig) -> Tuple[List[DrumHit], str]:
    tempo = 500000
    abs_sec = 0.0
    try:
        merged = [("", msg) for msg in mido.merge_tracks(mid.tracks)]
    except Exception:
        merged = merge_tracks_fallback(mid)

    has_channel_10 = False
    if config.prefer_channel_10:
        for _, msg in merged:
            if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0 and getattr(msg, "channel", -1) == 9:
                has_channel_10 = True
                break

    candidate_track_names = set()
    if not has_channel_10:
        for idx, track in enumerate(mid.tracks):
            if track_looks_like_drum(track):
                candidate_track_names.add(extract_track_name(track) or f"Track {idx + 1}")

    hits: List[DrumHit] = []
    mode = "未识别到鼓轨"
    for track_name, msg in merged:
        if msg.type == "set_tempo":
            abs_sec += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
            tempo = msg.tempo
            continue

        abs_sec += mido.tick2second(msg.time, mid.ticks_per_beat, tempo)
        if msg.type != "note_on" or getattr(msg, "velocity", 0) <= 0:
            continue

        note = int(msg.note)
        ch = int(getattr(msg, "channel", 0))
        src_name = track_name.strip()

        if has_channel_10:
            use = ch == 9
            mode = "优先使用第10通道"
        elif candidate_track_names:
            use = (src_name in candidate_track_names) if src_name else (35 <= note <= 83)
            mode = "使用识别出的鼓轨"
        else:
            use = 35 <= note <= 83
            mode = "回退到常见鼓音高范围"

        if not use:
            continue

        hits.append(
            DrumHit(
                t=abs_sec,
                midi_note=note,
                velocity=int(msg.velocity),
                channel=ch,
                source_track=src_name,
                original_name=gm_name(note),
            )
        )

    return hits, mode


def coarse_family_key(note: int) -> Tuple[Optional[str], str]:
    if note in DIRECT_NOTE_TO_KEY:
        return DIRECT_NOTE_TO_KEY[note], ""
    if note <= 36:
        return "F", "低于标准鼓区，回退到底鼓"
    if 37 <= note <= 40:
        return "Q", "军鼓/边击类回退到军鼓"
    if 41 <= note <= 50:
        if note <= 45:
            return "H", "低音桶鼓区域回退到低音桶鼓"
        if note <= 48:
            return "W", "中音桶鼓区域回退到中音桶鼓"
        return "E", "高音桶鼓区域回退到高音桶鼓"
    if 51 <= note <= 59:
        return "T", "镲片类未知变体回退到主踩镲"
    if 60 <= note <= 66:
        if note <= 62:
            return "E", "高音手鼓/定音鼓回退到高音桶鼓"
        if note <= 64:
            return "W", "中音手鼓回退到中音桶鼓"
        return "H", "低音手鼓/定音鼓回退到低音桶鼓"
    if 67 <= note <= 75:
        return "T", "辅助打击乐回退到踩镲"
    if 76 <= note <= 79:
        return "E", "高木块/高木鱼回退到高音桶鼓"
    if 80 <= note <= 81:
        return "H", "低木块回退到低音桶鼓"
    if 82 <= note <= 83:
        return "T", "三角铁回退到踩镲"
    return None, "无法识别的音符已忽略"


def build_groups(hits: List[DrumHit], window: float) -> List[List[DrumHit]]:
    if not hits:
        return []
    hits = sorted(hits, key=lambda x: (x.t, x.midi_note))
    groups = [[hits[0]]]
    for hit in hits[1:]:
        if hit.t - groups[-1][-1].t <= window:
            groups[-1].append(hit)
        else:
            groups.append([hit])
    for idx, grp in enumerate(groups):
        for h in grp:
            h.group_index = idx
    return groups


def analyze_context(groups: List[List[DrumHit]]) -> Dict[str, object]:
    note_counts = Counter()
    key_counts = Counter()
    for grp in groups:
        for hit in grp:
            note_counts[hit.midi_note] += 1
            if hit.midi_note in DIRECT_NOTE_TO_KEY:
                key_counts[DIRECT_NOTE_TO_KEY[hit.midi_note]] += 1

    ride_total = note_counts[51] + note_counts[53] + note_counts[59]
    crash_total = note_counts[49] + note_counts[57] + note_counts[52] + note_counts[55]
    hihat_total = note_counts[42] + note_counts[44] + note_counts[46]

    repeated_ride_groups = 0
    for grp in groups:
        notes = {h.midi_note for h in grp}
        if notes & {51, 53, 59}:
            repeated_ride_groups += 1

    return {
        "ride_total": ride_total,
        "crash_total": crash_total,
        "hihat_total": hihat_total,
        "repeated_ride_groups": repeated_ride_groups,
    }


def context_map(hit: DrumHit, group: List[DrumHit], ctx: Dict[str, object], config: AppConfig) -> Tuple[Optional[str], str]:
    note = hit.midi_note
    key, reason = coarse_family_key(note)
    if not config.use_context_replace:
        return key, reason

    notes_in_group = {h.midi_note for h in group}
    group_has_crash1 = 49 in notes_in_group
    group_has_crash2 = 57 in notes_in_group
    group_has_hh = bool(notes_in_group & {42, 44, 46})

    # Ride 系
    if note in {51, 59}:
        if len(group) == 1 and hit.velocity >= config.accent_velocity:
            return "Y", "高力度单独Ride更像强调镲，替代到强音镲2"
        return "T", "Ride节奏型优先替代到直接敲踩镲"
    if note == 53:
        if hit.velocity >= config.accent_velocity:
            return "Y", "Ride Bell高力度更亮，替代到强音镲2"
        return "T", "Ride Bell普通力度替代到直接敲踩镲"

    # China / Splash
    if note == 52:
        if group_has_crash2:
            return "R", "同组已有Crash2，中国镲改落到强音镲1分流"
        return "Y", "中国镲优先替代到更尖的强音镲2"
    if note == 55:
        if group_has_crash2:
            return "R", "同组已有Crash2，Splash分流到强音镲1"
        return "Y", "Splash优先替代到更尖的强音镲2"

    # 开镲：常规落T；高力度且独立强调时可上Y
    if note == 46:
        if hit.velocity >= config.accent_velocity and len(group) <= 2 and not group_has_hh:
            return "Y", "高力度开镲独立强调，替代到强音镲2"
        return "T", "开放踩镲保留在踩镲主键"

    # Tambourine / Cowbell 一类：如果同组拍点密且已经有主踩镲，转给镲强调，否则保T
    if note in {54, 56}:
        if group_has_hh and hit.velocity >= config.accent_velocity:
            return "R", "高力度铃鼓/牛铃在主踩镲上方分流到强音镲1"
        return "T", "辅助节奏乐器替代到直接敲踩镲"

    return key, reason


def apply_velocity_rules(hit: DrumHit, key: str, config: AppConfig) -> Tuple[str, float, str]:
    hold = config.base_tap_hold
    if not config.use_velocity_rules:
        return key, hold, ""

    v = hit.velocity
    extra = ""
    if key in {"Q", "F", "T", "S"}:
        if v <= config.ghost_velocity:
            hold *= 0.72
            extra = "低力度轻击缩短按下时长"
        elif v >= config.accent_velocity:
            hold *= 1.20
            extra = "高力度重音略延长按下时长"
    elif key in {"R", "Y"}:
        if v <= config.ghost_velocity:
            hold *= 0.82
            extra = "轻镲缩短按下时长"
        elif v >= config.accent_velocity:
            hold *= 1.28
            extra = "重镲略延长按下时长"
    else:
        if v <= config.ghost_velocity:
            hold *= 0.78
            extra = "低力度桶鼓缩短按下时长"
        elif v >= config.accent_velocity:
            hold *= 1.18
            extra = "高力度桶鼓略延长按下时长"

    hold = max(0.004, min(0.028, hold))
    return key, hold, extra


def smart_keep_group(group: List[DrumHit], config: AppConfig, ignored_counter: Counter) -> List[DrumHit]:
    if not config.use_smart_keep or not group:
        return group

    # 同键只留一个最有代表性的
    best_by_key: Dict[str, DrumHit] = {}
    for hit in group:
        old = best_by_key.get(hit.mapped_key)
        if old is None:
            best_by_key[hit.mapped_key] = hit
            continue
        old_score = (
            1 if old.fallback_from else 0,
            old.velocity,
            -old.priority,
            old.midi_note,
        )
        new_score = (
            1 if hit.fallback_from else 0,
            hit.velocity,
            -hit.priority,
            hit.midi_note,
        )
        if new_score > old_score:
            ignored_counter[f"同组同键去重:{old.original_name}->{old.mapped_name}"] += 1
            best_by_key[hit.mapped_key] = hit
        else:
            ignored_counter[f"同组同键去重:{hit.original_name}->{hit.mapped_name}"] += 1

    kept = list(best_by_key.values())
    kept.sort(key=lambda x: (x.priority, -x.velocity, x.midi_note))

    # 超过5键时，按优先级+力度裁掉次要装饰，保证主节奏
    max_keep = 5
    if len(kept) > max_keep:
        kept_sorted = sorted(
            kept,
            key=lambda x: (x.priority, -x.velocity, 0 if x.mapped_key in {"F", "Q", "T", "S"} else 1)
        )
        survivors = kept_sorted[:max_keep]
        survivor_ids = {id(h) for h in survivors}
        for hit in kept:
            if id(hit) not in survivor_ids:
                ignored_counter[f"同刻智能裁剪:{hit.original_name}->{hit.mapped_name}"] += 1
        kept = sorted(survivors, key=lambda x: (x.priority, x.midi_note))

    return kept


def density_limit_hits(hits: List[DrumHit], config: AppConfig, ignored_counter: Counter) -> List[DrumHit]:
    if config.density_limit_hz <= 0:
        return hits
    min_gap = 1.0 / config.density_limit_hz
    last_t_by_key: Dict[str, float] = {}
    out: List[DrumHit] = []
    for hit in hits:
        last_t = last_t_by_key.get(hit.mapped_key)
        if last_t is not None and hit.t - last_t < min_gap:
            # 军鼓/底鼓/主踩镲高力度例外保留
            if hit.mapped_key in {"F", "Q", "T"} and hit.velocity >= 115:
                out.append(hit)
                last_t_by_key[hit.mapped_key] = hit.t
            else:
                ignored_counter[f"同键过密裁剪:{hit.original_name}->{hit.mapped_name}"] += 1
            continue
        out.append(hit)
        last_t_by_key[hit.mapped_key] = hit.t
    return out


def build_playback_plan(mid_path: str, config: AppConfig) -> PlaybackPlan:
    mid = mido.MidiFile(mid_path)
    hits, selected_mode = collect_hits(mid, config)

    note_counter = Counter()
    mapped_counter = Counter()
    fallback_counter = Counter()
    ignored_counter = Counter()
    preview_counter = defaultdict(lambda: {"count": 0, "target": "", "reason": ""})

    for hit in hits:
        note_counter[f"{hit.midi_note}:{hit.original_name}"] += 1

    groups = build_groups(hits, config.coarse_group_window)
    ctx = analyze_context(groups)

    mapped_hits: List[DrumHit] = []
    for grp in groups:
        mapped_group: List[DrumHit] = []
        for hit in grp:
            key, reason = context_map(hit, grp, ctx, config)
            if not key:
                ignored_counter[f"{hit.midi_note}:{hit.original_name}"] += 1
                continue
            hit.mapped_key = key
            hit.mapped_name = KEY_TO_NAME[key]
            hit.fallback_from = reason
            hit.priority = KEY_PRIORITY[key]

            _, hold, velocity_reason = apply_velocity_rules(hit, key, config)
            hit.tap_hold = hold
            if velocity_reason:
                hit.fallback_from = (hit.fallback_from + "；" + velocity_reason).strip("；")

            mapped_group.append(hit)
            mapped_counter[f"{key}:{KEY_TO_NAME[key]}"] += 1
            if hit.fallback_from:
                fallback_counter[f"{hit.original_name} -> {KEY_TO_NAME[key]}"] += 1

            row = preview_counter[hit.original_name]
            row["count"] += 1
            row["target"] = f"{key} {KEY_TO_NAME[key]}"
            row["reason"] = hit.fallback_from or "直接映射"

        kept = smart_keep_group(mapped_group, config, ignored_counter)
        mapped_hits.extend(kept)

    mapped_hits.sort(key=lambda x: (x.t, x.priority, x.midi_note))
    mapped_hits = density_limit_hits(mapped_hits, config, ignored_counter)

    preview_rows = []
    for original_name, data in sorted(preview_counter.items(), key=lambda kv: (-kv[1]["count"], kv[0])):
        preview_rows.append((original_name, data["count"], data["target"], data["reason"]))

    return PlaybackPlan(
        hits=mapped_hits,
        selected_mode=selected_mode,
        total_source_hits=len(hits),
        total_mapped_hits=len(mapped_hits),
        note_counter=note_counter,
        mapped_counter=mapped_counter,
        fallback_counter=fallback_counter,
        ignored_counter=ignored_counter,
        preview_rows=preview_rows,
    )


def format_summary(plan: PlaybackPlan) -> str:
    lines = [
        f"识别模式：{plan.selected_mode}",
        f"源鼓音符：{plan.total_source_hits}",
        f"可播放事件：{plan.total_mapped_hits}",
        "",
        "按键统计：",
    ]
    for key in ["F", "Q", "T", "S", "R", "Y", "E", "W", "H"]:
        lines.append(f"  {key} {KEY_TO_NAME[key]}：{plan.mapped_counter.get(f'{key}:{KEY_TO_NAME[key]}', 0)}")
    if plan.fallback_counter:
        lines.append("")
        lines.append("自动替代（节选）：")
        for name, count in plan.fallback_counter.most_common(16):
            lines.append(f"  {name} × {count}")
    if plan.ignored_counter:
        lines.append("")
        lines.append("被忽略/裁剪（节选）：")
        for name, count in plan.ignored_counter.most_common(16):
            lines.append(f"  {name} × {count}")
    return "\n".join(lines)


def play_plan(plan: PlaybackPlan, mid_path: str, config: AppConfig, controller: Controller, status_cb=None):
    def set_status(text: str):
        if status_cb:
            status_cb(text)

    if not plan.hits:
        set_status("没有可播放的鼓事件。")
        return

    for i in range(int(config.start_delay), 0, -1):
        if controller.stop_requested.is_set() or controller.exit_requested.is_set():
            set_status("已取消开始。")
            return
        set_status(f"倒计时：{i}...")
        time.sleep(1)
    frac = config.start_delay - int(config.start_delay)
    if frac > 0:
        time.sleep(frac)

    pressed_keys: set = set()
    start_perf = time.perf_counter()
    last_status_time = 0.0

    def release_all():
        for key in list(pressed_keys):
            try:
                release_key(key)
            except Exception:
                pass
        pressed_keys.clear()

    try:
        for hit in plan.hits:
            if controller.stop_requested.is_set() or controller.exit_requested.is_set():
                set_status("检测到停止请求，正在收尾...")
                break

            while True:
                now = time.perf_counter() - start_perf
                remain = hit.t - now
                if remain <= 0:
                    break
                if remain > config.live_wait_coarse_threshold:
                    time.sleep(max(0.0008, remain - 0.002))
                else:
                    time.sleep(config.live_wait_fine_sleep)

            key = hit.mapped_key
            if key in pressed_keys:
                release_key(key)
                pressed_keys.discard(key)
                if config.retrigger_gap > 0:
                    time.sleep(config.retrigger_gap)

            press_key(key)
            pressed_keys.add(key)
            time.sleep(hit.tap_hold)
            release_key(key)
            pressed_keys.discard(key)

            now_ui = time.perf_counter()
            if now_ui - last_status_time >= config.status_update_interval:
                last_status_time = now_ui
                set_status(
                    f"播放中：{os.path.basename(mid_path)} | "
                    f"{hit.original_name} -> {hit.mapped_name} ({hit.mapped_key}) 力度={hit.velocity}"
                )
    finally:
        release_all()

    if controller.exit_requested.is_set():
        set_status("已退出。")
    elif controller.stop_requested.is_set():
        set_status("已停止当前演奏。")
    else:
        set_status("播放结束。")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = AppConfig()
        self.controller = Controller()
        self.current_plan: Optional[PlaybackPlan] = None

        self.root.title(APP_TITLE)
        self.root.geometry("1180x760")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_exit)

        self.path_var = tk.StringVar()
        self.file_var = tk.StringVar(value="当前文件：未选择")
        self.state_var = tk.StringVar(value="状态：待机")
        self.status_var = tk.StringVar(value="请选择鼓 MIDI 文件，然后点击“分析”或“开始”。")
        self.admin_var = tk.StringVar(value=f"管理员：{'是' if is_admin() else '否'}")

        main = tk.Frame(root, padx=14, pady=14)
        main.pack(fill="both", expand=True)

        tk.Label(main, text=APP_TITLE, font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        tk.Label(
            main,
            text="固定9键：Q Snare Drum  W Mid Tom  E High Tom  R Crash Cymbal 1  T Hi-Hat  Y Crash Cymbal 2  S Pedal Hi-Hat  F Bass Drum  H Floor Tom",
            fg="#555"
        ).pack(anchor="w", pady=(2, 8))

        row1 = tk.Frame(main)
        row1.pack(fill="x")
        tk.Entry(row1, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        tk.Button(row1, text="选择 MIDI", width=12, command=self.choose_file).pack(side="left", padx=(8, 0))
        tk.Button(row1, text="分析 MIDI", width=12, command=self.analyze_file).pack(side="left", padx=(8, 0))

        row2 = tk.Frame(main)
        row2.pack(fill="x", pady=(10, 8))
        tk.Button(row2, text="开始 / 重播 (F10)", width=18, height=2, command=self.request_start).pack(side="left")
        tk.Button(row2, text="停止 (F11)", width=14, height=2, command=self.request_stop).pack(side="left", padx=8)
        tk.Button(row2, text="退出 (F12)", width=14, height=2, command=self.on_exit).pack(side="left")
        tk.Button(row2, text="管理员重启", width=14, height=2, command=self.restart_as_admin).pack(side="left", padx=(8, 0))

        row3 = tk.Frame(main)
        row3.pack(fill="x", pady=(8, 8))
        tk.Label(row3, text="起播倒计时").pack(side="left")
        self.delay_scale = tk.Scale(row3, from_=0, to=8, orient="horizontal", resolution=0.5, length=150)
        self.delay_scale.set(self.config.start_delay)
        self.delay_scale.pack(side="left", padx=(6, 14))

        tk.Label(row3, text="基础按下时长").pack(side="left")
        self.hold_scale = tk.Scale(row3, from_=0.004, to=0.025, orient="horizontal", resolution=0.001, length=150)
        self.hold_scale.set(self.config.base_tap_hold)
        self.hold_scale.pack(side="left", padx=(6, 14))

        tk.Label(row3, text="同键密度上限Hz").pack(side="left")
        self.density_scale = tk.Scale(row3, from_=20, to=70, orient="horizontal", resolution=1, length=150)
        self.density_scale.set(self.config.density_limit_hz)
        self.density_scale.pack(side="left", padx=(6, 14))

        row4 = tk.Frame(main)
        row4.pack(fill="x", pady=(0, 8))
        self.auto_analyze_var = tk.BooleanVar(value=True)
        self.context_var = tk.BooleanVar(value=True)
        self.velocity_var = tk.BooleanVar(value=True)
        self.smart_keep_var = tk.BooleanVar(value=True)
        tk.Checkbutton(row4, text="开始前自动重新分析", variable=self.auto_analyze_var).pack(side="left")
        tk.Checkbutton(row4, text="上下文替代", variable=self.context_var).pack(side="left", padx=12)
        tk.Checkbutton(row4, text="力度感知", variable=self.velocity_var).pack(side="left", padx=12)
        tk.Checkbutton(row4, text="智能保留", variable=self.smart_keep_var).pack(side="left", padx=12)

        tk.Label(main, textvariable=self.file_var, fg="#333").pack(anchor="w", pady=(2, 2))
        tk.Label(main, textvariable=self.state_var, fg="#0A5C36").pack(anchor="w", pady=(2, 2))
        tk.Label(main, textvariable=self.admin_var, fg="#666").pack(anchor="w", pady=(2, 6))
        tk.Label(main, textvariable=self.status_var, fg="#0A5C36", wraplength=1130, justify="left").pack(anchor="w", pady=(0, 8))

        content = tk.Frame(main)
        content.pack(fill="both", expand=True)

        left = tk.Frame(content)
        left.pack(side="left", fill="both", expand=True)

        right = tk.Frame(content)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        tk.Label(left, text="分析摘要").pack(anchor="w")
        self.summary_text = tk.Text(left, height=28, wrap="word", font=("Consolas", 10))
        self.summary_text.pack(fill="both", expand=True)
        self.summary_text.insert(
            "1.0",
            "增强项：\n"
            "1. 上下文替代：Ride/Bell/China/Splash 会按同组情况智能落键。\n"
            "2. 力度感知：高力度重音会略延长按下时长，低力度轻击会更短。\n"
            "3. 智能保留：同刻过多事件会优先保留 Bass Drum / Snare Drum / Hi-Hat，再保 Crash 和 Tom。\n"
        )
        self.summary_text.config(state="disabled")

        tk.Label(right, text="GUI映射预览").pack(anchor="w")
        cols = ("原始乐器", "次数", "映射到", "说明")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=28)
        for col, width in zip(cols, [180, 70, 180, 420]):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)

        self.hotkey_thread = threading.Thread(target=hotkey_worker, args=(self.controller,), daemon=True)
        self.hotkey_thread.start()
        self.root.after(100, self.poll_controller)

    def set_status(self, text: str):
        self.root.after(0, lambda: self.status_var.set(text))

    def set_state(self, text: str):
        self.root.after(0, lambda: self.state_var.set(f"状态：{text}"))

    def update_summary(self, text: str):
        def _do():
            self.summary_text.config(state="normal")
            self.summary_text.delete("1.0", "end")
            self.summary_text.insert("1.0", text)
            self.summary_text.config(state="disabled")
        self.root.after(0, _do)

    def update_preview(self, rows: List[Tuple[str, int, str, str]]):
        def _do():
            for item in self.tree.get_children():
                self.tree.delete(item)
            for row in rows:
                self.tree.insert("", "end", values=row)
        self.root.after(0, _do)

    def choose_file(self):
        path = filedialog.askopenfilename(
            title="选择鼓 MIDI 文件",
            filetypes=[("MIDI 文件", "*.mid *.midi"), ("所有文件", "*.*")]
        )
        if path:
            self.path_var.set(path)
            self.file_var.set(f"当前文件：{os.path.basename(path)}")
            self.set_status("已选择文件，可以分析或开始。")

    def get_midi_path(self) -> Optional[str]:
        path = normalize_path(self.path_var.get())
        if not path or not os.path.exists(path) or not path.lower().endswith((".mid", ".midi")):
            return None
        return path

    def sync_ui_config(self):
        self.config.start_delay = float(self.delay_scale.get())
        self.config.base_tap_hold = float(self.hold_scale.get())
        self.config.density_limit_hz = float(self.density_scale.get())
        self.config.use_context_replace = bool(self.context_var.get())
        self.config.use_velocity_rules = bool(self.velocity_var.get())
        self.config.use_smart_keep = bool(self.smart_keep_var.get())

    def analyze_file(self) -> Optional[PlaybackPlan]:
        midi_path = self.get_midi_path()
        if not midi_path:
            messagebox.showerror("错误", "请先选择有效的 MIDI 文件。")
            return None
        self.sync_ui_config()
        try:
            plan = build_playback_plan(midi_path, self.config)
            self.current_plan = plan
            self.file_var.set(f"当前文件：{os.path.basename(midi_path)}")
            self.update_summary(format_summary(plan))
            self.update_preview(plan.preview_rows)
            self.set_status(
                f"分析完成：源鼓音符 {plan.total_source_hits}，可播放事件 {plan.total_mapped_hits}。"
            )
            return plan
        except Exception as e:
            self.set_status(f"分析出错：{e}")
            messagebox.showerror("错误", f"分析 MIDI 失败：\n{e}")
            return None

    def request_start(self):
        if not self.get_midi_path():
            messagebox.showerror("错误", "请先选择有效的 MIDI 文件。")
            return
        self.controller.start_requested.set()

    def request_stop(self):
        if self.controller.is_playing.is_set():
            self.controller.stop_requested.set()
            self.set_status("已请求停止。")

    def restart_as_admin(self):
        if is_admin():
            messagebox.showinfo("提示", "当前已经是管理员运行。")
            return
        ok = relaunch_as_admin_current_process()
        if ok:
            self.controller.exit_requested.set()
            self.root.after(100, self.root.destroy)
        else:
            messagebox.showerror("错误", "管理员重启失败。")

    def on_exit(self):
        self.controller.stop_requested.set()
        self.controller.exit_requested.set()
        self.root.after(100, self.root.destroy)

    def start_playback_thread(self, midi_path: str):
        self.controller.start_requested.clear()
        self.controller.stop_requested.clear()
        self.controller.is_playing.set()
        self.set_state("播放中")
        self.file_var.set(f"当前文件：{os.path.basename(midi_path)}")

        def runner():
            try:
                self.sync_ui_config()
                plan = self.current_plan
                if self.auto_analyze_var.get() or plan is None:
                    plan = build_playback_plan(midi_path, self.config)
                    self.current_plan = plan
                    self.update_summary(format_summary(plan))
                    self.update_preview(plan.preview_rows)
                play_plan(plan, midi_path, self.config, self.controller, self.set_status)
            except Exception as e:
                self.set_status(f"播放出错：{e}")
            finally:
                self.controller.stop_requested.clear()
                self.controller.is_playing.clear()
                if not self.controller.exit_requested.is_set():
                    self.set_state("待机")
                    self.set_status("待机中。按 F10 可再次播放，按 F12 退出。")

        threading.Thread(target=runner, daemon=True).start()

    def poll_controller(self):
        if self.controller.exit_requested.is_set():
            try:
                self.root.destroy()
            except Exception:
                pass
            return

        if self.controller.start_requested.is_set() and not self.controller.is_playing.is_set():
            midi_path = self.get_midi_path()
            if not midi_path:
                self.controller.start_requested.clear()
                self.set_status("未找到有效 MIDI 文件。")
            else:
                self.start_playback_thread(midi_path)

        self.root.after(100, self.poll_controller)


def main():
    root = tk.Tk()
    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
