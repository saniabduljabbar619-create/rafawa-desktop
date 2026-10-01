# shared/utils/password_hash.py
import hashlib
import hmac
import os
from typing import Tuple

# PBKDF2-HMAC-SHA256 (no external deps). The iteration count is stored inside
# every hash, so it can be raised over time: old hashes keep verifying and are
# upgraded on the user's next successful login (see needs_rehash).
_ITER = 600_000

def _pbkdf2(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = _pbkdf2(password, salt)
    return f"pbkdf2_sha256${_ITER}${salt.hex()}${dk.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_hex, dk_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def needs_rehash(stored: str) -> bool:
    """True when this hash uses fewer iterations than the current standard."""
    try:
        algo, iters_s, _salt, _dk = stored.split("$", 3)
        return algo != "pbkdf2_sha256" or int(iters_s) < _ITER
    except Exception:
        return True
