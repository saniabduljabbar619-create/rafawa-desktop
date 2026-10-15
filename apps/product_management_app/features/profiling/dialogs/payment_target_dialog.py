#apps/product_management_app/features/profiling/dialogs/payment_target_dialog.py

from PySide2.QtWidgets import (
    QDialog, QVBoxLayout, QLabel,
    QRadioButton, QPushButton, QHBoxLayout, QButtonGroup
)


class PaymentTargetDialog(QDialog):

    def __init__(self, party, user_id, parent=None, walkin_name=None):
        super().__init__(parent)

        self.party = party
        self.walkin_name = (walkin_name or "").strip() or None
        self.user_id = user_id

        self.setWindowTitle("Select Payment Target")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)

        label = QLabel("Choose what you want to settle:")
        layout.addWidget(label)

        self.group = QButtonGroup(self)

        self.rb_sale = QRadioButton("Sale")
        self.rb_purchase = QRadioButton("Purchase")
        self.rb_advance = QRadioButton("Advance")

        self.group.addButton(self.rb_sale)
        self.group.addButton(self.rb_purchase)
        self.group.addButton(self.rb_advance)

        self.rb_sale.setChecked(True)

        layout.addWidget(self.rb_sale)
        layout.addWidget(self.rb_purchase)
        if not self.walkin_name:           # a walk-in has no account to hold an advance
            layout.addWidget(self.rb_advance)
        else:
            self.rb_advance.setVisible(False)

        btns = QHBoxLayout()

        self.btn_continue = QPushButton("Continue")
        self.btn_cancel = QPushButton("Cancel")

        btns.addWidget(self.btn_continue)
        btns.addWidget(self.btn_cancel)

        layout.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_continue.clicked.connect(self.route)

    def route(self):

        if self.rb_sale.isChecked():

            from apps.product_management_app.features.profiling.dialogs.sale_payment_dialog import SalePaymentDialog

            dlg = SalePaymentDialog(self.party, self.user_id, self, walkin_name=self.walkin_name)
            dlg.exec_()

        elif self.rb_purchase.isChecked():

            from apps.product_management_app.features.profiling.dialogs.purchase_payment_dialog import PurchasePaymentDialog
            dlg = PurchasePaymentDialog(self.party, self.user_id, self, walkin_name=self.walkin_name)
            dlg.exec_()

        else:

            from apps.product_management_app.features.profiling.dialogs.advance_settlement_dialog import AdvanceSettlementDialog
            dlg = AdvanceSettlementDialog(self.party, self.user_id, self)
            dlg.exec_()

        self.accept()