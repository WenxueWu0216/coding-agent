
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest
from stack import Stack

def test_lifo():
    s = Stack()
    s.push(1); s.push(2)
    assert s.pop() == 2

def test_empty_pop():
    with pytest.raises(IndexError, match="empty"):
        Stack().pop()
