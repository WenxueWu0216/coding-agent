
import pytest
from chunks import chunk
def test_even(): assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
def test_uneven(): assert chunk([1, 2, 3], 2) == [[1, 2], [3]]
def test_empty(): assert chunk([], 3) == []
def test_bad():
    with pytest.raises(ValueError):
        chunk([1], 0)
