VAT_RATE = 0.21  # the company's VAT rate, as a fraction


def price_with_vat(net):
    """Return the gross price (net plus VAT) for a net price."""
    return round(net * (1 + VAT_RATE), 2)
