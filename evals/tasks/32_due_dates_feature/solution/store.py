
from models import Todo


class TodoStore:
    def __init__(self):
        self._todos = []
        self._next_id = 1

    def add(self, title, due=None):
        todo = Todo(self._next_id, title, due=due)
        self._next_id += 1
        self._todos.append(todo)
        return todo

    def complete(self, todo_id):
        for t in self._todos:
            if t.id == todo_id:
                t.done = True
                return t
        raise KeyError(todo_id)

    def all(self):
        return list(self._todos)

    def overdue(self, today):
        late = [t for t in self._todos if not t.done and t.due is not None and t.due < today]
        return sorted(late, key=lambda t: t.due)
