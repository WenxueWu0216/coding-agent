
from intervals import merge_intervals

def test_basic(): assert merge_intervals([[1, 3], [2, 6], [8, 10]]) == [[1, 6], [8, 10]]
def test_unsorted(): assert merge_intervals([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]
def test_touching(): assert merge_intervals([[1, 2], [2, 3]]) == [[1, 3]]
def test_nested(): assert merge_intervals([[1, 10], [2, 3]]) == [[1, 10]]
def test_empty(): assert merge_intervals([]) == []

def test_input_untouched():
    data = [[1, 4], [2, 5]]
    merge_intervals(data)
    assert data == [[1, 4], [2, 5]]
