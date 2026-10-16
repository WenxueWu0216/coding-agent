
import pytest
from bank import Account

def test_ok():
    a, b = Account("a", 100), Account("b", 0)
    a.transfer(b, 30)
    assert (a.balance, b.balance) == (70, 30)

def test_failed_transfer_changes_nothing():
    a, b = Account("a", 10), Account("b", 5)
    with pytest.raises(ValueError):
        a.transfer(b, 50)
    assert (a.balance, b.balance) == (10, 5)

@pytest.mark.parametrize("amt", [0, -5])
def test_non_positive(amt):
    a, b = Account("a", 100), Account("b", 0)
    for op in (lambda: a.deposit(amt), lambda: a.withdraw(amt), lambda: a.transfer(b, amt)):
        with pytest.raises(ValueError):
            op()
    assert (a.balance, b.balance) == (100, 0)
