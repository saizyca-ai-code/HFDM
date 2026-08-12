from types import SimpleNamespace

from hfdm.hf_service import HuggingFaceService


class FakeRepoFile:
    def __init__(self, path: str, size: int, *, xet: bool = False):
        self.path = path
        self.size = size
        self.lfs = None
        self.xet_hash = "xet" if xet else None


class FakeHfApi:
    calls: list[tuple[str, str]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def model_info(self, repo_id, revision, token):
        self.calls.append(("model_info", repo_id))
        return SimpleNamespace(sha="a" * 40)

    def dataset_info(self, repo_id, revision, token):
        self.calls.append(("dataset_info", repo_id))
        return SimpleNamespace(sha="b" * 40)

    def list_repo_tree(self, repo_id, **kwargs):
        self.calls.append((f"tree:{kwargs['repo_type']}", repo_id))
        return [
            FakeRepoFile("README.md", 5),
            FakeRepoFile("data/train.parquet", 20, xet=True),
            FakeRepoFile("data/test.parquet", 10),
        ]


def test_dataset_resolution_uses_dataset_api_and_glob_preview(monkeypatch) -> None:
    FakeHfApi.calls = []
    monkeypatch.setattr("hfdm.hf_service.HfApi", FakeHfApi)
    monkeypatch.setattr("hfdm.hf_service.RepoFile", FakeRepoFile)

    result = HuggingFaceService().resolve(
        "https://huggingface.co/datasets/owner/repo/tree/dev",
        "hf_memory_only",
        ["**/*.parquet"],
        ["**/test.parquet"],
    )

    assert result.repo_type == "dataset"
    assert result.requested_revision == "dev"
    assert result.commit_hash == "b" * 40
    assert result.suggested_files == ["data/train.parquet"]
    assert result.files[1].lfs is True
    assert FakeHfApi.calls == [
        ("dataset_info", "owner/repo"),
        ("tree:dataset", "owner/repo"),
    ]
