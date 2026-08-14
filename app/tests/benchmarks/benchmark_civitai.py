from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

from hfdm.civitai_download import download_civitai_file
from hfdm.civitai_service import CivitaiService

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_common import (
    ResourceSampler,
    add_common_arguments,
    load_workload,
    publish_result,
    reconcile_files,
    resource_result,
    result_envelope,
)


class IntentionalBenchmarkInterruption(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="HFDM Civitai segmented-transfer benchmark")
    parser.add_argument("--workload", choices=("civitai-large",), default="civitai-large")
    parser.add_argument("--segments", type=int, choices=(1, 2, 4, 8), required=True)
    parser.add_argument("--interrupt-after-bytes", type=int, default=0)
    parser.add_argument("--expect-interruption", action="store_true")
    add_common_arguments(parser)
    args = parser.parse_args()
    workload = load_workload(args.workload, "civitai")
    args.destination.mkdir(parents=True, exist_ok=True)
    result = result_envelope(args, workload, args.destination)
    result.update({"provider": "civitai", "segments": args.segments})
    if args.dry_run:
        result["terminal_result"] = "planned"
        publish_result(result, args.output)
        return 0

    token = os.getenv("CIVITAI_TOKEN") or None
    resolve_started = time.perf_counter()
    try:
        resolution = CivitaiService().resolve(workload["source"], token, workload["version_id"])
        resolve_seconds = time.perf_counter() - resolve_started
        file = next((item for item in resolution.files if item.remote_id == workload["file_id"]), None)
        if (
            file is None
            or resolution.repo_id != workload["repo_id"]
            or resolution.commit_hash != str(workload["version_id"])
            or file.path != workload["filename"]
            or file.size != workload["expected_bytes"]
            or (file.sha256 or "").casefold() != workload["sha256"].casefold()
        ):
            raise RuntimeError("Resolved Civitai file does not match the manifest")
    except BaseException as exc:
        result.update({"terminal_result": "failed", "error_kind": type(exc).__name__, "error": str(exc)})
        publish_result(result, args.output)
        return 1
    if args.resolve_only:
        result.update({
            "repo_id": resolution.repo_id,
            "version_id": workload["version_id"],
            "file_id": workload["file_id"],
            "file_count": 1,
            "expected_bytes": file.size,
            "metadata_resolve_seconds": resolve_seconds,
            "terminal_result": "resolved",
        })
        publish_result(result, args.output)
        return 0

    progress_started = time.perf_counter()
    first_progress_at: float | None = None
    previous_bytes = 0
    previous_transferred_bytes = 0
    rate_window_bytes = 0
    rate_window_at = progress_started
    peak_bps = 0.0
    instrumentation: dict[str, Any] = {}

    def emit(event: dict[str, Any]) -> None:
        nonlocal first_progress_at, rate_window_at, rate_window_bytes
        nonlocal previous_bytes, previous_transferred_bytes, peak_bps
        if event.get("type") != "progress":
            return
        downloaded = int(event.get("downloaded") or 0)
        resumed_from = int(instrumentation.get("resumed_from_bytes") or 0)
        transferred = max(0, downloaded - resumed_from)
        now = time.perf_counter()
        if transferred > previous_transferred_bytes:
            first_progress_at = first_progress_at or now
        window_elapsed = now - rate_window_at
        if window_elapsed >= 0.5:
            peak_bps = max(peak_bps, (transferred - rate_window_bytes) / window_elapsed)
            rate_window_bytes = transferred
            rate_window_at = now
        previous_bytes = max(previous_bytes, downloaded)
        previous_transferred_bytes = max(previous_transferred_bytes, transferred)
        if args.interrupt_after_bytes and downloaded >= args.interrupt_after_bytes:
            raise IntentionalBenchmarkInterruption(
                f"intentional interruption after {downloaded} bytes"
            )

    try:
        with ResourceSampler() as resources:
            download_civitai_file(
                {
                    "destination": str(args.destination),
                    "filename": file.path,
                    "download_url": file.provider_metadata["download_url"],
                    "expected_size": file.size,
                    "expected_sha256": file.sha256,
                    "segments": args.segments,
                    "token": token,
                },
                emit,
                benchmark_observer=instrumentation.update,
            )
        elapsed = max(time.perf_counter() - progress_started, 0.001)
        reconciliation = reconcile_files(args.destination, [(file.path, file.size)])
        resumed_from = int(instrumentation.get("resumed_from_bytes") or 0)
        transferred_bytes = max(0, file.size - resumed_from)
        result.update(
            {
                "repo_id": resolution.repo_id,
                "version_id": workload["version_id"],
                "file_id": workload["file_id"],
                "file_count": 1,
                "expected_bytes": file.size,
                "progress_bytes": previous_bytes,
                "metadata_resolve_seconds": resolve_seconds,
                "ttfb_seconds": first_progress_at - progress_started if first_progress_at else None,
                "ttfb_basis": "provider_progress",
                "elapsed_seconds": elapsed,
                "average_expected_bps": file.size / elapsed if resumed_from == 0 else None,
                "average_transferred_bps": transferred_bytes / elapsed,
                "transferred_bytes_this_run": transferred_bytes,
                "peak_progress_bps": peak_bps,
                "peak_progress_window_seconds": 0.5,
                "retry_count": None,
                "range_supported": instrumentation.get("range_supported"),
                "fallback": instrumentation.get("fallback", False),
                "resumed_from_bytes": instrumentation.get("resumed_from_bytes", 0),
                "reconciliation": reconciliation,
                "resources": resource_result(resources),
                "sha256_verified": reconciliation["status"] == "available",
                "terminal_result": "completed" if reconciliation["status"] == "available" else "failed",
            }
        )
    except BaseException as exc:
        intentional = isinstance(exc, IntentionalBenchmarkInterruption)
        result.update(
            {
                "terminal_result": "interrupted" if intentional else "failed",
                "error_kind": type(exc).__name__,
                "error": str(exc),
                "progress_bytes": previous_bytes,
                "metadata_resolve_seconds": resolve_seconds,
                "range_supported": instrumentation.get("range_supported"),
                "fallback": instrumentation.get("fallback", False),
                "resumed_from_bytes": instrumentation.get("resumed_from_bytes", 0),
                "resources": resource_result(resources) if "resources" in locals() else None,
            }
        )
        publish_result(result, args.output)
        return 0 if intentional and args.expect_interruption else 1
    publish_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
