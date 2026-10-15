
from dates import parse_date
def test_parse(): assert parse_date("2026-10-10") == (2026, 10, 10)
def test_leading_zero(): assert parse_date("2027-05-01") == (2027, 5, 1)
