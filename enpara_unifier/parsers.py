"""Üç Enpara kaynağı için parser'lar.

  parse_credit_card_pdf  — Kredi Kartı Ekstresi (bozuk font; açıklamalar OCR ile)
  parse_account_pdf      — "Hesap Hareketleri" PDF (temiz metin, Hareket tipi kolonlu)
  parse_account_summary_pdf — Aylık "hesap ve ihtiyaç kredisi özeti" PDF
                               (bakiye/kredi snapshot + dönem hareketleri)
  parse_account_xls      — "Enpara hesap hareketleriniz.xls"

Hepsi `model.Transaction` listesi döndürür. Snapshot (bakiye/kredi) ayrıca
parse_account_summary_pdf tarafından dict olarak verilir.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Optional

import fitz  # PyMuPDF

from . import normalize as N
from .model import (Transaction, SOURCE_CREDIT_CARD, SOURCE_ACCOUNT,
                    ACC_ENPARA_VADESIZ, ACC_ENPARA_KART, ACC_GARANTI)

# --- yardımcılar ------------------------------------------------------------

_CC_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_ACC_DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_SUM_DATE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
_INSTALLMENT = re.compile(r"^\d{1,2}/\d{1,2}$")

_PAYMENT_KW = ("CEP SUBESI", "KREDI KARTI ODEMESI")

# Özet PDF açıklamalarının başındaki hareket tipi etiketleri (ASCII-katlanmış)
_SUMMARY_TYPES = {
    "ODEME", "GELEN TRANSFER", "GIDEN TRANSFER", "VERGI KESINTISI",
    "FAIZ", "FAST", "HAVALE", "EFT", "OTOMATIK ODEME",
}


def _is_payment(desc_ascii: str) -> bool:
    return any(k in desc_ascii for k in _PAYMENT_KW)


# ===========================================================================
#  KREDİ KARTI EKSTRESİ  (OCR'lı)
# ===========================================================================

def _row_clusters(words, y_tol=3.0):
    """PyMuPDF word kutularını y eksenine göre satırlara grupla."""
    rows = []
    for w in sorted(words, key=lambda w: (round(w[1], 1), w[0])):
        x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4]
        if not text.strip():
            continue
        placed = False
        for r in rows:
            if abs(r["yc"] - (y0 + y1) / 2) <= y_tol:
                r["words"].append((x0, y0, x1, y1, text))
                r["yc"] = (r["yc"] * r["n"] + (y0 + y1) / 2) / (r["n"] + 1)
                r["n"] += 1
                placed = True
                break
        if not placed:
            rows.append({"yc": (y0 + y1) / 2, "n": 1,
                         "words": [(x0, y0, x1, y1, text)]})
    for r in rows:
        r["words"].sort(key=lambda t: t[0])
    rows.sort(key=lambda r: r["yc"])
    return rows


class _OCRCache:
    def __init__(self, path: Optional[str]):
        self.path = path
        self.data = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, val):
        self.data[key] = val

    def save(self):
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=0)


_LEADING_JUNK = re.compile(r"^[^0-9A-Za-zÇĞİÖŞÜçğıöşü]+")


def _clean_ocr(text: str) -> str:
    text = text.replace("\n", " ").strip()
    # OCR'ın sol sütun ikon gürültüsü (İİ, 1), iğ) ...) — baştaki kısa çöp token'ı at
    parts = text.split(" ", 1)
    if len(parts) == 2 and len(parts[0]) <= 3 and not parts[0].isalpha():
        text = parts[1]
    text = _LEADING_JUNK.sub("", text)
    return N.clean_text(text)


def parse_credit_card_pdf(path: str, ocr: bool = True, dpi: int = 350,
                          cache: Optional[_OCRCache] = None) -> list[Transaction]:
    import pytesseract
    from PIL import Image
    import io

    doc = fitz.open(path)
    fname = os.path.basename(path)
    txns: list[Transaction] = []

    # sütun şablonunu 1. sayfadaki başlıklardan çıkar
    desc_x0 = desc_x1 = taksit_x0 = None
    for w in doc[0].get_text("words"):
        t = w[4]
        if t == "klama" or t.startswith("klama") or t == "Açıklama":
            desc_x0 = w[0]
        if t == "Taksit":
            taksit_x0 = w[0]
        if t == "Tutar":
            tutar_x0 = w[0]
    if desc_x0 is None:
        desc_x0 = 112.0
    if taksit_x0 is None:
        taksit_x0 = 455.0
    desc_x0 = max(desc_x0 - 2, 100.0)
    desc_x1 = taksit_x0 - 4

    zoom = dpi / 72.0
    mtx = fitz.Matrix(zoom, zoom)

    for pno, page in enumerate(doc):
        words = page.get_text("words")
        rows = _row_clusters(words)
        # OCR için sayfayı bir kez render et
        page_img = None

        for r in rows:
            ws = r["words"]
            joined = " ".join(t[4] for t in ws)
            # satır başında tarih var mı?
            first = ws[0][4].strip()
            if not _CC_DATE.match(first):
                continue
            # taksit (örn "2/3") taksit sütununda
            taksit = None
            for (x0, y0, x1, y1, t) in ws:
                if _INSTALLMENT.match(t.strip()):
                    taksit = t.strip()
            # tutar: işaret '-' ayrı token olabildiği için BİRLEŞİK satırdan,
            # en sağdaki "... TL" eşleşmesini al (işaret dahil)
            matches = re.findall(r"-?\s*[\d.]+,\d{2}\s*TL", joined)
            if not matches:
                continue
            money = matches[-1]
            raw = N.parse_money(money)
            if raw is None:
                continue
            d = N.parse_date(first)
            if d is None:
                continue

            # açıklama: OCR ile (yoksa metinden bozuk haliyle)
            yc = r["yc"]
            cache_key = f"{fname}|{pno}|{round(yc,1)}|{money}"
            desc = cache.get(cache_key) if cache else None
            if desc is None:
                if ocr:
                    if page_img is None:
                        pix = page.get_pixmap(matrix=mtx)
                        page_img = Image.open(io.BytesIO(pix.tobytes("png")))
                    y0 = min(t[1] for t in ws)
                    y1 = max(t[3] for t in ws)
                    box = (int(desc_x0 * zoom), int((y0 - 1) * zoom),
                           int(desc_x1 * zoom), int((y1 + 1) * zoom))
                    crop = page_img.crop(box)
                    raw_ocr = pytesseract.image_to_string(
                        crop, lang="tur", config="--psm 7")
                    desc = _clean_ocr(raw_ocr)
                else:
                    # OCR kapalı: metin sütunundan bozuk açıklamayı topla
                    seg = [t[4] for t in ws
                           if desc_x0 - 2 <= t[0] < desc_x1 and not N.is_money_token(t[4])]
                    desc = N.clean_text(" ".join(seg))
                if cache is not None:
                    cache.set(cache_key, desc)

            if not desc:
                desc = "(açıklama yok)"
            desc_ascii = N.ascii_fold(desc)
            is_pay = raw < 0 and _is_payment(desc_ascii)
            # nakit akışı işareti: harcama negatif, kart ödemesi/iade pozitif
            amount = -raw
            txns.append(Transaction(
                date=d, source=SOURCE_CREDIT_CARD, account=ACC_ENPARA_KART,
                source_file=fname,
                description=desc, amount=amount, installment=taksit,
                internal_transfer=is_pay, description_ascii=desc_ascii,
            ))
    return txns


def parse_balance_snapshot(path: str) -> dict:
    """'Varlık ve Borç Dökümü' PDF'i: güncel kredi kartı borcu, kredi kalanı vs.
    Kart bakiyesini gerçek (faturalanmamış dahil) borca uzlaştırmak için kullanılır."""
    doc = fitz.open(path)
    text = N.clean_text(doc[0].get_text())

    def g(p):
        m = re.search(p, text)
        return N.parse_money(m.group(1)) if m else None

    d = None
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*Tarihli Varl", text)
    if m:
        d = N.parse_date(m.group(1))
    return {
        "tarih": d,
        "kredi_karti_borc": g(r"Kredi Kartları\s+([\d.]+,\d{2})"),
        "kredi_kalan": g(r"Kredilerim\s+([\d.]+,\d{2})"),
        "toplam_borc": g(r"([\d.]+,\d{2})\s+USD Cinsinden"),
    }


def credit_card_opening(path: str):
    """Kredi kartı ekstresindeki 'Bir önceki ekstre bakiyeniz' (devreden borç).
    Hesap reconcile için kart açılış bakiyesi = -(bu değer)."""
    doc = fitz.open(path)
    text = N.clean_text(doc[0].get_text())
    m = re.search(r"bakiyeniz\s*(-?\s*[\d.]+,\d{2})\s*TL", text, re.IGNORECASE)
    return N.parse_money(m.group(1)) if m else None


# ===========================================================================
#  HESAP HAREKETLERİ PDF  (temiz metin, Hareket tipi kolonlu)
# ===========================================================================

def parse_account_pdf(path: str) -> list[Transaction]:
    doc = fitz.open(path)
    fname = os.path.basename(path)
    txns: list[Transaction] = []
    lines: list[str] = []
    for page in doc:
        lines.extend(page.get_text().split("\n"))

    i, n = 0, len(lines)
    while i < n:
        ln = lines[i].strip()
        if not _ACC_DATE.match(ln):
            i += 1
            continue
        d = N.parse_date(ln)
        tipi = lines[i + 1].strip() if i + 1 < n else ""
        j = i + 2
        desc_parts = []
        while j < n and not (N.is_money_token(lines[j])):
            # bir sonraki satır yeni tarihse dur (bozuk kayıt koruması)
            if _ACC_DATE.match(lines[j].strip()):
                break
            desc_parts.append(lines[j].strip())
            j += 1
        if j >= n or not N.is_money_token(lines[j]):
            i = j
            continue
        tutar = N.parse_money(lines[j])
        bakiye = N.parse_money(lines[j + 1]) if j + 1 < n and N.is_money_token(lines[j + 1]) else None
        desc = N.clean_text(" ".join(p for p in desc_parts if p))
        desc_ascii = N.ascii_fold(desc + " " + tipi)
        txns.append(Transaction(
            date=d, source=SOURCE_ACCOUNT, account=ACC_ENPARA_VADESIZ,
            source_file=fname,
            description=desc, amount=tutar or 0.0, balance=bakiye,
            hareket_tipi=tipi, internal_transfer=_is_payment(N.ascii_fold(desc)),
            description_ascii=desc_ascii,
        ))
        i = j + (2 if bakiye is not None else 1)
    return txns


# ===========================================================================
#  AYLIK HESAP / İHTİYAÇ KREDİSİ ÖZETİ PDF
# ===========================================================================

def parse_account_summary_pdf(path: str):
    """(transactions, snapshot) döndürür. snapshot: bakiye + kredi durumu dict."""
    doc = fitz.open(path)
    fname = os.path.basename(path)
    text = "\n".join(p.get_text() for p in doc)
    lines = text.split("\n")

    snapshot = _extract_summary_snapshot(text, fname)

    txns: list[Transaction] = []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i].strip()
        if not _SUM_DATE.match(ln):
            i += 1
            continue
        d = N.parse_date(ln)
        j = i + 1
        desc_parts = []
        while j < n and not N.is_money_token(lines[j]):
            if _SUM_DATE.match(lines[j].strip()):
                break
            desc_parts.append(lines[j].strip())
            j += 1
        if j >= n or not N.is_money_token(lines[j]):
            i = j
            continue
        tutar = N.parse_money(lines[j])
        bakiye = N.parse_money(lines[j + 1]) if j + 1 < n and N.is_money_token(lines[j + 1]) else None
        desc = N.clean_text(" ".join(p for p in desc_parts if p))
        # özet açıklaması "Ödeme, ...", "Gelen Transfer, İSİM, ..." şeklinde:
        # ilk parça hareket tipi → onu ayır, açıklama gönderen/alıcı adıyla başlasın
        # (büyük hesap PDF'iyle tutarlı olsun; gelir/banka eşleştirmesi için kritik).
        tipi = None
        if "," in desc:
            head = desc.split(",")[0].strip()
            if N.ascii_fold(head) in _SUMMARY_TYPES:
                tipi = head
                desc = desc.split(",", 1)[1].strip()
        desc_ascii = N.ascii_fold(desc)
        txns.append(Transaction(
            date=d, source=SOURCE_ACCOUNT, account=ACC_ENPARA_VADESIZ,
            source_file=fname,
            description=desc, amount=tutar or 0.0, balance=bakiye,
            hareket_tipi=tipi, internal_transfer=_is_payment(desc_ascii),
            description_ascii=desc_ascii,
        ))
        i = j + (2 if bakiye is not None else 1)
    return txns, snapshot


def _extract_summary_snapshot(text: str, fname: str) -> dict:
    snap = {"source_file": fname, "tarih": None, "vadesiz_tl": None,
            "toplam_varlik": None, "kredi_kalan": None, "kredi_taksit": None,
            "kredi_sonraki_taksit": None}
    m = re.search(r"(\d{2}/\d{2}/\d{4})", text)
    if m:
        snap["tarih"] = N.parse_date(m.group(1))
    m = re.search(r"Vadesiz TL\s*\n-\s*\n-\s*\n([\d.,]+)\s*TL", text)
    if m:
        snap["vadesiz_tl"] = N.parse_money(m.group(1))
    m = re.search(r"Toplam\s*\n([\d.,]+)\s*TL", text)
    if m:
        snap["toplam_varlik"] = N.parse_money(m.group(1))
    m = re.search(r"Kalan toplam borç[^\n]*\n.*?(\d{1,3}(?:\.\d{3})*,\d{2})\s*TL",
                  text, re.S)
    # kredi satırı: "... 232,82 TL ... 1.396,64 TL"
    m2 = re.search(r"İhtiyaç kredisi[\s\S]{0,200}", text)
    if m2:
        nums = re.findall(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*TL", m2.group(0))
        if nums:
            snap["kredi_taksit"] = N.parse_money(nums[0])
            snap["kredi_kalan"] = N.parse_money(nums[-1])
        dm = re.search(r"(\d{2}/\d{2}/\d{4})", m2.group(0))
        if dm:
            snap["kredi_sonraki_taksit"] = N.parse_date(dm.group(1))
    return snap


# ===========================================================================
#  ENPARA XLS
# ===========================================================================

def parse_account_xls(path: str) -> list[Transaction]:
    import pandas as pd
    fname = os.path.basename(path)
    raw = pd.read_excel(path, header=None)
    # başlık satırını bul (Tarih / Hareket tipi / Açıklama / İşlem Tutarı / Bakiye)
    hdr = None
    for i in range(len(raw)):
        rowvals = [str(v) for v in raw.iloc[i].tolist()]
        if any("Tarih" == v for v in rowvals) and any("Bakiye" in v for v in rowvals):
            hdr = i
            break
    if hdr is None:
        return []
    cols = {str(v).strip(): k for k, v in enumerate(raw.iloc[hdr].tolist())}
    c_tarih = cols.get("Tarih")
    c_tip = next((k for v, k in cols.items() if "Hareket" in v), None)
    c_acik = next((k for v, k in cols.items() if "Açıklama" in v), None)
    c_tutar = next((k for v, k in cols.items() if "Tutar" in v), None)
    c_bak = next((k for v, k in cols.items() if "Bakiye" in v), None)

    txns: list[Transaction] = []
    for i in range(hdr + 1, len(raw)):
        row = raw.iloc[i].tolist()
        dval = row[c_tarih] if c_tarih is not None else None
        d = N.parse_date(str(dval)) if dval is not None else None
        if d is None:
            continue
        tipi = N.clean_text(str(row[c_tip])) if c_tip is not None else None
        desc = N.clean_text(str(row[c_acik])) if c_acik is not None else ""
        tutar = _xls_num(row[c_tutar]) if c_tutar is not None else None
        bakiye = _xls_num(row[c_bak]) if c_bak is not None else None
        desc_ascii = N.ascii_fold(desc + " " + (tipi or ""))
        txns.append(Transaction(
            date=d, source=SOURCE_ACCOUNT, account=ACC_ENPARA_VADESIZ,
            source_file=fname,
            description=desc, amount=tutar or 0.0, balance=bakiye,
            hareket_tipi=tipi, internal_transfer=_is_payment(N.ascii_fold(desc)),
            description_ascii=desc_ascii,
        ))
    return txns


# ===========================================================================
#  GARANTİ BBVA XLS  (nokta-ondalık, "Etiket" sütunu, maskeli isim)
# ===========================================================================

def parse_garanti_xls(path: str) -> list[Transaction]:
    import pandas as pd
    fname = os.path.basename(path)
    raw = pd.read_excel(path, header=None)
    # başlık satırı: Tarih / Açıklama / Etiket / Tutar / Bakiye / Dekont No
    hdr = None
    for i in range(min(40, len(raw))):
        vals = [str(v).strip() for v in raw.iloc[i].tolist()]
        if "Tarih" in vals and "Tutar" in vals and "Bakiye" in vals:
            hdr = i
            break
    if hdr is None:
        return []
    cols = {str(v).strip(): k for k, v in enumerate(raw.iloc[hdr].tolist())}
    c = lambda name: next((k for v, k in cols.items() if name in v), None)
    c_tarih, c_acik, c_etiket = cols.get("Tarih"), c("Açıklama"), c("Etiket")
    c_tutar, c_bak = c("Tutar"), c("Bakiye")

    txns = []
    for i in range(hdr + 1, len(raw)):
        row = raw.iloc[i].tolist()
        d = N.parse_date(str(row[c_tarih])) if c_tarih is not None else None
        if d is None:
            continue
        desc = N.clean_text(str(row[c_acik])) if c_acik is not None else ""
        etiket = N.clean_text(str(row[c_etiket])) if c_etiket is not None else None
        tutar = _xls_num(row[c_tutar]) if c_tutar is not None else None
        bakiye = _xls_num(row[c_bak]) if c_bak is not None else None
        if tutar is None:
            continue
        # gönderen/alıcı adı genelde "-FAST"/"-EFT"/"-MOBIL" öncesi
        desc_ascii = N.ascii_fold(desc)
        txns.append(Transaction(
            date=d, source=SOURCE_ACCOUNT, account=ACC_GARANTI, source_file=fname,
            description=desc, amount=tutar, balance=bakiye,
            hareket_tipi=etiket, description_ascii=desc_ascii,
        ))
    return txns


def _xls_num(v):
    """XLS hücresi: float olabilir ('-505.03') ya da TR-metin ('5.000,00 TL')."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    if re.match(r"^-?\d+(\.\d+)?$", s):   # zaten float (nokta=ondalık)
        return float(s)
    return N.parse_money(s)
