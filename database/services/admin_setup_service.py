from sqlalchemy import select

from database.session import get_session
from database.models.user import User
from database.models.role import Role
from shared.utils.password_hash import hash_password


class AdminSetupService:
    @staticmethod
    def create_first_admin(username: str, password: str) -> int:
        username = (username or "").strip()
        password = (password or "").strip()

        if len(username) < 3:
            raise ValueError("Username must be at least 3 characters.")
        if len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        with get_session() as session:
            # Only allowed if no users exist
            has_user = session.execute(select(User.id).limit(1)).first()
            if has_user:
                raise ValueError("Admin setup is already complete.")

            # Ensure roles exist
            admin_role = session.execute(select(Role).where(Role.name == "admin")).scalar_one_or_none()
            cashier_role = session.execute(select(Role).where(Role.name == "cashier")).scalar_one_or_none()

            if not admin_role:
                admin_role = Role(name="admin")
                session.add(admin_role)
            if not cashier_role:
                cashier_role = Role(name="cashier")
                session.add(cashier_role)

            session.flush()

            # Create first admin
            user = User(
                username=username,
                password_hash=hash_password(password),
                role_id=int(admin_role.id),
                is_active=True,
            )
            session.add(user)
            session.commit()
            return int(user.id)
