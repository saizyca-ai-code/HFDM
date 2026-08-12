import pytest

from hfdm.file_selection import InvalidGlobPattern, select_repo_paths


FILES = [
    "README.md",
    "data/train/000.parquet",
    "data/train/001.parquet",
    "data/test/000.parquet",
    "metadata/train.jsonl",
]


def test_include_and_exclude_globs_preview_dataset_selection() -> None:
    assert select_repo_paths(FILES, ["**/*.parquet"], ["data/test/**"]) == [
        "data/train/000.parquet",
        "data/train/001.parquet",
    ]


def test_empty_globs_select_every_file() -> None:
    assert select_repo_paths(FILES) == FILES


def test_recursive_glob_also_matches_root_file() -> None:
    assert select_repo_paths(["root.parquet", "data/nested.parquet"], ["**/*.parquet"]) == [
        "root.parquet",
        "data/nested.parquet",
    ]


@pytest.mark.parametrize("pattern", ["../secret", "/absolute/**", "data/../../secret"])
def test_unsafe_glob_is_rejected(pattern: str) -> None:
    with pytest.raises(InvalidGlobPattern):
        select_repo_paths(FILES, [pattern])
