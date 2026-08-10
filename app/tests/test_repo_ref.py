import pytest

from hfdm.repo_ref import InvalidRepoReference, parse_repo_reference


@pytest.mark.parametrize(
    ("source", "repo_id", "revision"),
    [
        ("Comfy-Org/z_image_turbo", "Comfy-Org/z_image_turbo", "main"),
        ("https://huggingface.co/Comfy-Org/z_image_turbo", "Comfy-Org/z_image_turbo", "main"),
        (
            "https://huggingface.co/Comfy-Org/z_image_turbo/tree/main",
            "Comfy-Org/z_image_turbo",
            "main",
        ),
        (
            "https://huggingface.co/Comfy-Org/z_image_turbo/tree/dev?x=1",
            "Comfy-Org/z_image_turbo",
            "dev",
        ),
    ],
)
def test_parse_repo_reference(source: str, repo_id: str, revision: str) -> None:
    result = parse_repo_reference(source)
    assert result.repo_id == repo_id
    assert result.revision == revision


@pytest.mark.parametrize(
    "source",
    ["", "not-a-repo", "https://example.com/a/b", "https://huggingface.co/datasets/a/b"],
)
def test_invalid_repo_reference(source: str) -> None:
    with pytest.raises(InvalidRepoReference):
        parse_repo_reference(source)
