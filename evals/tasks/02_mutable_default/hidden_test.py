
from tags import add_tag
def test_independent():
    assert add_tag("a") == ["a"]
    assert add_tag("b") == ["b"]
def test_existing(): assert add_tag("c", ["x"]) == ["x", "c"]
