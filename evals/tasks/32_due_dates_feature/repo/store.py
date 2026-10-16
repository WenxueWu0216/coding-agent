
from models import Todo


class TodoStore:
    def __init__(self):
        self._todos = []
        self._next_id = 1

    def add(self, title):
        todo = Todo(self._next_id, title)
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
