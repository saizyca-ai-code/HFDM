from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from huggingface_hub import hf_hub_download
from tqdm import tqdm

from hfdm.hf_service import HuggingFaceService


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

    def __init__(self, *args, **kwargs):
        kwargs["disable"] = True
        self._metric_n = 0
        self._metric_at = time.perf_counter()
        super().__init__(*args, **kwargs)

    def update(self, n=1):
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


def parse_patterns(values: list[str]) -> list[str]:
    return [pattern for value in values for pattern in value.split(",") if pattern.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="HFDM Hugging Face Model/Dataset benchmark")
    parser.add_argument("source", help="Model ID/URL or Dataset ID/URL")
    parser.add_argument("destination", type=Path, help="Dedicated benchmark download directory")
    parser.add_argument("--profile", choices=("balanced", "maximum", "hdd"), default="balanced")
    parser.add_argument("--concurrency", type=int, choices=range(1, 17), default=4)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    args = parser.parse_args()

    token = os.getenv("HF_TOKEN") or None
    include = parse_patterns(args.include)
    exclude = parse_patterns(args.exclude)
    resolution = HuggingFaceService().resolve(args.source, token, include, exclude)
    selected = set(resolution.suggested_files)
    files = [item for item in resolution.files if item.path in selected]
    if not files:
        parser.error("glob selection produced no files")
    args.destination.mkdir(parents=True, exist_ok=True)

    metrics = ProgressMetrics()
    MetricTqdm.recorder = metrics

    def download(path: str) -> str:
        return hf_hub_download(
            repo_id=resolution.repo_id,
            repo_type=resolution.repo_type,
            filename=path,
            revision=resolution.commit_hash,
            local_dir=args.destination,
            token=token or False,
            tqdm_class=MetricTqdm,
        )

    with xet_profile(args.profile):
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            list(pool.map(download, [item.path for item in files]))

    finished_at = time.perf_counter()
    elapsed = max(finished_at - metrics.started_at, 0.001)
    expected_bytes = sum(item.size for item in files)
    result = {
        "provider": "huggingface",
        "repo_type": resolution.repo_type,
        "repo_id": resolution.repo_id,
        "requested_revision": resolution.requested_revision,
        "commit_hash": resolution.commit_hash,
        "profile": args.profile,
        "concurrency": args.concurrency,
        "file_count": len(files),
        "expected_bytes": expected_bytes,
        "progress_bytes": metrics.progress_bytes,
        "first_progress_seconds": (
            metrics.first_progress_at - metrics.started_at
            if metrics.first_progress_at is not None
            else None
        ),
        "elapsed_seconds": elapsed,
        "average_expected_bps": expected_bytes / elapsed,
        "peak_progress_bps": metrics.peak_bps,
        "destination": str(args.destination.resolve()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
