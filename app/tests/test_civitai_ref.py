import pytest

from hfdm.civitai_ref import InvalidCivitaiReference, parse_civitai_reference


@pytest.mark.parametrize(
    ("value", "model_id", "version_id"),
    [
        ("123", 123, None),
        ("model:123", 123, None),
        ("version:456", None, 456),
        ("https://civitai.com/models/123/example", 123, None),
        ("https://civitai.com/models/123/example?modelVersionId=456", 123, 456),
        ("https://civitai.com/api/v1/model-versions/456", None, 456),
        ("https://civitai.com/api/download/models/456", None, 456),
    ],
)
def test_parse_civitai_reference(value: str, model_id: int | None, version_id: int | None) -> None:
    parsed = parse_civitai_reference(value)
    assert parsed.model_id == model_id
    assert parsed.version_id == version_id


@pytest.mark.parametrize("value", ["", "model:nope", "https://example.com/models/1", "models/0"])
def test_invalid_civitai_reference_is_rejected(value: str) -> None:
    with pytest.raises(InvalidCivitaiReference):
        parse_civitai_reference(value)
