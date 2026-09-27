from typing import Optional
from sqlalchemy import select

from database.session import get_session
from database.models.user import User
from database.models.role import Role
from shared.utils.password_hash import verify_password
from shared.auth.session import UserSession


class AuthService:
    @staticmethod
    def authenticate(username: str, password: str) -> Optional[UserSession]:
        username = (username or "").strip()
        password = (password or "").strip()
        if not username or not password:
            return None

        with get_session() as session:
            stmt = (
                select(User, Role)
                .join(Role, User.role_id == Role.id)
                .where(User.username == username)
            )
            row = session.execute(stmt).first()
            if not row:
                return None

            user, role = row[0], row[1]
            if not bool(user.is_active):
                return None

            if not verify_password(password, user.password_hash):
                return None

            return UserSession(
                user_id=int(user.id),
                username=str(user.username),
                role=str(getattr(role, "name", "") or ""),
            )
