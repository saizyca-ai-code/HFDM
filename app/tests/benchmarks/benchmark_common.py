from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import platform
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"


def load_workload(name: str, provider: str) -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    workload = payload.get("workloads", {}).get(name)
    if not isinstance(workload, dict) or workload.get("provider") != provider:
        raise ValueError(f"Unknown {provider} workload: {name}")
    return workload


def machine_metadata(destination: Path, disk_type: str, network_label: str) -> dict[str, Any]:
    usage = shutil.disk_usage(destination)
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "physical_memory_bytes": _physical_memory(),
        "disk_type": disk_type,
        "disk_total_bytes": usage.total,
        "disk_free_bytes_before": usage.free,
        "network_label": network_label or None,
    }


def result_envelope(args: Any, workload: dict[str, Any], destination: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": str(uuid.uuid4()),
        "run_number": args.run_number,
        "cache_mode": args.cache_mode,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workload": args.workload,
        "manifest": workload,
        "destination": str(destination.resolve()),
        "machine": machine_metadata(destination, args.disk_type, args.network_label),
    }


def add_common_arguments(parser: Any) -> None:
    parser.add_argument("destination", type=Path, help="Dedicated benchmark directory")
    parser.add_argument("--run-number", type=int, choices=range(1, 100), default=1)
    parser.add_argument("--cache-mode", choices=("cold", "warm", "resume"), default="cold")
    parser.add_argument("--disk-type", choices=("ssd", "hdd", "unknown"), default="unknown")
    parser.add_argument("--network-label", default="", help="Non-secret connection label")
    parser.add_argument("--output", type=Path, help="Also write the JSON result atomically")
    parser.add_argument("--dry-run", action="store_true", help="Validate the plan without network")
    parser.add_argument("--resolve-only", action="store_true", help="Resolve and validate metadata without downloading")


def publish_result(result: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, output)
    print(encoded, end="")


def reconcile_files(destination: Path, files: list[tuple[str, int]]) -> dict[str, Any]:
    started = time.perf_counter()
    missing: list[str] = []
    changed: list[str] = []
    for relative, expected_size in files:
        path = destination / relative
        if not path.is_file():
            missing.append(relative)
        elif path.stat().st_size != expected_size:
            changed.append(relative)
    return {
        "duration_seconds": time.perf_counter() - started,
        "missing_count": len(missing),
        "changed_count": len(changed),
        "status": "available" if not missing and not changed else "changed",
    }


class ResourceSampler:
    def __init__(self) -> None:
        self.process_cpu_started = 0.0
        self.peak_rss_bytes: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "ResourceSampler":
        self.process_cpu_started = time.process_time()
        self.peak_rss_bytes = _current_rss()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        value = _current_rss()
        if value is not None:
            self.peak_rss_bytes = max(self.peak_rss_bytes or 0, value)
        self.process_cpu_seconds = time.process_time() - self.process_cpu_started

    def _sample(self) -> None:
        while not self._stop.wait(0.1):
            value = _current_rss()
            if value is not None:
                self.peak_rss_bytes = max(self.peak_rss_bytes or 0, value)


class FileProgressSampler:
    """Observe hf_xet progress through destination/cache file growth.

    hf_xet does not consistently invoke the Python tqdm callback. Watching the
    dedicated per-run directories gives the harness a provider-independent TTFB
    and peak estimate without touching the production download implementation.
    """

    def __init__(self, roots: list[Path], expected_bytes: int) -> None:
        self.roots = roots
        self.expected_bytes = expected_bytes
        self.started_at = time.perf_counter()
        self.first_progress_at: float | None = None
        self.progress_bytes = 0
        self.peak_bps = 0.0
        self._baseline = self._size()
        self._last_size = self._baseline
        self._last_at = self.started_at
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "FileProgressSampler":
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._observe()
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self._observe()

    def _sample(self) -> None:
        while not self._stop.wait(0.1):
            self._observe()

    def _observe(self) -> None:
        now = time.perf_counter()
        current = self._size()
        delta = max(0, current - self._last_size)
        if delta:
            self.first_progress_at = self.first_progress_at or now
            self.peak_bps = max(self.peak_bps, delta / max(now - self._last_at, 0.001))
        self.progress_bytes = min(self.expected_bytes, max(0, current - self._baseline))
        self._last_size = current
        self._last_at = now

    def _size(self) -> int:
        total = 0
        for root in self.roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
        return total


def resource_result(sampler: ResourceSampler) -> dict[str, Any]:
    return {
        "process_cpu_seconds": getattr(sampler, "process_cpu_seconds", None),
        "peak_process_rss_bytes": sampler.peak_rss_bytes,
    }


def _physical_memory() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    return status.total_physical if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None


def _current_rss() -> int | None:
    if os.name != "nt":
        return None

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("page_fault_count", ctypes.c_ulong),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    get_memory = kernel32.K32GetProcessMemoryInfo
    get_memory.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(Counters),
        ctypes.wintypes.DWORD,
    ]
    get_memory.restype = ctypes.wintypes.BOOL
    handle = kernel32.GetCurrentProcess()
    if get_memory(handle, ctypes.byref(counters), counters.cb):
        return int(counters.working_set_size)
    return None
