from database.models.audit_log import AuditLog
from database.services.audit_service import AuditService
from database.services.dashboard_service import delete_sale
from database.services.drawer_service import DrawerService
from database.services.payment_service import PaymentService
from database.services.return_service import ReturnService
from database.services.sales_service import SaleService
from database.services.user_admin_service import UserAdminService
from database.session import get_session
from shared.auth.session import UserSession, set_session
from tests.conftest import ADMIN_ID, CASHIER_A, CASHIER_B, CUSTOMER_ID, make_sale, pay


def _actions():
    return [r["action"] for r in AuditService.list_recent(100)][::-1]


def test_void_payment_is_audited_with_actor_and_details():
    res = make_sale()
    pid = pay("sale", res["sale_id"], CUSTOMER_ID, "in", 1500)
    PaymentService.void_payment(pid, ADMIN_ID, "wrong amount")
    row = AuditService.list_recent(1)[0]
    assert (row["action"], row["entity_type"], row["entity_id"]) == ("payment.void", "payment", pid)
    assert (row["username"], row["role"], row["user_id"]) == ("admin", "admin", ADMIN_ID)
    assert row["details"]["reason"] == "wrong amount" and row["details"]["amount"] == 1500.0


def test_sale_void_delete_and_user_admin_are_audited():
    a = make_sale()
    SaleService.void_sale(a["sale_id"], ADMIN_ID, "typo")
    b = make_sale()
    delete_sale(b["sale_id"], ADMIN_ID, "cleanup")
    uid = UserAdminService.create_user(username="carol", password="secret1", role_name="cashier")
    UserAdminService.set_active(uid, False)
    UserAdminService.reset_password(uid, "another1")
    assert _actions() == ["sale.void", "sale.delete", "user.create", "user.disable", "user.password_reset"]
    # the delete row keeps a snapshot of what disappeared
    assert AuditService.list_recent(entity_type="sale", entity_id=b["sale_id"], action="sale.delete")[0][
        "details"]["snapshot"]["total_amount"] == 20000.0


def test_audit_rows_commit_and_roll_back_with_the_operation():
    res = make_sale()
    pid = pay("sale", res["sale_id"], CUSTOMER_ID, "in", 100)
    PaymentService.void_payment(pid, ADMIN_ID)
    assert _actions() == ["payment.void"]
    PaymentService.void_payment(pid, ADMIN_ID)             # idempotent no-op -> no second audit row
    assert _actions() == ["payment.void"]


def test_settlements_and_refunds_are_audited():
    res = make_sale(user=CASHIER_A)
    pay("sale", res["sale_id"], CUSTOMER_ID, "in", 4000, user=CASHIER_B)
    DrawerService.compute(CASHIER_A)
    DrawerService.settle_with(CASHIER_A, CASHIER_B, ADMIN_ID, note="paid in cash")
    rid = ReturnService.create_return(document_type="sale", document_id=res["sale_id"],
                                      item_id=res["sale_item_ids"][0], qty_kg=100, qty_bags=1,
                                      reason="x", created_by_user_id=CASHIER_A)
    ReturnService.approve_sale_return(rid, CASHIER_A)
    assert "drawer.settle_with" in _actions() and "return.refund" in _actions()


def test_a_broken_audit_table_never_blocks_the_business_operation():
    res = make_sale()
    with get_session() as s:
        s.execute(AuditLog.__table__.delete())
    AuditLog.__table__.drop(bind=__import__("database.engine", fromlist=["engine"]).engine)
    try:
        assert SaleService.void_sale(res["sale_id"], ADMIN_ID)["ok"]       # still works
    finally:
        AuditLog.__table__.create(bind=__import__("database.engine", fromlist=["engine"]).engine)


def test_cashier_actor_is_recorded():
    set_session(UserSession(user_id=CASHIER_A, username="alice", role="cashier"))
    AuditService.record("custom.event", "thing", 1, "hello")
    assert AuditService.list_recent(1)[0]["username"] == "alice"
