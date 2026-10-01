# shared/uix/layout/topbar.py
from PySide2.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PySide2.QtCore import Signal


class TopBar(QWidget):
    back_requested = Signal()
    logout_requested = Signal()

    def __init__(self, title: str = ""):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.setObjectName("TopBar")
        from PySide2.QtCore import Qt
        self.setAttribute(Qt.WA_StyledBackground, True)

        # Back button
        self.btn_back = QPushButton("← Back")
        self.btn_back.setFixedHeight(28)
        layout.addWidget(self.btn_back)

        # Title
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(self.title_lbl)

        layout.addStretch(1)

        # Who is signed in (updated on every navigation)
        self.user_chip = QLabel("")
        self.user_chip.setObjectName("UserChip")
        self.user_chip.setVisible(False)
        layout.addWidget(self.user_chip)

        # Logout button
        self.btn_logout = QPushButton("Logout")
        self.btn_logout.setFixedHeight(28)
        layout.addWidget(self.btn_logout)

        # Wiring
        self.btn_back.clicked.connect(self.back_requested.emit)
        self.btn_logout.clicked.connect(self.logout_requested.emit)

    def set_title(self, title: str):
        self.title_lbl.setText(title)
        self.refresh_user()

    def refresh_user(self):
        from shared.auth.session import get_session
        sess = get_session()
        if sess and getattr(sess, "username", ""):
            role = (getattr(sess, "role", "") or "").strip().title()
            self.user_chip.setText(f"{sess.username}  \u00b7  {role}" if role else sess.username)
            self.user_chip.setVisible(True)
        else:
            self.user_chip.setVisible(False)
