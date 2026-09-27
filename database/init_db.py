#database/init_db.py
from database.base import Base
from database.engine import engine

# Ensure models are imported so metadata is complete
from database.models import (
    Role, User, Party,
    Category, Product,
    Purchase, PurchaseItem, PurchaseItemBag,
    Sale, SaleItem, SaleItemBag,
    StockMovement,
    Advance, SaleDiscount


)
from database.models.sale_return import SaleReturn
from database.models import purchase_return
from database.models import advance_allocations
from database.models import cashier_settlement



def init_database():
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully.")


if __name__ == "__main__":
    init_database()
