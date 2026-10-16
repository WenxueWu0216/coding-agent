
from pricing import compute_total


class Cart:
    def __init__(self):
        self.items = []

    def add(self, price, qty=1):
        self.items.append((price, qty))

    def total(self, tax_rate=0.0):
        return compute_total(self.items, tax_rate)
