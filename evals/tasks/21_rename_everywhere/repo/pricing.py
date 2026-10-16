
def calc_total(items, tax_rate=0.0):
    """items is a list of (price, qty) pairs."""
    subtotal = sum(price * qty for price, qty in items)
    return round(subtotal * (1 + tax_rate), 2)
