from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import sys
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True, slots=True)
class ComputeBackend:
    requested_gpu: bool
    using_gpu: bool
    backend_name: str
    device_label: str
    reason: str = ""

    @property
    def summary_text(self) -> str:
        if self.using_gpu:
            return f"GPU（{self.backend_name} / {self.device_label}）"
        if not self.requested_gpu:
            return "CPU（未开启 GPU 加速）"
        if self.reason:
            return f"CPU（{self.reason}）"
        return "CPU（GPU 不可用）"

    @property
    def detail_text(self) -> str:
        if self.using_gpu:
            return f"统计加速：{self.backend_name} / {self.device_label}"
        if not self.requested_gpu:
            return "当前使用 CPU。"
        if self.reason:
            return self.reason
        return "GPU 当前不可用，已自动改用 CPU。"


@lru_cache(maxsize=2)
def _resolve_compute_backend_cached(use_gpu: bool) -> ComputeBackend:
    if not use_gpu:
        return ComputeBackend(False, False, "cpu", "CPU", "未开启 GPU 加速")
    frozen = bool(getattr(sys, "frozen", False))
    try:
        import torch  # type: ignore
    except Exception:
        reason = "打包版未包含 PyTorch，请在打包环境安装 CUDA 版 torch 后重新打包" if frozen else "当前环境未检测到 PyTorch，请先安装支持 CUDA 的 torch"
        return ComputeBackend(True, False, "cpu", "CPU", reason)
    try:
        torch_cuda_version = str(getattr(getattr(torch, "version", None), "cuda", None) or "").strip()
        cuda_built = bool(getattr(getattr(torch, "backends", None), "cuda", None) and torch.backends.cuda.is_built())
        if not cuda_built or not torch_cuda_version:
            return ComputeBackend(True, False, "torch-cpu", "CPU", "已检测到 PyTorch，但当前为 CPU 版，需安装 CUDA 版 torch")
        if bool(torch.cuda.is_available()):
            idx = int(torch.cuda.current_device())
            name = str(torch.cuda.get_device_name(idx))
            return ComputeBackend(True, True, "torch-cuda", name, f"CUDA {torch_cuda_version}")
        return ComputeBackend(True, False, "torch-cuda", "CPU", f"已检测到 CUDA 版 PyTorch（CUDA {torch_cuda_version}），但 torch.cuda.is_available() 为 False")
    except Exception as exc:
        return ComputeBackend(True, False, "cpu", "CPU", f"GPU 初始化失败：{exc}")


def resolve_compute_backend(use_gpu: bool = False) -> ComputeBackend:
    return _resolve_compute_backend_cached(bool(use_gpu))


def _normalize_bars(raw_bars: Sequence[float]) -> List[float]:
    bars_list = [float(v) for v in raw_bars]
    peak = max(bars_list, default=0.0)
    if peak > 0.0:
        return [float(v) / float(peak) for v in bars_list]
    return [0.0 for _ in bars_list]


def _build_track_raw_bars_with_backend(
    note_ranges_by_track: Mapping[int, Sequence[Tuple[float, float, int]]],
    duration: float,
    bins: int,
    *,
    use_gpu: bool = False,
) -> Optional[Tuple[Dict[int, List[float]], Dict[int, List[bool]], ComputeBackend]]:
    backend = resolve_compute_backend(use_gpu)
    if duration <= 0 or bins <= 0:
        return {}, {}, backend
    if not note_ranges_by_track:
        return {}, {}, backend
    if not backend.requested_gpu:
        return None
    try:
        import torch  # type: ignore
    except Exception:
        return None

    track_keys = [int(key) for key, ranges in note_ranges_by_track.items() if ranges]
    if not track_keys:
        return {}, {}, backend

    track_keys.sort()
    track_lookup = {track_key: idx for idx, track_key in enumerate(track_keys)}
    track_ids: List[int] = []
    starts: List[float] = []
    ends: List[float] = []
    velocities: List[float] = []
    for track_key in track_keys:
        for start_sec, end_sec, velocity in note_ranges_by_track.get(track_key, ()):
            track_ids.append(track_lookup[track_key])
            starts.append(float(start_sec))
            ends.append(float(end_sec))
            velocities.append(float(velocity) / 127.0)

    if not starts:
        return {}, {}, backend

    device = "cuda" if backend.using_gpu else "cpu"
    try:
        track_index = torch.tensor(track_ids, dtype=torch.int64, device=device)
        starts_tensor = torch.tensor(starts, dtype=torch.float32, device=device)
        ends_tensor = torch.tensor(ends, dtype=torch.float32, device=device)
        velocity_tensor = torch.tensor(velocities, dtype=torch.float32, device=device)

        track_count = len(track_keys)
        bin_count = max(1, int(bins))
        bin_width = max(1e-6, float(duration) / float(bin_count))
        start_idx = torch.clamp((starts_tensor / bin_width).to(torch.int64), 0, bin_count - 1)
        end_idx = torch.clamp((ends_tensor / bin_width).to(torch.int64), 0, bin_count - 1)
        row_width = bin_count + 1

        delta = torch.zeros((track_count, row_width), dtype=torch.float32, device=device)
        flat_delta = delta.view(-1)
        start_flat_idx = track_index * row_width + start_idx
        flat_delta.scatter_add_(0, start_flat_idx, velocity_tensor)

        end_plus = end_idx + 1
        valid_end = end_plus < row_width
        if bool(valid_end.any()):
            end_flat_idx = track_index[valid_end] * row_width + end_plus[valid_end]
            flat_delta.scatter_add_(0, end_flat_idx, -velocity_tensor[valid_end])

        raw_bars_tensor = torch.cumsum(delta[:, :-1], dim=1)

        active_delta = torch.zeros((track_count, row_width), dtype=torch.int32, device=device)
        flat_active = active_delta.view(-1)
        ones = torch.ones_like(start_idx, dtype=torch.int32)
        flat_active.scatter_add_(0, start_flat_idx, ones)
        if bool(valid_end.any()):
            flat_active.scatter_add_(0, end_flat_idx, -ones[valid_end])
        active_tensor = torch.cumsum(active_delta[:, :-1], dim=1) > 0

        if backend.using_gpu:
            torch.cuda.synchronize()

        raw_bars_cpu = raw_bars_tensor.detach().cpu().tolist()
        active_cpu = active_tensor.detach().cpu().tolist()
        raw_bars_by_track = {
            track_key: [float(v) for v in raw_bars_cpu[row_idx]]
            for row_idx, track_key in enumerate(track_keys)
        }
        active_by_track = {
            track_key: [bool(v) for v in active_cpu[row_idx]]
            for row_idx, track_key in enumerate(track_keys)
        }
        return raw_bars_by_track, active_by_track, backend
    except Exception:
        return None


def build_raw_bars_by_track_with_backend(
    note_ranges_by_track: Mapping[int, Sequence[Tuple[float, float, int]]],
    duration: float,
    bins: int,
    *,
    use_gpu: bool = False,
) -> Optional[Tuple[Dict[int, List[float]], ComputeBackend]]:
    built = _build_track_raw_bars_with_backend(note_ranges_by_track, duration, bins, use_gpu=use_gpu)
    if built is None:
        return None
    raw_bars_by_track, _active_by_track, backend = built
    return raw_bars_by_track, backend


def build_timeline_with_backend(
    note_ranges: Sequence[Tuple[float, float, int]],
    duration: float,
    bins: int,
    *,
    use_gpu: bool = False,
) -> Optional[Tuple[List[float], List[bool], ComputeBackend]]:
    if duration <= 0 or bins <= 0 or not note_ranges:
        backend = resolve_compute_backend(use_gpu)
        return [0.0 for _ in range(max(1, bins))], [False for _ in range(max(1, bins))], backend
    built = _build_track_raw_bars_with_backend({0: note_ranges}, duration, bins, use_gpu=use_gpu)
    if built is None:
        return None
    raw_bars_by_track, active_by_track, backend = built
    raw_bars = raw_bars_by_track.get(0, [0.0 for _ in range(max(1, bins))])
    active = active_by_track.get(0, [False for _ in range(max(1, bins))])
    return _normalize_bars(raw_bars), active, backend
