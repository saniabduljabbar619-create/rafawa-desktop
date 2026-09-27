# database/__init__.py

from database.base import Base
from database.engine import engine

# Import ALL models here so SQLAlchemy sees them
from database.models import (
    user,
    role,
    permission,
    audit_log,
    cashier_sale,
    cashier_payment,
    party,
    category,
    product,
    purchase,
    purchase_item,
    purchase_item_bag,
    sale,
    sale_item,
    sale_item_bag,
    stock_movement,
    sale_return,
    SaleDiscount,
)


def init_database():
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully.")


if __name__ == "__main__":
    init_database()
