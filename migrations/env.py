"""Alembic environment: uses the application's own engine and model metadata."""
from logging.config import fileConfig

from alembic import context

import database.init_db  # noqa: F401  (imports the core models)
from database import models as _models  # noqa: F401
from database.base import Base
from database.engine import engine

# make sure every model module is registered on Base.metadata.
# legacy_record is skipped: it is unused and its foreign key points at a table
# ("party") that does not exist, which would make the metadata unusable.
import importlib
import pkgutil
import database.models as _pkg
_SKIP = {"legacy_record"}
for _m in pkgutil.iter_modules(_pkg.__path__):
    if _m.name not in _SKIP:
        importlib.import_module(f"database.models.{_m.name}")

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=str(engine.url), target_metadata=target_metadata,
                      literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, render_as_batch=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
