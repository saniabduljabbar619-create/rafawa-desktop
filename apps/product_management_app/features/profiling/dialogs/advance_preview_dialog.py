from PySide2.QtWidgets import QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem
from sqlalchemy import func

from database.session import get_session
from database.models.advance import Advance
from database.models.advance_allocations import AdvanceAllocation
from database.models.payment import Payment


class AdvancePreviewDialog(QDialog):

    def __init__(self, advance_id, parent=None):
        super().__init__(parent)

        self.setWindowTitle(f"Advance #{advance_id}")
        self.resize(620, 380)

        layout = QVBoxLayout(self)

        with get_session() as s:
            a = s.query(Advance).get(int(advance_id))
            allocs = (
                s.query(AdvanceAllocation)
                .filter(AdvanceAllocation.advance_id == int(advance_id))
                .order_by(AdvanceAllocation.id.asc())
                .all()
            )
            pays = (
                s.query(Payment)
                .filter(Payment.entity_type == "advance", Payment.entity_id == int(advance_id))
                .filter(Payment.status != "voided")
                .order_by(Payment.created_at.asc(), Payment.id.asc())
                .all()
            )
            amt = float(a.amount or 0) if a else 0.0
            used = sum(float(al.amount or 0) for al in allocs) + sum(float(p.amount or 0) for p in pays)
            rem = max(amt - used, 0.0)

            layout.addWidget(QLabel(f"Date: {a.created_at if a else ''}"))
            layout.addWidget(QLabel(f"Direction: {'Advance OUT (given)' if a and a.direction == 'given' else 'Advance IN (received)'}"))
            layout.addWidget(QLabel(f"Type / Ledger: {a.advance_type if a else ''} / {a.ledger or '' if a else ''}"))
            layout.addWidget(QLabel(
                f"Total Amount: \u20a6{amt:,.2f}   |   Used / Paid: \u20a6{used:,.2f}   |   Remaining: \u20a6{rem:,.2f}"
            ))
            if a and a.notes:
                layout.addWidget(QLabel(f"Notes: {a.notes}"))

            events = []
            running = amt
            for al in allocs:
                u_amt = float(al.amount or 0)
                running = max(running - u_amt, 0.0)
                doc_ref = f"{'PUR' if al.entity_type == 'purchase' else 'SAL'}-{al.entity_id}"
                events.append((
                    str(al.created_at or "")[:19],
                    f"Applied to {doc_ref}",
                    f"{u_amt:,.2f}",
                    f"{running:,.2f}",
                ))
            for p in pays:
                p_amt = float(p.amount or 0)
                running = max(running - p_amt, 0.0)
                events.append((
                    str(p.created_at or "")[:19],
                    f"Payment ({p.method or 'cash'})",
                    f"{p_amt:,.2f}",
                    f"{running:,.2f}",
                ))

        table = QTableWidget(len(events), 4)
        table.setHorizontalHeaderLabels(["Date", "Detail", "Amount Paid / Used", "Remaining"])
        for r, ev in enumerate(events):
            for c, val in enumerate(ev):
                table.setItem(r, c, QTableWidgetItem(str(val)))
        layout.addWidget(table)
