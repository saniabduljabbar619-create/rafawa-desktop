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


## Debt Center (admin) - a standalone book

`features/debt_center/`, styled like alg-erp's Master Supplier sheets (cream paper, ink totals row, amber).
Hub tiles (double-click): **My Creditors**, **My Debtors**, **Expenses & Expenditures**.

- **Standalone.** `models/debt.py` (`debt_people`, `debt_entries`) references no party, customer, supplier,
  sale, purchase or payment, and nothing else reads it: recording a debt here changes nothing elsewhere.
  Any number of people; one sheet each: Date | What it was for | owed | paid | How | Note | running balance,
  dark totals row. Type in the empty last row to add; click a cell to change it (audited); remove crosses a
  row out (never deleted) and it can be brought back. `services/debt_book_service.py`, admin only.
- **Expenses vs Expenditures** are two separate tables and sheets (tabs along the bottom), as in alg-erp:
  Expenses = day-to-day running costs (`expenses`, EXP-), Expenditures = larger outlays (`expenditures`,
  EXD-). `services/expense_service.py` serves both. They are not linked to the cashier drawer.
- Migrations: `0005` expenses, `0007` debt book + expenditures.

## Offsets and swaps (settle with goods)

`services/offset_service.py`, model `document_offsets` (migration `0006`).

- **Settle unpaid sales with a purchase.** After a purchase from a party who still owes us, the intake
  asks "settle their unpaid sales with this?" (never automatic). `OffsetService.apply` sets the purchase
  against their open sales, oldest first on both sides. What is left is paid/collected as usual.
- **Swap.** Cashier Purchase -> *Swap / Exchange*: goods he brings (purchase) and goods we give him
  (sale) are recorded together by `SwapService.record` and set against each other; only the
  difference is left owing either way. If the second document fails the first is voided.
- **Net off.** `OffsetService.apply(party_id, kind="net_off")` cancels the overlap for a party we both owe and are owed by (service only; no screen).
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
