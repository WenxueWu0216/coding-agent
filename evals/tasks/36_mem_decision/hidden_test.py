from datetime import datetime, timedelta, timezone

from timeutil import to_storage


def test_utc_millis():
    v = to_storage(datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert v == 1767225600000 and isinstance(v, int)


def test_other_zone():
    tz = timezone(timedelta(hours=2))
    assert to_storage(datetime(2026, 1, 1, 2, 0, 0, 500000, tzinfo=tz)) == 1767225600500
