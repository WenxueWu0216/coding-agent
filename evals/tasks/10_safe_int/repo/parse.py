
def safe_int(s, default=None):
    try:
        return int(s.strip)
    except Exception:
        return default
