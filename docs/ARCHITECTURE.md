# Architecture

```
GUI (PySide2)            apps/product_management_app/features/<area>/{views,dialogs}
   |  calls
Services (business rules) database/services/*          <- all money/stock/permission logic, unit-tested
   |  uses
Models (SQLAlchemy)       database/models/*  + Alembic migrations/
   |
MySQL (SQLite in tests)
```

Rules of thumb
- **No business logic in views.** A view gathers input and shows results; calculations and
  validation live in a service so a test can reach them without a display.
- **One definition per concept.** "What does this document still owe?" is
  `services/document_balance.py`; stock on hand is `StockMovement` + `STOCK_IN/OUT_TYPES`;
  the shared walk-in party is `shared.config.constants.WALKIN_PARTY_ID`.
- **Append-only history.** Voids keep the row (`status="voided"`) and reverse stock with
  offsetting movements; every void/delete/settlement is also written to `audit_logs`.
- **Permissions in services** (`shared/auth/permissions.py`), not only in the UI.
- **Time is local** (`datetime.now()`), matching the database `NOW()` defaults.

Drawer engine (`services/drawer/`): `compute.py` (per-cashier drawer), `ledger.py`
(cross-cashier settlement ledger), `lists.py` (party totals / transaction lists / search).
`services/drawer_service.py` remains as the import path the rest of the app uses.


## Workspaces and roles

| Role | Opens | Can |
|---|---|---|
| `admin` / `pm` | Product Management, Cashier, Weigher | everything |
| `cashier` | Cashier only | sales, payments, **Weigh Tickets** (verify, price, finalize) |
| `weigher` | Weigher only | register/select a party, weigh rounds, print the slip |

`shared/auth/permissions.py` holds the role sets (`WEIGH_ROLES`, `INTAKE_ROLES`) and
`workspace_allowed()`; the launcher and `main.open_workspace` both use it.

**Weigher flow** (`services/weigh_service.py`, models in `models/weigh_ticket.py`, screens in
`features/weigher/`):

```
weigher:        register/select party -> product/area/grade -> record rounds (1-3 or partial bags + kg)
                -> auto totals -> choose slip (full/summary) -> submit -> print & sign   [no prices]
admin/cashier:  Weigh Tickets -> Open Intake -> verify & edit rounds -> confirm price per kg
                -> amount = kg x price -> Finalize (creates the Purchase) -> payment -> receipt
```

The weigher has **no authority over price, payment or finalization** - the service refuses those
calls for the role and never returns a price to a weigher. A ticket is `open` (being weighed) ->
`submitted` (locked for the weigher) -> `finalized` (a `Purchase` exists) or `cancelled`.
Finalizing takes a row lock on the ticket so two terminals cannot book it twice. Corrections made
at intake are written to `audit_logs`.


## Debt Center (admin)

`features/debt_center/` - hub with three tiles; double-click opens **My Creditors** (we owe, from
purchases), **My Debtors** (they owe us, from sales) or **Expenses**. A party row opens a
**worksheet**: open documents (tick some to pay only those, otherwise oldest first), a payment panel,
and the payment trail.

- `services/debt_center_service.py` - balances come from `document_balance` (never re-derived);
  `pay()` spreads one amount over the open documents in a single transaction, refuses overpayment,
  writes ordinary `Payment` rows (so every other screen sees them) and an audit entry.
- `services/expense_service.py` + `models/expense.py` (migration `0005`) - expenses are never deleted;
  a wrong one is voided with a reason, edits are audited. Admin only. Expenses are not yet linked
  to the cashier drawer.
- Walk-ins are not listed (they share one anonymous party, so a per-person balance does not exist).

## Offsets and swaps (settle with goods)

`services/offset_service.py`, model `document_offsets` (migration `0006`).

- **Settle unpaid sales with a purchase.** After a purchase from a party who still owes us, the intake
  asks "settle their unpaid sales with this?" (never automatic). `OffsetService.apply` sets the purchase
  against their open sales, oldest first on both sides. What is left is paid/collected as usual.
- **Swap.** Cashier Purchase -> *Swap / Exchange*: goods he brings (purchase) and goods we give him
  (sale) are recorded together by `SwapService.record` and set against each other; only the
  difference is left owing either way. If the second document fails the first is voided.
- **Net off.** Debt Center worksheet, for a party we both owe and are owed by.
- An offset moves no cash (drawer untouched). `document_balance` counts it as a settlement, so every
  balance screen agrees; a voided counterpart releases it, and an admin can reverse one (audited).

## Orders (unified)

One sidebar entry, **Orders** (`features/orders/views/orders_hub.py`), in the Product Management and
Cashier workspaces. Tabs: **Tracking board** (every Sales and Purchase Order: status, days on the road,
overdue delivery, value, payment balance; double-click opens the order, *Track history* shows who did
what and when), **Sales Orders** (companies) and **Purchase Orders** (suppliers).

Companies and PO suppliers stay two tables (their orders keep their own foreign keys) but are ONE pool:
`OrderHubService.sync_partners()` copies any partner missing on the other side (matched by name), and
both `create()` services call it, so a partner added on one side shows in the other side's picker.
Details are copied at creation; later edits to a phone/address are not kept in step.
