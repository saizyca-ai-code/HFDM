from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlparse


class InvalidRepoReference(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RepoReference:
    repo_id: str
    revision: str = "main"
    repo_type: str = "model"


def parse_repo_reference(value: str) -> RepoReference:
    raw = value.strip()
    if not raw:
        raise InvalidRepoReference("請輸入 Hugging Face Model ID 或網址")

    revision = "main"
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "huggingface.co",
            "www.huggingface.co",
        }:
            raise InvalidRepoReference("只支援 huggingface.co 模型網址")
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        if parts and parts[0] in {"datasets", "spaces"}:
            raise InvalidRepoReference("V1 只支援 model repo")
        if parts and parts[0] == "models":
            parts = parts[1:]
        if len(parts) < 2:
            raise InvalidRepoReference("網址中找不到 owner/repo")
        repo_parts = parts[:2]
        rest = parts[2:]
        if rest:
            if rest[0] != "tree" or len(rest) < 2:
                raise InvalidRepoReference("V1 支援 repo 首頁或 /tree/<revision> 網址")
            revision = rest[1]
        repo_id = "/".join(repo_parts)
    else:
        parts = [part for part in raw.strip("/").split("/") if part]
        if len(parts) != 2:
            raise InvalidRepoReference("Model ID 必須是 owner/repo")
        repo_id = "/".join(parts)

    owner, name = repo_id.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise InvalidRepoReference("Model ID 格式不正確")
    if any(char in repo_id for char in "\\\0"):
        raise InvalidRepoReference("Model ID 含有不允許的字元")
    return RepoReference(repo_id=repo_id, revision=revision)
