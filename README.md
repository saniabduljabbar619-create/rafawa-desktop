# Rafawa Desktop

PySide2 desktop application for the Rafawa agro-trading business: crops and
fertilizer sales, purchases/intake, returns, advances, party ledgers, cashier
drawer reconciliation, company orders, and cloud backup sync.

## Layout

| Path | What lives there |
|---|---|
| `apps/product_management_app/` | The GUI (PySide2): `features/<area>/views|dialogs`, `app_state.py`, `main.py` |
| `database/models/` | SQLAlchemy models (MySQL in production) |
| `database/services/` | All business logic: sales, purchases, returns, payments, ledger, drawer, dashboard, orders |
| `shared/` | Config (`settings.py`, `constants.py`), API client, auth session, UI widgets |
| `tests/` | pytest suite (runs on SQLite, no MySQL or display needed) |

Business rules belong in `database/services/` so they can be tested without the GUI.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.template .env                                   # then fill in the database settings
```

Python 3.9 or 3.10 (PySide2 5.15 has no wheels for newer versions).

`.env` is looked for in the working directory, next to the packaged exe, and in
the project root. Key settings: `DATABASE_URL` (or `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`),
and optionally `CLOUD_DATABASE_URL` / `CLOUD_DB_*` for the one-way cloud backup sync.

## Tests and checks

```bash
pytest            # unit tests against a throwaway SQLite database
ruff check .      # correctness lint (undefined names, redefinitions, syntax)
```

CI (`.github/workflows/ci.yml`) runs both on every pull request.

## Conventions

- Money and stock logic changes need a test in `tests/` (see `tests/conftest.py` for the fixtures).
- Voiding keeps history (`status="voided"`); hard deletes go through `dashboard_service` and leave a `DeletionLog` row.
- All timestamps are local time (`datetime.now()`), matching the database's `NOW()` defaults.
- The shared walk-in party id is `shared.config.constants.WALKIN_PARTY_ID`; never hardcode it.
