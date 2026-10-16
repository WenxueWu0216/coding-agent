
from app import display_cart, display_price
from money import format_cents

def test_root_cause_fixed():
    assert format_cents(1005) == "$10.05"
    assert format_cents(7) == "$0.07"
    assert format_cents(1999) == "$19.99"
    assert format_cents(100) == "$1.00"

def test_app():
    assert display_price(1005) == "$10.05"
    assert display_cart([5, 250]) == "$0.05, $2.50"
