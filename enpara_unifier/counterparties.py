"""Karşı-taraf yönlendirme — DÜZENLENEBİLİR.

Bir işlemin karşı tarafına göre nasıl ele alınacağını belirler:
  • gelir       → işveren/müşteri (***REMOVED***, ***REMOVED***, maaş) = GELİR kategorisi
  • transfer    → kendi başka bankan / kişi (alacak-verecek) / yatırım hesabı
  • (eşleşmezse) → normal kategorize (gider) — categorize.py devreye girer

Eşleştirme ASCII'ye katlanmış BÜYÜK harf açıklama üzerinde yapılır.
"""

from __future__ import annotations

from .normalize import ascii_fold
from .model import ACC_ENPARA_VADESIZ, ACC_GARANTI

# Off-budget hesap anahtarları (Actual'da ayrı hesap olur)
ACC_***REMOVED*** = "person:***REMOVED***"
ACC_***REMOVED*** = "person:***REMOVED***"
ACC_DIGER = "person:diger"
ACC_BINANCE = "inv:binance"
ACC_MIDAS = "inv:midas"

# Off-budget hesapların görünen adları + tipi
OFFBUDGET_ACCOUNTS = {
    ACC_***REMOVED***: ("***REMOVED*** (***REMOVED***)", "kisi"),
    ACC_***REMOVED***: ("***REMOVED*** (kuzen)", "kisi"),
    ACC_DIGER: ("Diğer Kişiler (Borç/Alacak)", "kisi"),
    ACC_BINANCE: ("Binance (yatırım)", "yatirim"),
    ACC_MIDAS: ("Midas (yatırım)", "yatirim"),
}

# Gelir karşı-tarafları: (ASCII anahtar, gelir kategorisi)
INCOME_RULES = [
    ("***REMOVED***", "İş Geliri (***REMOVED***)"),
    ("***REMOVED***", "İş Geliri (***REMOVED***)"),       # = ***REMOVED***
    ("***REMOVED***", "Maaş (***REMOVED***)"),
    ("***REMOVED***", "Maaş (***REMOVED***)"),
    ("***REMOVED***", "Maaş"),
]

# Belirli kişi/yatırım transfer hedefleri: (ASCII anahtar, hesap)
TRANSFER_RULES = [
    ("***REMOVED***", ACC_***REMOVED***),
    ("***REMOVED***", ACC_***REMOVED***),
    ("BINANCE", ACC_BINANCE),
    ("MIDAS", ACC_MIDAS),
]

# Kendi adın (banka↔banka öz-transfer). Garanti maskeler: "AL**** GU****".
# Aile (***REMOVED***/EKIN/***REMOVED***) HARİÇ — sadece "ALI" / maskeli.
SELF_PATTERNS = ["***REMOVED***", "AL**** GU****", "AL** GU**"]

# 'Diğer Kişiler' çöp kutusuna düşmeyi engelleyen kurum/işlem belirteçleri
_NON_PERSON = [
    "LTD", "A.S", "TIC", " SAN", "MARKET", "GIDA", "ELEKTRON", "ENERJI",
    "KESINTI", "VERGI", "KREDI", "MOKA", "IYZICO", "PARAM", "PAYCELL",
    "HEPSI", "GOOGLE", "FATURA", "BANKASI", "BILISIM", "TEKNOLOJI", "YAZILIM",
    "ATM", "PARA YATIRMA", "MENKUL", "SIGORTA", "ODEME HIZMET", "ANONIM",
]


def _is_self(desc_ascii: str) -> bool:
    return any(p in desc_ascii for p in SELF_PATTERNS)


def looks_personal(desc_ascii: str, hareket_tipi: str | None) -> bool:
    """Tanınmayan ama açıkça kişiye/kişiden FAST/EFT transferi mi (→ Diğer Kişiler)."""
    ht = ascii_fold(hareket_tipi or "")
    is_tr = ("TRANSFER" in ht or "PARA TRANSFERI" in ht
             or "FAST" in desc_ascii or "EFT" in desc_ascii
             or "BIREYSEL ODEME" in desc_ascii)
    if not is_tr:
        return False
    if any(tok in desc_ascii for tok in _NON_PERSON):
        return False
    name = desc_ascii.split("-")[0].split(",")[0].strip()
    words = [w for w in name.split() if w.isalpha() and len(w) > 1]
    return len(words) >= 2


def route(desc_ascii: str, account_key: str, hareket_tipi: str | None) -> dict:
    """{category?, is_income?, transfer_to?} döndürür; boşsa normal kategorize edilir."""
    for pat, cat in INCOME_RULES:
        if pat in desc_ascii:
            return {"category": cat, "is_income": True}
    for pat, target in TRANSFER_RULES:
        if pat in desc_ascii:
            return {"transfer_to": target}
    if _is_self(desc_ascii):
        other = ACC_GARANTI if account_key.startswith("enpara") else ACC_ENPARA_VADESIZ
        return {"transfer_to": other}
    if looks_personal(desc_ascii, hareket_tipi):
        return {"transfer_to": ACC_DIGER}
    return {}
