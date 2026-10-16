
from inventory.discounts import apply_discount
from inventory.storage import get_product
from inventory.utils import round_money


def line_total(line):
    return get_product(line.sku).price * line.qty


def order_total(lines, code=None):
    subtotal = sum(line_total(l) for l in lines)
    return round_money(apply_discount(subtotal, code))
