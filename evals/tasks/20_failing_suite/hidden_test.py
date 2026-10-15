
import pytest
from stack import Stack
def test_lifo():
    s = Stack()
    for i in range(3):
        s.push(i)
    assert [s.pop(), s.pop(), s.pop()] == [2, 1, 0]
def test_empty():
    with pytest.raises(IndexError, match="empty"):
        Stack().pop()
def test_peek():
    s = Stack(); s.push(9)
    assert s.peek() == 9 and s.pop() == 9
