from __future__ import annotations

import io
import json
from typing import Any

from hfdm.civitai_service import CivitaiService
from hfdm.schemas import CivitaiSearchRequest


class JsonResponse(io.BytesIO):
    def __init__(self, value: dict[str, Any]):
        super().__init__(json.dumps(value).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_civitai_model_resolves_versions_files_and_stable_metadata() -> None:
    requests = []
    model = {
        "id": 123,
        "name": "Example LoRA",
        "type": "LORA",
        "creator": {"username": "artist"},
        "modelVersions": [
            {
                "id": 456,
                "name": "v2",
                "baseModel": "SDXL 1.0",
                "trainedWords": ["example"],
                "files": [
                    {
                        "id": 789,
                        "name": "example.safetensors",
                        "sizeKB": 2,
                        "type": "Model",
                        "primary": True,
                        "metadata": {"format": "SafeTensor", "fp": "fp16"},
                        "hashes": {"SHA256": "A" * 64},
                        "virusScanResult": "Success",
                        "pickleScanResult": "Success",
                        "downloadUrl": "https://civitai.com/api/download/models/456?type=Model&token=secret",
                    }
                ],
                "images": [{"url": "https://image.civitai.com/preview.jpeg"}],
            },
            {"id": 455, "name": "v1", "files": []},
        ],
    }

    def opener(request, timeout=0):
        requests.append(request)
        return JsonResponse(model)

    resolution = CivitaiService(opener).resolve("model:123", "civitai_secret", 456)

    assert resolution.provider == "civitai"
    assert resolution.repo_id == "models/123"
    assert resolution.commit_hash == "456"
    assert resolution.display_name == "Example LoRA"
    assert [version.id for version in resolution.versions] == ["456", "455"]
    assert resolution.suggested_files == ["example.safetensors"]
    file = resolution.files[0]
    assert file.remote_id == "789"
    assert file.size == 2048
    assert file.sha256 == "A" * 64
    assert file.provider_metadata["download_url"] == (
        "https://civitai.com/api/download/models/456?type=Model"
    )
    assert requests[0].get_header("Authorization") == "Bearer civitai_secret"
    assert "civitai_secret" not in resolution.model_dump_json()


def test_civitai_search_maps_filters_and_results() -> None:
    captured = []
    payload = {
        "items": [
            {
                "id": 123,
                "name": "Example",
                "type": "LORA",
                "creator": {"username": "artist"},
                "modelVersions": [
                    {"id": 456, "baseModel": "SDXL 1.0", "images": [{"url": "preview"}]}
                ],
            }
        ],
        "metadata": {"currentPage": 1, "totalPages": 2},
    }

    def opener(request, timeout=0):
        captured.append(request.full_url)
        return JsonResponse(payload)

    result = CivitaiService(opener).search(
        CivitaiSearchRequest(
            query="example",
            username="artist",
            types=["LORA"],
            base_models=["SDXL 1.0"],
        )
    )

    assert result.items[0].latest_version_id == 456
    assert result.next_page == 2
    assert "query=example" in captured[0]
    assert "types=LORA" in captured[0]
    assert "baseModels=SDXL+1.0" in captured[0]
