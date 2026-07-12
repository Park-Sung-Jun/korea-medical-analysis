import pytest

from scripts.prepare_official_hospitals import prepare_official_hospitals


def test_prepare_official_hospitals_removes_only_named_non_designated_entry():
    source = {
        "meta": {"title": "old"},
        "hospitals": [
            {"id": 1, "name": "공식병원"},
            {"id": 2, "name": "강원대학교병원"},
        ],
    }

    result = prepare_official_hospitals(
        source,
        excluded_names={"강원대학교병원"},
        expected_count=1,
    )

    assert [h["name"] for h in result["hospitals"]] == ["공식병원"]
    assert result["meta"]["count"] == 1
    assert result["meta"]["period"] == "2024-01-01~2026-12-31"


def test_prepare_official_hospitals_fails_when_exclusion_or_count_is_unexpected():
    source = {"hospitals": [{"id": 1, "name": "공식병원"}]}

    with pytest.raises(ValueError, match="제외 대상"):
        prepare_official_hospitals(
            source,
            excluded_names={"강원대학교병원"},
            expected_count=1,
        )
