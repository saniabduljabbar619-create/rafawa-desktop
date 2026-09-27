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
