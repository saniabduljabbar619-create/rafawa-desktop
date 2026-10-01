"""
Database backups.

    python -m database.backup                 # one backup now (default folder, keep 14)
    python -m database.backup --dir D:\\Backups --keep 30

MySQL  -> `mysqldump` (must be installed and on PATH) piped to a .sql.gz
SQLite -> consistent copy via SQLite's online backup API (used by tests / dev)

The password is passed through the MYSQL_PWD environment variable, never on the
command line (where other users could see it in the process list).

Schedule it with Windows Task Scheduler (see README) or let the app do one
automatic backup per day (AUTO_BACKUP=1 in .env).

To restore a MySQL backup:   gunzip -c backup.sql.gz | mysql -u USER -p DBNAME
"""
from __future__ import annotations

import argparse
import gzip
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy.engine import Engine, make_url

from shared.paths import sub_dir

_log = logging.getLogger("rafawa.backup")


class BackupError(RuntimeError):
    pass


def default_backup_dir() -> Path:
    override = os.getenv("BACKUP_DIR", "").strip()
    if override:
        p = Path(override)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return sub_dir("backups")


def mysqldump_command(url, executable: str = "mysqldump") -> List[str]:
    """Argument list for mysqldump (no password in it)."""
    cmd = [executable, "--single-transaction", "--routines", "--triggers", "--no-tablespaces",
           "--default-character-set=utf8mb4",
           f"--host={url.host or '127.0.0.1'}", f"--port={url.port or 3306}",
           f"--user={url.username or ''}"]
    cmd.append(url.database or "")
    return cmd


def _prune(directory: Path, prefix: str, keep: int) -> List[Path]:
    files = sorted(directory.glob(f"{prefix}*"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for old in files[max(keep, 1):]:
        old.unlink()
        removed.append(old)
    return removed


def backup_database(engine: Optional[Engine] = None, directory: Optional[Path] = None,
                    keep: int = 14) -> Path:
    if engine is None:
        from database.engine import engine as _engine
        engine = _engine
    directory = Path(directory) if directory else default_backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    url = engine.url
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = url.database or "database"

    if url.get_backend_name() == "sqlite":
        src = Path(url.database or "")
        target = directory / f"{src.stem}-{stamp}.sqlite"
        with sqlite3.connect(str(src)) as source, sqlite3.connect(str(target)) as dest:
            source.backup(dest)
        _prune(directory, f"{src.stem}-", keep)
    else:
        exe = shutil.which("mysqldump")
        if not exe:
            raise BackupError("mysqldump was not found on PATH. Install the MySQL client tools "
                              "(or add their bin folder to PATH) and try again.")
        target = directory / f"{name}-{stamp}.sql.gz"
        env = dict(os.environ)
        if url.password:
            env["MYSQL_PWD"] = url.password
        proc = subprocess.Popen(mysqldump_command(url, exe), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        with gzip.open(target, "wb") as out:
            shutil.copyfileobj(proc.stdout, out)
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            target.unlink(missing_ok=True)
            raise BackupError(f"mysqldump failed: {err.strip() or 'unknown error'}")
        _prune(directory, f"{name}-", keep)

    if not target.exists() or target.stat().st_size == 0:
        raise BackupError("The backup file is empty.")
    _log.info("Backup written: %s (%d bytes)", target, target.stat().st_size)
    return target


def backup_due(directory: Optional[Path] = None, every_hours: float = 20.0) -> bool:
    """True when there is no backup newer than `every_hours` (for the once-a-day auto backup)."""
    directory = Path(directory) if directory else default_backup_dir()
    files = [p for p in directory.iterdir() if p.is_file()] if directory.exists() else []
    if not files:
        return True
    newest = max(p.stat().st_mtime for p in files)
    return (datetime.now().timestamp() - newest) > every_hours * 3600


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Back up the Rafawa database.")
    ap.add_argument("--dir", help="folder for backups (default: BACKUP_DIR or the app data folder)")
    ap.add_argument("--keep", type=int, default=14, help="how many recent backups to keep")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        path = backup_database(directory=Path(args.dir) if args.dir else None, keep=args.keep)
    except BackupError as e:
        print(f"BACKUP FAILED: {e}", file=sys.stderr)
        return 1
    print(f"Backup written: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
