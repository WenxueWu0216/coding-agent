
def paginate(items, page, per_page):
    """Return the items on a 1-indexed page.

    Raises ValueError if page < 1 or per_page < 1.
    A page past the end returns an empty list.
    """
    start = page * per_page
    return items[start:start + per_page]


def total_pages(n_items, per_page):
    """Number of pages needed to show n_items. 0 items -> 0 pages."""
    return n_items // per_page
