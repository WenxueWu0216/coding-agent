
from search import binary_search
def test_every_element():
    xs = [1, 3, 5, 7, 9, 11]
    assert all(binary_search(xs, x) == i for i, x in enumerate(xs))
def test_missing(): assert binary_search([1, 3, 5], 4) == -1
def test_single(): assert binary_search([7], 7) == 0
