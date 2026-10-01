#database/services/user_admin_service.py
from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import select
from database.session import get_session
from shared.auth.permissions import require_admin
from database.models.user import User
from database.models.role import Role
from shared.utils.password_hash import hash_password


class UserAdminService:
    @staticmethod
    def list_users(
        role_name: Optional[str] = None,
        role_names: Optional[List[str]] = None,
    ) -> List[Tuple[int, str, str, bool, str]]:
        """
        Returns: (id, username, role_name, is_active, created_at_str)

        Pass role_name (e.g. "cashier") to restrict to a single role, or
        role_names (e.g. ["cashier", "admin"]) to restrict to several at
        once - e.g. the aggregated Business History screen, which lists
        every operator regardless of role. role_names takes precedence if
        both are given. Neither given -> every user, any role.
        """
        with get_session() as s:
            stmt = (
                select(User, Role)
                .join(Role, User.role_id == Role.id)
                .order_by(User.username.asc())
            )
            if role_names:
                wanted = [r.strip().lower() for r in role_names if r and r.strip()]
                if wanted:
                    stmt = stmt.where(Role.name.in_(wanted))
            elif role_name:
                stmt = stmt.where(Role.name == role_name.strip().lower())
            rows = s.execute(stmt).all()

        out: List[Tuple[int, str, str, bool, str]] = []
        for user, role in rows:
            created_at = getattr(user, "created_at", None)
            created_txt = created_at.strftime("%Y-%m-%d %H:%M") if created_at else ""
            out.append((
                int(user.id),
                str(user.username),
                str(getattr(role, "name", "") or ""),
                bool(user.is_active),
                created_txt,
            ))
        return out

    @staticmethod
    @require_admin
    def create_user(*, username: str, password: str, role_name: str) -> int:
        username = (username or "").strip()
        password = (password or "").strip()
        role_name = (role_name or "").strip().lower()

        if len(username) < 3:
            raise ValueError("Username must be at least 3 characters.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")
        if role_name not in ("admin", "cashier"):
            raise ValueError("role_name must be 'admin' or 'cashier'.")

        with get_session() as s:
            # username uniqueness
            exists = s.execute(select(User.id).where(User.username == username)).first()
            if exists:
                raise ValueError("Username already exists.")

            role = s.execute(select(Role).where(Role.name == role_name)).scalar_one_or_none()
            if not role:
                role = Role(name=role_name)
                s.add(role)
                s.flush()

            u = User(
                username=username,
                password_hash=hash_password(password),
                role_id=int(role.id),
                is_active=True,
            )
            s.add(u)
            s.commit()
            return int(u.id)

    @staticmethod
    @require_admin
    def set_active(user_id: int, is_active: bool) -> None:
        user_id = int(user_id or 0)
        if user_id <= 0:
            raise ValueError("user_id is required.")

        with get_session() as s:
            u = s.get(User, user_id)
            if not u:
                raise ValueError("User not found.")
            u.is_active = bool(is_active)
            s.commit()

    @staticmethod
    @require_admin
    def delete_user(user_id: int) -> None:
        """
        A TRUE delete - only allowed when the user has never created any
        record anywhere in the app. Eight tables carry created_by_user_id
        (advance, advance_allocations, payment, purchase, purchase_return,
        sale, sale_return, stock_movement) - some of these have a real
        foreign key to users.id and would fail loudly on their own, but
        advance/payment do not, meaning a raw delete could succeed while
        silently orphaning real financial records. This checks all eight
        proactively rather than relying on inconsistent FK enforcement.

        If ANY history exists, this raises rather than deletes - use
        set_active(user_id, False) instead, which preserves everything.
        """
        from database.models.advance import Advance
        from database.models.advance_allocations import AdvanceAllocation
        from database.models.payment import Payment
        from database.models.purchase import Purchase
        from database.models.purchase_return import PurchaseReturn
        from database.models.sale import Sale
        from database.models.sale_return import SaleReturn
        from database.models.stock_movement import StockMovement

        user_id = int(user_id or 0)
        if user_id <= 0:
            raise ValueError("user_id is required.")

        with get_session() as s:
            u = s.get(User, user_id)
            if not u:
                raise ValueError("User not found.")

            checks = [
                (Advance, "advance(s)"),
                (AdvanceAllocation, "advance allocation(s)"),
                (Payment, "payment(s)"),
                (Purchase, "purchase(s)"),
                (PurchaseReturn, "purchase return(s)"),
                (Sale, "sale(s)"),
                (SaleReturn, "sale return(s)"),
                (StockMovement, "stock movement(s)"),
            ]
            for model, label in checks:
                count = s.query(model).filter(
                    model.created_by_user_id == user_id
                ).count()
                if count > 0:
                    raise ValueError(
                        f"Cannot delete this user - they have created {count} "
                        f"{label} that would be orphaned. Use \"Toggle Active\" "
                        f"instead to remove them from active use while keeping "
                        f"the history intact."
                    )

            s.delete(u)
            s.commit()

    @staticmethod
    def reset_password(user_id: int, new_password: str) -> None:
        user_id = int(user_id or 0)
        new_password = (new_password or "").strip()
        if user_id <= 0:
            raise ValueError("user_id is required.")
        if len(new_password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        with get_session() as s:
            u = s.get(User, user_id)
            if not u:
                raise ValueError("User not found.")
            u.password_hash = hash_password(new_password)
            s.commit()

    @staticmethod
    def get_user_header(user_id: int) -> Tuple[int, str, str, bool]:
        """
        Returns: (id, username, role_name, is_active)
        """
        user_id = int(user_id or 0)
        if user_id <= 0:
            raise ValueError("user_id is required.")

        with get_session() as s:
            row = s.execute(
                select(User, Role)
                .join(Role, User.role_id == Role.id)
                .where(User.id == user_id)
            ).first()

        if not row:
            raise ValueError("User not found.")

        u, r = row[0], row[1]
        return int(u.id), str(u.username), str(getattr(r, "name", "") or ""), bool(u.is_active)