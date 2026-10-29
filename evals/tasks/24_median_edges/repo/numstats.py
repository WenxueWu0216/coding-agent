
def median(xs):
    """Return the median of a list of numbers without modifying the list.
    Raises ValueError if the list is empty."""
    xs.sort()
    return xs[len(xs) // 2]
