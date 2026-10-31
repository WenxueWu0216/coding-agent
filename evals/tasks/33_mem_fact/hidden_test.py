from billing import price_with_vat


def test_rate():
    assert price_with_vat(100) == 121.0


def test_rounding():
    assert price_with_vat(9.99) == 12.09
