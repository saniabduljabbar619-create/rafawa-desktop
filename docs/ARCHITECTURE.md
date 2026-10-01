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
