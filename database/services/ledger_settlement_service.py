# database/services/ledger_settlement_service.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from database.session import get_session
from database.models.ledger_settlement_marker import LedgerSettlementMarker


class LedgerSettlementService:
    """
    Checked right after a payment commits (Sale payment, Purchase
    payment, or Advance settlement) - records a real, persisted marker
    for any ledger category that genuinely just settled to zero. See
    LedgerSettlementMarker's own docstring for why this is a real event,
    not something recomputed by scanning history each time the Activity
    tab opens.

    Four possible categories get checked: payables, receivables,
    advances, and "all" (the more prominent marker for when every
    category is genuinely zero at once). Deduplicated so a category
    that's already marked and nothing has unsettled it since doesn't
    get a redundant marker on every subsequent payment.
    """

    ZERO_EPS = 0.005

    @staticmethod
    def _most_recent_activity_at(party_id: int):
        """The timestamp of the most recent real activity (sale,
        purchase, advance, return, or payment) for this party - used to
        decide whether an existing marker is still current, or whether
        something has happened since that could have unsettled it."""
        from database.services.party_ledger_service import PartyLedgerService
        acts = PartyLedgerService.list_activity_details(party_id=party_id, limit=1)
        return acts[0]["dt"] if acts else None

    @staticmethod
    def check_and_record(party_id: int, created_by_user_id: int) -> List[Dict]:
        party_id = int(party_id or 0)
        created_by_user_id = int(created_by_user_id or 0)
        if party_id <= 0 or created_by_user_id <= 0:
            return []

        from database.services.party_balance_service import PartyBalanceService
        bal = PartyBalanceService.compute(party_id, created_by_user_id, "admin")
        overall = bal.get("overall", {})

        payables_zero = float(overall.get("payable", 0) or 0) <= LedgerSettlementService.ZERO_EPS
        receivables_zero = float(overall.get("receivable", 0) or 0) <= LedgerSettlementService.ZERO_EPS
        advances_zero = (
            float(overall.get("advance_in", 0) or 0) <= LedgerSettlementService.ZERO_EPS
            and float(overall.get("advance_out", 0) or 0) <= LedgerSettlementService.ZERO_EPS
        )

        most_recent_activity = LedgerSettlementService._most_recent_activity_at(party_id)
        created: List[Dict] = []

        with get_session() as s:
            def _maybe_mark(category: str, is_zero: bool):
                if not is_zero:
                    return
                last_marker = (
                    s.query(LedgerSettlementMarker)
                    .filter(LedgerSettlementMarker.party_id == party_id)
                    .filter(LedgerSettlementMarker.category == category)
                    .order_by(LedgerSettlementMarker.created_at.desc())
                    .first()
                )
                # Already marked, and nothing real has happened since -
                # still current, don't duplicate.
                if last_marker and most_recent_activity and last_marker.created_at >= most_recent_activity:
                    return

                marker = LedgerSettlementMarker(
                    party_id=party_id, category=category,
                    created_by_user_id=created_by_user_id,
                    created_at=datetime.utcnow(),
                )
                s.add(marker)
                s.flush()
                created.append({"category": category, "id": int(marker.id)})

            _maybe_mark("payables", payables_zero)
            _maybe_mark("receivables", receivables_zero)
            _maybe_mark("advances", advances_zero)
            _maybe_mark("all", payables_zero and receivables_zero and advances_zero)

        return created