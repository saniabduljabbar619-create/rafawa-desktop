# shared/config/settings.py
import os
from dataclasses import dataclass

def _dotenv_candidates():
    """
    Where to look for .env, in priority order. The old code only checked the
    current working directory, so a packaged exe started from a shortcut or
    another folder silently ran with no database settings at all.
    """
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(here))
    dirs = [os.getcwd()]
    if getattr(sys, "frozen", False):            # PyInstaller build
        dirs.append(os.path.dirname(sys.executable))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
    dirs.append(project_root)
    seen = set()
    for d in dirs:
        p = os.path.join(d, ".env")
        if p not in seen:
            seen.add(p)
            yield p


def _load_dotenv(path: str = "") -> None:
    # minimal .env loader (avoids extra dependency)
    path = path or next((p for p in _dotenv_candidates() if os.path.exists(p)), "")
    if not path:
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

_load_dotenv()

# DB settings required by database/engine.py
_db_url = os.getenv("DATABASE_URL", "").strip()

if not _db_url:
    host = os.getenv("DB_HOST", "127.0.0.1").strip()
    port = os.getenv("DB_PORT", "3306").strip()
    name = os.getenv("DB_NAME", "").strip()
    user = os.getenv("DB_USER", "").strip()
    password = os.getenv("DB_PASSWORD", "").strip()  # should already be URL-encoded
    charset = os.getenv("DB_CHARSET", "utf8mb4").strip()

    if all([host, port, name, user, password]):
        _db_url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}?charset={charset}"

# Cloud sync target - deliberately separate from the local DB above.
# Local MySQL stays primary always; this is only ever a one-way,
# periodic backup destination (Stage 1 - see the architecture agreed
# with the client). Empty means sync is disabled - the mechanism
# doesn't care WHY it's empty, whether that's dev mode with no cloud
# instance provisioned yet, or a real Aiven instance simply not
# configured on this particular machine.
_cloud_db_url = os.getenv("CLOUD_DATABASE_URL", "").strip()

if not _cloud_db_url:
    cloud_host = os.getenv("CLOUD_DB_HOST", "").strip()
    cloud_port = os.getenv("CLOUD_DB_PORT", "3306").strip()
    cloud_name = os.getenv("CLOUD_DB_NAME", "").strip()
    cloud_user = os.getenv("CLOUD_DB_USER", "").strip()
    cloud_password = os.getenv("CLOUD_DB_PASSWORD", "").strip()
    cloud_charset = os.getenv("CLOUD_DB_CHARSET", "utf8mb4").strip()

    if all([cloud_host, cloud_port, cloud_name, cloud_user, cloud_password]):
        _cloud_db_url = (
            f"mysql+pymysql://{cloud_user}:{cloud_password}@{cloud_host}:{cloud_port}"
            f"/{cloud_name}?charset={cloud_charset}"
        )

# SSL - a real CA certificate path, since that's the correct, PyMySQL-
# native way to require SSL (Aiven and most managed providers give you
# a downloadable ca.pem). Deliberately NOT a query-string "ssl_mode"
# param - that's a MySQL Connector/Python name, not a PyMySQL one, and
# passing it silently breaks every connection attempt. Empty means no
# SSL - correct for a local/dev target that has none configured.
_cloud_ssl_ca = os.getenv("CLOUD_DB_SSL_CA", "").strip()

@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Rafawa Desktop V1")

    # existing UIX settings
    api_base_url: str = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
    request_timeout_s: int = int(os.getenv("REQUEST_TIMEOUT_S", "15"))

    # DB fields used by database/engine.py
    database_url: str = _db_url
    DB_ECHO: bool = os.getenv("DB_ECHO", "0").lower() in ("1", "true", "yes")
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))

    # Cloud sync (Stage 1 - one-way, periodic, disaster-recovery backup;
    # local MySQL stays primary and never blocks on this)
    cloud_database_url: str = _cloud_db_url
    cloud_ssl_ca: str = _cloud_ssl_ca
    CLOUD_SYNC_ENABLED: bool = os.getenv("CLOUD_SYNC_ENABLED", "1" if _cloud_db_url else "0").lower() in ("1", "true", "yes")
    CLOUD_SYNC_INTERVAL_SECONDS: int = int(os.getenv("CLOUD_SYNC_INTERVAL_SECONDS", "90"))
    CLOUD_SYNC_BATCH_SIZE: int = int(os.getenv("CLOUD_SYNC_BATCH_SIZE", "500"))

settings = Settings()