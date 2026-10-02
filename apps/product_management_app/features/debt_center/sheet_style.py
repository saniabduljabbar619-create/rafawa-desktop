# apps/product_management_app/features/debt_center/sheet_style.py
"""
The look of the Debt Center: the same warm "exercise book" sheets as alg-erp's Master Supplier workspace
(cream paper, ink-blue totals row, amber accent). Colours and object names follow alg-erp so the two apps
feel like one product.
"""
INK, MUTED, FAINT = "#1c2333", "#4a5266", "#8a8578"
GREEN, AMBER, RED, BLUE, PURPLE = "#3f6b4a", "#b8792e", "#a23b34", "#2f6f9f", "#6d4c8f"

STYLE = """
QWidget#dcRoot { background: #f4f2ec; }
QLabel { background: transparent; }
QLabel#dcTitle { color: #1c2333; font-size: 21px; font-weight: 800; }
QLabel#dcSub { color: #4a5266; font-size: 12px; }
QFrame#msKpi { background: #ffffff; border: 1px solid #e4e0d6; border-radius: 10px; }
QFrame#msKpi QLabel { background: transparent; }
QFrame#msKpiDark { background: #1c2333; border-radius: 10px; }
QFrame#msKpiDark QLabel { background: transparent; color: #ffffff; }
QLabel#msKpiVal { color: #1c2333; font-size: 22px; font-weight: 800; }
QLabel#msKpiCap { color: #4a5266; font-size: 12px; }
QFrame#msKpiDark QLabel#msKpiCap { color: #c7cddc; }
QLabel#msHint { color: #4a5266; font-size: 12px; }
QTableWidget#msSheet { background: #ffffff; border: 1px solid #e4e0d6; gridline-color: #ebe7de; font-size: 13.5px; }
QTableWidget#msSheet::item { padding: 4px 8px; }
QTableWidget#msSheet::item:selected { background: #f6ead9; color: #1c2333; }
QTableWidget#msSheet QHeaderView::section { background: #faf8f4; border: none; border-right: 1px solid #ebe7de;
    border-bottom: 1px solid #e4e0d6; padding: 6px 8px; font-weight: 700; color: #1c2333; }
QTableWidget#msFoot { background: #1c2333; color: #ffffff; border: none; font-weight: 700; font-size: 13.5px; }
QTableWidget#msFoot::item { padding: 4px 8px; }
QTableWidget#dcList { background: #ffffff; border: 1px solid #e4e0d6; gridline-color: transparent; font-size: 13.5px; }
QTableWidget#dcList::item { padding: 6px 10px; border-bottom: 1px solid #f1eee7; }
QTableWidget#dcList::item:selected { background: #f6ead9; color: #1c2333; }
QTableWidget#dcList QHeaderView::section { background: #faf8f4; border: none; border-bottom: 1px solid #e4e0d6;
    padding: 8px 10px; font-weight: 700; color: #1c2333; }
QTabBar#msSheets::tab { background: #ece8df; color: #4a5266; padding: 8px 18px; border: 1px solid #e4e0d6;
    border-top: none; border-bottom-left-radius: 7px; border-bottom-right-radius: 7px; font-weight: 600; margin-right: 2px;
    min-width: 110px; }
QTabBar#msSheets::tab:selected { background: #ffffff; color: #1c2333; border-top: 3px solid #b8792e; }
QPushButton#msBig { background: #b8792e; color: #ffffff; border: none; border-radius: 7px; padding: 8px 14px;
    font-weight: 700; font-size: 13.5px; }
QPushButton#msPay { background: #2f6b45; color: #ffffff; border: none; border-radius: 7px; padding: 8px 14px;
    font-weight: 700; font-size: 13.5px; }
QPushButton#msBig:hover { background: #9c6425; }
QPushButton#msPay:hover { background: #26583a; }
QPushButton#dcGhost { background: #ffffff; color: #1c2333; border: 1px solid #d9d4c6; border-radius: 7px;
    padding: 8px 14px; font-weight: 700; }
QPushButton#dcGhost:hover { border-color: #b9b3a4; }
QPushButton#dcBack { background: transparent; color: #4a5266; border: none; font-weight: 700; padding: 6px 4px; }
QPushButton#dcBack:hover { color: #1c2333; }
QLineEdit#dcSearch { background: #ffffff; border: 1px solid #d9d4c6; border-radius: 7px; padding: 7px 10px; }
QFrame#dcTile { background: #ffffff; border: 1px solid #e4e0d6; border-radius: 14px; }
QFrame#dcTile:hover { border-color: #b9b3a4; }
QFrame#dcTile QLabel { background: transparent; }
"""
