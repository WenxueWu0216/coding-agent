
def last_n(xs, n):
    """Return the last n items of xs. n=0 returns an empty list."""
    return xs[-n:] if n > 0 else []
