LEDGER_CROPS = "crops"
LEDGER_FERTILIZERS = "fertilizers"
LEDGERS = [LEDGER_CROPS, LEDGER_FERTILIZERS]

# Shared anonymous "walk-in" party (parties.id) that sales/purchases with no
# registered counterparty are filed under. Single source of truth - never
# hardcode the number elsewhere.
WALKIN_PARTY_ID = 11


def norm_ledger(x: str) -> str:
    s = (x or "").strip().lower()
    if s in ("fertilizer", "fertilizers"):
        return LEDGER_FERTILIZERS
    if s in ("crops", "crop"):
        return LEDGER_CROPS
    if s in ("money", "cash"):
        return "money"
    return LEDGER_CROPS  # safe default
