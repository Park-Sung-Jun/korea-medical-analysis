import pytest

from scripts.stage_boundary_migration import carry_forward_enrichments, validate_stage


def feature(code, name, **properties):
    return {
        "type": "Feature",
        "properties": {"code": code, "name": name, **properties},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
        },
    }


def collection(features, **meta):
    return {"type": "FeatureCollection", "meta": meta, "features": features}


def test_carry_forward_preserves_existing_metrics_without_mapping_retired_bucheon_parent():
    old = collection(
        [
            feature(
                "11110",
                "기존구",
                aging_class=1,
                access_min_tmap=25.0,
                access_class_tmap=1,
                bivar_class_tmap="A1B1",
                access_min_exact=True,
                access_band="60~90",
                access_suspect=False,
                er_min=8.2,
            ),
            feature("41190", "부천시", checkup_rate=76.0, hosp_gen_cnt=4),
        ],
        tmap_fields="old tmap metadata",
    )
    new = collection(
        [
            feature("11110", "기존구", aging_class=3, access_min=15),
            feature("41192", "부천시원미구", aging_class=1, access_min=15),
            feature("41194", "부천시소사구", aging_class=1, access_min=15),
            feature("41196", "부천시오정구", aging_class=1, access_min=15),
        ],
        count=4,
    )

    stats = carry_forward_enrichments(new, old)

    by_code = {f["properties"]["code"]: f["properties"] for f in new["features"]}
    assert stats == {"matched": 1, "new": 3, "retired": ["41190"]}
    assert by_code["11110"]["access_min_tmap"] == 25.0
    assert by_code["11110"]["er_min"] == 8.2
    assert by_code["11110"]["bivar_class_tmap"] == "A3B1"
    assert "access_min_exact" not in by_code["11110"]
    assert "access_band" not in by_code["11110"]
    assert "access_suspect" not in by_code["11110"]
    assert "checkup_rate" not in by_code["41192"]
    assert "hosp_gen_cnt" not in by_code["41194"]
    assert new["meta"]["tmap_fields"] == "old tmap metadata"


def test_validate_stage_requires_unique_codes_and_expected_replacement_codes():
    valid = collection(
        [
            feature("41192", "부천시원미구"),
            feature("41194", "부천시소사구"),
            feature("41196", "부천시오정구"),
        ]
    )
    assert validate_stage(valid, expected_count=3)["count"] == 3

    duplicate = collection([feature("41192", "A"), feature("41192", "B")])
    with pytest.raises(ValueError, match="중복"):
        validate_stage(duplicate, expected_count=2)

    retired = collection(
        [
            feature("41190", "부천시"),
            feature("41192", "부천시원미구"),
            feature("41194", "부천시소사구"),
        ]
    )
    with pytest.raises(ValueError, match="41190"):
        validate_stage(retired, expected_count=3)
