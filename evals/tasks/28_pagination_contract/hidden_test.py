
import pytest
from pages import paginate, total_pages

ITEMS = list(range(1, 11))

def test_first_page(): assert paginate(ITEMS, 1, 3) == [1, 2, 3]
def test_last_partial(): assert paginate(ITEMS, 4, 3) == [10]
def test_past_end(): assert paginate(ITEMS, 5, 3) == []
def test_total(): assert [total_pages(n, 3) for n in (0, 1, 3, 10)] == [0, 1, 1, 4]

@pytest.mark.parametrize("page,per", [(0, 3), (-1, 3), (1, 0)])
def test_bad_args(page, per):
    with pytest.raises(ValueError):
        paginate(ITEMS, page, per)
