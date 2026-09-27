# verify_row_counts.py
# -*- coding: utf-8 -*-
"""
Independent row-count check - local vs cloud - using the exact same
connection mechanism CloudSyncEngine itself already uses successfully.
Sidesteps the standalone mysql.exe CLI client entirely, since that's a
separate tool with its own connection behavior, unrelated to whether
the actual sync (which already proved itself) is working.

Run from the project root, same as test_cloud_sync.py:

    python verify_row_counts.py
"""
import sys
sys.path.insert(0, ".")

import sqlalchemy as sa
from shared.config.settings import settings

TABLES = [
    "parties", "products", "sales", "sale_items", "purchases",
    "purchase_items", "payments", "advances", "advance_allocations",
    "cashier_settlements",
]

print("=" * 64)
print("ROW COUNT VERIFICATION - local vs cloud")
print("=" * 64)

local_engine = sa.create_engine(settings.database_url, future=True)

cloud_connect_args = {}
if settings.cloud_ssl_ca:
    cloud_connect_args["ssl"] = {"ca": settings.cloud_ssl_ca}
cloud_engine = sa.create_engine(
    settings.cloud_database_url, connect_args=cloud_connect_args, future=True
)

print(f"\n{'Table':<24}{'Local':>10}{'Cloud':>10}{'Match?':>10}")
print("-" * 54)

all_match = True
with local_engine.connect() as lconn, cloud_engine.connect() as cconn:
    for table in TABLES:
        try:
            local_count = lconn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()
        except Exception as e:
            local_count = f"ERR: {e}"

        try:
            cloud_count = cconn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()
        except sa.exc.ProgrammingError:
            # The cloud mirror table is only ever created lazily, the
            # first time there's genuinely a row to push for it - a
            # table with zero local rows simply never gets one. Not an
            # error, just "nothing's been synced here yet".
            cloud_count = 0 if local_count == 0 else "MISSING TABLE"
        except Exception as e:
            cloud_count = f"ERR: {e}"

        match = (local_count == cloud_count)
        if not match:
            all_match = False
        print(f"{table:<24}{str(local_count):>10}{str(cloud_count):>10}{'YES' if match else 'NO':>10}")

print("\n" + "=" * 64)
if all_match:
    print("ALL TABLES MATCH - every row genuinely made it to the cloud.")
else:
    print("MISMATCH DETECTED - see the table(s) marked 'NO' above.")
    print("If a mismatch is small and the cloud count is LOWER, it may just")
    print("mean the sync hasn't caught up yet - run test_cloud_sync.py again")
    print("and re-check. If cloud is HIGHER, or the gap doesn't close after")
    print("re-syncing, come back with these numbers.")
print("=" * 64)