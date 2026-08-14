from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from hfdm.civitai_download import download_civitai_file


BENCHMARKS = Path(__file__).parent / "benchmarks"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, BENCHMARKS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_has_fixed_public_identities_and_expected_sizes() -> None:
    payload = json.loads((BENCHMARKS / "manifest.json").read_text(encoding="utf-8"))
    workloads = payload["workloads"]
    assert workloads["hf-model-large"]["commit_hash"] == "41f01f3fe87f28c78e2fbf8b568835947dd65ed9"
    assert (workloads["hf-dataset-small"]["file_count"], workloads["hf-dataset-small"]["expected_bytes"]) == (306, 285358149)
    assert (workloads["hf-dataset-mixed"]["file_count"], workloads["hf-dataset-mixed"]["expected_bytes"]) == (12, 1848207780)
    assert workloads["civitai-large"]["file_id"] == "93211"
    assert len(workloads["civitai-large"]["sha256"]) == 64


def test_huggingface_selectors_keep_workloads_separate() -> None:
    module = _load("benchmark_huggingface")

    class File:
        def __init__(self, path: str):
            self.path = path

    files = [File("model.safetensors"), File("data/en_us/train.tsv"), File("data/is_is/audio/train.tar.gz")]
    assert [item.path for item in module.select_files(files, {"paths": ["model.safetensors"]})] == ["model.safetensors"]
    assert [item.path for item in module.select_files(files, {"regex": r"^data/[^/]+/(dev|test|train)\.tsv$"})] == ["data/en_us/train.tsv"]
    assert [item.path for item in module.select_files(files, {"prefixes": ["data/is_is/"]})] == ["data/is_is/audio/train.tar.gz"]


def test_civitai_benchmark_observer_reports_range_fallback(tmp_path: Path) -> None:
    module = _load("benchmark_common")
    observations = {}

    class Response:
        status = 200
        headers = {"Content-Length": "3"}

        def __init__(self):
            self._data = bytearray(b"abc")

        def read(self, amount=-1):
            if not self._data:
                return b""
            if amount < 0:
                amount = len(self._data)
            chunk = bytes(self._data[:amount])
            del self._data[:amount]
            return chunk

        def getcode(self):
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    download_civitai_file(
        {
            "destination": str(tmp_path),
            "filename": "model.bin",
            "download_url": "https://civitai.com/api/download/models/1",
            "expected_size": 3,
            "segments": 4,
        },
        lambda _: None,
        opener=lambda *_, **__: Response(),
        benchmark_observer=observations.update,
    )
    assert observations == {"range_supported": False, "fallback": False}
    assert module.reconcile_files(tmp_path, [("model.bin", 3)])["status"] == "available"
