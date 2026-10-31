def to_storage(dt):
    """Convert a timezone-aware datetime to the project's timestamp storage format."""
    return int(dt.timestamp() * 1000)
