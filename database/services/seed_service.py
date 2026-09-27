# database/services/seed_service.py
from sqlalchemy import select
from database.session import get_session
from database.models.role import Role


class SeedService:
    @staticmethod
    def ensure_bootstrap_data():
        """
        Ensures baseline roles exist. Safe to call on every start.
        Admin account is created via first-run setup UI.
        """
        with get_session() as session:
            existing = {r.name for r in session.execute(select(Role)).scalars().all()}

            changed = False
            if "admin" not in existing:
                session.add(Role(name="admin"))
                changed = True
            if "cashier" not in existing:
                session.add(Role(name="cashier"))
                changed = True

            if changed:
                session.commit()

    @staticmethod
    def has_any_user() -> bool:
        from sqlalchemy import select
        from database.models.user import User

        with get_session() as session:
            return bool(session.execute(select(User.id).limit(1)).first())
