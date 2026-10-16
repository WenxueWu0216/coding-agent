
from inventory.cli import quote
from inventory.models import OrderLine
from inventory.pricing import order_total
from inventory.reports import summarize

L = [OrderLine("A1", 2), OrderLine("B2", 4)]   # 8 + 6 = 14

def test_no_code(): assert order_total(L) == 14.0
def test_save15(): assert order_total(L, "SAVE15") == 11.9
def test_half(): assert order_total(L, "HALF") == 7.0
def test_unknown_code(): assert order_total(L, "NOPE") == 14.0
def test_cli(): assert quote("C3:1", "SAVE15") == "$34.00"
def test_report(): assert summarize([(L, None), (L, "HALF")]) == {"count": 2, "revenue": 21.0}
