# apps/product_management_app/launcher.py
# -*- coding: utf-8 -*-
"""
The Launcher - one screen, two moods. Logged out: a single, centered
login card on a full dark-green backdrop matching the sidebar's own
theme - no separate popup dialog, no bare header with a small button.
Logged in: the header appears and the screen becomes either two
workspace cards to choose from (admin) or nothing at all, since a
cashier is routed away immediately (existing, already-correct logic -
see _refresh_ui, unchanged in substance).

Public interface unchanged: LauncherView is a QWidget with a
workspace_selected(str) signal emitting "pm" or "cashier" - main.py
only ever touches that signal, nothing internal.
"""
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QMessageBox, QFrame, QStackedWidget, QSpacerItem, QSizePolicy
)
from PySide2.QtCore import Signal, Qt

from shared.auth.session import set_session, get_session as get_user_session
from database.services.auth_service import AuthService
from database.services.seed_service import SeedService
from database.services.admin_setup_service import AdminSetupService

# Matches shared/uix/theme.py's own palette exactly - the same dark
# green as the sidebar (#0b2a24), the same primary green as every
# PrimaryButton elsewhere in the app (#1f8f6b).
_DARK_GREEN = "#0b2a24"
_DARK_GREEN_LIGHTER = "#12382f"
_PRIMARY_GREEN = "#1f8f6b"
_PRIMARY_GREEN_HOVER = "#177a5b"
_PRIMARY_GREEN_PRESSED = "#136a50"
_ACCENT_GREEN = "#34d399"


class _AuthCard(QFrame):
    """The single, centered card itself - white, rounded, elevated off
    the dark backdrop. Reused for both the login form and the first-run
    admin-setup form, so both feel like the same, deliberate screen
    rather than two different designs bolted together."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AuthCard")
        self.setFixedWidth(380)
        self.setStyleSheet(f"""
            QFrame#AuthCard {{
                background: #ffffff;
                border-radius: 18px;
            }}
            QFrame#AuthCard QLabel#AuthTitle {{
                background: transparent;
                font-size: 20px;
                font-weight: 900;
                color: {_DARK_GREEN};
            }}
            QFrame#AuthCard QLabel#AuthSubtitle {{
                background: transparent;
                font-size: 12px;
                color: #6b7280;
            }}
            QFrame#AuthCard QLabel#FieldLabel {{
                background: transparent;
                font-size: 11px;
                font-weight: 700;
                color: #6b7280;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            QFrame#AuthCard QLineEdit {{
                background: #f6f7fb;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                padding: 10px 12px;
                font-size: 13px;
                min-height: 20px;
            }}
            QFrame#AuthCard QLineEdit:focus {{
                border: 1px solid {_PRIMARY_GREEN};
                background: #ffffff;
            }}
            QFrame#AuthCard QPushButton#AuthPrimary {{
                background: {_PRIMARY_GREEN};
                color: #ffffff;
                border: none;
                border-radius: 10px;
                padding: 11px;
                font-size: 13px;
                font-weight: 800;
                min-height: 22px;
            }}
            QFrame#AuthCard QPushButton#AuthPrimary:hover {{ background: {_PRIMARY_GREEN_HOVER}; }}
            QFrame#AuthCard QPushButton#AuthPrimary:pressed {{ background: {_PRIMARY_GREEN_PRESSED}; }}
            QFrame#AuthCard QPushButton#AuthLink {{
                background: transparent;
                border: none;
                color: {_PRIMARY_GREEN};
                font-size: 11px;
                font-weight: 700;
                padding: 4px;
            }}
            QFrame#AuthCard QPushButton#AuthLink:hover {{ color: {_PRIMARY_GREEN_HOVER}; text-decoration: underline; }}
            QFrame#AuthCard QLabel#AuthError {{
                background: transparent;
                color: #b91c1c;
                font-size: 11px;
            }}
        """)


class _WorkspaceCard(QFrame):
    """One of the two workspace-selection cards, shown once an admin is
    logged in. Same visual language as _AuthCard, restyled with a bit
    more presence (bigger radius, a subtle green accent bar) since these
    are the two doors the whole app opens through."""
    clicked = Signal()

    def __init__(self, title: str, subtitle: str, button_label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("WorkspaceCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(180)
        self.setStyleSheet(f"""
            QFrame#WorkspaceCard {{
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 16px;
            }}
            QFrame#WorkspaceCard:hover {{
                border: 1px solid {_PRIMARY_GREEN};
            }}
            QFrame#WorkspaceCard QLabel#CardTitle {{
                background: transparent;
                font-size: 16px;
                font-weight: 900;
                color: {_DARK_GREEN};
            }}
            QFrame#WorkspaceCard QLabel#CardSubtitle {{
                background: transparent;
                font-size: 12px;
                color: #6b7280;
            }}
            QFrame#WorkspaceCard QPushButton#CardButton {{
                background: {_PRIMARY_GREEN};
                color: #ffffff;
                border: none;
                border-radius: 10px;
                padding: 10px;
                font-size: 12px;
                font-weight: 800;
                min-height: 20px;
            }}
            QFrame#WorkspaceCard QPushButton#CardButton:hover {{ background: {_PRIMARY_GREEN_HOVER}; }}
            QFrame#WorkspaceCard QPushButton#CardButton:pressed {{ background: {_PRIMARY_GREEN_PRESSED}; }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(8)

        accent = QFrame()
        accent.setFixedHeight(4)
        accent.setStyleSheet(f"background: {_ACCENT_GREEN}; border-radius: 2px;")
        lay.addWidget(accent)

        t = QLabel(title)
        t.setObjectName("CardTitle")
        s = QLabel(subtitle)
        s.setObjectName("CardSubtitle")
        s.setWordWrap(True)

        lay.addWidget(t)
        lay.addWidget(s)
        lay.addStretch(1)

        self.btn = QPushButton(button_label)
        self.btn.setObjectName("CardButton")
        self.btn.clicked.connect(self.clicked.emit)
        lay.addWidget(self.btn)


class LauncherView(QWidget):
    workspace_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        SeedService.ensure_bootstrap_data()

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # ---- header - only appears once logged in ----
        self._header_bar = QWidget()
        self._header_bar.setStyleSheet("background: #ffffff; border-bottom: 1px solid #e5e7eb;")
        header_lay = QHBoxLayout(self._header_bar)
        header_lay.setContentsMargins(24, 16, 24, 16)

        title = QLabel("Rafawa \u2013 App Launcher")
        title.setStyleSheet(f"background: transparent; font-size: 18px; font-weight: 900; color: {_DARK_GREEN};")
        header_lay.addWidget(title)
        header_lay.addStretch(1)

        self.status = QLabel("")
        self.status.setStyleSheet("background: transparent; color: #6b7280; font-size: 12px;")
        header_lay.addWidget(self.status)

        self.btn_logout = QPushButton("Logout")
        header_lay.addWidget(self.btn_logout)
        self._root.addWidget(self._header_bar)

        # ---- the rest of the screen - swaps between the logged-out
        # auth backdrop and the logged-in workspace picker ----
        self._stack = QStackedWidget()
        self._root.addWidget(self._stack, 1)

        self._build_logged_out_page()
        self._build_logged_in_page()

        self.btn_logout.clicked.connect(self._logout)

        self._refresh_ui()

    # ------------------------------------------------------------------
    # LOGGED OUT - full dark-green backdrop, one centered card
    # ------------------------------------------------------------------
    def _build_logged_out_page(self):
        page = QWidget()
        page.setStyleSheet(f"background: {_DARK_GREEN};")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 24, 24, 24)

        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)

        self._auth_card = _AuthCard()
        card_lay = QVBoxLayout(self._auth_card)
        card_lay.setContentsMargins(32, 32, 32, 32)
        card_lay.setSpacing(6)

        brand = QLabel("RAFAWA")
        brand.setStyleSheet(
            f"background: transparent; color: {_PRIMARY_GREEN}; "
            f"font-size: 12px; font-weight: 900; letter-spacing: 2px;"
        )
        card_lay.addWidget(brand)

        self._auth_title = QLabel("Sign in")
        self._auth_title.setObjectName("AuthTitle")
        card_lay.addWidget(self._auth_title)

        self._auth_subtitle = QLabel("Enter your credentials to continue.")
        self._auth_subtitle.setObjectName("AuthSubtitle")
        self._auth_subtitle.setWordWrap(True)
        card_lay.addWidget(self._auth_subtitle)

        card_lay.addSpacing(10)

        user_label = QLabel("Username")
        user_label.setObjectName("FieldLabel")
        card_lay.addWidget(user_label)
        self.username = QLineEdit()
        self.username.setPlaceholderText("Enter your username")
        card_lay.addWidget(self.username)

        card_lay.addSpacing(6)

        pass_label = QLabel("Password")
        pass_label.setObjectName("FieldLabel")
        card_lay.addWidget(pass_label)
        self.password = QLineEdit()
        self.password.setPlaceholderText("Enter your password")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self._attempt_login)
        card_lay.addWidget(self.password)

        card_lay.addSpacing(4)

        self.auth_error = QLabel("")
        self.auth_error.setObjectName("AuthError")
        self.auth_error.setWordWrap(True)
        self.auth_error.setVisible(False)
        card_lay.addWidget(self.auth_error)

        card_lay.addSpacing(8)

        self.btn_primary = QPushButton("Sign In")
        self.btn_primary.setObjectName("AuthPrimary")
        self.btn_primary.setDefault(True)
        card_lay.addWidget(self.btn_primary)

        # Second field set (confirm password) - only shown for first-run
        # admin creation. Built once, hidden by default, so both forms
        # live in exactly the same card rather than two separate designs.
        self._confirm_label = QLabel("Confirm Password")
        self._confirm_label.setObjectName("FieldLabel")
        self._confirm_label.setVisible(False)
        card_lay.addWidget(self._confirm_label)
        self.password2 = QLineEdit()
        self.password2.setPlaceholderText("Re-enter your password")
        self.password2.setEchoMode(QLineEdit.Password)
        self.password2.setVisible(False)
        card_lay.addWidget(self.password2)

        row.addWidget(self._auth_card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        self.btn_primary.clicked.connect(self._on_primary_clicked)

        self._stack.addWidget(page)

    # ------------------------------------------------------------------
    # LOGGED IN (admin) - the two workspace cards
    # ------------------------------------------------------------------
    def _build_logged_in_page(self):
        page = QWidget()
        page.setStyleSheet("background: #f6f7fb;")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(24, 24, 24, 24)

        outer.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(20)

        self.card_pm = _WorkspaceCard(
            "Product Management / Admin",
            "Inventory, profiles, catalog, returns, admin operations.",
            "Open Product Management",
        )
        self.card_cashier = _WorkspaceCard(
            "Cashier",
            "Sales checkout, intake delivery, payments and daily operations.",
            "Open Cashier",
        )
        self.card_pm.clicked.connect(lambda: self.workspace_selected.emit("pm"))
        self.card_cashier.clicked.connect(lambda: self.workspace_selected.emit("cashier"))

        row.addWidget(self.card_pm, 1)
        row.addWidget(self.card_cashier, 1)
        outer.addLayout(row)
        outer.addStretch(1)

        self._stack.addWidget(page)

    # ------------------------------------------------------------------
    def _refresh_ui(self):
        has_any = SeedService.has_any_user()
        sess = get_user_session()

        if not sess:
            self._header_bar.setVisible(False)
            self._stack.setCurrentIndex(0)

            if has_any:
                # Normal login mode
                self._auth_title.setText("Sign in")
                self._auth_subtitle.setText("Enter your credentials to continue.")
                self.btn_primary.setText("Sign In")
                self._confirm_label.setVisible(False)
                self.password2.setVisible(False)
            else:
                # First-run: nobody exists yet, so this IS the setup form
                self._auth_title.setText("Create Admin Account")
                self._auth_subtitle.setText(
                    "No users exist yet - set up the first Admin account to begin."
                )
                self.btn_primary.setText("Create Admin")
                self._confirm_label.setVisible(True)
                self.password2.setVisible(True)

            self.auth_error.setVisible(False)
            return

        # Logged in
        self._header_bar.setVisible(True)
        self.status.setText(f"Logged in as: {sess.username} \u2022 {sess.role}")

        role = (sess.role or "").strip().lower()
        if role == "cashier":
            # Auto-route immediately - a cashier never sees this screen
            # at all, matching the existing, already-correct behavior.
            self.workspace_selected.emit("cashier")
            return

        self._stack.setCurrentIndex(1)

    # ------------------------------------------------------------------
    def _on_primary_clicked(self):
        if SeedService.has_any_user():
            self._attempt_login()
        else:
            self._attempt_create_admin()

    def _attempt_login(self):
        u = (self.username.text() or "").strip()
        p = (self.password.text() or "").strip()
        if not u or not p:
            self._show_auth_error("Username and password are required.")
            return

        sess = AuthService.authenticate(username=u, password=p)
        if not sess:
            self._show_auth_error("Invalid username or password.")
            return

        set_session(sess)
        self._clear_auth_fields()
        self._refresh_ui()

    def _attempt_create_admin(self):
        u = (self.username.text() or "").strip()
        p1 = (self.password.text() or "").strip()
        p2 = (self.password2.text() or "").strip()

        if not u or not p1 or not p2:
            self._show_auth_error("All fields are required.")
            return
        if p1 != p2:
            self._show_auth_error("Passwords do not match.")
            return

        try:
            AdminSetupService.create_first_admin(username=u, password=p1)
        except Exception as e:
            self._show_auth_error(str(e))
            return

        sess = AuthService.authenticate(username=u, password=p1)
        if not sess:
            QMessageBox.information(self, "Created", "Admin created. Please sign in.")
            self._clear_auth_fields()
            self._refresh_ui()
            return

        set_session(sess)
        self._clear_auth_fields()
        self._refresh_ui()

    def _show_auth_error(self, message: str):
        self.auth_error.setText(message)
        self.auth_error.setVisible(True)

    def _clear_auth_fields(self):
        self.username.clear()
        self.password.clear()
        self.password2.clear()
        self.auth_error.setVisible(False)

    def _logout(self):
        set_session(None)
        self._refresh_ui()