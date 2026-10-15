
from roman import roman_to_int
def test_cases():
    assert roman_to_int("III") == 3
    assert roman_to_int("IV") == 4
    assert roman_to_int("MCMXCIV") == 1994
    assert roman_to_int("MMXXVI") == 2026
