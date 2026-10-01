"""
Is the database on the schema this version of the app expects?

Compares the database's Alembic revision with the newest revision shipped in
migrations/. The app calls this at startup and warns instead of failing
mysteriously later (e.g. a missing audit_logs table).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import sqlalchemy as sa

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class SchemaStatus:
    current: Optional[str]       # revision stored in the database (None = never migrated)
    head: Optional[str]          # newest revision in this build
    up_to_date: bool
    message: str


def head_revision() -> Optional[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_ROOT, "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def check_schema(engine=None) -> SchemaStatus:
    if engine is None:
        from database.engine import engine as _engine
        engine = _engine
    head = head_revision()
    with engine.connect() as conn:
        tables = set(sa.inspect(conn).get_table_names())
        current = None
        if "alembic_version" in tables:
            current = conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    if current == head:
        return SchemaStatus(current, head, True, "Database schema is up to date.")
    if current is None:
        msg = ("The database has not been migrated yet. Ask your administrator to run "
               "'alembic upgrade head' (after taking a backup).")
    else:
        msg = (f"The database schema is at revision {current} but this version of the app expects "
               f"{head}. Ask your administrator to run 'alembic upgrade head' (after taking a backup).")
    return SchemaStatus(current, head, False, msg)
