# Architecture

```
GUI (PySide2)            apps/product_management_app/features/<area>/{views,dialogs}
   |  calls
Services (business rules) database/services/*          <- all money/stock/permission logic, unit-tested
   |  uses
Models (SQLAlchemy)       database/models/*  + Alembic migrations/
   |
MySQL (SQLite in tests)
```

Rules of thumb
- **No business logic in views.** A view gathers input and shows results; calculations and
  validation live in a service so a test can reach them without a display.
- **One definition per concept.** "What does this document still owe?" is
  `services/document_balance.py`; stock on hand is `StockMovement` + `STOCK_IN/OUT_TYPES`;
  the shared walk-in party is `shared.config.constants.WALKIN_PARTY_ID`.
- **Append-only history.** Voids keep the row (`status="voided"`) and reverse stock with
  offsetting movements; every void/delete/settlement is also written to `audit_logs`.
- **Permissions in services** (`shared/auth/permissions.py`), not only in the UI.
- **Time is local** (`datetime.now()`), matching the database `NOW()` defaults.

Drawer engine (`services/drawer/`): `compute.py` (per-cashier drawer), `ledger.py`
(cross-cashier settlement ledger), `lists.py` (party totals / transaction lists / search).
`services/drawer_service.py` remains as the import path the rest of the app uses.


## Workspaces and roles

| Role | Opens | Can |
|---|---|---|
| `admin` / `pm` | Product Management, Cashier, Weigher | everything |
| `cashier` | Cashier only | sales, payments, **Weigh Tickets** (verify, price, finalize) |
| `weigher` | Weigher only | register/select a party, weigh rounds, set the price per kg, print the slip, edit/remove their own weighings (Finalize List) |

`shared/auth/permissions.py` holds the role sets (`WEIGH_ROLES`, `INTAKE_ROLES`) and
`workspace_allowed()`; the launcher and `main.open_workspace` both use it.

**Weigher flow** (`services/weigh_service.py`, models in `models/weigh_ticket.py`, screens in
`features/weigher/`):

```
weigher:        register/select party -> product/area/grade -> record rounds (1-3 or partial bags + kg)
                -> auto totals -> PRICE per kg for each product (usual price filled in, editable; typed
                where there is none) -> choose slip (full/summary) -> submit -> print & sign
admin/cashier:  Weigh Tickets -> Open Intake -> verify & edit rounds -> confirm (or change) the price per kg
                -> amount = kg x price -> Finalize (creates the Purchase) -> payment -> receipt
weigher later:  Finalize List -> every weighing with its amount and status -> Edit (rounds, prices) /
                Remove (cancel, kept in the history) / Print Slip - until the cashier finalizes it
```

The weigher has **no authority over payment or finalization** - the service refuses those calls for the
role. The price they set is a proposal: a ticket cannot be submitted without a price on every weighed line,
the slip shows it, and the cashier confirms or changes it at finalization. A weigher may edit or remove
their own ticket while it is `open` or `submitted`; edits after submitting are audited. A ticket is `open`
(being weighed) -> `submitted` (awaiting the cashier) -> `finalized` (a `Purchase` exists; frozen) or `cancelled`.
Finalizing takes a row lock on the ticket so two terminals cannot book it twice. Corrections made
at intake are written to `audit_logs`.


## Debt Center (admin) - a standalone book

`features/debt_center/`, styled like alg-erp's Master Supplier sheets (cream paper, ink totals row, amber).
Hub tiles (double-click): **My Creditors**, **My Debtors**, **Expenses & Expenditures**.

- **Standalone.** `models/debt.py` (`debt_people`, `debt_entries`, `debt_payments`) references no party, customer, supplier,
  sale, purchase or payment, and nothing else reads it: recording a debt here changes nothing elsewhere.
  Any number of people; one sheet each: Date | What it was for | amount | Note | paid so far | row still unpaid |
  running balance, dark totals row. Type in the empty last row to add a debt; click a cell to change it
  (audited; the amount cannot go below what is already paid). Payments are NOT typed over the row: the
  **Payments** tab (tabs along the bottom) tracks every record: pick the record, type the amount, Save - each
  payment is its own row (`debt_payments`), capped at what the record still owes, crossed out rather than
  deleted; the rest is paid later the same way. Paying 30m then 10m on a 50m debt gives 40m paid; remove crosses a
  row out (never deleted) and it can be brought back. `services/debt_book_service.py`, admin only.
- **Expenses vs Expenditures** are two separate tables and sheets (tabs along the bottom), as in alg-erp:
  Expenses = day-to-day running costs (`expenses`, EXP-), Expenditures = larger outlays (`expenditures`,
  EXD-). `services/expense_service.py` serves both. They are not linked to the cashier drawer.
- Migrations: `0005` expenses, `0007` debt book + expenditures, `0009` debt payments (old "paid" amounts move across).

## Offsets and swaps (settle with goods)

`services/offset_service.py`, model `document_offsets` (migration `0006`).

- **Settle unpaid sales with a purchase.** After a purchase from a party who still owes us, the intake
  asks "settle their unpaid sales with this?" (never automatic). `OffsetService.apply` sets the purchase
  against their open sales, oldest first on both sides. What is left is paid/collected as usual.
- **Swap.** Cashier Purchase -> *Swap / Exchange*: goods he brings (purchase) and goods we give him
  (sale) are recorded together by `SwapService.record` and set against each other; only the
  difference is left owing either way. If the second document fails the first is voided.
- **Swap from a customer profile.** Besides Activity/Crops/..., a registered customer's profile has one two-sided
  **Sales/Purchases** tab (Purchases on the left, Sales on the right, a line between; one row per document with a
  tick box on EVERY row - paid or not - and a Flag column/banner saying what ticking it means, e.g. "already paid
  - adds nothing to set off"; multi-select on both sides, one Swap button),
  **Products** (bought from / sold to him per product) and **Transaction history** (everything in date order
  with a running position) - `services/customer_records_service.py`, read-only. *Swap with ticked ->* opens
  the swap page (`profiling/dialogs/swap_records_dialog.py`), which opens straight onto the right workspace -
  purchases ticked -> the Sale workspace, sales ticked -> the Purchase workspace, both -> both: the ticked purchases (we owe him) and sales
  (he owes us) are the starting point; goods we give him are ticked from the shelf by category; ticking (or double-clicking) a product opens the
  checkout's own `SaleItemDialog` for quantity, by-bag mode, variant and price (the product's selling price is
  filled in and editable; with none the operator types it, and a line without a price is refused), goods he brings are added line by line; both sides are totalled and
  `SwapService.record_from_records` records the new documents and sets everything off, oldest first; if the two
  sides match it is settled, otherwise the rest stays open on the documents (reported from the real
  balances afterwards). Voided documents are hidden on the tabs unless *Show voided* is ticked. A
  paid record can be ticked but contributes nothing; nothing is written unless both sides have value, and the documents it
  created are voided if a later step fails.
- **Net off.** `OffsetService.apply(party_id, kind="net_off")` cancels the overlap for a party we both owe and are owed by (service only; no screen).
- The payment screens (Purchase Payment / Sale Payment dialogs, price adjustments, receipts, user activity) read
  "what is still owed" from the same shared definition (`document_balance`), through
  `SaleAdjustmentService._sale_balance` / `PurchaseAdjustmentService._purchase_balance`, so a swap-settled
  document shows its reduced balance (or nothing) there and voided documents are never listed.
- **The walk-in id.** Party id 11 is reserved for the shared anonymous walk-in account, but ids are handed out by
  the database, so a real customer can end up with id 11. `services/walkin_account.is_shared_walkin` therefore
  checks the row's type (not just the number): a registered party with that id is a real party (swaps and
  set-offs work, its documents are not flagged as walk-ins), and an anonymous sale is refused rather than posted
  to that customer when no real walk-in account exists.
- An offset moves no cash (drawer untouched). `document_balance` counts it as a settlement, so every
  balance screen agrees; a voided counterpart releases it, and an admin can reverse one (audited).

## Orders (unified)

One sidebar entry, **Orders** (`features/orders/views/orders_hub.py`), in the Product Management and
Cashier workspaces. Tabs: **Tracking board** (every Sales and Purchase Order: status, days on the road,
overdue delivery, value, payment balance; double-click opens the order, *Track history* shows who did
what and when), **Sales Orders** (companies) and **Purchase Orders** (suppliers).

Companies and PO suppliers stay two tables (their orders keep their own foreign keys) but are ONE pool:
`OrderHubService.sync_partners()` copies any partner missing on the other side (matched by name), and
both `create()` services call it, so a partner added on one side shows in the other side's picker.
Details are copied at creation; later edits to a phone/address are not kept in step.


## Orders as sheets

Sales Orders and Purchase Orders share ONE sheet-based workspace (`features/orders/views/order_sheet_page.py`),
opened from Orders -> partner profile -> order, in the same style as the Debt Center sheets
(`debt_center/views/sheet_grid.py`). The old per-action dialogs were removed.

- **Order lines tab**: draft = type lines into the last row (product, bags, kg for crops, price; saved as soon as
  complete, no stock moves); on the road = enter what the other side received / rejected and the price (kept as
  you type, accepted and amount are automatic, "Fill from order" for a clean delivery); confirmed = final figures.
- **Payments tab**: type a payment into the last row; before confirmation it is an advance; remove = void with a
  reason. **History tab**: who did what and when.
- A stage strip (Draft -> Dispatched/In transit -> Confirmed -> Paid) and KPIs show where the order is.
- `services/order_sheet_service.py` is a thin normalising layer over `SalesOrderService` / `PurchaseOrderService`
  (the rules - stock, validation, totals - stay there). Draft/dispatch/revert/cancel are admin-only; the delivery
  result, confirmation and payments are open to admin and cashier.

### Moving an order along, review notes and post-delivery corrections

- The stage strip has one **next step** button with a plain-language hint (Draft -> Dispatch / Mark in transit ->
  Confirm delivery / receipt -> Record a payment). A draft can sit for days; notes and the expected date can change.
- **Review notes** (`order_reviews`, migration `0008`): at ANY stage, type a note in the History sheet's last row
  (Note, Delay, Damaged goods, Shortage, Wrong count, Price issue, Other). Never edited or deleted.
- **Correct a line after delivery** (admin; `OrderSheetService.correct_line`): on a CONFIRMED order change a line's
  rejected quantity and/or price with a required reason. The line, the order total and (optionally, with a stock
  check first) one stock movement are updated together and logged with before/after. Payments are not touched: an
  overpayment is shown ("Overpaid - refund or void a payment"), never hidden.
