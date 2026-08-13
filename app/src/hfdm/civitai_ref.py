from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


class InvalidCivitaiReference(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CivitaiReference:
    model_id: int | None = None
    version_id: int | None = None


def is_civitai_reference(value: str) -> bool:
    raw = value.strip().casefold()
    return (
        "civitai.com" in raw
        or raw.startswith(("model:", "models/", "version:", "versions/"))
        or raw.isdecimal()
    )


def parse_civitai_reference(value: str) -> CivitaiReference:
    raw = value.strip()
    if not raw:
        raise InvalidCivitaiReference("請輸入 Civitai model／version 網址或 ID")

    model_id: int | None = None
    version_id: int | None = None
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "civitai.com",
            "www.civitai.com",
        }:
            raise InvalidCivitaiReference("只支援 civitai.com 網址")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "models":
            model_id = _positive_id(parts[1], "model")
        elif len(parts) >= 4 and parts[:3] == ["api", "v1", "model-versions"]:
            version_id = _positive_id(parts[3], "version")
        elif len(parts) >= 4 and parts[:3] == ["api", "download", "models"]:
            version_id = _positive_id(parts[3], "version")
        else:
            raise InvalidCivitaiReference("網址中找不到 Civitai model／version ID")
        query = parse_qs(parsed.query)
        query_version = query.get("modelVersionId", query.get("modelversionid", []))
        if query_version:
            version_id = _positive_id(query_version[0], "version")
    else:
        normalized = raw.strip("/")
        lowered = normalized.casefold()
        if lowered.startswith("model:"):
            model_id = _positive_id(normalized.split(":", 1)[1], "model")
        elif lowered.startswith("models/"):
            model_id = _positive_id(normalized.split("/", 1)[1], "model")
        elif lowered.startswith("version:"):
            version_id = _positive_id(normalized.split(":", 1)[1], "version")
        elif lowered.startswith("versions/"):
            version_id = _positive_id(normalized.split("/", 1)[1], "version")
        elif normalized.isdecimal():
            model_id = _positive_id(normalized, "model")
        else:
            raise InvalidCivitaiReference(
                "Civitai ID 請使用 model:123、version:456，或直接輸入 model ID"
            )
    return CivitaiReference(model_id=model_id, version_id=version_id)


def _positive_id(value: str, kind: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise InvalidCivitaiReference(f"Civitai {kind} ID 格式不正確") from exc
    if parsed < 1:
        raise InvalidCivitaiReference(f"Civitai {kind} ID 必須大於 0")
    return parsed
