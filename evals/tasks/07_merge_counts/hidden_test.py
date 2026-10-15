
from counts import merge_counts
def test_merge(): assert merge_counts({"a": 1, "b": 2}, {"b": 3, "c": 4}) == {"a": 1, "b": 5, "c": 4}
def test_no_mutation():
    a = {"x": 1}
    merge_counts(a, {"x": 1})
    assert a == {"x": 1}
