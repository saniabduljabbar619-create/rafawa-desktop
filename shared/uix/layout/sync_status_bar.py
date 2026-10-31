# shared/uix/layout/sync_status_bar.py
# -*- coding: utf-8 -*-
"""
A thin, sticky bar for cloud sync status - lives at the very top of
AppShell (above the sidebar and content, spanning the full window
width), so every screen, every role, always shows it. Purely a
display - it never touches the sync engine directly, only reacts to
signals from CloudSyncWorker, running safely on its own background
thread.
"""
from PySide2.QtWidgets import QWidget, QHBoxLayout, QLabel, QProgressBar
from PySide2.QtCore import Qt
from datetime import datetime

_BAR_BG = "#ffffff"
_PRIMARY_GREEN = "#1f8f6b"
_AMBER = "#b45309"
_MUTED = "#6b7280"


class SyncStatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("SyncStatusBar")
        # A quiet footer, not a banner: light, with a hairline above it.
        self.setStyleSheet(f"QWidget#SyncStatusBar {{ background: {_BAR_BG}; border-top: 1px solid #e5e7eb; }}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 3, 12, 3)
        lay.setSpacing(10)

        self.label = QLabel("Cloud sync: starting…")
        self.label.setStyleSheet(f"background: transparent; color: {_MUTED}; font-size: 11px; font-weight: 600;")
        lay.addWidget(self.label)

        # A real, counting progress bar - fills table by table during an
        # active sync pass, not just a spinner. Hidden between passes,
        # since "0/10" sitting there permanently between syncs reads as
        # broken, not idle.
        self.bar = QProgressBar()
        self.bar.setFixedHeight(14)
        self.bar.setFixedWidth(160)
        self.bar.setTextVisible(True)
        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background: #e5e7eb;
                border: none;
                border-radius: 7px;
                color: #064e3b;
                font-size: 9px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: {_PRIMARY_GREEN};
                border-radius: 7px;
            }}
        """)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)

        lay.addStretch(1)

    # ------------------------------------------------------------------
    # Slots - connect these to a CloudSyncWorker instance's signals.
    # Each one only ever touches this widget's own labels/bar, nothing
    # else - safe by construction since Qt guarantees these run on the
    # main thread regardless of which thread emitted the signal.
    # ------------------------------------------------------------------
    def on_not_configured(self, reason: str = ""):
        self.bar.setVisible(False)
        self.label.setText("Cloud sync: " + (reason.split(" - ")[0] if reason else "not configured"))
        self.setToolTip(reason or "Cloud sync is not configured")
        self.label.setStyleSheet(f"background: transparent; color: {_MUTED}; font-size: 11px; font-weight: 600;")

    def on_sync_detail(self, errors: str):
        """The errors of the pass that just ended - shown as a tooltip so a failure can be read, not guessed."""
        self.setToolTip(errors)

    def on_sync_started(self):
        self.setToolTip("")
        self.bar.setVisible(True)
        self.bar.setMinimum(0)
        self.bar.setValue(0)
        self.label.setText("Syncing to cloud…")
        self.label.setStyleSheet(f"background: transparent; color: {_PRIMARY_GREEN}; font-size: 11px; font-weight: 700;")

    def on_table_progress(self, table_name: str, index: int, total: int, ok: bool, pushed: int):
        self.bar.setMaximum(total)
        self.bar.setValue(index)
        self.bar.setFormat(f"{index}/{total}")
        self.label.setText(f"Syncing… {table_name} ({index}/{total})")

    def on_sync_finished(self, all_ok: bool, total_pushed: int, failed_tables: list):
        self.bar.setVisible(False)
        now_s = datetime.now().strftime("%H:%M")
        if all_ok:
            if total_pushed > 0:
                self.label.setText(f"\u2713 Synced to cloud \u2014 {total_pushed} update(s) \u2014 {now_s}")
            else:
                self.label.setText(f"\u2713 Cloud sync up to date \u2014 {now_s}")
            self.label.setStyleSheet(f"background: transparent; color: {_PRIMARY_GREEN}; font-size: 11px; font-weight: 600;")
        else:
            names = ", ".join(failed_tables[:3])
            more = f" (+{len(failed_tables) - 3} more)" if len(failed_tables) > 3 else ""
            self.label.setText(f"\u26a0 Cloud sync issue: {names}{more} \u2014 will retry")
            self.label.setStyleSheet(f"background: transparent; color: {_AMBER}; font-size: 11px; font-weight: 700;")