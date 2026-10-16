
from inventory.pricing import order_total


def summarize(orders):
    """orders: list of (lines, code) tuples."""
    totals = [order_total(lines, code) for lines, code in orders]
    return {"count": len(totals), "revenue": round(sum(totals), 2)}
