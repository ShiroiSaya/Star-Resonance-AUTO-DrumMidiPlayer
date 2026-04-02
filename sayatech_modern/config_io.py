from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_NAME_MAP = {name: idx for idx, name in enumerate(NOTE_NAMES)}

DEFAULT_ITEMS: List[tuple[str, str]] = [
    ("START_DELAY", "3.0"),
    ("MIN_NOTE_LEN", "0.15"),
    ("MAX_SIMULTANEOUS", "none"),
    ("RETRIGGER_GAP", "0.021"),
    ("HIGH_FREQ_COMPAT", "false"),
    ("HIGH_FREQ_RELEASE_ADVANCE", "0.000"),
    ("RETRIGGER_MODE", "true"),
    ("RETRIGGER_PRIORITY", "latest"),
    ("INSTRUMENT_MODE", "钢琴"),
    ("LEFTMOST_NOTE", "C3"),
    ("VISIBLE_OCTAVES", "3"),
    ("UNLOCKED_MIN_NOTE", "C3"),
    ("UNLOCKED_MAX_NOTE", "B4"),
    ("KEYMAP", "z,1,x,2,c,v,3,b,4,n,5,m,a,6,s,7,d,f,8,g,9,h,0,j,q,i,w,o,e,r,p,t,[,y,],u"),
    ("AUTO_TRANSPOSE", "true"),
    ("AUTO_SHIFT_FROM_RANGE", "true"),
    ("USE_SHIFT_OCTAVE", "true"),
    ("SHIFT_KEY", "shift"),
    ("LOOKAHEAD_NOTES", "24"),
    ("SWITCH_MARGIN", "2"),
    ("MIN_NOTES_BETWEEN_SWITCHES", "12"),
    ("SHIFT_WEIGHT", "1.45"),
    ("USE_PEDAL", "true"),
    ("PEDAL_ON_VALUE", "64"),
    ("PEDAL_TAP_TIME", "0.08"),
    ("CHORD_PRIORITY", "false"),
    ("CHORD_SPLIT_THRESHOLD", "0.05"),
    ("OCTAVE_FOLD_PRIORITY", "true"),
    ("OCTAVE_FOLD_WEIGHT", "0.58"),
    ("MAX_MELODIC_JUMP_AFTER_FOLD", "12"),
    ("BAR_AWARE_TRANSPOSE", "true"),
    ("BAR_TRANSPOSE_SCOPE", "phrase"),
    ("BAR_TRANSPOSE_THRESHOLD", "1"),
    ("SHIFT_HOLD_BASS", "true"),
    ("SHIFT_HOLD_MAX_NOTE", "B3"),
    ("SHIFT_HOLD_MAX_CHORD_RANK", "1"),
    ("SHIFT_HOLD_CONFLICT_CLEAR", "true"),
    ("SHIFT_HOLD_RELEASE_DELAY", "0.03"),
    ("MELODY_PRIORITY", "true"),
    ("MELODY_PITCH_WEIGHT", "1.0"),
    ("MELODY_DURATION_WEIGHT", "0.7"),
    ("MELODY_CONTINUITY_WEIGHT", "1.1"),
    ("MELODY_KEEP_TOP", "2"),
    ("OCTAVE_AVOID_COLLISION", "false"),
    ("OCTAVE_PREVIEW_NEIGHBORS", "0"),
    ("BASE_TAP_HOLD", "0.010"),
    ("SAME_TIME_WINDOW", "0.008"),
    ("DENSITY_LIMIT_HZ", "42.0"),
    ("COARSE_GROUP_WINDOW", "0.065"),
    ("ACCENT_VELOCITY", "108"),
    ("GHOST_VELOCITY", "42"),
    ("USE_CONTEXT_REPLACE", "true"),
    ("USE_VELOCITY_RULES", "true"),
    ("USE_SMART_KEEP", "true"),
    ("PREFER_CHANNEL_10", "true"),
    ("AUTO_ELEVATE", "false"),
    ("GUI_TITLE", "SayaTech MIDI 自动弹奏"),
    ("INPUT_BACKEND", "sendinput"),
]

DEFAULT_RAW_MAP: Dict[str, str] = dict(DEFAULT_ITEMS)
SAVE_ORDER: List[str] = [key for key, _ in DEFAULT_ITEMS]
DEFAULT_TEMPLATE = "\n".join(f"{key}={value}" for key, value in DEFAULT_ITEMS) + "\n"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str
    section: str
    help_text: str
    options: Optional[List[str]] = None


SUPPORTED_FIELDS: List[FieldSpec] = [
    FieldSpec("INSTRUMENT_MODE", "乐器模式", "choice", "音域与键位", "选择当前乐器档案。贝斯会使用独立的起始页和区间约束。", ["钢琴", "吉他", "贝斯"]),
    FieldSpec("LEFTMOST_NOTE", "基础窗口起点", "note", "音域与键位", "基础窗口最左边对应的音名。通常设成你当前页最左边那个白键，例如 C3。"),
    FieldSpec("VISIBLE_OCTAVES", "窗口八度数", "int", "音域与键位", "当前一页横向能覆盖多少个八度。3 代表 36 键布局。"),
    FieldSpec("UNLOCKED_MIN_NOTE", "可弹最低音", "note", "音域与键位", "你在游戏里已经解锁并且能实际弹到的最低音。"),
    FieldSpec("UNLOCKED_MAX_NOTE", "可弹最高音", "note", "音域与键位", "你在游戏里已经解锁并且能实际弹到的最高音。"),
    FieldSpec("KEYMAP", "键位映射", "text", "音域与键位", "从左到右的实际按键映射。顺序必须和游戏键位一一对应。"),
    FieldSpec("AUTO_TRANSPOSE", "自动适配音域", "bool", "演奏与切区", "自动把整首歌整体挪到更适合当前可弹范围的位置，适用于钢琴 / 吉他 / 贝斯。大多数情况建议开启。"),
    FieldSpec("AUTO_SHIFT_FROM_RANGE", "按音域自动判断切区", "bool", "演奏与切区", "根据你当前可弹范围自动判断是否需要区间移动；完整覆盖时会自动固定区间，避免无意义切换。"),
            FieldSpec("LOOKAHEAD_NOTES", "预读音符数", "int", "演奏与切区", "提前观察后面多少个音再决定要不要切换窗口。越大越稳，反应也会稍慢。"),
    FieldSpec("SWITCH_MARGIN", "切换保守度", "int", "演奏与切区", "越大越不容易切换窗口，能减少来回抖动。"),
    FieldSpec("MIN_NOTES_BETWEEN_SWITCHES", "切换冷却音符数", "int", "演奏与切区", "两次窗口切换之间至少要隔多少个音，防止频繁左右横跳。"),
    FieldSpec("SHIFT_WEIGHT", "区间移动偏好", "float", "演奏与切区", "越大越倾向选择区间移动。短区间固定时这个值会自动降权。"),
    FieldSpec("MIN_NOTE_LEN", "最短按键时长", "float", "演奏与切区", "单个音至少按住多久。太短容易吞音，默认 0.15 秒更稳。"),
    FieldSpec("RETRIGGER_MODE", "同键重新起音", "bool", "重触发与踏板", "同一个键还没松开时再次遇到同键音，会先抬再按，保证重新起音。"),
    FieldSpec("RETRIGGER_PRIORITY", "重叠音释放策略", "choice", "重触发与踏板", "latest 表示按到最后出现的那层，first 表示优先保留第一次的结束时长。", ["latest", "first"]),
    FieldSpec("RETRIGGER_GAP", "同键重按间隔", "float", "重触发与踏板", "同键抬起后再次按下前等待多久。值越小反应越快，但某些游戏里过短会吞音。"),
    FieldSpec("USE_PEDAL", "启用踏板识别", "bool", "重触发与踏板", "读取 MIDI 踏板并用空格键模拟。钢琴曲常用，没踏板的歌关不关都行。"),
    FieldSpec("PEDAL_ON_VALUE", "踏板触发阈值", "int", "重触发与踏板", "MIDI 踏板值高于这个阈值时视为踩下。常见默认是 64。"),
    FieldSpec("PEDAL_TAP_TIME", "踏板点按时长", "float", "重触发与踏板", "模拟空格踏板时，每次点按保持多久。"),
    FieldSpec("CHORD_PRIORITY", "和弦优先", "bool", "和弦与折返", "更偏向保留和弦结构，而不是只盯着旋律线。"),
    FieldSpec("CHORD_SPLIT_THRESHOLD", "和弦判定间隔", "float", "和弦与折返", "起音时间相差不超过这个值的音，会被当成同一和弦。"),
    FieldSpec("OCTAVE_FOLD_PRIORITY", "启用八度折返", "bool", "和弦与折返", "音超出当前窗口时，优先尝试用上下八度折返来救回可弹性。"),
    FieldSpec("OCTAVE_FOLD_WEIGHT", "折返力度", "float", "和弦与折返", "越大越愿意接受八度折返。太高会让旋律跳感变重。"),
    FieldSpec("MAX_MELODIC_JUMP_AFTER_FOLD", "折返最大跳进", "int", "和弦与折返", "折返后允许的最大旋律跳进。超过后会尽量避免这种映射。"),
    FieldSpec("BAR_AWARE_TRANSPOSE", "启用局部移八度", "bool", "局部移八度", "当局部片段高音压得太满时，允许只在局部片段里整体挪一组八度。"),
    FieldSpec("BAR_TRANSPOSE_SCOPE", "局部移八度范围", "choice", "局部移八度", "决定局部移八度按多大的片段判断：phrase、halfbar 或 bar。", ["phrase", "halfbar", "bar"]),
    FieldSpec("BAR_TRANSPOSE_THRESHOLD", "局部移八度触发数", "int", "局部移八度", "片段里超界音达到多少个时，才触发局部移八度。"),
    FieldSpec("MELODY_PRIORITY", "启用旋律优先", "bool", "旋律优先", "除了看音高，还会结合时值和连续性来判断哪条线更像主旋律。"),
    FieldSpec("MELODY_PITCH_WEIGHT", "旋律音高权重", "float", "旋律优先", "旋律评分里音高因素的权重。"),
    FieldSpec("MELODY_DURATION_WEIGHT", "旋律时值权重", "float", "旋律优先", "旋律评分里音长因素的权重。"),
    FieldSpec("MELODY_CONTINUITY_WEIGHT", "旋律连贯权重", "float", "旋律优先", "越大越倾向保留走向连续、跳进较小的旋律线。"),
    FieldSpec("MELODY_KEEP_TOP", "旋律保留层数", "int", "旋律优先", "和弦里优先保留多少层被识别成旋律的重要音。"),
    FieldSpec("SHIFT_HOLD_BASS", "保留低音层", "bool", "低音层保留", "切去高区时，允许低音层暂时继续保留，形成低音和弦 + 高音旋律的效果。"),
    FieldSpec("SHIFT_HOLD_MAX_NOTE", "低音保留上限", "note", "低音层保留", "只有不高于这个音的低音层，才允许在切区时继续保留。"),
    FieldSpec("SHIFT_HOLD_MAX_CHORD_RANK", "低音保留层级", "int", "低音层保留", "一组和弦里最多保留几层偏低的音。0 是最低一层，1 是最低两层。"),
    FieldSpec("SHIFT_HOLD_CONFLICT_CLEAR", "冲突时覆盖旧低音", "bool", "低音层保留", "新音和旧的低音保留冲突时，允许自然覆盖旧锁音，避免粘键。"),
    FieldSpec("SHIFT_HOLD_RELEASE_DELAY", "低音延迟释放", "float", "低音层保留", "切区后低音层延迟多久再抬起。太长会糊，太短又听不出层次。"),
    FieldSpec("OCTAVE_AVOID_COLLISION", "启用防撞", "bool", "高级模式", "尝试避免折返后与邻近音域撞到同一个键位。一般只在特殊曲子里再开。"),
    FieldSpec("OCTAVE_PREVIEW_NEIGHBORS", "邻近预览数量", "int", "高级模式", "看后面多少个邻近音来辅助防撞判断。0 代表关闭。"),
    FieldSpec("HIGH_FREQ_COMPAT", "启用高频兼容", "bool", "高级模式", "遇到高密度音符时，允许提前抬起按键来换取更稳定的重新触发。默认关闭，只有你确认游戏会吞短间隔连点时再开。"),
    FieldSpec("HIGH_FREQ_RELEASE_ADVANCE", "高频兼容提前抬起", "float", "高级模式", "把原本的抬键时间整体提前多少秒。0 代表关闭；例如 0.018 表示提前 18ms 抬起。"),
]

FIELD_MAP = {spec.key: spec for spec in SUPPORTED_FIELDS}
NOTE_FIELDS = {"LEFTMOST_NOTE", "UNLOCKED_MIN_NOTE", "UNLOCKED_MAX_NOTE", "SHIFT_HOLD_MAX_NOTE"}
BOOL_FIELDS = {
    "USE_CONTEXT_REPLACE", "USE_VELOCITY_RULES", "USE_SMART_KEEP", "PREFER_CHANNEL_10",
    "RETRIGGER_MODE", "AUTO_SHIFT_FROM_RANGE", "AUTO_TRANSPOSE", "USE_PEDAL", "USE_SHIFT_OCTAVE",
    "CHORD_PRIORITY", "OCTAVE_FOLD_PRIORITY", "BAR_AWARE_TRANSPOSE", "SHIFT_HOLD_BASS", "SHIFT_HOLD_CONFLICT_CLEAR",
    "MELODY_PRIORITY", "OCTAVE_AVOID_COLLISION", "HIGH_FREQ_COMPAT", "AUTO_ELEVATE",
}
INT_FIELDS = {
    "ACCENT_VELOCITY", "GHOST_VELOCITY", "VISIBLE_OCTAVES", "PEDAL_ON_VALUE", "LOOKAHEAD_NOTES", "SWITCH_MARGIN",
    "MIN_NOTES_BETWEEN_SWITCHES", "BAR_TRANSPOSE_THRESHOLD", "MAX_MELODIC_JUMP_AFTER_FOLD", "SHIFT_HOLD_MAX_CHORD_RANK",
    "MELODY_KEEP_TOP", "OCTAVE_PREVIEW_NEIGHBORS",
}
FLOAT_FIELDS = {
    "BASE_TAP_HOLD", "SAME_TIME_WINDOW", "DENSITY_LIMIT_HZ", "COARSE_GROUP_WINDOW", "START_DELAY", "MIN_NOTE_LEN",
    "RETRIGGER_GAP", "HIGH_FREQ_RELEASE_ADVANCE", "PEDAL_TAP_TIME", "SHIFT_WEIGHT", "CHORD_SPLIT_THRESHOLD", "OCTAVE_FOLD_WEIGHT",
    "SHIFT_HOLD_RELEASE_DELAY", "MELODY_PITCH_WEIGHT", "MELODY_DURATION_WEIGHT", "MELODY_CONTINUITY_WEIGHT",
}
LIST_FIELDS = {"KEYMAP"}


def strip_inline_comment(value: str) -> str:
    value = value.strip()
    for marker in (" #", "\t#", " ;", "\t;"):
        pos = value.find(marker)
        if pos != -1:
            return value[:pos].strip()
    return value.strip()


def parse_bool(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def note_name_to_midi(name: str) -> int:
    s = name.strip().upper()
    if len(s) < 2:
        raise ValueError(f"无效音名: {name}")
    if len(s) == 2:
        base, octave = s[0], s[1]
    else:
        base, octave = s[:2], s[2]
    return NOTE_NAME_MAP[base] + (int(octave) + 1) * 12


def midi_to_note_name(note: int) -> str:
    return f"{NOTE_NAMES[note % 12]}{note // 12 - 1}"


def parse_value(key: str, raw: Any) -> Any:
    if key in NOTE_FIELDS:
        return note_name_to_midi(str(raw))
    if key in BOOL_FIELDS:
        return parse_bool(raw)
    if key in INT_FIELDS:
        return int(str(raw).strip())
    if key in FLOAT_FIELDS:
        return float(str(raw).strip())
    if key in LIST_FIELDS:
        return [x.strip() for x in str(raw).split(",") if x.strip()]
    return str(raw).strip()


def serialize_value(key: str, value: Any) -> str:
    if key in NOTE_FIELDS:
        return midi_to_note_name(int(value))
    if key in BOOL_FIELDS:
        return "true" if bool(value) else "false"
    if key in LIST_FIELDS:
        return ",".join(str(x).strip() for x in value)
    return str(value)


def ensure_config_file(path: str) -> str:
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_TEMPLATE)
    return path


def load_config(path: str) -> Dict[str, Any]:
    ensure_config_file(path)
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()
    raw: Dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        raw[key.strip().upper()] = strip_inline_comment(value)
    config: Dict[str, Any] = {}
    for key, default_raw in DEFAULT_ITEMS:
        config[key] = parse_value(key, raw.get(key, default_raw))
    for key, value in raw.items():
        if key not in config:
            config[key] = parse_value(key, value)
    return config


def save_config(path: str, config: Dict[str, Any]) -> None:
    ensure_config_file(path)
    out: List[str] = []
    for key in SAVE_ORDER:
        if key in config:
            out.append(f"{key}={serialize_value(key, config[key])}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")
