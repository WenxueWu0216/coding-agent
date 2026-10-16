
import pytest
from duration import parse_duration

@pytest.mark.parametrize("text,seconds", [
    ("45s", 45), ("1h30m", 5400), ("1h5m10s", 3910), ("10m", 600), ("0s", 0),
    ("2h0m5s", 7205), ("100s", 100),
])
def test_valid(text, seconds):
    assert parse_duration(text) == seconds

@pytest.mark.parametrize("text", ["", "5x", "h", "1.5h", "30s1h", "1h 30m", "m5"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)
