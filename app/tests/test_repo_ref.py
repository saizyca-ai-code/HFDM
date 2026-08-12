import pytest

from hfdm.repo_ref import InvalidRepoReference, parse_repo_reference


@pytest.mark.parametrize(
    ("source", "repo_id", "revision", "repo_type"),
    [
        ("Comfy-Org/z_image_turbo", "Comfy-Org/z_image_turbo", "main", "model"),
        ("https://huggingface.co/Comfy-Org/z_image_turbo", "Comfy-Org/z_image_turbo", "main", "model"),
        (
            "https://huggingface.co/Comfy-Org/z_image_turbo/tree/main",
            "Comfy-Org/z_image_turbo",
            "main",
            "model",
        ),
        (
            "https://huggingface.co/Comfy-Org/z_image_turbo/tree/dev?x=1",
            "Comfy-Org/z_image_turbo",
            "dev",
            "model",
        ),
        ("datasets/owner/repo", "owner/repo", "main", "dataset"),
        ("https://huggingface.co/datasets/owner/repo", "owner/repo", "main", "dataset"),
        (
            "https://huggingface.co/datasets/owner/repo/tree/refs%2Fpr%2F2",
            "owner/repo",
            "refs/pr/2",
            "dataset",
        ),
    ],
)
def test_parse_repo_reference(
    source: str, repo_id: str, revision: str, repo_type: str
) -> None:
    result = parse_repo_reference(source)
    assert result.repo_id == repo_id
    assert result.revision == revision
    assert result.repo_type == repo_type


@pytest.mark.parametrize(
    "source",
    ["", "not-a-repo", "https://example.com/a/b", "https://huggingface.co/spaces/a/b"],
)
def test_invalid_repo_reference(source: str) -> None:
    with pytest.raises(InvalidRepoReference):
        parse_repo_reference(source)
