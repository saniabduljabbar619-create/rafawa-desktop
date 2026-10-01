import pytest

from database.models.user import User
from database.services.auth_service import AuthService
from database.services.dashboard_service import delete_sale
from database.services.payment_service import PaymentService
from database.services.sales_service import SaleService
from database.services.user_admin_service import UserAdminService
from database.session import get_session
from shared.auth import throttle
from shared.auth.permissions import PermissionDenied, has_role, system_context
from shared.auth.session import UserSession, set_session
from shared.utils import password_hash as ph
from tests.conftest import ADMIN_ID, CASHIER_A, make_sale, pay


def _as(role):
    set_session(UserSession(user_id=CASHIER_A, username="alice", role=role))


# ------------------------------------------------------------ authorization
def test_cashier_cannot_void_delete_or_administer():
    res = make_sale()
    pid = pay("sale", res["sale_id"], 1, "in", 100)
    _as("cashier")
    with pytest.raises(PermissionDenied):
        PaymentService.void_payment(pid, CASHIER_A)
    with pytest.raises(PermissionDenied):
        SaleService.void_sale(res["sale_id"], CASHIER_A)
    with pytest.raises(PermissionDenied):
        delete_sale(res["sale_id"], CASHIER_A)
    with pytest.raises(PermissionDenied):
        UserAdminService.create_user(username="mallory", password="secret1", role_name="admin")


def test_no_session_is_denied_but_system_context_is_allowed():
    res = make_sale()
    set_session(None)
    with pytest.raises(PermissionDenied):
        SaleService.void_sale(res["sale_id"], ADMIN_ID)
    with system_context():
        assert SaleService.void_sale(res["sale_id"], ADMIN_ID)["ok"]
    assert not has_role("admin")


def test_admin_and_pm_roles_are_allowed():
    res = make_sale()
    _as("pm")
    assert SaleService.void_sale(res["sale_id"], CASHIER_A)["ok"]


def test_permission_denied_is_a_permission_error():
    assert issubclass(PermissionDenied, PermissionError)


# ------------------------------------------------------------ passwords
def test_hash_roundtrip_and_wrong_password():
    h = ph.hash_password("s3cret!")
    assert ph.verify_password("s3cret!", h) and not ph.verify_password("nope", h)
    assert not ph.needs_rehash(h)


def test_legacy_120k_hash_still_verifies_and_is_flagged_for_upgrade():
    import hashlib, os
    salt = os.urandom(16)
    legacy = "pbkdf2_sha256$120000$%s$%s" % (
        salt.hex(), hashlib.pbkdf2_hmac("sha256", b"oldpass", salt, 120_000).hex())
    assert ph.verify_password("oldpass", legacy)
    assert ph.needs_rehash(legacy)
    assert ph.needs_rehash("garbage")


def test_login_upgrades_a_legacy_hash():
    import hashlib, os
    salt = os.urandom(16)
    legacy = "pbkdf2_sha256$120000$%s$%s" % (
        salt.hex(), hashlib.pbkdf2_hmac("sha256", b"oldpass", salt, 120_000).hex())
    with get_session() as s:
        s.get(User, CASHIER_A).password_hash = legacy
    assert AuthService.authenticate("alice", "oldpass").username == "alice"
    with get_session() as s:
        assert not ph.needs_rehash(s.get(User, CASHIER_A).password_hash)
    assert AuthService.authenticate("alice", "oldpass") is not None   # still works


# ------------------------------------------------------------ login throttle
def _set_password(pw="correct-horse"):
    with get_session() as s:
        s.get(User, CASHIER_A).password_hash = ph.hash_password(pw)


def test_bad_credentials_return_none():
    _set_password()
    assert AuthService.authenticate("alice", "wrong") is None
    assert AuthService.authenticate("nobody", "whatever") is None


def test_inactive_user_cannot_log_in():
    _set_password()
    with get_session() as s:
        s.get(User, CASHIER_A).is_active = False
    assert AuthService.authenticate("alice", "correct-horse") is None


def test_repeated_failures_lock_the_account_even_for_the_right_password():
    _set_password()
    for _ in range(throttle.MAX_FREE_FAILURES):
        assert AuthService.authenticate("alice", "wrong") is None
    with pytest.raises(throttle.AccountLocked) as e:
        AuthService.authenticate("alice", "correct-horse")
    assert e.value.seconds > 0


def test_lock_expires_and_success_resets_the_counter():
    _set_password()
    t0 = 1_000_000.0
    for _ in range(throttle.MAX_FREE_FAILURES):
        throttle.record_failure("alice", now=t0)
    with pytest.raises(throttle.AccountLocked):
        throttle.check("alice", now=t0 + 1)
    throttle.check("alice", now=t0 + throttle.BASE_LOCK_SECONDS + 1)      # lock over
    throttle.reset()
    assert AuthService.authenticate("alice", "correct-horse") is not None
    assert AuthService.authenticate("alice", "wrong") is None             # counter restarted


def test_lock_delay_grows_but_is_capped():
    for _ in range(40):
        throttle.record_failure("bob", now=0)
    with pytest.raises(throttle.AccountLocked) as e:
        throttle.check("bob", now=1)
    assert e.value.seconds <= throttle.MAX_LOCK_SECONDS
