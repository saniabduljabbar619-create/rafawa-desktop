"""
One-off helper: convert timestamps the OLD code wrote in UTC to local time.

Background
----------
Until now many rows were stamped with datetime.utcnow() (payments, advances,
sale/purchase completed_at, order dispatch/confirm/cancel, returns, ...) while
other rows used the database's local NOW(). The app now writes local time
everywhere, so rows written BEFORE that change are off by your UTC offset.

Usage (dry run is the default - it only counts rows):

    python shift_utc_timestamps.py --hours 1 --before "2026-10-02 09:00:00"
    python shift_utc_timestamps.py --hours 1 --before "2026-10-02 09:00:00" --apply

--hours   your local offset from UTC (e.g. 1 for Nigeria / WAT, 0 = nothing to do)
--before  local date-time at which you deployed the new code; only rows older
          than this are shifted, so rows already written in local time are
          never touched. RUN THIS ONCE ONLY, and take a database backup first.

Only columns that the old code always wrote with utcnow() are shifted. Rows
that were already local (return refunds, cash-clearance payments) are skipped.
"""
import argparse

from sqlalchemy import text

from database.session import get_session

# (table, column, extra WHERE clause for rows the old code wrote in UTC)
TARGETS = [
    ("sales", "completed_at", ""),
    ("purchases", "completed_at", ""),
    ("advances", "created_at", ""),
    # refund and clearance payments used the DB default (already local)
    ("payments", "created_at",
     "AND COALESCE(method, '') <> 'refund' "
     "AND COALESCE(notes, '') NOT LIKE 'Cash balance clearance%'"),
    ("sale_returns", "created_at", ""),
    ("purchase_returns", "created_at", ""),
    ("sales_orders", "dispatched_at", ""),
    ("sales_orders", "confirmed_at", ""),
    ("sales_orders", "cancelled_at", ""),
    ("purchase_orders", "dispatched_at", ""),
    ("purchase_orders", "confirmed_at", ""),
    ("purchase_orders", "cancelled_at", ""),
    ("order_payments", "voided_at", ""),
    ("ledger_settlement_markers", "created_at", ""),
]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, required=True, help="local offset from UTC, in hours")
    ap.add_argument("--before", required=True, help="only shift rows older than this local datetime")
    ap.add_argument("--apply", action="store_true", help="actually update (default: dry run)")
    args = ap.parse_args()

    if args.hours == 0:
        print("Offset is 0 - nothing to do.")
        return

    with get_session() as s:
        for table, col, extra in TARGETS:
            where = f"{col} IS NOT NULL AND {col} < :cutoff {extra}"
            n = s.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"),
                          {"cutoff": args.before}).scalar()
            print(f"{table}.{col}: {n} row(s)")
            if args.apply and n:
                s.execute(
                    text(f"UPDATE {table} SET {col} = DATE_ADD({col}, INTERVAL :h HOUR) WHERE {where}"),
                    {"cutoff": args.before, "h": args.hours},
                )
        if not args.apply:
            print("\nDry run only - nothing changed. Re-run with --apply to update.")
            s.rollback()
        else:
            print("\nApplied.")


if __name__ == "__main__":
    main()
