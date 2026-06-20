"""Karşı-taraf yönlendirme motoru (GENEL — kişisel veri içermez).

Bir işlemin karşı tarafına göre nasıl ele alınacağını belirler:
  • gelir       → işveren/müşteri = GELİR kategorisi
  • transfer    → kendi başka bankan / kişi (alacak-verecek) / yatırım hesabı
  • (eşleşmezse) → normal kategorize (gider)

KİŞİSEL kurallar (isimler, işveren, borçlar) bu dosyada DEĞİL; gitignore'lu
`rules.local.json` dosyasında tutulur (şablon: `rules.example.json`). Böylece
araç public repoda paylaşılabilir, kişisel veri sızmaz.

Eşleştirme ASCII'ye katlanmış BÜYÜK harf açıklama üzerinde yapılır.
"""

from __future__ import annotations

import json
import os

from .normalize import ascii_fold
from .model import ACC_ENPARA_VADESIZ, ACC_GARANTI

# 'Diğer Kişiler' çöp kutusuna düşmeyi engelleyen kurum/işlem belirteçleri (genel)
_NON_PERSON = [
    "LTD", "A.S", "TIC", " SAN", "MARKET", "GIDA", "ELEKTRON", "ENERJI",
    "KESINTI", "VERGI", "KREDI", "MOKA", "IYZICO", "PARAM", "PAYCELL",
    "HEPSI", "GOOGLE", "FATURA", "BANKASI", "BILISIM", "TEKNOLOJI", "YAZILIM",
    "ATM", "PARA YATIRMA", "MENKUL", "SIGORTA", "ODEME HIZMET", "ANONIM",
]

# Catch-all kişisel transfer hedefi (her zaman tanımlı olmalı)
ACC_DIGER = "person:diger"

# --- yerel kuralları yükle --------------------------------------------------

def _load_rules() -> dict:
    """rules.local.json (yoksa rules.example.json) yükler."""
    base = os.path.dirname(os.path.dirname(__file__))   # paket üst klasörü
    for fname in ("rules.local.json", "rules.example.json"):
        p = os.environ.get("ENPARA_RULES") if fname == "rules.local.json" else None
        p = p or os.path.join(base, fname)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    return {}


_R = _load_rules()

INCOME_RULES = [tuple(x) for x in _R.get("income", [])]          # [(anahtar, kategori)]
# Sadece GELEN (amount>0) iken gelir sayılanlar (ör. tahsilat platformu = iş geliri)
INCOME_INCOMING = [tuple(x) for x in _R.get("income_incoming", [])]
TRANSFER_RULES = [tuple(x) for x in _R.get("transfers", [])]     # [(anahtar, hesap)]
SELF_PATTERNS = _R.get("holder_patterns", [])                   # kendi adın (öz-transfer)
OFFBUDGET_ACCOUNTS = {k: tuple(v) for k, v in _R.get("offbudget_accounts", {}).items()}
MANUAL_BALANCES = {k: tuple(v) for k, v in _R.get("manual_balances", {}).items()}

# Diğer Kişiler her zaman tanımlı olsun
OFFBUDGET_ACCOUNTS.setdefault(ACC_DIGER, ("Diğer Kişiler (Borç/Alacak)", "kisi"))


def _is_self(desc_ascii: str) -> bool:
    return any(p in desc_ascii for p in SELF_PATTERNS)


def income_incoming(desc_ascii: str) -> str | None:
    """Yalnızca para GELİRKEN gelir sayılan karşı-taraflar (ör. tahsilat platformu)."""
    for pat, cat in INCOME_INCOMING:
        if pat in desc_ascii:
            return cat
    return None


def party_name(description: str) -> str:
    """Açıklamadan karşı tarafın temiz adını çıkarır.
    'AD SOYAD-FAST-...' / 'AD SOYAD, Bireysel Ödeme' → 'Ad Soyad'."""
    s = description or ""
    for sep in ("-", ","):
        i = s.find(sep)
        if i > 0:
            s = s[:i]
    return " ".join(s.split()).strip()


def party_key(name: str) -> str:
    """Ada göre kararlı off-budget hesap anahtarı: 'person:ad_soyad'."""
    slug = ascii_fold(name).lower()
    slug = "".join(c if c.isalnum() else "_" for c in slug).strip("_")
    slug = "_".join(p for p in slug.split("_") if p)[:40]
    return "person:" + slug if slug else ACC_DIGER


def is_transfer_like(desc_ascii: str, hareket_tipi: str | None) -> bool:
    """Kişiye/kişiden havale/FAST/EFT gibi bir transfer mi (kurum değil)."""
    ht = ascii_fold(hareket_tipi or "")
    is_tr = ("TRANSFER" in ht or "PARA TRANSFERI" in ht
             or "FAST" in desc_ascii or "EFT" in desc_ascii
             or "BIREYSEL ODEME" in desc_ascii or "HAVALE" in desc_ascii)
    if not is_tr:
        return False
    return not any(tok in desc_ascii for tok in _NON_PERSON)


def looks_personal(desc_ascii: str, hareket_tipi: str | None) -> bool:
    """Transfer + çıkarılabilir KİŞİ ADI var (→ kişiye özel hesap)."""
    if not is_transfer_like(desc_ascii, hareket_tipi):
        return False
    name = desc_ascii.split("-")[0].split(",")[0].strip()
    words = [w for w in name.split() if w.isalpha() and len(w) > 1]
    return len(words) >= 2


def route(desc_ascii: str, account_key: str, hareket_tipi: str | None) -> dict:
    """Açık kurallar: {category?, is_income?, transfer_to?}. Kişi/Diğer yönlendirme
    consolidate._route_txn'de (strict-kategorize sonrası) yapılır."""
    for pat, cat in INCOME_RULES:
        if pat in desc_ascii:
            return {"category": cat, "is_income": True}
    for pat, target in TRANSFER_RULES:
        if pat in desc_ascii:
            return {"transfer_to": target}
    if _is_self(desc_ascii):
        other = ACC_GARANTI if account_key.startswith("enpara") else ACC_ENPARA_VADESIZ
        return {"transfer_to": other}
    return {}
