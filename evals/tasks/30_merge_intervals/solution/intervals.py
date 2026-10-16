
def merge_intervals(intervals):
    """Merge overlapping [start, end] intervals and return them sorted by start.
    Intervals that touch (one ends where the next starts) are merged too.
    The input list must not be modified."""
    out = []
    for start, end in sorted(intervals, key=lambda iv: iv[0]):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out
