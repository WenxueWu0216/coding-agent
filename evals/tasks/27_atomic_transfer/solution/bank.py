
class Account:
    def __init__(self, owner, balance=0):
        self.owner = owner
        self.balance = balance

    @staticmethod
    def _check(amount):
        if amount <= 0:
            raise ValueError("amount must be positive")

    def deposit(self, amount):
        self._check(amount)
        self.balance += amount

    def withdraw(self, amount):
        self._check(amount)
        if amount > self.balance:
            raise ValueError("insufficient funds")
        self.balance -= amount

    def transfer(self, other, amount):
        self.withdraw(amount)
        other.deposit(amount)
