from .role import Role
from .user import User
from .party import Party

from .category import Category
from .product import Product

from .purchase import Purchase
from .purchase_item import PurchaseItem
from .purchase_item_bag import PurchaseItemBag
from .purchase_return import PurchaseReturn        # 🚀 Added to fix the ImportError

from .sale import Sale
from .sale_item import SaleItem
from .sale_item_bag import SaleItemBag
from .sale_return import SaleReturn         # was stale-commented-out here; SaleReturn has been in real use throughout
from .sale_discount import SaleDiscount     # per-line manual discount adjustments (Payments section)

from .stock_movement import StockMovement
from .payment import Payment
from .advance import Advance
from .advance_allocations import AdvanceAllocation # ⚡ Streamlined to use relative import style

from .cashier_settlement import CashierSettlement
from .cash_shortage import CashShortage             # cash-handling shortage/overpayment corrections
from .purchase_price_adjustment import PurchasePriceAdjustment  # after-the-fact purchase price corrections
from .ledger_settlement_marker import LedgerSettlementMarker    # "everything settled" milestone markers
from .driver import Driver                                      # reusable driver record for Sales/Purchase Order dispatch
from .company import Company                                     # Sales Order buyer - never a customer, isolated from Party
from .order_payment import OrderPayment                          # shared payment table for Sales/Purchase Orders
from .purchase_order_supplier import PurchaseOrderSupplier        # PO supplier - isolated from Party, mirroring Company
from .purchase_order import PurchaseOrder                         # Purchase Order header
from .purchase_order_item import PurchaseOrderItem                 # Purchase Order line items
from .sync_state import SyncState                                # cloud sync - per-table progress cursor
from .sync_log import SyncLog                                    # cloud sync - audit trail of every attempt
from .deletion_log import DeletionLog                            # deletion logs
from .sales_order import SalesOrder                 # Sales Order module
from .sales_order_item import SalesOrderItem        # must be imported here so SQLAlchemy's
                                                     # class registry can resolve SalesOrder's
                                                     # relationship("SalesOrderItem") string
                                                     # reference at first-query time
from .weigh_ticket import WeighTicket, WeighTicketLine, WeighRound   # weigher workspace: delivery weighing tickets
from .quick_purchase_order import QuickPurchaseOrder, QuickPurchaseLine, QuickPurchaseRoute   # saved Quick Purchase orders (draft/pending/completed + routing)
from .expense import Expense                          # Debt Center: business expenses
from .document_offset import DocumentOffset           # purchase <-> sale offsets (swap / settle with goods)
from .expenditure import Expenditure                  # Debt Center: larger outlays, separate from Expenses
from .debt import DebtPerson, DebtEntry, DebtPayment               # Debt Center: standalone creditor/debtor book
from .order_review import OrderReview                 # order review notes and post-delivery corrections
