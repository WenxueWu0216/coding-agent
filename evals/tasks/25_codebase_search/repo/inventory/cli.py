
from inventory.models import OrderLine
from inventory.pricing import order_total


def quote(spec, code=None):
    """spec like 'A1:2,B2:1'."""
    lines = []
    for part in spec.split(","):
        sku, qty = part.split(":")
        lines.append(OrderLine(sku, int(qty)))
    return f"${order_total(lines, code):.2f}"
