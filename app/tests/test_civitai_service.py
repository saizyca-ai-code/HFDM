from __future__ import annotations

import io
import json
from typing import Any

from hfdm.civitai_service import CivitaiService


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
        "tags": ["style", "character"],
        "creator": {"username": "artist"},
        "modelVersions": [
            {
                "id": 456,
                "name": "v2",
                "baseModel": "SDXL 1.0",
                "baseModelType": "Standard",
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
                "images": [
                    {
                        "type": "image",
                        "url": "https://image.civitai.com/preview.jpeg",
                        "meta": {"prompt": "a test image", "steps": 20},
                    }
                ],
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
    assert resolution.provider_metadata["model_type"] == "LORA"
    assert resolution.provider_metadata["version_name"] == "v2"
    assert resolution.provider_metadata["base_model"] == "SDXL 1.0"
    assert resolution.provider_metadata["base_model_type"] == "Standard"
    assert resolution.provider_metadata["tags"] == ["style", "character"]
    assert [version.id for version in resolution.versions] == ["456", "455"]
    assert resolution.suggested_files == ["example.safetensors"]
    assert [item.path for item in resolution.files[1:]] == [
        "examples/01-128e5f77efbcf4f8.jpeg",
        "examples/01-128e5f77efbcf4f8.json",
    ]
    assert resolution.files[1].provider_metadata["kind"] == "example_image"
    assert resolution.files[2].provider_metadata["kind"] == "generation_metadata"
    assert '"prompt": "a test image"' in resolution.files[2].provider_metadata["inline_text"]
    file = resolution.files[0]
    assert file.remote_id == "789"
    assert file.size == 2048
    assert file.sha256 == "A" * 64
    assert file.provider_metadata["download_url"] == (
        "https://civitai.com/api/download/models/456?type=Model"
    )
    assert file.provider_metadata["comfyui_folder"] == "loras"
    assert file.provider_metadata["comfyui_path"] == "ComfyUI/models/loras"
    assert requests[0].get_header("Authorization") == "Bearer civitai_secret"
    assert "civitai_secret" not in resolution.model_dump_json()


def test_file_role_overrides_page_model_type_for_comfyui_folder() -> None:
    service = CivitaiService()

    assert service._comfyui_folder("Diffusion Model", "Checkpoint") == "diffusion_models"
    assert service._comfyui_folder("Text Encoder", "Checkpoint") == "text_encoders"
    assert service._comfyui_folder("Model", "Checkpoint") == "checkpoints"
    assert service._comfyui_folder("Model", "LORA") == "loras"
