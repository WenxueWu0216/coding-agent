from listutil import dedupe


def test_order_and_tuple():
    assert dedupe([3, 1, 3, 2, 1]) == (3, 1, 2)


def test_no_mutation():
    xs = [1, 1, 2]
    dedupe(xs)
    assert xs == [1, 1, 2]


def test_empty():
    assert dedupe([]) == ()
