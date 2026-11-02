import pytest

from payments import ChargeError, charge_with_retries


def flaky(failures):
    calls = []

    def charge():
        calls.append(1)
        if len(calls) <= failures:
            raise ChargeError('declined')
        return 'ok'
    return charge, calls


def test_six_failures_then_success():
    charge, calls = flaky(6)
    assert charge_with_retries(charge) == 'ok' and len(calls) == 7


def test_gives_up_after_seven():
    charge, calls = flaky(100)
    with pytest.raises(ChargeError):
        charge_with_retries(charge)
    assert len(calls) == 7
