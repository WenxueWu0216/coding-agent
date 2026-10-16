
from pricing import calc_total as ct


def daily_report(orders):
    """orders is a list of item lists."""
    return sum(ct(items) for items in orders)
