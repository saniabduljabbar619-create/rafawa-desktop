# database/services/drawer/__init__.py
"""
Drawer / operator-accountability engine (see _common for the model summary).

Split by responsibility so each file stays reviewable:
  compute.py - DrawerService.compute (the per-cashier drawer)
  ledger.py  - persistent cross-cashier settlement ledger and settle actions
  lists.py   - party totals, transaction lists, returns list, search
"""
from .compute import ComputeMixin
from .ledger import LedgerMixin
from .lists import ListsMixin


class DrawerService(ComputeMixin, LedgerMixin, ListsMixin):
    """Public API - unchanged: every method is still DrawerService.<name>(...)."""
