# database/services/audit_service.py
"""
Audit trail. ``AuditService.record(...)`` is called from the services that
change money, stock visibility or user access.

The actor is taken from the logged-in session. The write happens in the
CALLER's transaction when a session is passed (so the audit row commits or
rolls back together with the change it describes); otherwise it is written on
its own. A failure to audit is logged loudly but never blocks the business
operation - important while a database has not yet been migrated to include
the ``audit_logs`` table.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from database.models.audit_log import AuditLog
from database.session import get_session
from shared.auth.session import get_session as get_login_session

_log = logging.getLogger("rafawa.audit")


class AuditService:

    @staticmethod
    def record(action: str, entity_type: Optional[str] = None, entity_id: Optional[int] = None,
               summary: str = "", details: Optional[Dict[str, Any]] = None, session=None) -> None:
        who = get_login_session()
        row = AuditLog(
            user_id=int(who.user_id) if who and who.user_id else None,
            username=(who.username if who else None),
            role=(who.role if who else None),
            action=action,
            entity_type=entity_type,
            entity_id=int(entity_id) if entity_id is not None else None,
            summary=(summary or "")[:255] or None,
            details=json.dumps(details, default=str) if details else None,
        )
        _log.info("%s %s#%s by %s: %s", action, entity_type, entity_id,
                  getattr(who, "username", "system"), summary)
        try:
            if session is not None:
                # SAVEPOINT: a missing/broken audit table must not poison the caller's transaction
                with session.begin_nested():
                    session.add(row)
            else:
                with get_session() as s:
                    s.add(row)
        except Exception:
            _log.exception("AUDIT WRITE FAILED for %s %s#%s", action, entity_type, entity_id)

    @staticmethod
    def list_recent(limit: int = 200, action: Optional[str] = None,
                    entity_type: Optional[str] = None, entity_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with get_session() as s:
            q = s.query(AuditLog)
            if action:
                q = q.filter(AuditLog.action == action)
            if entity_type:
                q = q.filter(AuditLog.entity_type == entity_type)
            if entity_id is not None:
                q = q.filter(AuditLog.entity_id == int(entity_id))
            rows = q.order_by(AuditLog.id.desc()).limit(max(1, min(int(limit), 2000))).all()
            return [{
                "id": r.id, "when": r.created_at, "user_id": r.user_id, "username": r.username,
                "role": r.role, "action": r.action, "entity_type": r.entity_type,
                "entity_id": r.entity_id, "summary": r.summary,
                "details": json.loads(r.details) if r.details else None,
            } for r in rows]
