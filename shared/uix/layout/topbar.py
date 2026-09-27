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

        # Logout button
        self.btn_logout = QPushButton("Logout")
        self.btn_logout.setFixedHeight(28)
        layout.addWidget(self.btn_logout)

        # Wiring
        self.btn_back.clicked.connect(self.back_requested.emit)
        self.btn_logout.clicked.connect(self.logout_requested.emit)

    def set_title(self, title: str):
        self.title_lbl.setText(title)
