from PySide2.QtWidgets import QWidget,QVBoxLayout,QTableWidget,QTableWidgetItem
from database.services.walkin_service import WalkinService


class WalkinProfileView(QWidget):

    def __init__(self, walkin_name):
        super().__init__()

        self.walkin_name = walkin_name

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels([
            "Trader","Sales","Purchases"
        ])

        layout.addWidget(self.table)

        self.refresh()


    def refresh(self):

        rows = WalkinService.list_traders()

        self.table.setRowCount(0)

        for r in rows:

            if r["name"] != self.walkin_name:
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)

            self.table.setItem(row,0,QTableWidgetItem(r["name"]))
            self.table.setItem(row,1,QTableWidgetItem(str(r["sales"])))
            self.table.setItem(row,2,QTableWidgetItem(str(r["purchases"])))