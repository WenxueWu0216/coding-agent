
from slug import slugify

def test_spec():
    assert slugify("Crème Brûlée") == "creme-brulee"
    assert slugify("  Rock & Roll!!  ") == "rock-roll"
    assert slugify("a -- b") == "a-b"

def test_more():
    assert slugify("Ça va?") == "ca-va"
    assert slugify("Top 10 Tips") == "top-10-tips"
    assert slugify("---") == ""
