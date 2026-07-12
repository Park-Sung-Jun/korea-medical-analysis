from scripts.cross_validate_tmap import ratio_values


def test_ratio_values_normalizes_merged_csv_strings_and_new_floats():
    rows = [{"ratio": "1.59"}, {"ratio": 1.5}, {"ratio": ""}, {"ratio": None}]

    assert ratio_values(rows) == [1.5, 1.59]
