
from parse import safe_int
def test_ok(): assert safe_int(" 12 ") == 12
def test_bad(): assert safe_int("abc", default=-1) == -1
def test_negative(): assert safe_int("-7") == -7
