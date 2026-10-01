"""
Test fixtures: every test gets a fresh SQLite database built from the real
SQLAlchemy models, so business logic (money, stock, ledgers) is exercised
for real without needing MySQL or a display.

DATABASE_URL must be set before any `database.*` module is imported (the
engine is created at import time), hence the module-level setup below.
"""
import os
import sys
import tempfile
from datetime import datetime

_TMP = tempfile.mkdtemp(prefix="rafawa-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ.pop("CLOUD_DATABASE_URL", None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import database.init_db  # noqa: E402,F401  (registers every model on Base)
from database.base import Base  # noqa: E402
from database.engine import engine  # noqa: E402
from database.session import get_session  # noqa: E402
from database.models.party import Party  # noqa: E402
from database.models.product import Product  # noqa: E402
from database.models.role import Role  # noqa: E402
from database.models.stock_movement import StockMovement  # noqa: E402
from database.models.user import User  # noqa: E402
from shared.config.constants import WALKIN_PARTY_ID  # noqa: E402

ADMIN_ID, CASHIER_A, CASHIER_B = 1, 2, 3
CUSTOMER_ID, SUPPLIER_ID = 1, 2
CROP_ID, FERT_ID = 1, 2


@pytest.fixture(autouse=True)
def login_as_admin():
    """Most tests exercise admin-capable code paths; security tests override this."""
    from shared.auth import throttle
    from shared.auth.session import UserSession, set_session
    throttle.reset()
    set_session(UserSession(user_id=ADMIN_ID, username="admin", role="admin"))
    yield
    set_session(None)


@pytest.fixture(autouse=True)
def db():
    """Fresh schema + a minimal world: 3 users, parties, 2 products with stock."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with get_session() as s:
        s.add(Role(id=1, name="admin"))
        s.flush()
        for uid, name in ((ADMIN_ID, "admin"), (CASHIER_A, "alice"), (CASHIER_B, "bob")):
            s.add(User(id=uid, username=name, password_hash="x", role_id=1))
        s.add(Party(id=CUSTOMER_ID, party_type="customer", display_name="Customer One",
                    is_customer=True))
        s.add(Party(id=SUPPLIER_ID, party_type="supplier", display_name="Supplier One",
                    is_supplier=True))
        # ids up to the shared walk-in bucket must exist for foreign keys
        for pid in range(3, WALKIN_PARTY_ID):
            s.add(Party(id=pid, party_type="customer", display_name=f"Filler {pid}"))
        s.add(Party(id=WALKIN_PARTY_ID, party_type="walkin", display_name="Walk-in"))
        s.add(Product(id=CROP_ID, name="Maize", product_type="crops", sku="CROP-MAIZE",
                      unit_label="kg", default_cost_rate=80, default_sale_rate=100))
        s.add(Product(id=FERT_ID, name="NPK", product_type="fertilizer", sku="FERT-NPK",
                      unit_label="bag", default_cost_rate=9000, default_sale_rate=10000))
        s.flush()
        # Opening stock: 20 bags / 2000 kg of maize, 50 bags of NPK
        s.add(StockMovement(product_id=CROP_ID, movement_type="purchase_in", qty_bags=20,
                            total_weight_kg=2000, unit_rate=80, reference_type="opening", reference_id=0, created_by_user_id=ADMIN_ID))
        s.add(StockMovement(product_id=FERT_ID, movement_type="purchase_in", qty_bags=50,
                            total_weight_kg=0, unit_rate=9000, reference_type="opening", reference_id=0, created_by_user_id=ADMIN_ID))
    yield


def stock(product_id: int):
    """(bags, kg) currently on hand for a product."""
    from database.models.stock_movement import STOCK_IN_TYPES, STOCK_OUT_TYPES
    with get_session() as s:
        bags = kg = 0.0
        for mv in s.query(StockMovement).filter(StockMovement.product_id == product_id):
            sign = 1 if mv.movement_type in STOCK_IN_TYPES else -1 if mv.movement_type in STOCK_OUT_TYPES else 0
            bags += sign * float(mv.qty_bags or 0)
            kg += sign * float(mv.total_weight_kg or 0)
        return bags, kg


def make_sale(items=None, buyer=CUSTOMER_ID, user=CASHIER_A, **extra):
    from database.services.sales_service import SaleService
    items = items or [{"product_id": CROP_ID, "qty_bags": 2, "total_weight_kg": 200, "unit_price": 100}]
    payload = {"buyer_party_id": buyer, "created_by_user_id": user, "items": items}
    payload.update(extra)
    return SaleService.record(payload)


def pay(entity_type, entity_id, party_id, direction, amount, user=CASHIER_A, method="cash", notes=""):
    from database.services.payment_service import PaymentService
    return PaymentService.create_payment(entity_type, entity_id, party_id, direction, amount,
                                         user, method=method, notes=notes)


@pytest.fixture
def now():
    return datetime.now()
