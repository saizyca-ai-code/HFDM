from __future__ import annotations

import argparse
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from huggingface_hub import hf_hub_download
from tqdm import tqdm

from hfdm.hf_service import HuggingFaceService

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_common import (
    FileProgressSampler,
    ResourceSampler,
    add_common_arguments,
    load_workload,
    publish_result,
    reconcile_files,
    resource_result,
    result_envelope,
)


class ProgressMetrics:
    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.first_progress_at: float | None = None
        self.progress_bytes = 0
        self.peak_bps = 0.0
        self._lock = threading.Lock()

    def observe(self, amount: int, elapsed: float) -> None:
        if amount <= 0:
            return
        now = time.perf_counter()
        with self._lock:
            if self.first_progress_at is None:
                self.first_progress_at = now
            self.progress_bytes += amount
            self.peak_bps = max(self.peak_bps, amount / max(elapsed, 0.001))


class MetricTqdm(tqdm):
    recorder: ProgressMetrics

    def __init__(self, *args: Any, **kwargs: Any):
        kwargs["disable"] = True
        self._metric_n = 0
        self._metric_at = time.perf_counter()
        super().__init__(*args, **kwargs)

    def update(self, n: int | float = 1):
        result = super().update(n)
        now = time.perf_counter()
        current = int(self.n)
        self.recorder.observe(current - self._metric_n, now - self._metric_at)
        self._metric_n = current
        self._metric_at = now
        return result


@contextmanager
def xet_profile(profile: str) -> Iterator[None]:
    keys = ("HF_XET_HIGH_PERFORMANCE", "HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY")
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    if profile == "maximum":
        os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"
    elif profile == "hdd":
        os.environ["HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY"] = "1"
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def select_files(files: list[Any], selector: dict[str, Any]) -> list[Any]:
    paths = set(selector.get("paths") or [])
    prefixes = tuple(selector.get("prefixes") or [])
    expression = re.compile(selector["regex"]) if selector.get("regex") else None
    return [
        item
        for item in files
        if (paths and item.path in paths)
        or (prefixes and item.path.startswith(prefixes))
        or (expression and expression.fullmatch(item.path))
    ]


def validate_selection(workload: dict[str, Any], resolution: Any, files: list[Any]) -> None:
    expected = (workload["file_count"], workload["expected_bytes"])
    actual = (len(files), sum(item.size for item in files))
    if resolution.repo_id != workload["repo_id"] or resolution.commit_hash != workload["commit_hash"]:
        raise RuntimeError("Resolved Hugging Face identity does not match the manifest")
    if actual != expected:
        raise RuntimeError(f"Selected files changed: expected {expected}, received {actual}")


def main() -> int:
    parser = argparse.ArgumentParser(description="HFDM Hugging Face benchmark")
    parser.add_argument("--workload", choices=("hf-model-large", "hf-dataset-small", "hf-dataset-mixed"), required=True)
    parser.add_argument("--profile", choices=("balanced", "maximum", "hdd"), default="balanced")
    parser.add_argument("--concurrency", type=int, choices=range(1, 17), default=1)
    add_common_arguments(parser)
    args = parser.parse_args()
    workload = load_workload(args.workload, "huggingface")
    args.destination.mkdir(parents=True, exist_ok=True)
    result = result_envelope(args, workload, args.destination)
    result.update({"provider": "huggingface", "profile": args.profile, "concurrency": args.concurrency})
    if args.dry_run:
        result["terminal_result"] = "planned"
        publish_result(result, args.output)
        return 0

    token = os.getenv("HF_TOKEN") or None
    resolve_started = time.perf_counter()
    try:
        resolution = HuggingFaceService().resolve(workload["source"], token)
        resolve_seconds = time.perf_counter() - resolve_started
        files = select_files(resolution.files, workload["selector"])
        validate_selection(workload, resolution, files)
    except BaseException as exc:
        result.update({"terminal_result": "failed", "error_kind": type(exc).__name__, "error": str(exc)})
        publish_result(result, args.output)
        return 1
    if args.resolve_only:
        result.update({
            "repo_id": resolution.repo_id,
            "commit_hash": resolution.commit_hash,
            "file_count": len(files),
            "expected_bytes": sum(item.size for item in files),
            "metadata_resolve_seconds": resolve_seconds,
            "terminal_result": "resolved",
        })
        publish_result(result, args.output)
        return 0
    metrics = ProgressMetrics()
    MetricTqdm.recorder = metrics

    def download(item: Any) -> str:
        return hf_hub_download(
            repo_id=resolution.repo_id,
            repo_type=resolution.repo_type,
            filename=item.path,
            revision=resolution.commit_hash,
            local_dir=args.destination,
            token=token or False,
            tqdm_class=MetricTqdm,
        )

    try:
        xet_cache = Path(os.environ.get("HF_XET_CACHE") or Path(os.environ.get("HF_HOME", args.destination / ".hf-home")) / "xet")
        with ResourceSampler() as resources, FileProgressSampler(
            [args.destination, xet_cache], workload["expected_bytes"]
        ) as file_progress, xet_profile(args.profile):
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                list(pool.map(download, files))
        elapsed = max(time.perf_counter() - metrics.started_at, 0.001)
        reconciliation = reconcile_files(args.destination, [(item.path, item.size) for item in files])
        first_progress = [
            value for value in (metrics.first_progress_at, file_progress.first_progress_at) if value
        ]
        result.update(
            {
                "repo_id": resolution.repo_id,
                "commit_hash": resolution.commit_hash,
                "file_count": len(files),
                "expected_bytes": workload["expected_bytes"],
                "progress_bytes": max(metrics.progress_bytes, file_progress.progress_bytes),
                "metadata_resolve_seconds": resolve_seconds,
                "ttfb_seconds": min(first_progress) - metrics.started_at if first_progress else None,
                "ttfb_basis": "tqdm" if metrics.first_progress_at else "file_growth",
                "elapsed_seconds": elapsed,
                "average_expected_bps": workload["expected_bytes"] / elapsed,
                "peak_progress_bps": metrics.peak_bps or None,
                "peak_file_growth_bps": file_progress.peak_bps,
                "retry_count": None,
                "fallback": None,
                "reconciliation": reconciliation,
                "resources": resource_result(resources),
                "terminal_result": "completed" if reconciliation["status"] == "available" else "failed",
            }
        )
    except BaseException as exc:
        result.update({"terminal_result": "failed", "error_kind": type(exc).__name__, "error": str(exc)})
        publish_result(result, args.output)
        return 1
    publish_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
