from PySide2.QtWidgets import QWidget, QVBoxLayout, QTabWidget

from apps.product_management_app.features.catalog.views.products_list import ProductsListView
from apps.product_management_app.features.catalog.views.product_capital_view import ProductCapitalView
from apps.product_management_app.features.catalog.views.product_inventory_capital_view import ProductInventoryCapitalView


class CatalogView(QWidget):
    """
    Enterprise Catalog Container

    Tabs:
        - Products (CRUD + Stock)
        - Capital Overview (Analytics)
    """

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.tabs = QTabWidget()

        self.products_tab = ProductsListView()
        self.capital_tab = ProductCapitalView()
        self.inventory_capital_tab = ProductInventoryCapitalView()


        self.tabs.addTab(self.products_tab, "Products")
        self.tabs.addTab(self.capital_tab, "Capital Overview")
        self.tabs.addTab(self.inventory_capital_tab, "Live Inventory Capital")

        layout.addWidget(self.tabs)