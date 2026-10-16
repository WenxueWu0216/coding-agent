
from pathlib import Path
from csvline import parse_csv_line

def test_plain(): assert parse_csv_line("a,b,c") == ["a", "b", "c"]
def test_quoted_comma(): assert parse_csv_line('a,"b,c",d') == ["a", "b,c", "d"]
def test_escaped_quote(): assert parse_csv_line('"he said ""hi""",x') == ['he said "hi"', "x"]
def test_empty_fields(): assert parse_csv_line("a,,b,") == ["a", "", "b", ""]

def test_no_csv_module():
    src = Path("csvline.py").read_text()
    assert "import csv" not in src and "from csv" not in src
