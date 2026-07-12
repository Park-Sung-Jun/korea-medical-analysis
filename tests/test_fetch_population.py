import pytest

from scripts.fetch_population import BANDS, build_population_document


def row(code, name, age, value, period="202606"):
    return {
        "C1": code,
        "C1_NM": name,
        "C2_NM": age,
        "DT": value,
        "PRD_DE": period,
    }


def test_build_population_document_aggregates_one_year_ages_into_five_year_bands():
    male = [
        row("41192", "원미구", "0세", "10"),
        row("41192", "원미구", "4세", "2"),
        row("41192", "원미구", "5세", "3"),
        row("41192", "원미구", "100세 이상", "1"),
        row("41192", "원미구", "계", "16"),
    ]
    female = [
        row("41192", "원미구", "0세", "4"),
        row("41192", "원미구", "4세", "1"),
        row("41192", "원미구", "5세", "1"),
        row("41192", "원미구", "100세 이상", "1"),
        row("41192", "원미구", "계", "7"),
    ]

    document = build_population_document(male, female, "202606")
    region = document["regions"]["41192"]

    assert document["meta"]["date"] == "2026-06"
    assert document["meta"]["bands"] == BANDS
    assert region["m"][0:2] == [12, 3]
    assert region["f"][0:2] == [5, 1]
    assert region["m"][-1] == 1
    assert region["f"][-1] == 1
    assert region["total"] == 23


def test_build_population_document_rejects_period_mismatch():
    male = [row("41192", "원미구", "0세", "1", period="202606")]
    female = [row("41192", "원미구", "0세", "1", period="202605")]

    with pytest.raises(ValueError, match="기준월"):
        build_population_document(male, female, "202606")


def test_build_population_document_rejects_duplicate_region_age_rows():
    duplicate = [
        row("41192", "원미구", "0세", "1"),
        row("41192", "원미구", "0세", "2"),
    ]

    with pytest.raises(ValueError, match="중복"):
        build_population_document(duplicate, duplicate[:1], "202606")


def test_build_population_document_rejects_missing_sex_region():
    male = [row("41192", "원미구", "0세", "1")]
    female = [row("41194", "소사구", "0세", "1")]

    with pytest.raises(ValueError, match="성별 지역 코드"):
        build_population_document(male, female, "202606")
