#apps\product_management_app\features\admin_users\views\users_list.py
from __future__ import annotations

from PySide2.QtCore import Qt
from PySide2.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QMessageBox, QHeaderView, QFrame, QDialog,
    QLineEdit, QComboBox, QFormLayout
)
from shared.uix.layout.scrollable import make_scrollable_page

from database.services.user_admin_service import UserAdminService
from apps.product_management_app.features.admin_users.views.drawer_screen import DrawerScreen


class CreateUserDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create User")
        self.setModal(True)
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.role = QComboBox()
        self.role.addItem("Admin", "admin")
        self.role.addItem("Cashier", "cashier")

        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("Role", self.role)

        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Create")
        self.btn_ok.setDefault(True)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)
        root.addLayout(row)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)


class ResetPasswordDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reset Password")
        self.setModal(True)
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("New Password", self.password)

        root.addLayout(form)

        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_ok = QPushButton("Reset")
        self.btn_ok.setDefault(True)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)
        root.addLayout(row)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)


class UsersListView(QWidget):
    def __init__(self):
        super().__init__()

        root = make_scrollable_page(self, margins=(16, 16, 16, 16), spacing=12)

        header = QHBoxLayout()
        title = QLabel("Users")
        title.setStyleSheet("font-size: 18px; font-weight: 900;")
        header.addWidget(title)
        header.addStretch(1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_create = QPushButton("Create User")
        self.btn_toggle = QPushButton("Toggle Active")
        self.btn_reset = QPushButton("Reset Password")
        self.btn_delete = QPushButton("Delete User")
        header.addWidget(self.btn_refresh)
        header.addWidget(self.btn_create)
        header.addWidget(self.btn_toggle)
        header.addWidget(self.btn_reset)
        header.addWidget(self.btn_delete)

        root.addLayout(header)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Username", "Role", "Active", "Created"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(self.table.SelectRows)
        self.table.setEditTriggers(self.table.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)

        root.addWidget(self.table, 1)

        self.footer = QLabel("")
        self.footer.setStyleSheet("color: #666;")
        root.addWidget(self.footer)

        self.btn_refresh.clicked.connect(self.reload)
        self.btn_create.clicked.connect(self.create_user)
        self.btn_toggle.clicked.connect(self.toggle_active)
        self.btn_reset.clicked.connect(self.reset_password)
        self.btn_delete.clicked.connect(self.delete_user)
        self.table.cellDoubleClicked.connect(self.open_profile)

        self.reload()

    def _selected_user_id(self) -> int:
        row = self.table.currentRow()
        if row < 0:
            return 0
        item = self.table.item(row, 0)
        return int(item.text()) if item else 0

    def reload(self):
        try:
            rows = UserAdminService.list_users()
        except Exception as e:
            QMessageBox.critical(self, "Load Failed", str(e))
            return

        self.table.setRowCount(len(rows))
        for r, (uid, username, role, is_active, created_txt) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(str(uid)))
            self.table.setItem(r, 1, QTableWidgetItem(username))
            self.table.setItem(r, 2, QTableWidgetItem(role))
            self.table.setItem(r, 3, QTableWidgetItem("Yes" if is_active else "No"))
            self.table.setItem(r, 4, QTableWidgetItem(created_txt))

        self.footer.setText(f"Loaded {len(rows)} user(s). Double-click a user to open profile.")

    def create_user(self):
        dlg = CreateUserDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return

        username = (dlg.username.text() or "").strip()
        password = (dlg.password.text() or "").strip()
        role_name = str(dlg.role.currentData() or "")

        try:
            uid = UserAdminService.create_user(username=username, password=password, role_name=role_name)
        except Exception as e:
            QMessageBox.critical(self, "Create Failed", str(e))
            return

        QMessageBox.information(self, "Created", f"User created (id={uid}).")
        self.reload()

    def toggle_active(self):
        uid = self._selected_user_id()
        if uid <= 0:
            QMessageBox.warning(self, "Select", "Select a user first.")
            return

        # Read current active value from table
        row = self.table.currentRow()
        cur = (self.table.item(row, 3).text() or "").strip().lower() == "yes"
        new_val = not cur

        try:
            UserAdminService.set_active(uid, new_val)
        except Exception as e:
            QMessageBox.critical(self, "Update Failed", str(e))
            return

        self.reload()

    def reset_password(self):
        uid = self._selected_user_id()
        if uid <= 0:
            QMessageBox.warning(self, "Select", "Select a user first.")
            return

        dlg = ResetPasswordDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return

        pw = (dlg.password.text() or "").strip()
        try:
            UserAdminService.reset_password(uid, pw)
        except Exception as e:
            QMessageBox.critical(self, "Reset Failed", str(e))
            return

        QMessageBox.information(self, "Success", "Password updated.")
        self.reload()

    def delete_user(self):
        uid = self._selected_user_id()
        if uid <= 0:
            QMessageBox.warning(self, "Select", "Select a user first.")
            return

        confirm = QMessageBox.question(
            self, "Delete User",
            "Permanently delete this user? This cannot be undone.\n\n"
            "If this user has ever created any sale, purchase, payment, "
            "advance, or return, the delete will be refused - use "
            "\"Toggle Active\" for anyone with real history instead.",
            QMessageBox.Yes | QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            UserAdminService.delete_user(uid)
        except Exception as e:
            QMessageBox.critical(self, "Delete Failed", str(e))
            return

        QMessageBox.information(self, "Success", "User deleted.")
        self.reload()

    def open_profile(self, row: int, col: int):
        uid_item = self.table.item(row, 0)
        if not uid_item:
            return
        uid = int(uid_item.text() or "0")
        if uid <= 0:
            return
        DrawerScreen(user_id=uid, parent=self).exec_()