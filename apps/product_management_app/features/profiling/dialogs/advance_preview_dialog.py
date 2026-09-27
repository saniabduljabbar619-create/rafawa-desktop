from PySide2.QtWidgets import QDialog, QVBoxLayout, QLabel

from database.session import get_session
from database.models.advance import Advance


class AdvancePreviewDialog(QDialog):

    def __init__(self, advance_id, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Advance #{advance_id}")
        self.resize(450, 250)

        layout = QVBoxLayout(self)

        with get_session() as s:

            a = s.query(Advance).get(advance_id)

        layout.addWidget(QLabel(f"Date: {a.created_at}"))
        layout.addWidget(QLabel(f"Amount: {a.amount:,.2f}"))
        layout.addWidget(QLabel(f"Direction: {a.direction}"))
        layout.addWidget(QLabel(f"Type: {a.advance_type}"))

        if a.notes:
            layout.addWidget(QLabel(f"Notes: {a.notes}"))