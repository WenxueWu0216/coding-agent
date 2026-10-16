
from pathlib import Path
import pricing
from cart import Cart
from invoice import invoice_lines, invoice_total_no_tax
from report import daily_report

def test_new_name():
    assert pricing.compute_total([(10.0, 2)], 0.1) == 22.0
    assert not hasattr(pricing, "calc_total")

def test_callers_work():
    c = Cart(); c.add(5.0, 3)
    assert c.total() == 15.0
    assert invoice_lines([(2.0, 1)], 0.5)[-1] == "TOTAL 3.00"
    assert invoice_total_no_tax([(1.5, 2)]) == 3.0
    assert daily_report([[(1.0, 1)], [(2.0, 2)]]) == 5.0

def test_old_name_gone_everywhere():
    for f in Path(".").rglob("*.py"):
        if f.name != "test_hidden.py":
            assert "calc_total" not in f.read_text(), f
