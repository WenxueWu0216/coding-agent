
from inventory.models import Product

CATALOG = {
    "A1": Product("A1", "Notebook", 4.00),
    "B2": Product("B2", "Pen", 1.50),
    "C3": Product("C3", "Backpack", 40.00),
}


def get_product(sku):
    return CATALOG[sku]
