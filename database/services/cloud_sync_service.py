# database/services/cloud_sync_service.py
from __future__ import annotations

import logging
import traceback
from datetime import datetime
from typing import Dict, List, Optional, Type

import sqlalchemy as sa
from sqlalchemy import create_engine, MetaData, Table, Column
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine

from shared.config.settings import settings
from database.session import get_session
from database.models.sync_state import SyncState
from database.models.sync_log import SyncLog

log = logging.getLogger("cloud_sync")


def _sync_table_specs():
    """
    Deferred import - avoids any risk of circular imports at module load
    time, since this touches nearly every model in the app.

    Order matters for readability only, not correctness - the cloud
    mirror tables carry no foreign key constraints (see
    CloudSyncEngine's own docstring for why), so there's no dependency
    ordering requirement here the way there would be for a live,
    referentially-enforced database.
    """
    from database.models.party import Party
    from database.models.product import Product
    from database.models.sale import Sale
    from database.models.sale_item import SaleItem
    from database.models.purchase import Purchase
    from database.models.purchase_item import PurchaseItem
    from database.models.payment import Payment
    from database.models.advance import Advance
    from database.models.advance_allocations import AdvanceAllocation
    from database.models.cashier_settlement import CashierSettlement

    return [
        (Party, "parties"),
        (Product, "products"),
        (Sale, "sales"),
        (SaleItem, "sale_items"),
        (Purchase, "purchases"),
        (PurchaseItem, "purchase_items"),
        (Payment, "payments"),
        (Advance, "advances"),
        (AdvanceAllocation, "advance_allocations"),
        (CashierSettlement, "cashier_settlements"),
    ]


class CloudSyncEngine:
    """
    Stage 1 cloud sync, exactly as agreed: local MySQL stays primary and
    is NEVER blocked by this - every failure here is caught, logged, and
    swallowed, never raised back to whatever called sync. The cloud copy
    is a passive, periodic, one-way (local -> cloud) backup mirror for
    disaster recovery, not a live, multi-location system - see the
    architecture discussion this was scoped from.

    The cloud-side tables are deliberately schema-simplified mirrors:
    same columns, no foreign key constraints. This is a genuine, correct
    design choice for a DR backup, not a shortcut - the local database
    has already validated every relationship before a row ever reaches
    here, so re-enforcing referential integrity on the cloud copy only
    adds table-creation-order complexity for no real safety benefit.

    Change detection is a per-table updated_at cursor (SyncState) - each
    run only pulls rows genuinely new or changed since the last
    successful push, upserted idempotently (INSERT ... ON DUPLICATE KEY
    UPDATE) so a retried or overlapping batch is always safe to re-run.
    The cursor only advances after a full batch succeeds - a partial or
    failed batch leaves it untouched, so nothing is ever silently
    skipped, only retried.

    Target-agnostic by construction: settings.cloud_database_url decides
    where "the cloud" actually is. Empty means disabled. This is what
    lets the mechanism be built and proven now, in dev mode, against
    any reachable MySQL, then pointed at a real Aiven instance later
    with zero code changes.
    """

    _cloud_engine: Optional[Engine] = None
    _cloud_metadata: Optional[MetaData] = None
    _mirror_tables: Dict[str, Table] = {}

    # ------------------------------------------------------------------
    @staticmethod
    def is_configured() -> bool:
        return bool(settings.cloud_database_url) and settings.CLOUD_SYNC_ENABLED

    @classmethod
    def _get_cloud_engine(cls) -> Engine:
        if cls._cloud_engine is None:
            connect_args = {}
            if settings.cloud_ssl_ca:
                # PyMySQL's real SSL mechanism - a dict with "ca" pointing
                # at the certificate file, passed via connect_args, not a
                # URL query parameter (that name belongs to a different
                # driver entirely and silently breaks every connection).
                connect_args["ssl"] = {"ca": settings.cloud_ssl_ca}
                connect_args["connect_timeout"] = 8   # seconds - fail fast, don't hang the sync thread
            cls._cloud_engine = create_engine(
                settings.cloud_database_url,
                pool_pre_ping=True,   # detects a dead/dropped connection before using it, not after
                pool_recycle=280,     # stays under most managed MySQL providers' own idle-connection timeout
                connect_args=connect_args,
                future=True,
            )
        return cls._cloud_engine

    @classmethod
    def _get_mirror_table(cls, model_class: Type, table_name: str) -> Table:
        """
        Builds (and caches) a schema-simplified copy of the local
        model's table on the cloud side - same columns and types, no
        foreign key constraints, matching this engine's own documented
        design choice. Created on the cloud database on first use if it
        doesn't already exist there.
        """
        if table_name in cls._mirror_tables:
            return cls._mirror_tables[table_name]

        if cls._cloud_metadata is None:
            cls._cloud_metadata = MetaData()

        columns = []
        for col in model_class.__table__.columns:
            columns.append(Column(
                col.name, col.type,
                primary_key=col.primary_key,
                nullable=col.nullable if not col.primary_key else False,
                autoincrement=col.autoincrement if col.primary_key else False,
            ))

        mirror = Table(table_name, cls._cloud_metadata, *columns)
        mirror.create(bind=cls._get_cloud_engine(), checkfirst=True)
        cls._mirror_tables[table_name] = mirror
        return mirror

    # ------------------------------------------------------------------
    @classmethod
    def sync_all(cls, progress_callback=None) -> Dict:
        """
        Runs one sync pass across every configured table. Never raises -
        every failure is caught per-table, logged to SyncLog, and this
        moves on to the next table rather than letting one bad table
        block the rest.

        progress_callback(table_name, index, total, result), if given,
        is called once per table, right after that table's own sync
        attempt finishes - lets a UI-facing caller (the sync status bar's
        own background worker) observe progress without this engine
        needing to know anything about Qt or threads.
        """
        if not cls.is_configured():
            return {"ok": False, "reason": "not_configured", "results": []}

        specs = _sync_table_specs()
        results = []
        for i, (model_class, table_name) in enumerate(specs):
            result = cls.sync_table(model_class, table_name)
            results.append(result)
            if progress_callback:
                progress_callback(table_name, i + 1, len(specs), result)
        return {"ok": True, "results": results}

    @classmethod
    def sync_table(cls, model_class: Type, table_name: str) -> Dict:
        started_at = datetime.utcnow()
        try:
            cursor, cursor_row_id = cls._get_cursor(table_name)
            rows = cls._fetch_dirty_rows(model_class, cursor, cursor_row_id)

            if not rows:
                cls._write_log(table_name, started_at, 0, True, None)
                return {"table": table_name, "ok": True, "pushed": 0}

            mirror = cls._get_mirror_table(model_class, table_name)
            cls._upsert_rows(mirror, rows)

            new_cursor = max(r["updated_at"] for r in rows if r.get("updated_at") is not None)
            new_row_id = max(r["id"] for r in rows if r.get("id") is not None)
            cls._advance_cursor(table_name, new_cursor, new_row_id)
            cls._write_log(table_name, started_at, len(rows), True, None)

            return {"table": table_name, "ok": True, "pushed": len(rows)}

        except Exception as e:
            # Never propagate - a cloud sync failure must never be
            # visible to, or block, anything the operator is doing
            # locally. Logged in full for later diagnosis instead.
            err = f"{e}\n{traceback.format_exc()}"
            log.error("Cloud sync failed for table %s: %s", table_name, err)
            cls._write_log(table_name, started_at, 0, False, str(e))
            cls._write_cursor_failure(table_name, str(e))
            return {"table": table_name, "ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    @staticmethod
    def _get_cursor(table_name: str):
        with get_session() as s:
            state = s.get(SyncState, table_name)
            if state is None:
                return None, None
            return state.last_synced_cursor, state.last_synced_row_id

    @staticmethod
    def _fetch_dirty_rows(model_class: Type, cursor, cursor_row_id) -> List[Dict]:
        with get_session() as s:
            q = s.query(model_class)
            if cursor is not None:
                # Tie-break on id when updated_at is equal (e.g. a burst
                # of rows in the same second) - otherwise a row sharing
                # the exact cursor timestamp could be skipped forever.
                if cursor_row_id is not None:
                    q = q.filter(
                        sa.or_(
                            model_class.updated_at > cursor,
                            sa.and_(model_class.updated_at == cursor, model_class.id > cursor_row_id),
                        )
                    )
                else:
                    q = q.filter(model_class.updated_at > cursor)
            q = q.order_by(model_class.updated_at.asc(), model_class.id.asc())
            q = q.limit(settings.CLOUD_SYNC_BATCH_SIZE)

            out = []
            for row in q.all():
                d = {}
                for col in model_class.__table__.columns:
                    d[col.name] = getattr(row, col.name)
                out.append(d)
            return out

    @classmethod
    def _upsert_rows(cls, mirror: Table, rows: List[Dict]) -> None:
        engine = cls._get_cloud_engine()
        pk_cols = [c.name for c in mirror.primary_key.columns]
        update_cols = [c.name for c in mirror.columns if c.name not in pk_cols]

        with engine.begin() as conn:
            for row in rows:
                stmt = mysql_insert(mirror).values(**row)
                if update_cols:
                    stmt = stmt.on_duplicate_key_update(
                        **{c: stmt.inserted[c] for c in update_cols}
                    )
                conn.execute(stmt)

    @staticmethod
    def _advance_cursor(table_name: str, new_cursor, new_row_id: int) -> None:
        with get_session() as s:
            state = s.get(SyncState, table_name)
            if state is None:
                state = SyncState(table_name=table_name)
                s.add(state)
            state.last_synced_cursor = new_cursor
            state.last_synced_row_id = new_row_id
            state.last_run_at = datetime.utcnow()
            state.last_success = True
            state.last_error = None

    @staticmethod
    def _write_cursor_failure(table_name: str, error: str) -> None:
        with get_session() as s:
            state = s.get(SyncState, table_name)
            if state is None:
                state = SyncState(table_name=table_name)
                s.add(state)
            state.last_run_at = datetime.utcnow()
            state.last_success = False
            state.last_error = error[:1000]

    @staticmethod
    def _write_log(table_name: str, started_at: datetime, rows_pushed: int,
                   success: bool, error: Optional[str]) -> None:
        with get_session() as s:
            s.add(SyncLog(
                table_name=table_name, started_at=started_at, finished_at=datetime.utcnow(),
                rows_pushed=rows_pushed, success=success,
                error_message=(error[:1000] if error else None),
            ))

    # ------------------------------------------------------------------
    @classmethod
    def status_summary(cls) -> List[Dict]:
        """For a small admin-facing status view - not built yet, but
        this is the data it would read from."""
        with get_session() as s:
            states = s.query(SyncState).all()
            return [{
                "table_name": st.table_name,
                "last_synced_cursor": st.last_synced_cursor,
                "last_run_at": st.last_run_at,
                "last_success": st.last_success,
                "last_error": st.last_error,
            } for st in states]