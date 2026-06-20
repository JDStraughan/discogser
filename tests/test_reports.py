from discogser.pipeline import _csv_cell


def test_csv_formula_injection_is_neutralised():
    assert _csv_cell("=HYPERLINK(0)") == "'=HYPERLINK(0)"
    assert _csv_cell("+1") == "'+1"
    assert _csv_cell("-1+1") == "'-1+1"
    assert _csv_cell("@SUM(A1)") == "'@SUM(A1)"


def test_csv_ordinary_values_untouched():
    assert _csv_cell("Pink Floyd") == "Pink Floyd"
    assert _csv_cell("12.00") == "12.00"
    assert _csv_cell(42) == "42"
    assert _csv_cell(None) == ""
