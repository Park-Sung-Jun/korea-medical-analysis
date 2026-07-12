from scripts.merge_tmap import apply_tmap_row


def test_apply_tmap_row_fills_isochrone_deadzone_from_exact_ors_matrix_value():
    properties = {"aging_class": 3, "access_min": None, "access_class": 3}
    row = {"tmap_min": "95.0", "ors_min": "78.2", "ratio": "1.21"}

    apply_tmap_row(properties, row)

    assert properties["access_min"] == 78.2
    assert properties["access_min_exact"] is True
    assert properties["access_band"] == "60~90"
    assert properties["access_suspect"] is False
    assert properties["bivar_class"] == "A3B3"
    assert properties["access_min_tmap"] == 95.0
    assert properties["bivar_class_tmap"] == "A3B3"
