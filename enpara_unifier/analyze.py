"""Analiz: dedup, kart↔hesap eşleştirme, öz-transfer tespiti, tekrar eden
ödemeler ve aylık nakit akışı."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta
from statistics import mean

from .model import Transaction, SOURCE_CREDIT_CARD, SOURCE_ACCOUNT


# --- 1) Hesap kayıtlarını kaynaklar arası tekilleştir ----------------------

def dedupe_account(txns: list[Transaction]) -> list[Transaction]:
    """XLS / Hesap PDF / Özet PDF arasında çakışan hesap satırlarını teke indir.
    Anahtar: (tarih, tutar, bakiye). Bakiye en güçlü ayraçtır."""
    acc = [t for t in txns if t.source == SOURCE_ACCOUNT]
    other = [t for t in txns if t.source != SOURCE_ACCOUNT]
    best: dict[tuple, Transaction] = {}
    for t in acc:
        key = (t.date, round(t.amount, 2),
               round(t.balance, 2) if t.balance is not None else None)
        cur = best.get(key)
        if cur is None or _info_score(t) > _info_score(cur):
            best[key] = t
    return other + list(best.values())


def _info_score(t: Transaction) -> int:
    s = 0
    if t.hareket_tipi:
        s += 2
    if t.balance is not None:
        s += 1
    s += min(len(t.description), 40) // 10
    return s


# --- 2) Öz-transfer (kişinin kendi hesapları arası) tespiti ----------------

def flag_self_transfers(txns: list[Transaction], holder: str | None):
    """Gelen/Giden Transfer karşı tarafı hesap sahibiyse iç transfer say."""
    if not holder:
        return
    from .normalize import ascii_fold
    h = ascii_fold(holder)
    if not h:
        return
    for t in txns:
        if t.source != SOURCE_ACCOUNT:
            continue
        if "TRANSFER" in (t.description_ascii or "") and h in (t.description_ascii or ""):
            t.internal_transfer = True
            yon = "Gelen" if t.amount > 0 else "Giden"
            t.category = f"Öz Transfer ({yon})"


# --- 3) Kredi kartı ödemesi  <->  hesaptan çıkan ödeme eşleştirme -----------

def match_card_payments(txns: list[Transaction], day_tol: int = 4) -> int:
    """Karttaki 'Cep Şubesi' ödemesi ile hesaptaki 'kredi kartı ödemesi'ni
    tutar + (yakın) tarihe göre eşleştir; match_id ata. Eşleşme sayısı döner."""
    card_pays = [t for t in txns
                 if t.source == SOURCE_CREDIT_CARD and t.internal_transfer]
    acc_pays = [t for t in txns
                if t.source == SOURCE_ACCOUNT and t.internal_transfer
                and "KREDI KARTI ODEMESI" in (t.description_ascii or "")]
    used = set()
    n = 0
    for c in card_pays:
        cand = None
        for i, a in enumerate(acc_pays):
            if i in used:
                continue
            if abs(round(a.amount, 2)) != round(c.amount, 2):
                continue
            if abs((a.date - c.date).days) > day_tol:
                continue
            cand = i
            break
        if cand is not None:
            used.add(cand)
            mid = f"KK-{c.date:%Y%m%d}-{int(round(c.amount*100))}"
            c.match_id = mid
            acc_pays[cand].match_id = mid
            n += 1
    return n


# --- 4) Tekrar eden ödemeler / abonelikler ---------------------------------

_KEY_STRIP = re.compile(r"[^A-Z0-9 ]")


def _merchant_key(t: Transaction) -> str:
    a = _KEY_STRIP.sub(" ", t.description_ascii or "")
    # harf içeren, >1 uzunlukta token'ları al (sorgu no / tutar gibi sayısalları ele)
    toks = [w for w in a.split() if len(w) > 1 and any(c.isalpha() for c in w)][:3]
    return " ".join(toks)


def detect_recurring(txns: list[Transaction], min_months: int = 3) -> list[dict]:
    """Aynı satıcıda ≥min_months farklı ayda görünen ödemeleri 'tekrar eden'
    say. Abonelik/fatura tespiti için kullanışlı."""
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in txns:
        if t.internal_transfer or t.amount >= 0:
            continue
        key = _merchant_key(t)
        if len(key) < 3:
            continue
        groups[key].append(t)

    out = []
    for key, items in groups.items():
        months = {(t.date.year, t.date.month) for t in items}
        if len(months) < min_months:
            continue
        amts = [abs(t.amount) for t in items]
        out.append({
            "merchant": key,
            "occurrences": len(items),
            "months": len(months),
            "avg_amount": round(mean(amts), 2),
            "min_amount": round(min(amts), 2),
            "max_amount": round(max(amts), 2),
            "total": round(sum(amts), 2),
            "category": items[0].category,
            "first": min(t.date for t in items),
            "last": max(t.date for t in items),
        })
    out.sort(key=lambda d: d["total"], reverse=True)
    return out


# --- 5) Aylık nakit akışı + kategori dağılımı ------------------------------

def monthly_cashflow(txns: list[Transaction]) -> list[dict]:
    """Ay bazında gelir / gider / net. İç transferler hariç tutulur.
    Gider = hesaptan çıkanlar (kart ödemesi hariç) + kart harcamaları."""
    m = defaultdict(lambda: {"income": 0.0, "expense": 0.0,
                             "card_spend": 0.0, "n": 0})
    for t in txns:
        if t.internal_transfer:
            continue
        ym = f"{t.date.year}-{t.date.month:02d}"
        rec = m[ym]
        rec["n"] += 1
        if t.amount > 0:
            rec["income"] += t.amount
        else:
            rec["expense"] += -t.amount
            if t.source == SOURCE_CREDIT_CARD:
                rec["card_spend"] += -t.amount
    out = []
    for ym in sorted(m):
        r = m[ym]
        out.append({
            "month": ym,
            "income": round(r["income"], 2),
            "expense": round(r["expense"], 2),
            "net": round(r["income"] - r["expense"], 2),
            "card_spend": round(r["card_spend"], 2),
            "tx_count": r["n"],
        })
    return out


def category_breakdown(txns: list[Transaction]) -> list[dict]:
    """Kategori bazında toplam gider (iç transferler hariç)."""
    c = defaultdict(lambda: {"total": 0.0, "n": 0})
    for t in txns:
        if t.internal_transfer or t.amount >= 0:
            continue
        c[t.category]["total"] += -t.amount
        c[t.category]["n"] += 1
    out = [{"category": k, "total": round(v["total"], 2), "count": v["n"]}
           for k, v in c.items()]
    out.sort(key=lambda d: d["total"], reverse=True)
    return out
