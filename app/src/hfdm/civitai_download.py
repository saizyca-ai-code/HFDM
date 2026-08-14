from __future__ import annotations

import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


class CivitaiDownloadError(RuntimeError):
    pass


class CivitaiAuthRequired(CivitaiDownloadError):
    pass


class RangeUnsupported(CivitaiDownloadError):
    pass


CHUNK_SIZE = 1024 * 1024


class SafeCivitaiRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlparse(req.full_url).hostname != urlparse(newurl).hostname:
            redirected.remove_header("Authorization")
        return redirected


_DEFAULT_OPENER = build_opener(SafeCivitaiRedirectHandler()).open


def download_civitai_file(
    payload: dict[str, Any],
    emit: Callable[[dict[str, Any]], None],
    *,
    opener: Callable[..., Any] = _DEFAULT_OPENER,
    benchmark_observer: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    destination = Path(payload["destination"])
    target = (destination / Path(payload["filename"])).resolve()
    try:
        target.relative_to(destination.resolve())
    except ValueError as exc:
        raise CivitaiDownloadError("Civitai target escaped the destination") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    expected_size = int(payload.get("expected_size") or 0)
    expected_sha256 = str(payload.get("expected_sha256") or "").casefold() or None
    provider_metadata = payload.get("provider_metadata") or {}
    source_kind = str(provider_metadata.get("kind") or "model")
    if source_kind == "generation_metadata":
        _write_inline_file(target, str(provider_metadata.get("inline_text") or ""), expected_sha256, emit)
        return
    download_url = _validate_download_url(str(payload["download_url"]), source_kind)
    token = payload.get("token") or None
    if source_kind == "example_image":
        token = None
    segment_count = max(1, min(int(payload.get("segments") or 1), 8))
    part = target.with_name(f"{target.name}.part")
    meta = target.with_name(f"{target.name}.part.json")
    state = {
        "version": 1,
        "download_url": download_url,
        "expected_size": expected_size,
        "expected_sha256": expected_sha256,
    }
    stored_state = _read_state(meta)
    if (
        expected_size <= 0
        and stored_state
        and stored_state.get("download_url") == download_url
        and stored_state.get("expected_sha256") == expected_sha256
    ):
        expected_size = int(stored_state.get("expected_size") or 0)
        state["expected_size"] = expected_size
    if stored_state != state:
        _clear_partial(part, meta)
    _write_state(meta, state)
    if benchmark_observer:
        resumed_bytes = part.stat().st_size if part.is_file() else 0
        resumed_bytes += sum(
            path.stat().st_size
            for path in part.parent.glob(f"{part.name}.*")
            if path.is_file() and path != meta
        )
        benchmark_observer({"resumed_from_bytes": resumed_bytes})

    range_supported, discovered_size, validator = _probe(download_url, token, opener)
    if benchmark_observer:
        benchmark_observer({"range_supported": range_supported, "fallback": False})
    if expected_size <= 0:
        expected_size = discovered_size
        state["expected_size"] = expected_size
        _write_state(meta, state)
    if expected_size <= 0:
        raise CivitaiDownloadError("Civitai server did not provide the file size")
    emit({"type": "progress", "downloaded": 0, "total": expected_size})

    if range_supported and segment_count > 1 and expected_size >= segment_count:
        try:
            _download_segments(
                download_url, token, part, expected_size, segment_count, validator, emit, opener
            )
        except RangeUnsupported:
            if benchmark_observer:
                benchmark_observer({"range_supported": True, "fallback": True})
            _clear_segment_files(part)
            _download_single(download_url, token, part, expected_size, validator, emit, opener)
    else:
        _download_single(download_url, token, part, expected_size, validator, emit, opener)

    if not part.is_file() or part.stat().st_size != expected_size:
        actual = part.stat().st_size if part.exists() else 0
        raise CivitaiDownloadError(
            f"Civitai download size mismatch: expected {expected_size}, received {actual}"
        )
    if expected_sha256:
        actual_sha256 = _sha256(part)
        if actual_sha256.casefold() != expected_sha256:
            _clear_partial(part, meta)
            raise CivitaiDownloadError("Civitai SHA256 verification failed")
    os.replace(part, target)
    meta.unlink(missing_ok=True)
    _clear_segment_files(part)
    emit({"type": "progress", "downloaded": expected_size, "total": expected_size})


def _probe(
    url: str, token: str | None, opener: Callable[..., Any]
) -> tuple[bool, int, str | None]:
    request = _request(url, token, range_header="bytes=0-0")
    try:
        with _open_with_refresh(request, opener) as response:
            status = _status(response)
            validator = response.headers.get("ETag") or response.headers.get("Last-Modified")
            if status == 206:
                content_range = response.headers.get("Content-Range", "")
                total_text = content_range.rsplit("/", 1)[-1]
                total = int(total_text) if total_text.isdecimal() else 0
                response.read(1)
                return True, total, validator
            total_text = response.headers.get("Content-Length", "")
            return False, int(total_text) if total_text.isdecimal() else 0, validator
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise CivitaiAuthRequired("Civitai API Token is required for this file") from exc
        raise CivitaiDownloadError(f"Unable to probe Civitai download: {exc}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CivitaiDownloadError(f"Unable to probe Civitai download: {exc}") from exc


def _download_segments(
    url: str,
    token: str | None,
    part: Path,
    total: int,
    count: int,
    validator: str | None,
    emit: Callable[[dict[str, Any]], None],
    opener: Callable[..., Any],
) -> None:
    ranges = _split_ranges(total, count)
    progress_lock = threading.Lock()
    downloaded = [0] * len(ranges)
    for index, (start, end) in enumerate(ranges):
        segment = _segment_path(part, index)
        length = end - start + 1
        if segment.exists() and segment.stat().st_size > length:
            segment.unlink()
        downloaded[index] = segment.stat().st_size if segment.exists() else 0
    emit({"type": "progress", "downloaded": sum(downloaded), "total": total})

    def transfer(index: int, start: int, end: int) -> None:
        segment = _segment_path(part, index)
        offset = downloaded[index]
        if offset >= end - start + 1:
            return
        request = _request(
            url,
            token,
            range_header=f"bytes={start + offset}-{end}",
            validator=validator,
        )
        try:
            with _open_with_refresh(request, opener) as response:
                if _status(response) != 206:
                    raise RangeUnsupported("Civitai server ignored a segment Range request")
                mode = "ab" if offset else "wb"
                with segment.open(mode) as handle:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        with progress_lock:
                            downloaded[index] += len(chunk)
                            current = sum(downloaded)
                        emit({"type": "progress", "downloaded": min(current, total), "total": total})
        except RangeUnsupported:
            raise
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise CivitaiAuthRequired("Civitai API Token is required for this file") from exc
            raise CivitaiDownloadError(f"Civitai segment download failed: {exc}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CivitaiDownloadError(f"Civitai segment download failed: {exc}") from exc
        if segment.stat().st_size != end - start + 1:
            raise CivitaiDownloadError("Civitai segment ended before its expected byte range")

    with ThreadPoolExecutor(max_workers=count, thread_name_prefix="hfdm-civitai") as pool:
        futures = [
            pool.submit(transfer, index, start, end)
            for index, (start, end) in enumerate(ranges)
        ]
        for future in futures:
            future.result()
    with part.open("wb") as output:
        for index in range(len(ranges)):
            with _segment_path(part, index).open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    output.write(chunk)


def _download_single(
    url: str,
    token: str | None,
    part: Path,
    total: int,
    validator: str | None,
    emit: Callable[[dict[str, Any]], None],
    opener: Callable[..., Any],
) -> None:
    offset = part.stat().st_size if part.exists() else 0
    if offset > total:
        part.unlink()
        offset = 0
    request = _request(
        url,
        token,
        range_header=f"bytes={offset}-" if offset else None,
        validator=validator if offset else None,
    )
    try:
        with _open_with_refresh(request, opener) as response:
            status = _status(response)
            append = offset > 0 and status == 206
            if offset > 0 and status != 206:
                offset = 0
            mode = "ab" if append else "wb"
            downloaded = offset
            emit({"type": "progress", "downloaded": downloaded, "total": total})
            with part.open(mode) as handle:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    emit({"type": "progress", "downloaded": min(downloaded, total), "total": total})
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise CivitaiAuthRequired("Civitai API Token is required for this file") from exc
        raise CivitaiDownloadError(f"Civitai download failed: {exc}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CivitaiDownloadError(f"Civitai download failed: {exc}") from exc


def _request(
    url: str,
    token: str | None,
    *,
    range_header: str | None,
    validator: str | None = None,
) -> Request:
    headers = {"Accept": "application/octet-stream", "User-Agent": "HFDM/2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if range_header:
        headers["Range"] = range_header
    if validator:
        headers["If-Range"] = validator
    return Request(url, headers=headers)


def _open_with_refresh(request: Request, opener: Callable[..., Any]) -> Any:
    try:
        return opener(request, timeout=60)
    except HTTPError as exc:
        if exc.code not in {401, 403}:
            raise
        return opener(request, timeout=60)


def _validate_download_url(value: str, source_kind: str = "model") -> str:
    parsed = urlparse(value)
    if source_kind == "example_image":
        if parsed.scheme == "https" and parsed.hostname == "image.civitai.com":
            return value
        raise CivitaiDownloadError("Untrusted Civitai example image URL")
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"civitai.com", "www.civitai.com"}
        or not parsed.path.startswith("/api/download/models/")
    ):
        raise CivitaiDownloadError("Untrusted Civitai download URL")
    return value


def _write_inline_file(
    target: Path,
    content: str,
    expected_sha256: str | None,
    emit: Callable[[dict[str, Any]], None],
) -> None:
    encoded = content.encode("utf-8")
    if expected_sha256 and hashlib.sha256(encoded).hexdigest().casefold() != expected_sha256:
        raise CivitaiDownloadError("Civitai metadata SHA256 verification failed")
    temporary = target.with_name(f"{target.name}.part")
    temporary.write_bytes(encoded)
    os.replace(temporary, target)
    emit({"type": "progress", "downloaded": len(encoded), "total": len(encoded)})


def _split_ranges(total: int, count: int) -> list[tuple[int, int]]:
    count = min(count, total)
    base, remainder = divmod(total, count)
    result: list[tuple[int, int]] = []
    start = 0
    for index in range(count):
        length = base + (1 if index < remainder else 0)
        result.append((start, start + length - 1))
        start += length
    return result


def _segment_path(part: Path, index: int) -> Path:
    return part.with_name(f"{part.name}.{index}")


def _clear_segment_files(part: Path) -> None:
    for path in part.parent.glob(f"{part.name}.*"):
        if path.name != f"{part.name}.json" and path.is_file():
            path.unlink()


def _clear_partial(part: Path, meta: Path) -> None:
    part.unlink(missing_ok=True)
    meta.unlink(missing_ok=True)
    _clear_segment_files(part)


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_state(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _status(response: Any) -> int:
    return int(getattr(response, "status", response.getcode()))
