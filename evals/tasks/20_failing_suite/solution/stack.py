
class Stack:
    def __init__(self):
        self.items = []

    def push(self, x):
        self.items.append(x)

    def pop(self):
        if not self.items:
            raise IndexError("pop from empty stack")
        return self.items.pop()

    def peek(self):
        return self.items[-1]
