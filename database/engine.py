# database/engine.py

from sqlalchemy import create_engine
from shared.config.settings import settings

engine = create_engine(
    settings.database_url,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # auto-reconnect if MySQL drops
    future=True,         # SQLAlchemy 2.0 behavior
)


# MySQL's CONCAT() is used in a few raw queries. SQLite only has it from 3.44 on, and the Python that ships
# with Windows is older, so give SQLite connections (tests, local use) the same function.
if engine.dialect.name == "sqlite":
    from sqlalchemy import event

    def _concat(*parts):
        return None if any(p is None for p in parts) else "".join(str(p) for p in parts)

    @event.listens_for(engine, "connect")
    def _sqlite_concat(dbapi_connection, _record):
        dbapi_connection.create_function("CONCAT", -1, _concat)
