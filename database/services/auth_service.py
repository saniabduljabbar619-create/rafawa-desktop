from typing import Optional
from sqlalchemy import select

from database.session import get_session
from database.models.user import User
from database.models.role import Role
import logging
from functools import lru_cache

from shared.utils.password_hash import verify_password, hash_password, needs_rehash
from shared.auth.session import UserSession
from shared.auth import throttle

_log = logging.getLogger("rafawa.security")


class AuthService:
    @staticmethod
    def authenticate(username: str, password: str) -> Optional[UserSession]:
        """
        Returns the session on success, None on bad credentials.
        Raises throttle.AccountLocked after repeated failures for a username.
        """
        username = (username or "").strip()
        password = (password or "").strip()
        if not username or not password:
            return None

        throttle.check(username)

        with get_session() as session:
            stmt = (
                select(User, Role)
                .join(Role, User.role_id == Role.id)
                .where(User.username == username)
            )
            row = session.execute(stmt).first()

            user, role = (row[0], row[1]) if row else (None, None)
            # Verify even for unknown/inactive users so response time does not
            # reveal which usernames exist.
            stored = user.password_hash if user else _dummy_hash()
            ok = verify_password(password, stored) and user is not None and bool(user.is_active)

            if not ok:
                throttle.record_failure(username)
                _log.warning("LOGIN FAILED user=%r", username)
                return None

            throttle.record_success(username)
            if needs_rehash(user.password_hash):
                user.password_hash = hash_password(password)   # committed on exit
            _log.info("LOGIN OK user=%r role=%r", username, getattr(role, "name", ""))

            return UserSession(
                user_id=int(user.id),
                username=str(user.username),
                role=str(getattr(role, "name", "") or ""),
            )


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A valid hash of a random password, so a lookup miss costs the same as a hit."""
    return hash_password("not-a-real-password-" + "x" * 8)
