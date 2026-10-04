# database/services/walkin_account.py
"""
Is this party id the SHARED walk-in account?

The app reserves party id WALKIN_PARTY_ID (11) for the anonymous bucket that every casual walk-in sale and
purchase is recorded under (told apart only by `walkin_name`). The id is a fixed number, but parties are
numbered by the database as they are created, so a real customer or supplier can end up with that very id
(it happened: a registered customer got id 11). Comparing ids alone then treats a real account as the
anonymous bucket - swaps and set-offs were refused for it, and its documents were flagged as walk-ins.

So the id only counts when the row behind it really is the walk-in account (party_type "walkin"), or when
no such row exists yet (the legacy case). A registered customer/supplier/company with that id is a real party.
"""
from __future__ import annotations

from database.models.party import Party
from database.session import get_session
from shared.config.constants import WALKIN_PARTY_ID


def is_shared_walkin(party_id, s=None) -> bool:
    try:
        pid = int(party_id)
    except (TypeError, ValueError):
        return False
    if pid != WALKIN_PARTY_ID:
        return False

    def _check(sess) -> bool:
        row = sess.query(Party.party_type).filter(Party.id == pid).first()
        return row is None or str(row[0] or "").lower() == "walkin"

    if s is not None:
        return _check(s)
    with get_session() as sess:
        return _check(sess)
