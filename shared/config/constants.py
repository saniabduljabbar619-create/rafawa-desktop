LEDGER_CROPS = "crops"
LEDGER_FERTILIZERS = "fertilizers"
LEDGERS = [LEDGER_CROPS, LEDGER_FERTILIZERS]


def norm_ledger(x: str) -> str:
    s = (x or "").strip().lower()
    if s in ("fertilizer", "fertilizers"):
        return LEDGER_FERTILIZERS
    if s in ("crops", "crop"):
        return LEDGER_CROPS
    if s in ("money", "cash"):
        return "money"
    return LEDGER_CROPS  # safe default
