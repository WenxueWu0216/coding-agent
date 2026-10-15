
def safe_int(s, default=None):
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return default
