
from datetime import date
from models import Todo
from store import TodoStore
from views import format_todo

def test_default_due():
    assert Todo(1, "x").due is None

def test_overdue():
    s = TodoStore()
    a = s.add("a", due=date(2026, 10, 5))
    s.add("b", due=date(2026, 10, 20))
    c = s.add("c", due=date(2026, 10, 1))
    s.add("d")
    e = s.add("e", due=date(2026, 9, 1))
    s.complete(e.id)
    assert [t.title for t in s.overdue(date(2026, 10, 15))] == ["c", "a"]

def test_due_today_not_overdue():
    s = TodoStore()
    s.add("x", due=date(2026, 10, 15))
    assert s.overdue(date(2026, 10, 15)) == []

def test_format():
    assert format_todo(Todo(3, "Pay rent", due=date(2026, 11, 1))) == "[ ] 3. Pay rent (due 2026-11-01)"
    assert format_todo(Todo(4, "Call mom", done=True)) == "[x] 4. Call mom"
