
from listops import last_n
def test_basic(): assert last_n([1, 2, 3, 4], 2) == [3, 4]
def test_zero(): assert last_n([1, 2, 3], 0) == []
def test_all(): assert last_n([1, 2], 2) == [1, 2]
