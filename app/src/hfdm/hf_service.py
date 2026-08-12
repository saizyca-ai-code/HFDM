from __future__ import annotations

from huggingface_hub import HfApi
from huggingface_hub.hf_api import RepoFile

from .repo_ref import RepoReference, parse_repo_reference
from .schemas import RepoFileInfo, RepoResolution


class HuggingFaceService:
    def resolve(self, source: str, token: str | None = None) -> RepoResolution:
        ref: RepoReference = parse_repo_reference(source)
        return self._resolve_reference(ref, token)

    def resolve_existing(
        self,
        repo_id: str,
        requested_revision: str,
        token: str | None = None,
    ) -> RepoResolution:
        return self._resolve_reference(
            RepoReference(repo_id=repo_id, revision=requested_revision, repo_type="model"),
            token,
        )

    @staticmethod
    def _resolve_reference(ref: RepoReference, token: str | None) -> RepoResolution:
        api = HfApi(token=token or False, library_name="hfdm")
        info = api.model_info(ref.repo_id, revision=ref.revision, token=token or False)
        commit_hash = info.sha
        files: list[RepoFileInfo] = []
        for item in api.list_repo_tree(
            ref.repo_id,
            recursive=True,
            expand=True,
            revision=commit_hash,
            repo_type="model",
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
        return RepoResolution(
            repo_id=ref.repo_id,
            requested_revision=ref.revision,
            commit_hash=commit_hash,
            files=files,
            total_bytes=sum(item.size for item in files),
        )
