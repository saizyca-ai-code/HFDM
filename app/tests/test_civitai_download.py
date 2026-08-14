from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from urllib.error import HTTPError

from hfdm.civitai_download import CivitaiAuthRequired, CivitaiDownloadError, download_civitai_file


class BinaryResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int, headers: dict[str, str]):
        super().__init__(data)
        self.status = status
        self.headers = headers

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def range_opener(content: bytes):
    def opener(request, timeout=0):
        value = request.get_header("Range")
        if value:
            left, right = value.removeprefix("bytes=").split("-", 1)
            start = int(left)
            end = int(right) if right else len(content) - 1
            return BinaryResponse(
                content[start : end + 1],
                206,
                {
                    "Content-Range": f"bytes {start}-{end}/{len(content)}",
                    "Content-Length": str(end - start + 1),
                    "ETag": '"fixture"',
                },
            )
        return BinaryResponse(content, 200, {"Content-Length": str(len(content))})

    return opener


def test_segmented_civitai_download_merges_and_verifies_sha256(tmp_path: Path) -> None:
    content = b"0123456789abcdefghijklmnopqrstuvwxyz"
    events = []
    payload = {
        "destination": str(tmp_path),
        "filename": "model.safetensors",
        "download_url": "https://civitai.com/api/download/models/456",
        "expected_size": len(content),
        "expected_sha256": hashlib.sha256(content).hexdigest(),
        "segments": 4,
        "token": "memory-only",
    }

    download_civitai_file(payload, events.append, opener=range_opener(content))

    assert (tmp_path / "model.safetensors").read_bytes() == content
    assert not list(tmp_path.glob("*.part*"))
    assert events[-1]["downloaded"] == len(content)


def test_civitai_segment_files_are_reused_after_interruption(tmp_path: Path) -> None:
    content = b"abcdefghij"
    sha = hashlib.sha256(content).hexdigest()
    part = tmp_path / "model.bin.part"
    (tmp_path / "model.bin.part.0").write_bytes(content[:3])
    (tmp_path / "model.bin.part.json").write_text(
        json.dumps(
            {
                "version": 1,
                "download_url": "https://civitai.com/api/download/models/1",
                "expected_size": len(content),
                "expected_sha256": sha,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    requested_ranges = []
    observations = {}
    base_opener = range_opener(content)

    def opener(request, timeout=0):
        requested_ranges.append(request.get_header("Range"))
        return base_opener(request, timeout)

    download_civitai_file(
        {
            "destination": str(tmp_path),
            "filename": "model.bin",
            "download_url": "https://civitai.com/api/download/models/1",
            "expected_size": len(content),
            "expected_sha256": sha,
            "segments": 2,
        },
        lambda event: None,
        opener=opener,
        benchmark_observer=observations.update,
    )

    assert (tmp_path / "model.bin").read_bytes() == content
    assert "bytes=3-4" in requested_ranges
    assert observations["resumed_from_bytes"] == 3
    assert not part.exists()


def test_sha256_mismatch_never_publishes_target(tmp_path: Path) -> None:
    content = b"bad"
    with pytest.raises(CivitaiDownloadError, match="SHA256"):
        download_civitai_file(
            {
                "destination": str(tmp_path),
                "filename": "model.bin",
                "download_url": "https://civitai.com/api/download/models/1",
                "expected_size": len(content),
                "expected_sha256": "0" * 64,
                "segments": 2,
            },
            lambda event: None,
            opener=range_opener(content),
        )
    assert not (tmp_path / "model.bin").exists()


def test_download_auth_failure_is_classified_for_ui_recovery(tmp_path: Path) -> None:
    def forbidden(request, timeout=0):
        raise HTTPError(request.full_url, 403, "forbidden", {}, None)

    with pytest.raises(CivitaiAuthRequired):
        download_civitai_file(
            {
                "destination": str(tmp_path),
                "filename": "private.bin",
                "download_url": "https://civitai.com/api/download/models/1",
                "expected_size": 3,
                "segments": 1,
            },
            lambda event: None,
            opener=forbidden,
        )


def test_civitai_example_image_download_discovers_size(tmp_path: Path) -> None:
    content = b"example-image"
    events = []
    download_civitai_file(
        {
            "destination": str(tmp_path),
            "filename": "examples/preview.jpeg",
            "download_url": "https://image.civitai.com/example/preview.jpeg",
            "expected_size": 0,
            "provider_metadata": {"kind": "example_image"},
            "segments": 2,
            "token": "must-not-be-sent",
        },
        events.append,
        opener=range_opener(content),
    )
    assert (tmp_path / "examples" / "preview.jpeg").read_bytes() == content
    assert events[-1] == {"type": "progress", "downloaded": len(content), "total": len(content)}


def test_civitai_generation_metadata_is_written_without_network(tmp_path: Path) -> None:
    content = '{"prompt":"hello"}\n'
    expected = hashlib.sha256(content.encode()).hexdigest()

    def no_network(*args, **kwargs):
        raise AssertionError("inline metadata must not use the network")

    download_civitai_file(
        {
            "destination": str(tmp_path),
            "filename": "examples/preview.json",
            "expected_size": len(content.encode()),
            "expected_sha256": expected,
            "provider_metadata": {"kind": "generation_metadata", "inline_text": content},
        },
        lambda event: None,
        opener=no_network,
    )
    assert (tmp_path / "examples" / "preview.json").read_text() == content
