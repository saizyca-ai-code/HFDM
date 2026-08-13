from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .civitai_ref import CivitaiReference, parse_civitai_reference
from .schemas import CivitaiSearchRequest, CivitaiSearchResult, RepoFileInfo, RepoResolution, SourceVersionInfo


class CivitaiServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class CivitaiService:
    api_root = "https://civitai.com/api/v1"

    def __init__(self, opener: Callable[..., Any] = urlopen):
        self._opener = opener

    def resolve(
        self,
        source: str,
        token: str | None = None,
        version_id: int | None = None,
    ) -> RepoResolution:
        ref = parse_civitai_reference(source)
        if version_id is not None:
            ref = CivitaiReference(model_id=ref.model_id, version_id=version_id)
        if ref.model_id is not None:
            model = self._get_json(f"/models/{ref.model_id}", token)
            return self._resolution(model, ref.version_id, token)
        if ref.version_id is not None:
            version = self._get_json(f"/model-versions/{ref.version_id}", token)
            model_id = int(version.get("modelId") or 0)
            if model_id < 1:
                raise CivitaiServiceError("Civitai version 缺少 model ID")
            model = self._get_json(f"/models/{model_id}", token)
            return self._resolution(model, ref.version_id, token)
        raise CivitaiServiceError("Civitai reference does not contain an ID", status_code=422)

    def resolve_existing(
        self,
        repo_id: str,
        requested_revision: str,
        token: str | None = None,
        version_id: int | None = None,
    ) -> RepoResolution:
        try:
            model_id = int(repo_id.split("/", 1)[1])
        except (IndexError, ValueError) as exc:
            raise CivitaiServiceError("Civitai history identity is invalid", status_code=422) from exc
        source = f"model:{model_id}"
        selected = version_id
        if selected is None and requested_revision != "latest":
            try:
                selected = int(requested_revision)
            except ValueError:
                selected = None
        return self.resolve(source, token, selected)

    def search(self, request: CivitaiSearchRequest, token: str | None = None) -> CivitaiSearchResult:
        params: list[tuple[str, str | int]] = [
            ("limit", request.limit),
            ("page", request.page),
            ("sort", request.sort),
            ("period", request.period),
        ]
        for key in ("query", "tag", "username"):
            value = getattr(request, key)
            if value:
                params.append((key, value))
        params.extend(("types", value) for value in request.types)
        params.extend(("baseModels", value) for value in request.base_models)
        payload = self._get_json(f"/models?{urlencode(params)}", token)
        items = []
        for raw in payload.get("items") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            versions = raw.get("modelVersions") or []
            latest = versions[0] if versions and isinstance(versions[0], dict) else {}
            images = latest.get("images") or []
            creator = raw.get("creator") or {}
            items.append(
                {
                    "id": int(raw["id"]),
                    "name": str(raw.get("name") or raw["id"]),
                    "model_type": raw.get("type"),
                    "creator": creator.get("username") if isinstance(creator, dict) else None,
                    "latest_version_id": int(latest["id"]) if latest.get("id") else None,
                    "base_model": latest.get("baseModel"),
                    "preview_url": (
                        images[0].get("url")
                        if images and isinstance(images[0], dict)
                        else None
                    ),
                }
            )
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        current = int(metadata.get("currentPage") or request.page)
        total = int(metadata.get("totalPages") or current)
        return CivitaiSearchResult(
            items=items,
            current_page=current,
            total_pages=total,
            next_page=current + 1 if current < total else None,
        )

    def _get_json(self, path: str, token: str | None) -> dict[str, Any]:
        headers = {"Accept": "application/json", "User-Agent": "HFDM/2"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(f"{self.api_root}{path}", headers=headers)
        try:
            with self._opener(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise CivitaiServiceError(
                    "無法存取 Civitai model，請確認 API Token 或下載權限",
                    status_code=exc.code,
                ) from exc
            if exc.code == 404:
                raise CivitaiServiceError("找不到 Civitai model／version", status_code=404) from exc
            raise CivitaiServiceError(f"Civitai API 回應 {exc.code}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise CivitaiServiceError(f"讀取 Civitai API 失敗：{exc}") from exc
        if not isinstance(payload, dict):
            raise CivitaiServiceError("Civitai API 回應格式不正確")
        return payload

    def _resolution(
        self,
        model: dict[str, Any],
        version_id: int | None,
        token: str | None,
    ) -> RepoResolution:
        model_id = int(model.get("id") or 0)
        if model_id < 1:
            raise CivitaiServiceError("Civitai model 缺少 ID")
        raw_versions = model.get("modelVersions") or []
        if not isinstance(raw_versions, list) or not raw_versions:
            raise CivitaiServiceError("此 Civitai model 沒有可下載版本", status_code=404)
        selected = None
        if version_id is not None:
            selected = next(
                (item for item in raw_versions if int(item.get("id") or 0) == version_id),
                None,
            )
            if selected is None:
                raise CivitaiServiceError("指定的 version 不屬於此 model", status_code=422)
        else:
            selected = raw_versions[0]

        selected_id = int(selected.get("id") or 0)
        if selected_id < 1:
            raise CivitaiServiceError("Civitai version 缺少 ID")
        files = self._files(selected)
        if not files:
            raise CivitaiServiceError("此 Civitai version 沒有可下載檔案", status_code=404)
        versions = [self._version(item) for item in raw_versions if item.get("id")]
        suggested = [item.path for item in files if item.primary] or [item.path for item in files]
        creator = model.get("creator") or {}
        images = selected.get("images") or []
        preview = images[0].get("url") if images and isinstance(images[0], dict) else None
        requested_revision = str(version_id) if version_id is not None else "latest"
        return RepoResolution(
            provider="civitai",
            repo_id=f"models/{model_id}",
            repo_type="model",
            requested_revision=requested_revision,
            commit_hash=str(selected_id),
            files=files,
            total_bytes=sum(item.size for item in files),
            suggested_files=suggested,
            display_name=str(model.get("name") or f"Civitai model {model_id}"),
            version_name=str(selected.get("name") or selected_id),
            versions=versions,
            provider_metadata={
                "model_id": model_id,
                "version_id": selected_id,
                "model_type": model.get("type"),
                "base_model": selected.get("baseModel"),
                "creator": creator.get("username") if isinstance(creator, dict) else None,
                "trained_words": selected.get("trainedWords") or [],
                "preview_url": preview,
                "requires_token": bool(token),
            },
        )

    def _files(self, version: dict[str, Any]) -> list[RepoFileInfo]:
        files: list[RepoFileInfo] = []
        used_paths: set[str] = set()
        for raw in version.get("files") or []:
            if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
                continue
            file_id = str(raw["id"])
            path = str(raw["name"]).replace("\\", "/").split("/")[-1]
            if path in used_paths:
                path = f"{file_id}-{path}"
            used_paths.add(path)
            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
            hashes = raw.get("hashes") if isinstance(raw.get("hashes"), dict) else {}
            download_url = self._stable_download_url(
                raw.get("downloadUrl") or version.get("downloadUrl")
            )
            scan_parts = [raw.get("virusScanResult"), raw.get("pickleScanResult")]
            scan_status = " / ".join(str(item) for item in scan_parts if item)
            files.append(
                RepoFileInfo(
                    path=path,
                    size=max(0, round(float(raw.get("sizeKB") or 0) * 1024)),
                    remote_id=file_id,
                    sha256=str(hashes.get("SHA256")) if hashes.get("SHA256") else None,
                    primary=bool(raw.get("primary")),
                    file_type=str(raw.get("type")) if raw.get("type") else None,
                    format=str(metadata.get("format")) if metadata.get("format") else None,
                    precision=str(metadata.get("fp")) if metadata.get("fp") else None,
                    scan_status=scan_status or None,
                    provider_metadata={
                        "download_url": download_url,
                        "file_type": raw.get("type"),
                        "format": metadata.get("format"),
                        "precision": metadata.get("fp"),
                        "size_variant": metadata.get("size"),
                    },
                )
            )
        files.sort(key=lambda item: (not item.primary, item.path.casefold()))
        return files

    @staticmethod
    def _version(raw: dict[str, Any]) -> SourceVersionInfo:
        created = raw.get("createdAt")
        try:
            parsed_created = datetime.fromisoformat(str(created).replace("Z", "+00:00")) if created else None
        except ValueError:
            parsed_created = None
        return SourceVersionInfo(
            id=str(raw["id"]),
            name=str(raw.get("name") or raw["id"]),
            base_model=str(raw.get("baseModel")) if raw.get("baseModel") else None,
            created_at=parsed_created,
        )

    @staticmethod
    def _stable_download_url(value: Any) -> str:
        if not value:
            raise CivitaiServiceError("Civitai 檔案缺少 download URL")
        parsed = urlparse(str(value))
        if parsed.scheme != "https" or parsed.hostname not in {"civitai.com", "www.civitai.com"}:
            raise CivitaiServiceError("Civitai download URL 來源不受信任")
        if not parsed.path.startswith("/api/download/models/"):
            raise CivitaiServiceError("Civitai download URL 格式不正確")
        safe_query = urlencode(
            [(key, item) for key, item in parse_qsl(parsed.query) if key.casefold() != "token"]
        )
        return urlunparse(("https", "civitai.com", parsed.path, "", safe_query, ""))
