# apps/product_management_app/workspaces.py
from dataclasses import dataclass
from typing import Dict, Callable, List

from PySide2.QtWidgets import QWidget, QLabel
from shared.uix.layout.sidebar import SidebarItem


@dataclass(frozen=True)
class WorkspaceSpec:
    key: str
    window_title: str
    sidebar_title: str
    menu: List[SidebarItem]
    routes: Dict[str, Callable[[], QWidget]]
    default_route: str
    title_map: Dict[str, str]


def _current_admin_user_id():
    """
    Resolves the logged-in user's id for features (like the admin
    dashboard's row-delete audit trail) that need to know WHO is acting,
    not just that someone is logged in. `open_workspace()` in main.py
    already calls `require_user_id()` before a workspace is ever opened,
    so by the time this factory runs a session is guaranteed to exist —
    this just re-reads it. Falls back to None (delete buttons still
    render but deletion is blocked with a clear message) rather than
    raising, since a route factory failing here would break the whole
    workspace over what should only affect one feature's delete action.
    """
    try:
        from shared.auth.session import require_user_id
        return int(require_user_id() or 0) or None
    except Exception:
        return None


def build_workspaces() -> Dict[str, WorkspaceSpec]:
    # Implemented views (only import what exists today)
    from apps.product_management_app.features.catalog.views.products_list import ProductsListView
    from apps.product_management_app.features.catalog.catalog_view import CatalogView
    from apps.product_management_app.features.profiling.views.customers_list import CustomersListView
    from apps.product_management_app.features.profiling.views.walkins_list import WalkinsListView
    from apps.product_management_app.features.suppliers.views.suppliers_list import SuppliersListPage
    from apps.product_management_app.features.cashier.views.cashier_payments import CashierPaymentsView
    from apps.product_management_app.features.cashier.views.cashier_sales import CashierSalesView
    from apps.product_management_app.features.cashier.views.cashier_intake import CashierIntakeView
    from apps.product_management_app.features.cashier.views.quick_purchases import QuickPurchasesView
    from apps.product_management_app.features.profiling.views.admin_returns import AdminReturnsView
    from apps.product_management_app.features.admin_users.views.users_list import UsersListView
    from apps.product_management_app.features.admin_dashboard.views.admin_dashboard import AdminDashboardView
    from apps.product_management_app.features.cashier.views.cashier_daily_summary import CashierDailySummaryView
    from apps.product_management_app.features.orders.views.orders_hub import OrdersHubView
    from apps.product_management_app.features.business_history.views.cashier_list_view import CashierHistoryListView
    from apps.product_management_app.features.reports.views.reports_view import ReportsView
    from apps.product_management_app.features.weigher.views.weigher_station import WeigherStationView
    from apps.product_management_app.features.weigher.views.weigher_tickets import WeigherTicketsView
    from apps.product_management_app.features.weigher.views.weigh_tickets_admin import WeighTicketsAdminView
    from apps.product_management_app.features.debt_center.views.debt_center import DebtCenterView

    # 👑 THE ANCHOR BLUEPRINTS: Import the completed executive analytics and valuation dashboards
    from apps.product_management_app.features.catalog.views.product_profit_view import ProductProfitView
    from apps.product_management_app.features.catalog.views.product_capital_view import ProductCapitalView
    from apps.product_management_app.features.catalog.views.product_inventory_capital_view import ProductInventoryCapitalView

    def placeholder(text: str) -> QWidget:
        w = QLabel(text)
        w.setStyleSheet("font-size: 14px; padding: 16px;")
        return w

    pm = WorkspaceSpec(
        key="pm",
        window_title="Rafawa – Product Management",
        sidebar_title="Product Management",
        menu=[
            SidebarItem(label="Dashboard", route="admin.dashboard"),
            SidebarItem(label="Profiles", children=[
                SidebarItem(label="Customers", route="profiles.customers"),
                SidebarItem(label="Suppliers", route="profiles.suppliers"),
                SidebarItem(label="Walk-ins", route="profiles.walkins"),
            ]),
            SidebarItem(label="Catalog", children=[
                SidebarItem(label="Products", route="catalog.products"),
                SidebarItem(label="Sales", route="catalog.sales"),
                SidebarItem(label="Profits", route="catalog.profits"),
            ]),
            SidebarItem(label="Debt Center", route="admin.debt_center"),
            SidebarItem(label="Weigh Tickets", route="admin.weigh_tickets"),
            SidebarItem(label="Quick Purchases", route="admin.quick_purchases"),
            SidebarItem(label="Returns", route="admin.returns"),
            SidebarItem(label="Orders", route="admin.orders"),
            SidebarItem(label="Business History", route="admin.business_history"),
            SidebarItem(label="Reports", route="admin.reports"),
            SidebarItem(label="Users", route="admin.users"),
        ],
        routes={
            "admin.dashboard": lambda: AdminDashboardView(
                current_user_id=_current_admin_user_id()
            ),
            "profiles.customers": lambda: CustomersListView(),
            "profiles.suppliers": lambda: SuppliersListPage(),
            "profiles.walkins": lambda: WalkinsListView(mode="admin"),
            "catalog.products": lambda: CatalogView(),
            "catalog.sales": lambda: CashierSalesView(),
            "catalog.profits": lambda: ProductProfitView(),
            "catalog.capital": lambda: ProductCapitalView(),
            "catalog.live_valuation": lambda: ProductInventoryCapitalView(),
            "admin.debt_center": lambda: DebtCenterView(),
            "admin.weigh_tickets": lambda: WeighTicketsAdminView(mode="admin"),
            "admin.quick_purchases": lambda: QuickPurchasesView(),
            "admin.returns": lambda: AdminReturnsView(),
            "admin.orders": lambda: OrdersHubView(mode="admin"),
            "admin.business_history": lambda: CashierHistoryListView(),
            "admin.reports": lambda: ReportsView(),
            "admin.users": lambda: UsersListView(),
        },
        default_route="admin.dashboard",
        title_map={
            "admin.dashboard": "Admin • Dashboard",
            "profiles.customers": "Profiles • Customers",
            "profiles.suppliers": "Profiles • Suppliers",
            "profiles.walkins": "Profiles • Walk-ins",
            "catalog.products": "Catalog • Products",
            "catalog.sales": "Catalog • Sales",
            "catalog.profits": "Catalog • Realized Profit & Margins",
            "admin.debt_center": "Debt Center",
            "admin.weigh_tickets": "Weigh Tickets",
            "admin.quick_purchases": "Quick Purchases",
            "admin.returns": "Returns",
            "admin.orders": "Orders",
            "admin.business_history": "Aggregated Business History",
            "admin.reports": "Reports",
            "admin.users": "Admin • Users",
        },
    )

    cashier = WorkspaceSpec(
        key="cashier",
        window_title="Rafawa – Cashier",
        sidebar_title="Cashier",
        menu=[
            SidebarItem(label="Sales (Checkout)", route="cashier.sales"),
            SidebarItem(label="Weigh Tickets", route="cashier.weigh_tickets"),
            SidebarItem(label="Cashier Purchase", route="cashier.intake"),
            SidebarItem(label="Quick Purchases", route="cashier.quick_purchases"),
            SidebarItem(label="Payments", route="cashier.payments"),
            SidebarItem(label="Profiles", children=[
                SidebarItem(label="Customers", route="cashier.customers"),
                SidebarItem(label="Suppliers", route="cashier.suppliers"),
                SidebarItem(label="Walk-ins", route="cashier.walkins"),
            ]),
            SidebarItem(label="Returns", route="cashier.returns"),
            SidebarItem(label="Orders", route="cashier.orders"),
            SidebarItem(label="Receipts", route="cashier.receipts"),
            SidebarItem(label="Daily Summary", route="cashier.daily_summary"),
        ],
        routes={
            "cashier.sales": lambda: CashierSalesView(),
            "cashier.weigh_tickets": lambda: WeighTicketsAdminView(mode="cashier"),
            "cashier.intake": lambda: CashierIntakeView(),
            "cashier.quick_purchases": lambda: QuickPurchasesView(),
            "cashier.payments": lambda: CashierPaymentsView(),
            "cashier.customers": lambda: CustomersListView(mode="cashier"),
            "cashier.suppliers": lambda: SuppliersListPage(),
            "cashier.walkins": lambda: WalkinsListView(mode="cashier"),
            "cashier.returns": lambda: AdminReturnsView(mode="cashier"),
            "cashier.orders": lambda: OrdersHubView(mode="cashier"),
            "cashier.daily_summary": lambda: CashierDailySummaryView(),
        },
        default_route="cashier.sales",
        title_map={
            "cashier.sales": "Sales (Checkout)",
            "cashier.weigh_tickets": "Weigh Tickets",
            "cashier.intake": "Cashier Purchase",
            "cashier.quick_purchases": "Quick Purchases",
            "cashier.payments": "Payments",
            "cashier.customers": "Customers",
            "cashier.suppliers": "Suppliers",
            "cashier.walkins": "Walk-ins",
            "cashier.returns": "Returns",
            "cashier.orders": "Orders",
            "cashier.daily_summary": "Daily Summary",
        },
    )

    weigher = WorkspaceSpec(
        key="weigher",
        window_title="Rafawa – Weigher",
        sidebar_title="Weigher",
        menu=[
            SidebarItem(label="Weighing Station", route="weigher.station"),
            SidebarItem(label="Finalize List", route="weigher.finalize_list"),
        ],
        routes={
            "weigher.station": lambda: WeigherStationView(),
            "weigher.finalize_list": lambda: WeigherTicketsView(),
        },
        default_route="weigher.station",
        title_map={
            "weigher.station": "Weighing Station",
            "weigher.finalize_list": "Finalize List",
        },
    )

    return {"pm": pm, "cashier": cashier, "weigher": weigher}