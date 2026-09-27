# shared\auth\session.py

from dataclasses import dataclass
from typing import Optional

@dataclass
class UserSession:
    user_id: int
    username: str = ""
    role: str = ""

_current: Optional[UserSession] = None

def set_session(sess: Optional[UserSession]) -> None:
    global _current
    _current = sess

def get_session() -> Optional[UserSession]:
    return _current

def require_user_id() -> int:
    s = get_session()
    uid = int(getattr(s, "user_id", 0) or 0) if s else 0
    if uid <= 0:
        raise ValueError("No active user session. Please login again.")
    return uid
