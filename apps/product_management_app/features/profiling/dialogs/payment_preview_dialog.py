from PySide2.QtWidgets import QDialog, QVBoxLayout, QLabel

from database.session import get_session
from database.models.payment import Payment


class PaymentPreviewDialog(QDialog):

    def __init__(self, payment_id, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Payment #{payment_id}")
        self.resize(450, 250)

        layout = QVBoxLayout(self)

        with get_session() as s:

            p = s.query(Payment).get(payment_id)

        layout.addWidget(QLabel(f"Date: {p.created_at}"))
        layout.addWidget(QLabel(f"Amount: {p.amount:,.2f}"))
        layout.addWidget(QLabel(f"Direction: {p.direction}"))
        layout.addWidget(QLabel(f"Method: {p.method}"))
        layout.addWidget(QLabel(f"Reference Entity: {p.entity_type}-{p.entity_id}"))

        if p.notes:
            layout.addWidget(QLabel(f"Notes: {p.notes}"))