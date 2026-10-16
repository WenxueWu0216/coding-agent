
# Percent off, stored as whole numbers (15 means 15%).
CODES = {"SAVE15": 15, "HALF": 50}


def apply_discount(amount, code):
    if code is None or code not in CODES:
        return amount
    pct = CODES[code]
    return amount * (1 - pct / 100)
