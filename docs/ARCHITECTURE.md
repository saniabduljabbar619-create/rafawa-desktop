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
