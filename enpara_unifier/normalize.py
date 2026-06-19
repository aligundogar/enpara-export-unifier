"""Tarih, para tutarı ve metin normalizasyonu."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Optional

# --- Para -------------------------------------------------------------------

# "- 1.234,56 TL", "1.234,56 TL", "-505,03 TL", "550,00", "10,00 USD"
_MONEY_RE = re.compile(
    r"(?P<sign>-)?\s*(?P<num>\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)\s*"
    r"(?P<cur>TL|USD|EUR|GBP|gr\.?)?",
    re.IGNORECASE,
)

_MONEY_TOKEN_RE = re.compile(r"^-?\s*[\d.]+,\d{1,2}\s*(TL|USD|EUR|GBP)?$", re.IGNORECASE)


def is_money_token(s: str) -> bool:
    return bool(_MONEY_TOKEN_RE.match(s.strip()))


def parse_money(s: str) -> Optional[float]:
    """'-1.234,56 TL' -> -1234.56 (TR formatı: nokta=binlik, virgül=ondalık)."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    m = _MONEY_RE.search(s)
    if not m:
        return None
    num = m.group("num").replace(".", "").replace(",", ".")
    try:
        val = float(num)
    except ValueError:
        return None
    if m.group("sign") == "-":
        val = -val
    return val


def parse_currency(s: str) -> str:
    if not s:
        return "TL"
    s = s.upper()
    for c in ("USD", "EUR", "GBP", "TL"):
        if c in s:
            return c
    if "gr" in s.lower():
        return "ALTIN"
    return "TL"


# --- Tarih ------------------------------------------------------------------

# Desteklenen formatlar: DD/MM/YYYY, DD.MM.YYYY, DD/MM/YY, DD.MM.YY, YYYYMMDD
_DATE_PATTERNS = [
    (re.compile(r"^(\d{2})[/.](\d{2})[/.](\d{4})$"), ("d", "m", "Y")),
    (re.compile(r"^(\d{2})[/.](\d{2})[/.](\d{2})$"), ("d", "m", "y")),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), ("Y", "m", "d")),
]


def parse_date(s: str) -> Optional[date]:
    if s is None:
        return None
    s = str(s).strip()
    for rx, order in _DATE_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        parts = dict(zip(order, m.groups()))
        y = int(parts["Y"]) if "Y" in parts else 2000 + int(parts["y"])
        try:
            return date(y, int(parts["m"]), int(parts["d"]))
        except ValueError:
            return None
    return None


def looks_like_date(s: str) -> bool:
    return parse_date(s.strip()) is not None


# --- Metin ------------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x1f]")
_WS_RE = re.compile(r"\s+")

# Türkçe -> ASCII katlama (kategori eşlemesi için)
_TR_FOLD = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "İ": "i",
    "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def clean_text(s: str) -> str:
    """Kontrol karakterlerini temizle, boşlukları sadeleştir."""
    if s is None:
        return ""
    s = _CONTROL_RE.sub(" ", str(s))
    s = _WS_RE.sub(" ", s)
    return s.strip()


def ascii_fold(s: str) -> str:
    """Kategori/eşleştirme için: Türkçe karakterleri ASCII'ye çevir, BÜYÜK harf."""
    if not s:
        return ""
    s = clean_text(s)
    s = s.translate(_TR_FOLD)
    # kalan aksanlı karakterleri de düzleştir
    s = "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )
    return s.upper().strip()
