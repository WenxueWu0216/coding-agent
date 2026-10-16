
from money import format_cents


def display_price(cents):
    return format_cents(cents)


def display_cart(prices):
    return ", ".join(format_cents(c) for c in prices)
