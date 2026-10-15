
from temps import to_fahrenheit
def test_float(): assert abs(to_fahrenheit(37.5) - 99.5) < 1e-9
def test_int(): assert to_fahrenheit(100) == 212
