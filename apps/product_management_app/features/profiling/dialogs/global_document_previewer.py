from PySide2.QtWidgets import QMessageBox


class GlobalDocumentPreviewer:

    @staticmethod
    def open(parent, entity_type, entity_id):

        entity_type = str(entity_type).lower()

        try:

            if entity_type == "sale":

                from .sale_preview_dialog import SalePreviewDialog
                dlg = SalePreviewDialog(entity_id, parent)

            elif entity_type == "purchase":

                from .purchase_preview_dialog import PurchasePreviewDialog
                dlg = PurchasePreviewDialog(entity_id, parent)

            elif entity_type == "sale_return":

                from .sale_return_preview_dialog import SaleReturnPreviewDialog
                dlg = SaleReturnPreviewDialog(entity_id, parent)

            elif entity_type == "purchase_return":

                from .purchase_return_preview_dialog import PurchaseReturnPreviewDialog
                dlg = PurchaseReturnPreviewDialog(entity_id, parent)

            elif entity_type == "payment":

                from .payment_preview_dialog import PaymentPreviewDialog
                dlg = PaymentPreviewDialog(entity_id, parent)

            elif entity_type == "advance":

                from .advance_preview_dialog import AdvancePreviewDialog
                dlg = AdvancePreviewDialog(entity_id, parent)

            else:

                QMessageBox.information(
                    parent,
                    "Preview",
                    f"No preview available for {entity_type}"
                )
                return

            # Deliberate reprint (double-click no longer auto-prints). Inject a
            # Reprint button into whichever preview dialog opened - one place,
            # covers all document types.
            try:
                from PySide2.QtWidgets import QPushButton
                from apps.product_management_app.features.receipts_v2 import (
                    print_receipt, MODE_FULL, MODE_DELIVERY,
                )
                lay = dlg.layout()
                if lay is not None:
                    btn = QPushButton("Reprint")
                    btn.setMinimumHeight(34)
                    btn.clicked.connect(
                        lambda: print_receipt(dlg, entity_type, int(entity_id), MODE_FULL)
                    )
                    lay.addWidget(btn)

                    # Product documents can also (re)print a products-only
                    # delivery note - always available from the saved record,
                    # so a forgotten delivery note is never stuck.
                    if entity_type in ("sale", "purchase", "sale_return", "purchase_return"):
                        dbtn = QPushButton("Delivery Note")
                        dbtn.setMinimumHeight(34)
                        dbtn.setToolTip("Print a products-only delivery note for this document")
                        dbtn.clicked.connect(
                            lambda: print_receipt(dlg, entity_type, int(entity_id), MODE_DELIVERY)
                        )
                        lay.addWidget(dbtn)
            except Exception:
                pass

            dlg.exec_()

        except Exception as e:

            QMessageBox.critical(parent, "Preview Error", str(e))