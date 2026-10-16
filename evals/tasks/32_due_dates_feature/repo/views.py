
def format_todo(todo):
    box = "[x]" if todo.done else "[ ]"
    return f"{box} {todo.id}. {todo.title}"
