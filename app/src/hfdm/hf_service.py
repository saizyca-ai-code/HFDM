from __future__ import annotations

from huggingface_hub import HfApi
from huggingface_hub.hf_api import RepoFile

from .file_selection import select_repo_paths
from .repo_ref import RepoReference, parse_repo_reference
from .schemas import RepoFileInfo, RepoResolution


class HuggingFaceService:
    def resolve(
        self,
        source: str,
        token: str | None = None,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
    ) -> RepoResolution:
        ref: RepoReference = parse_repo_reference(source)
        return self._resolve_reference(ref, token, include_globs, exclude_globs)

    def resolve_existing(
        self,
        repo_id: str,
        requested_revision: str,
        token: str | None = None,
        repo_type: str = "model",
    ) -> RepoResolution:
        return self._resolve_reference(
            RepoReference(repo_id=repo_id, revision=requested_revision, repo_type=repo_type),
            token,
        )

    @staticmethod
    def _resolve_reference(
        ref: RepoReference,
        token: str | None,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
    ) -> RepoResolution:
        api = HfApi(token=token or False, library_name="hfdm")
        if ref.repo_type == "dataset":
            info = api.dataset_info(ref.repo_id, revision=ref.revision, token=token or False)
        else:
            info = api.model_info(ref.repo_id, revision=ref.revision, token=token or False)
        commit_hash = info.sha
        files: list[RepoFileInfo] = []
        for item in api.list_repo_tree(
            ref.repo_id,
            recursive=True,
            expand=True,
            revision=commit_hash,
            repo_type=ref.repo_type,
            token=token or False,
        ):
            if isinstance(item, RepoFile):
                files.append(
                    RepoFileInfo(
                        path=item.path,
                        size=item.size or 0,
                        lfs=item.lfs is not None or item.xet_hash is not None,
                    )
                )
        files.sort(key=lambda item: item.path.casefold())
        suggested = select_repo_paths(
            [item.path for item in files], include_globs, exclude_globs
        )
        return RepoResolution(
            repo_id=ref.repo_id,
            repo_type=ref.repo_type,
            requested_revision=ref.revision,
            commit_hash=commit_hash,
            files=files,
            total_bytes=sum(item.size for item in files),
            suggested_files=suggested,
        )
