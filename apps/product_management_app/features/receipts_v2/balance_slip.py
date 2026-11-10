# apps/product_management_app/features/receipts_v2/balance_slip.py
# -*- coding: utf-8 -*-
"""
Balance statement slip (80mm): what is still left on ONE document, and/or on everything still pending for the
same customer / supplier - printed in Hausa or English.

Pure text, no Qt, no database: `render_balance_slip(data, shop, lang, scope)` takes the dict produced by
`BalanceStatementService` (database/services/balance_statement_service.py).

scope:  "current" - this sale / purchase / advance only
        "total"   - everything still pending (unpaid or part-paid documents, unused advances)
        "both"    - the two, one under the other

Languages live in the one table below (STRINGS). The thermal printer prints plain ASCII (CP437), so the Hausa
hooked letters are written as their plain forms (b, d, k, y) - the way Hausa is commonly typed on a phone or a
till. Add a language by adding one more block to STRINGS; to correct a Hausa word, edit it in place.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .receipt_models import BOLD_MARKER, ShopInfo
from .receipt_render import WIDTH, _center, _fmt_dt, _rule, _wrap

SCOPE_CURRENT, SCOPE_TOTAL, SCOPE_BOTH = "current", "total", "both"
LANGUAGES = {"ha": "Hausa", "en": "English"}
DEFAULT_LANG = "ha"
MAX_LISTED = 10                 # pending documents listed one by one; the rest are counted

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        "title": "BALANCE STATEMENT",
        "name": "Name", "phone": "Phone", "date": "Date", "by": "By", "ref": "Ref",
        "this_sale": "THIS SALE", "this_purchase": "THIS PURCHASE", "this_advance": "THIS ADVANCE",
        "goods": "Goods", "total": "Total", "paid": "Paid", "balance": "BALANCE",
        "status": "Status", "paid_full": "Fully paid", "partial": "Part paid", "unpaid": "Not paid",
        "adv_out_what": "We gave you an advance", "adv_in_what": "You gave us an advance",
        "amount": "Amount", "used": "Used", "left": "LEFT",
        "adv_unused": "Not used yet", "adv_partial": "Partly used", "adv_done": "Fully used", "voided": "Cancelled",
        "against": "set against", "total_title": "TOTAL BALANCE - ALL PENDING",
        "sales_pending": "Sales not fully paid", "purchases_pending": "Purchases not fully paid",
        "adv_out_pending": "Advance we gave you, unused", "adv_in_pending": "Advance you gave us, unused",
        "subtotal": "Subtotal", "and_more": "...and {n} more", "nothing": "Nothing pending",
        "you_owe_us": "YOU OWE US", "we_owe_you": "WE OWE YOU",
        "net_you": "NET: YOU OWE US", "net_we": "NET: WE OWE YOU", "net_zero": "NET: NOTHING OWED",
        "sign": "Sign", "thanks": "Thank you for trading with us",
    },
    "ha": {
        "title": "TAKARDAR RAGOWAR KUDI",
        "name": "Suna", "phone": "Waya", "date": "Kwanan wata", "by": "Ma'aikaci", "ref": "Lamba",
        "this_sale": "WANNAN SAYARWA", "this_purchase": "WANNAN SIYAYYA", "this_advance": "WANNAN KUDIN GABA",
        "goods": "Kaya", "total": "Jimla", "paid": "An biya", "balance": "RAGOWA",
        "status": "Matsayi", "paid_full": "An gama biya", "partial": "An biya wani bangare",
        "unpaid": "Ba a biya komai ba",
        "adv_out_what": "Mun ba ku kudin gaba", "adv_in_what": "Kun ba mu kudin gaba",
        "amount": "Adadi", "used": "An yi amfani da", "left": "RAGOWA",
        "adv_unused": "Ba a yi amfani da shi ba", "adv_partial": "An yi amfani da wani bangare",
        "adv_done": "An gama amfani da shi", "voided": "An soke",
        "against": "an cire a kan", "total_title": "JIMILLAR RAGOWAR KUDI - DUK ABIN DA YA RAGE",
        "sales_pending": "Sayarwar da ba a gama biya ba", "purchases_pending": "Siyayyar da ba a gama biya ba",
        "adv_out_pending": "Kudin gaba da muka ba ku (ragowa)", "adv_in_pending": "Kudin gaba da kuka ba mu (ragowa)",
        "subtotal": "Jimla", "and_more": "...da wasu {n} kuma", "nothing": "Babu abin da ya rage",
        "you_owe_us": "KUNA BIN MU", "we_owe_you": "MUNA BIN KU",
        "net_you": "A TAKAICE: KUNA BIN MU", "net_we": "A TAKAICE: MUNA BIN KU", "net_zero": "BABU ABIN DA AKE BIN JUNA",
        "sign": "Sa hannu", "thanks": "Mun gode da hulda da mu",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    table = STRINGS.get(lang) or STRINGS[DEFAULT_LANG]
    text = table.get(key) or STRINGS["en"].get(key, key)
    return text.format(**kw) if kw else text


def _money(v) -> str:
    return "N" + f"{float(v or 0):,.2f}"


def _b(text: str) -> str:
    return f"{BOLD_MARKER}{text}{BOLD_MARKER}"


def _two(left: str, right: str) -> str:
    shown = len(left.replace(BOLD_MARKER, "")) + len(right.replace(BOLD_MARKER, ""))      # bold marks print nothing
    return left + " " * max(WIDTH - shown, 1) + right


def _day(dt) -> str:
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _status_word(lang: str, status: str) -> str:
    return {"paid": tr(lang, "paid_full"), "partial": tr(lang, "partial"), "unpaid": tr(lang, "unpaid")}.get(
        status, status)


def _current_section(d: Dict[str, Any], lang: str) -> List[str]:
    cur = d.get("current")
    if not cur:
        return []
    kind = d["kind"]
    out: List[str] = [_b(f"{tr(lang, 'this_' + kind)}: {d['ref']}")]
    if kind == "advance":
        what = tr(lang, "adv_out_what" if cur["direction"] == "out" else "adv_in_what")
        out += _wrap(what, WIDTH)
        out.append(_two(tr(lang, "amount") + ":", _money(cur["amount"])))
        out.append(_two(tr(lang, "used") + ":", _money(cur["used"])))
        out.append(_b(_two(tr(lang, "left") + ":", _money(cur["remaining"]))))
        if cur["voided"]:
            state = tr(lang, "voided")
        elif cur["used"] <= 0.005:
            state = tr(lang, "adv_unused")
        elif cur["remaining"] <= 0.005:
            state = tr(lang, "adv_done")
        else:
            state = tr(lang, "adv_partial")
        out.append(f"{tr(lang, 'status')}: {state}")
        for h in cur["history"]:
            when = _day(h["when"])
            if h["what"] == "against":
                out.append(f" - {_money(h['amount'])} {tr(lang, 'against')} {h['ref']} {when}".rstrip())
            else:
                out.append(f" - {_money(h['amount'])} {h['what']} {when}".rstrip())
        return out
    if cur["items"]:
        out += _wrap(f"{tr(lang, 'goods')}: " + ", ".join(cur["items"]), WIDTH)
    out.append(_two(tr(lang, "total") + ":", _money(cur["total"])))
    out.append(_two(tr(lang, "paid") + ":", _money(cur["paid"])))
    out.append(_b(_two(tr(lang, "balance") + ":", _money(cur["balance"]))))
    out.append(f"{tr(lang, 'status')}: {_status_word(lang, cur['status'])}")
    return out


def _doc_rows(lang: str, title_key: str, rows: List[Dict[str, Any]], money_key: str) -> List[str]:
    out: List[str] = [tr(lang, title_key) + ":"]
    for r in rows[:MAX_LISTED]:
        out.append(_two(f" {r['ref']} {_day(r['date'])}", _money(r[money_key])))
    if len(rows) > MAX_LISTED:
        out.append(" " + tr(lang, "and_more", n=len(rows) - MAX_LISTED))
    return out


def _total_section(d: Dict[str, Any], lang: str) -> List[str]:
    p = d["pending"]
    out: List[str] = [_b(_center(tr(lang, "total_title")[:WIDTH]))] if len(tr(lang, "total_title")) <= WIDTH \
        else [_b(x) for x in _wrap(tr(lang, "total_title"), WIDTH)]
    if not (p["they_owe_us"] or p["we_owe_them"]):
        out.append(_center(tr(lang, "nothing")))
        return out
    if p["sales"]:
        out += _doc_rows(lang, "sales_pending", p["sales"], "balance")
    if p["advance_out"]:
        out += _doc_rows(lang, "adv_out_pending", p["advance_out"], "remaining")
    if p["they_owe_us"]:
        out.append(_b(_two(tr(lang, "you_owe_us") + ":", _money(p["they_owe_us"]))))
        out.append("")
    if p["purchases"]:
        out += _doc_rows(lang, "purchases_pending", p["purchases"], "balance")
    if p["advance_in"]:
        out += _doc_rows(lang, "adv_in_pending", p["advance_in"], "remaining")
    if p["we_owe_them"]:
        out.append(_b(_two(tr(lang, "we_owe_you") + ":", _money(p["we_owe_them"]))))
    if p["they_owe_us"] and p["we_owe_them"]:
        out.append(_rule())
        net = p["net"]
        if net > 0.004:
            out.append(_b(_two(tr(lang, "net_you") + ":", _money(net))))
        elif net < -0.004:
            out.append(_b(_two(tr(lang, "net_we") + ":", _money(-net))))
        else:
            out.append(_b(tr(lang, "net_zero")))
    return out


def render_balance_slip(d: Dict[str, Any], shop: ShopInfo, lang: str = DEFAULT_LANG,
                        scope: str = SCOPE_BOTH) -> str:
    lang = lang if lang in STRINGS else DEFAULT_LANG
    if d.get("kind") == "party" or not d.get("current"):
        scope = SCOPE_TOTAL                         # nothing particular to show a "current" part for
    out: List[str] = []
    if shop.name:
        out.append(_b(_center(shop.name)))
    for ln in _wrap(shop.address, WIDTH) if shop.address else []:
        out.append(_center(ln))
    if shop.phone:
        out.append(_center(shop.phone))
    out += [_rule(), _b(_center(tr(lang, "title"))), _rule()]
    out.append(f"{tr(lang, 'name')}: {d.get('name') or ''}"[:WIDTH])
    if d.get("phone"):
        out.append(f"{tr(lang, 'phone')}: {d['phone']}"[:WIDTH])
    out.append(f"{tr(lang, 'date')}: {_fmt_dt(None)}")
    if d.get("served_by"):
        out.append(f"{tr(lang, 'by')}: {d['served_by']}")
    out.append(_rule())

    if scope in (SCOPE_CURRENT, SCOPE_BOTH):
        out += _current_section(d, lang)
        out.append(_rule())
    if scope in (SCOPE_TOTAL, SCOPE_BOTH):
        out += _total_section(d, lang)
        out.append(_rule())

    footer = tr(lang, "thanks") if lang != "en" else (shop.footer_note or tr(lang, "thanks"))
    out += ["", f"{tr(lang, 'sign')}: ________________", _center(footer), ""]
    return "\n".join(out)
