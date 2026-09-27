# test_cloud_sync.py
# -*- coding: utf-8 -*-
"""
Run this yourself, from the project root, to prove the cloud sync
mechanism against your OWN real local server - before ever touching a
real Aiven instance.

WHAT THIS DOES:
  - Connects to your REAL local database exactly as the app already
    does (reads the same DB_HOST/DB_NAME/etc. from your .env)
  - Connects to a SECOND, separate database (the "cloud simulation"
    target) that you create specifically for this test - see the SQL
    below
  - Runs one real sync pass and prints exactly what happened, table by
    table
  - Never writes anything to your real local database - only reads it

SETUP (run this SQL on your local server first, one time):

    CREATE DATABASE rafawa_cloud_sim CHARACTER SET utf8mb4;

Then add these lines to your .env (alongside your existing DB_* lines -
these are all NEW, separate variables, nothing existing changes):

    CLOUD_DB_HOST=127.0.0.1
    CLOUD_DB_PORT=3306
    CLOUD_DB_NAME=rafawa_cloud_sim
    CLOUD_DB_USER=<same user as your regular DB_USER, or a new one>
    CLOUD_DB_PASSWORD=<matching password>

Then just run:

    python test_cloud_sync.py

WHAT TO EXPECT:
  - First run: every table with real data pushes once, "pushed: N"
  - Run it again immediately after: everything should show "pushed: 0"
    - that's correct, it means nothing changed since the first run
  - Go make a real sale or purchase in the app, then run this again -
    only that change should show up, not the whole table again
"""
import sys
sys.path.insert(0, ".")

from shared.config.settings import settings

print("=" * 64)
print("CLOUD SYNC - standalone real-server test")
print("=" * 64)

if not settings.database_url:
    print("\nERROR: your LOCAL database isn't configured (DB_HOST/DB_NAME/")
    print("DB_USER/DB_PASSWORD in .env). This script uses the same local")
    print("connection the real app already uses - nothing to test without it.")
    sys.exit(1)

print(f"\nLocal database:  configured (reading your real data)")
print(f"Cloud target:    {'configured -> ' + settings.cloud_database_url.split('@')[-1] if settings.cloud_database_url else 'NOT CONFIGURED'}")
print(f"Sync enabled:    {settings.CLOUD_SYNC_ENABLED}")

if not settings.cloud_database_url:
    print("\nCLOUD_DB_HOST (and friends) aren't set in your .env yet.")
    print("See the setup instructions at the top of this file, then run")
    print("this again.")
    sys.exit(1)

from database.services.cloud_sync_service import CloudSyncEngine

if not CloudSyncEngine.is_configured():
    print("\nCloud sync target is set, but CLOUD_SYNC_ENABLED is off.")
    print("Set CLOUD_SYNC_ENABLED=1 in your .env, or just leave it unset -")
    print("it defaults on automatically once a cloud target is configured.")
    sys.exit(1)

print("\nRunning one real sync pass against your actual local data...\n")


def _on_progress(table_name, index, total, result):
    ok = result.get("ok")
    pushed = result.get("pushed", 0)
    status = "OK" if ok else f"FAILED: {result.get('error')}"
    print(f"  [{index}/{total}] {table_name:<24} pushed={pushed:<4} {status}")


result = CloudSyncEngine.sync_all(progress_callback=_on_progress)

print("\n" + "=" * 64)
if result.get("ok"):
    total_pushed = sum(r.get("pushed", 0) for r in result["results"])
    failed = [r["table"] for r in result["results"] if not r.get("ok")]
    if failed:
        print(f"DONE - {total_pushed} row(s) pushed, but {len(failed)} table(s)")
        print(f"failed: {', '.join(failed)}")
        print("\nCheck the error(s) above - common causes: the cloud database")
        print("doesn't exist yet (run the CREATE DATABASE from the setup")
        print("instructions), or the CLOUD_DB_USER doesn't have privileges")
        print("on it.")
    else:
        print(f"DONE - {total_pushed} row(s) pushed, everything succeeded.")
        print("\nNow go check the second database directly to see your real")
        print("data actually landed there, e.g.:")
        print(f"\n    mysql -u <user> -p rafawa_cloud_sim -e \"SELECT * FROM parties;\"")
        print("\nThen run this script again - it should show 'pushed=0'")
        print("everywhere, proving the cursor correctly remembers what's")
        print("already synced.")
else:
    print(f"Sync did not run: {result.get('reason')}")
print("=" * 64)