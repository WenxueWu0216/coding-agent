
def format_todo(todo):
    box = "[x]" if todo.done else "[ ]"
    text = f"{box} {todo.id}. {todo.title}"
    if todo.due is not None:
        text += f" (due {todo.due.isoformat()})"
    return text
