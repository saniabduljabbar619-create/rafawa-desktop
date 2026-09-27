# apps/product_management_app/features/cashier/views/cashier_daily_summary.py
# -*- coding: utf-8 -*-
"""
Cashier's own Daily Summary.

This IS the admin Cash Drawer screen (DrawerPanel) - same cards, same
splits, same verdicts, same table, same drilldowns - just embedded
without dialog chrome and pinned to whoever is logged in right now.
There is no user picker here: a cashier only ever sees their own drawer.

All money logic still lives in DrawerService; this file only resolves
the logged-in user_id and hosts the panel.
"""
from __future__ import annotations

from PySide2.QtWidgets import QWidget, QVBoxLayout, QLabel

from shared.auth.session import get_session
from apps.product_management_app.features.admin_users.views.drawer_screen import DrawerPanel


class CashierDailySummaryView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._panel = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        user_id = self._logged_in_user_id()
        if user_id > 0:
            self._panel = DrawerPanel(user_id, parent=self)
            layout.addWidget(self._panel)
        else:
            fallback = QLabel("No active session - please log in again.")
            fallback.setStyleSheet("font-size: 14px; padding: 16px; color: #c02b2b;")
            layout.addWidget(fallback)

    @staticmethod
    def _logged_in_user_id() -> int:
        try:
            sess = get_session()
            return int(getattr(sess, "user_id", 0) or 0)
        except Exception:
            return 0

    def showEvent(self, event):
        # This view can be re-shown without being recreated (switching
        # sidebar tabs and back), so pull fresh numbers every time it
        # becomes visible again - not just on first build. Same reasoning
        # the old implementation had for its own showEvent override.
        super().showEvent(event)
        if self._panel is not None:
            self._panel.reload()