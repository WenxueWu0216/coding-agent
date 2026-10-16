
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from duration import parse_duration

@pytest.mark.parametrize("text,seconds", [
    ("45s", 45),
    ("2m", 120),
    ("1h", 3600),
    ("1h30m", 5400),
    ("1h5m10s", 3910),
    ("90m", 5400),
])
def test_valid(text, seconds):
    assert parse_duration(text) == seconds

@pytest.mark.parametrize("text", ["", "5x", "h", "1.5h", "30s1h"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_duration(text)
