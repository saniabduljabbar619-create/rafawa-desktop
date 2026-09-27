# verify_drawer.py  (repo root)
# -*- coding: utf-8 -*-
"""
Phase 1 verification for the drawer/accountability engine.

Usage (from the project root, venv active):
    python verify_drawer.py <user_id>
    python verify_drawer.py <user_id> 2026-07-01 2026-07-13   # optional range
"""

import sys
from datetime import date


def _parse_date(s):
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def main():
    if len(sys.argv) < 2:
        print("usage: python verify_drawer.py <user_id> [start YYYY-MM-DD] [end YYYY-MM-DD]")
        return

    user_id = int(sys.argv[1])
    start = _parse_date(sys.argv[2]) if len(sys.argv) > 2 else None
    end = _parse_date(sys.argv[3]) if len(sys.argv) > 3 else None

    from database.services.drawer_service import DrawerService
    r = DrawerService.compute(user_id, start, end)

    c = r["cards"]
    print("=" * 62)
    print(f"DRAWER REPORT  -  {r['user']}  (user {user_id})")
    if start or end:
        print(f"Range: {start or '...'} -> {end or '...'}")
    print("=" * 62)
    print(f"{'Total Paid Sales':28}: {c['total_paid_sales']:>14,.2f}")
    print(f"{'Total Paid Purchase':28}: {c['total_paid_purchase']:>14,.2f}")
    print(f"{'Total Paid Advance IN':28}: {c['total_paid_advance_in']:>14,.2f}")
    print(f"{'Total Paid Advance OUT':28}: {c['total_paid_advance_out']:>14,.2f}")
    print("-" * 62)
    print(f"{'Sales (net)':28}: {c['sales_net']:>14,.2f}")
    print(f"{'Purchases (net)':28}: {c['purchases_net']:>14,.2f}")
    print(f"{'Advance IN (net)':28}: {c['advance_in_net']:>14,.2f}")
    print(f"{'Advance OUT (net)':28}: {c['advance_out_net']:>14,.2f}")
    print("-" * 62)
    print(f"{'Aggregated Balance IN':28}: {c['aggregated_in']:>14,.2f}")
    print(f"{'Aggregated Balance OUT':28}: {c['aggregated_out']:>14,.2f}")
    print(f"{'Others owe me (unsettled)':28}: {c['owed_to_me_total']:>14,.2f}")
    print(f"{'I owe others (unsettled)':28}: {c['i_owe_total']:>14,.2f}")
    print(f"{'NET POSITION':28}: {c['net_position']:>14,.2f}")

    h = r.get("human", {})
    if h:
        print("\n" + "=" * 62)
        print("PLAIN-LANGUAGE VIEW (what the operator sees)")
        print("=" * 62)
        print(f"  Money you collected : {h['money_collected']:>14,.2f}")
        print(f"  Money you paid out  : {h['money_paid_out']:>14,.2f}")
        print(f"  Cash handled (net)  : {h['cash_handled_net']:>14,.2f}")
        print(f"\n  STATUS: {h['status_en']}   /   {h['status_ha']}")
        for v in h.get("verdicts", []):
            print(f"    - {v['en']}    ({v['ha']})")

    cs = r["cross_settlements"]
    if cs["owed_to_me"]:
        print("\n--- Balance other cashiers must pay me ---")
        for b in cs["owed_to_me"]:
            print(f"  {b['other_user']:20} {b['amount']:>14,.2f}")
    if cs["i_owe"]:
        print("\n--- Balance I must pay other cashiers ---")
        for b in cs["i_owe"]:
            print(f"  {b['other_user']:20} {b['amount']:>14,.2f}")

    if cs["lines"]:
        print("\n--- Cross-cashier settlement lines ---")
        for ln in cs["lines"]:
            when = str(ln["when"])[:16]
            print(f"  [{when}] {ln['entity_type']:8} {ln['doc_no']:10} "
                  f"{ln['amount']:>12,.2f}  {ln['party'][:18]:18} "
                  f"created:{ln['creator'][:10]:10} settled:{ln['settler'][:10]:10} "
                  f"-> {ln['debtor']} owes {ln['creditor']}")

    print(f"\n--- Cash events handled by {r['user']} ({len(r['cash_events'])}) ---")
    for e in r["cash_events"][:60]:
        when = str(e["when"])[:16]
        cross = "  [CROSS]" if e["cross"] else ""
        print(f"  [{when}] {e['entity_type']:8} {e['doc_no']:10} "
              f"{e['direction']:3} {e['amount']:>12,.2f}  "
              f"{e['party'][:18]:18} doc-by:{e['doc_creator'][:10]}{cross}")
    if len(r["cash_events"]) > 60:
        print(f"  ... and {len(r['cash_events']) - 60} more")


if __name__ == "__main__":
    main()