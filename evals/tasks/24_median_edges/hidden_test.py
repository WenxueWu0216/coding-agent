
import pytest
from numstats import median

def test_odd(): assert median([3, 1, 2]) == 2
def test_even(): assert median([4, 1, 3, 2]) == 2.5
def test_floats(): assert median([1.5, 0.5]) == 1.0

def test_does_not_mutate_input():
    xs = [3, 1, 2]
    median(xs)
    assert xs == [3, 1, 2]

def test_empty():
    with pytest.raises(ValueError):
        median([])
