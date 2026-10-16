
def median(xs):
    """Return the median of a non-empty list of numbers."""
    xs.sort()
    return xs[len(xs) // 2]
