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
cp .env.example .env                                    # then fill in the database settings
```

Python 3.9 or 3.10 (PySide2 5.15 has no wheels for newer versions).

`.env` is looked for in the working directory, next to the packaged exe, and in
the project root. Key settings: `DATABASE_URL` (or `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`),
and optionally `CLOUD_DATABASE_URL` / `CLOUD_DB_*` for the one-way cloud backup sync.

## Tests and checks

```bash
pytest            # services + headless GUI smoke tests on a throwaway SQLite database
ruff check .      # correctness lint (undefined names, redefinitions, syntax)
```

CI (`.github/workflows/ci.yml`) runs both on every pull request.

## Conventions

- Money and stock logic changes need a test in `tests/` (see `tests/conftest.py` for the fixtures).
- Voiding keeps history (`status="voided"`); hard deletes go through `dashboard_service` and leave a `DeletionLog` row.
- All timestamps are local time (`datetime.now()`), matching the database's `NOW()` defaults.
- The shared walk-in party id is `shared.config.constants.WALKIN_PARTY_ID`; never hardcode it.

## Security model

- **Roles** live on the logged-in session (`shared/auth/session.py`). Sensitive service
  operations are guarded in the service layer with `@require_admin`
  (`shared/auth/permissions.py`): voiding payments/sales/purchases/advances, hard deletes,
  force-deleting a supplier, and creating/disabling/deleting users. The UI hiding a button is
  not the control - the service refuses (`PermissionDenied`) even if called directly.
  Code that legitimately runs without a login (first-run setup, maintenance scripts) uses
  `with system_context():`.
- **Passwords** are PBKDF2-HMAC-SHA256 (600k iterations). Older, weaker hashes keep working
  and are upgraded transparently on the user's next login.
- **Brute force**: after 5 wrong passwords a username is locked out with a growing delay
  (30s up to 15 min); the login screen shows the wait.
- Security events (login ok/failed, permission denials) go to the `rafawa.security` logger.
- Keep `.env` readable only by the Windows user that runs the app.

## Database migrations (Alembic)

Schema changes go through Alembic (`migrations/`), never by hand-editing production.

```bash
alembic upgrade head                        # bring a database up to date (safe to re-run)
alembic revision -m "describe the change"   # new migration; edit it, then upgrade
alembic current                             # which revision a database is on
```

- A **brand-new** database gets the whole schema from revision `0001`.
- An **existing** installation (tables already created by `init_db.py`) is adopted
  automatically: `0001` is a no-op there, later revisions apply normally.
- **Take a database backup before running migrations in production.**
- Revision `0002` adds `audit_logs` (who voided/deleted/settled what); `0003` converts the
  return tables from FLOAT to exact DECIMAL columns.

## Operations

| What | Where / how |
|---|---|
| Logs | `%LOCALAPPDATA%\RafawaDesktop\logs\` (`app.log`, `security.log` - rotating) |
| Crash reports | `%LOCALAPPDATA%\RafawaDesktop\crashes\crash-*.txt` - the app shows the path; send it to support |
| Schema check | At start-up the app compares the database to the newest migration and warns if `alembic upgrade head` is needed |
| Backup now | `python -m database.backup --dir D:\Backups --keep 30` (needs `mysqldump` on PATH) |
| Automatic backup | Set `AUTO_BACKUP=1` in `.env` (one per day when the app opens), or schedule the command above with Windows Task Scheduler |
| Restore | `gunzip -c backup.sql.gz \| mysql -u USER -p DBNAME` (stop all clients first) |

Set `RAFAWA_DATA_DIR` to relocate the data folder (portable installs).

## Building and releasing

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1              # tests -> RafawaApp.exe -> installer
powershell -ExecutionPolicy Bypass -File build.ps1 -SkipTests   # same, without the tests
powershell -ExecutionPolicy Bypass -File build.ps1 -ExeOnly     # only dist\RafawaApp.exe
```

In VS Code: **Terminal > Run Task > Build installer**. The installer step needs
[Inno Setup 6](https://jrsoftware.org/isdl.php) installed; the result is `RafawaAppSetup.exe` in the project folder.

The installer asks for the MySQL details (and, optionally, the cloud backup) and writes them to `.env` next to the
program, URL-encoding the password for you; nothing secret is stored in the installer. An existing `.env` is kept on
upgrades, and a silent install (`/SILENT`) copies `.env.example` for you to edit.

The version lives in `shared/version.py` (shown in the window title, logs and installer).
Pushing a tag such as `v3.5.0` runs `.github/workflows/release.yml`, which builds the exe and
installer on a Windows runner and attaches them as a build artifact.

**Update notifications:** publish a small `latest.json` (`{"version": "3.6.0", "url": "...", "notes": "..."}`)
somewhere clients can read (web, or a shared folder) and set `UPDATE_MANIFEST_URL` in `.env`.
Clients tell the user when a newer version exists; they never download or run anything automatically.

## Reports

Admin ▸ **Reports** shows the period summary (sales / purchases / returns / cash by method and by
cashier), receivables and payables aging (0-30 / 31-60 / 61-90 / 90+ days) and stock valuation at
cost, and exports everything to a formatted Excel workbook. The numbers come from
`database/services/report_service.py` (admin-only) and `document_balance.py`.
