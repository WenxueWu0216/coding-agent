
def chunk(xs, size):
    if size < 1:
        raise ValueError("size must be >= 1")
    return [xs[i:i + size] for i in range(0, len(xs), size)]
