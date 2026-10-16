
def format_cents(cents):
    """Format an integer number of cents as dollars, e.g. 1005 -> '$10.05'."""
    return f"${cents // 100}.{cents % 100:02d}"
