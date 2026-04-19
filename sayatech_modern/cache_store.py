from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import pickletools
import shutil
import tempfile
from typing import Any, Dict, Optional

from .app_paths import cache_dir
from .safe_execution import safe_call, safe_method

CACHE_VERSION = "v2"
_SECTIONS = {"analyses", "filtered", "tuner", "actions", "drum_reports"}
_FILE_FINGERPRINT_CACHE: dict[str, dict[str, Any]] = {}
_MEMORY_CACHE: dict[tuple[str, str, int, int], Any] = {}


def cache_root() -> str:
    root = str(cache_dir())
    os.makedirs(root, exist_ok=True)
    return root


def section_dir(section: str) -> str:
    sec = str(section or "misc").strip().lower()
    if sec not in _SECTIONS:
        sec = "analyses"
    path = os.path.join(cache_root(), sec)
    os.makedirs(path, exist_ok=True)
    return path


def _atomic_write_bytes(path: str, payload: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="cache_", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise


def _path_for_key(section: str, key: str) -> str:
    return os.path.join(section_dir(section), f"{key}.pkl.gz")


def _meta_path_for_key(section: str, key: str) -> str:
    return os.path.join(section_dir(section), f"{key}.json")


def freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(k), freeze_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(freeze_value(v) for v in value)
    if isinstance(value, float):
        return round(float(value), 9)
    return value


def stable_hash_payload(payload: Any) -> str:
    frozen = freeze_value(payload)
    raw = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_key(kind: str, payload: Any) -> str:
    return stable_hash_payload({"v": CACHE_VERSION, "kind": str(kind), "payload": payload})


def file_fingerprint(file_path: str) -> Dict[str, Any]:
    abs_path = os.path.abspath(file_path)
    st = os.stat(abs_path)
    cached = _FILE_FINGERPRINT_CACHE.get(abs_path)
    stat_key = (int(st.st_size), int(getattr(st, 'st_mtime_ns', int(st.st_mtime * 1_000_000_000))))
    if cached is not None and cached.get("_stat_key") == stat_key:
        return {k: v for k, v in cached.items() if k != "_stat_key"}
    sha = hashlib.sha256()
    with open(abs_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    fingerprint = {
        "path": abs_path,
        "size": int(st.st_size),
        "mtime_ns": stat_key[1],
        "sha256": sha.hexdigest(),
        "_stat_key": stat_key,
    }
    _FILE_FINGERPRINT_CACHE[abs_path] = fingerprint
    return {k: v for k, v in fingerprint.items() if k != "_stat_key"}


def _cache_file_stat(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return int(st.st_size), int(getattr(st, 'st_mtime_ns', int(st.st_mtime * 1_000_000_000)))


@safe_method(log_errors=True)
def load_pickle(section: str, key: str) -> Optional[Any]:
    """从缓存加载 pickle 数据"""
    path = _path_for_key(section, key)
    if not os.path.exists(path):
        return None

    try:
        stat_key = _cache_file_stat(path)
        mem_key = (str(section), str(key), stat_key[0], stat_key[1])
        if mem_key in _MEMORY_CACHE:
            return _MEMORY_CACHE[mem_key]

        with gzip.open(path, "rb") as f:
            value = pickle.load(f)

        _MEMORY_CACHE[mem_key] = value
        return value
    except Exception as e:
        # 记录错误但不抛出异常，返回 None
        from .crash_logging import append_runtime_log
        append_runtime_log(f"缓存加载失败 {section}/{key}: {e}", debug=True)
        return None


@safe_method(log_errors=True)
def save_pickle(section: str, key: str, value: Any, *, meta: Optional[Dict[str, Any]] = None) -> str:
    """保存到缓存"""
    try:
        raw = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        raw = safe_call(pickletools.optimize, raw, default=raw)
        payload = gzip.compress(raw, compresslevel=6)
        path = _path_for_key(section, key)
        _atomic_write_bytes(path, payload)

        # 更新内存缓存
        try:
            stat_key = _cache_file_stat(path)
            _MEMORY_CACHE[(str(section), str(key), stat_key[0], stat_key[1])] = value
        except Exception:
            pass

        # 保存元数据
        if meta is not None:
            try:
                meta_path = _meta_path_for_key(section, key)
                meta_payload = dict(meta)
                meta_payload.setdefault("cache_version", CACHE_VERSION)
                meta_json = json.dumps(meta_payload, ensure_ascii=False, separators=(",", ":"))
                _atomic_write_bytes(meta_path, meta_json.encode("utf-8"))
            except Exception:
                pass

        return path
    except Exception as e:
        from .crash_logging import append_runtime_log
        append_runtime_log(f"缓存保存失败 {section}/{key}: {e}", debug=True)
        return ""


def cache_size_bytes() -> int:
    root = cache_root()
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def format_size(num_bytes: int) -> str:
    value = float(max(0, int(num_bytes)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


@safe_method(log_errors=True)
def clear_cache() -> None:
    """清空所有缓存"""
    root = cache_root()
    for name in os.listdir(root):
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except Exception:
            pass
    _FILE_FINGERPRINT_CACHE.clear()
    _MEMORY_CACHE.clear()
