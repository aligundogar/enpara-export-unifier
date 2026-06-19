"""Analiz: dedup, kart↔hesap eşleştirme, öz-transfer tespiti, tekrar eden
ödemeler ve aylık nakit akışı."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta
from statistics import mean

from .model import (Transaction, SOURCE_CREDIT_CARD, SOURCE_ACCOUNT,
                    ACC_ENPARA_VADESIZ, ACC_ENPARA_KART, ACC_GARANTI)

# Kendi ekstresi olan (iki taraflı eşleşebilen) hesaplar
DATA_ACCOUNTS = {ACC_ENPARA_VADESIZ, ACC_ENPARA_KART, ACC_GARANTI}


# --- 1) Hesap kayıtlarını HESAP BAZINDA tekilleştir ------------------------

def dedupe_account(txns: list[Transaction]) -> list[Transaction]:
    """Aynı hesabın farklı kaynaklarındaki (XLS/PDF/özet) çakışan satırlarını
    teke indir. Anahtar: (hesap, tarih, tutar, bakiye)."""
    acc = [t for t in txns if t.source == SOURCE_ACCOUNT]
    other = [t for t in txns if t.source != SOURCE_ACCOUNT]
    best: dict[tuple, Transaction] = {}
    for t in acc:
        key = (t.account, t.date, round(t.amount, 2),
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


# --- 2) Genel transfer eşleştirme (banka↔banka, kart↔hesap) ---------------

def match_transfers(txns: list[Transaction], day_tol: int = 5) -> int:
    """transfer_to'su olan işlemleri eşleştirir.

    İki tarafı da verisi olan hesaplar arası (kart↔hesap, Enpara↔Garanti):
    gönderen (negatif) tarafı transfer olarak TUTULUR, alan (pozitif) taraf
    '__skip__' ile işaretlenir (Actual karşı tarafı gönderenden üretir).
    Eşleşmeyen veri-hesabı transferleri normale düşürülür (hayalet kayıt olmasın).
    Off-budget (kişi/yatırım) hedefleri eşleştirme istemez; olduğu gibi kalır.
    """
    receivers = [t for t in txns if t.transfer_to in DATA_ACCOUNTS and t.amount > 0]
    used = set()
    n = 0
    for s in txns:
        if s.transfer_to not in DATA_ACCOUNTS or s.amount >= 0:
            continue
        for i, r in enumerate(receivers):
            if i in used:
                continue
            if r.account != s.transfer_to or r.transfer_to != s.account:
                continue
            if round(abs(s.amount), 2) != round(r.amount, 2):
                continue
            if abs((r.date - s.date).days) > day_tol:
                continue
            used.add(i)
            mid = f"TR-{s.date:%Y%m%d}-{int(round(abs(s.amount) * 100))}"
            s.match_id = r.match_id = mid
            r.transfer_to = "__skip__"      # alan taraf atlanır
            n += 1
            break

    # eşleşmeyen veri-hesabı transferleri → normale düşür (karşı taraf veride yok)
    for t in txns:
        if t.transfer_to in DATA_ACCOUNTS and not t.match_id:
            _fallback_category(t)
            t.transfer_to = None
    return n


def _fallback_category(t: Transaction):
    d = t.description_ascii or ""
    if "KREDI KARTI ODEMESI" in d or "CEP SUBESI" in d:
        t.category = "Kredi Kartı Ödemesi"
    else:
        t.category = "Öz Transfer (Gelen)" if t.amount > 0 else "Öz Transfer (Giden)"
    t.internal_transfer = True


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
