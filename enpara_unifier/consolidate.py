"""Orkestratör: kaynakları keşfet → parse → zenginleştir → 4 formatta yaz."""

from __future__ import annotations

import csv
import glob
import os
import re
import sqlite3
from datetime import date

import fitz

from . import parsers as P
from . import analyze as A
from .categorize import categorize
from .model import Transaction, COLUMNS, COLUMNS_TR, SOURCE_CREDIT_CARD, SOURCE_ACCOUNT


# --- dosya sınıflandırma ----------------------------------------------------

def classify(path: str) -> str:
    low = path.lower()
    if low.endswith((".xls", ".xlsx")):
        return "account_xls"
    if not low.endswith(".pdf"):
        return "unknown"
    try:
        doc = fitz.open(path)
        head = doc[0].get_text()
    except Exception:
        head = ""
    if "Varlık ve Borç" in head or "varlık ve borç" in low:
        return "balance_snapshot"
    if "Kredi Kartı Ekstresi" in head or "Kart limiti" in head or "ekstreniz" in low:
        return "credit_card"
    if ("ihtiyaç kredisi özet" in head or "tüm hesaplarınız" in head
            or ("özet" in low and "hesap" in low)):
        return "account_summary"
    if "Hareket tipi" in head or "Hesap Hareketleri" in low or "hareketleri" in low:
        return "account_pdf"
    return "unknown"


_LABEL_STOP = {
    "Ad soyad", "Hesap adı", "Hesap tipi", "Tarih", "Hareket tipi", "Açıklama",
    "Bakiye", "Kart numarası", "Kart limiti", "Ekstre dönemi", "IBAN",
    "Başlangıç tarihi", "Bitiş tarihi", "Ekstre tarihi", "Son ödeme tarihi",
}
_NAME_RE = re.compile(
    r"^[A-ZÇĞİÖŞÜ][a-zçğıöşü]+(?: [A-ZÇĞİÖŞÜ][a-zçğıöşü]+){1,3}$")


def _extract_holder(path: str) -> str | None:
    """İlk sayfada 'Ali Gündoğar' gibi Başlık-Düzeni iki-dört kelimelik ilk
    kişi adını döndürür (etiket satırlarını atlar)."""
    try:
        doc = fitz.open(path)
        text = doc[0].get_text()
    except Exception:
        return None
    for ln in text.split("\n"):
        ln = ln.strip()
        if ln in _LABEL_STOP or any(ch.isdigit() for ch in ln):
            continue
        if _NAME_RE.match(ln):
            return ln
    return None


# --- ana akış ---------------------------------------------------------------

def consolidate(input_dir: str, output_dir: str, ocr: bool = True,
                verbose: bool = True) -> dict:
    files = sorted(
        glob.glob(os.path.join(input_dir, "*.pdf")) +
        glob.glob(os.path.join(input_dir, "*.xls")) +
        glob.glob(os.path.join(input_dir, "*.xlsx"))
    )
    os.makedirs(output_dir, exist_ok=True)
    cache = P._OCRCache(os.path.join(output_dir, ".ocr_cache.json"))

    txns: list[Transaction] = []
    snapshots: list[dict] = []
    balance_snaps: list[dict] = []
    cc_files: list[str] = []
    holder = None
    counts = {}

    for f in files:
        kind = classify(f)
        counts[kind] = counts.get(kind, 0) + 1
        name = os.path.basename(f)
        try:
            if kind == "credit_card":
                cc_files.append(f)
                t = P.parse_credit_card_pdf(f, ocr=ocr, cache=cache)
            elif kind == "account_pdf":
                t = P.parse_account_pdf(f)
                holder = holder or _extract_holder(f)
            elif kind == "account_summary":
                t, snap = P.parse_account_summary_pdf(f)
                snapshots.append(snap)
                holder = holder or _extract_holder(f)
            elif kind == "account_xls":
                t = P.parse_account_xls(f)
            elif kind == "balance_snapshot":
                snap = P.parse_balance_snapshot(f)
                snap["source_file"] = name
                balance_snaps.append(snap)
                if verbose:
                    print(f"  ✓ [{kind:16}]      kart borç={snap.get('kredi_karti_borc')}  {name}")
                continue
            else:
                if verbose:
                    print(f"  ? atlandı (tanınmadı): {name}")
                continue
            txns.extend(t)
            if verbose:
                print(f"  ✓ [{kind:16}] {len(t):4} kayıt  {name}")
        except Exception as e:
            print(f"  ✗ HATA {name}: {e}")
    cache.save()

    # zenginleştirme
    for t in txns:
        t.category = categorize(t.description, t.source, t.hareket_tipi)
    A.flag_self_transfers(txns, holder)
    txns = A.dedupe_account(txns)
    snapshots = _dedupe_snapshots(snapshots)
    n_match = A.match_card_payments(txns)
    txns.sort(key=lambda t: (t.date, t.source))

    monthly = A.monthly_cashflow(txns)
    cats = A.category_breakdown(txns)
    recurring = A.detect_recurring(txns)
    meta = _account_meta(txns, cc_files)
    anchors = _balance_anchors(balance_snaps)

    # çıktılar
    _write_csvs(output_dir, txns, monthly, cats, recurring, snapshots)
    _write_xlsx(output_dir, txns, monthly, cats, recurring, snapshots)
    _write_sqlite(output_dir, txns, monthly, cats, recurring, snapshots, meta, anchors)
    _write_markdown(output_dir, txns, monthly, cats, recurring, snapshots,
                    holder, n_match, ocr)

    return {
        "files": len(files), "by_kind": counts, "transactions": len(txns),
        "holder": holder, "matches": n_match, "months": len(monthly),
        "output_dir": output_dir,
    }


# --- yazıcılar --------------------------------------------------------------

def _rows(txns):
    for t in txns:
        r = t.to_row()
        yield [r[c] for c in COLUMNS]


def _write_csvs(out, txns, monthly, cats, recurring, snapshots):
    d = os.path.join(out, "csv")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "islemler.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([COLUMNS_TR[c] for c in COLUMNS])
        w.writerows(_rows(txns))
    _csv_dicts(os.path.join(d, "aylik_nakit_akisi.csv"), monthly,
               ["month", "income", "expense", "net", "card_spend", "tx_count"],
               ["Ay", "Gelir", "Gider", "Net", "Kart Harcaması", "İşlem Sayısı"])
    _csv_dicts(os.path.join(d, "kategori_dagilimi.csv"), cats,
               ["category", "total", "count"], ["Kategori", "Toplam Gider", "Adet"])
    _csv_dicts(os.path.join(d, "tekrar_eden_odemeler.csv"), recurring,
               ["merchant", "category", "occurrences", "months", "avg_amount",
                "min_amount", "max_amount", "total", "first", "last"],
               ["Satıcı", "Kategori", "Tekrar", "Ay Sayısı", "Ort. Tutar",
                "Min", "Maks", "Toplam", "İlk", "Son"])
    if snapshots:
        _csv_dicts(os.path.join(d, "bakiye_kredi_snapshot.csv"), snapshots,
                   ["tarih", "vadesiz_tl", "toplam_varlik", "kredi_kalan",
                    "kredi_taksit", "kredi_sonraki_taksit", "source_file"],
                   ["Tarih", "Vadesiz TL", "Toplam Varlık", "Kredi Kalan Borç",
                    "Kredi Tutarı", "Sonraki Taksit", "Kaynak Dosya"])


def _account_meta(txns, cc_files):
    """Actual'a aktarım için hesap tanımları + açılış bakiyeleri.
    Açılış bakiyesi sayesinde Actual'daki hesap bakiyeleri Enpara'nınkiyle tutar."""
    from .model import SOURCE_CREDIT_CARD as CC, SOURCE_ACCOUNT as ACC
    meta = []

    # Vadesiz: en erken hesap işleminden geriye -> açılış = bakiye - tutar
    acc_tx = sorted([t for t in txns if t.source == ACC and t.balance is not None],
                    key=lambda t: t.date)
    acc_open = round(acc_tx[0].balance - acc_tx[0].amount, 2) if acc_tx else 0.0
    meta.append({"kaynak": ACC, "ad": "Enpara Vadesiz TL", "tip": "checking",
                 "acilis_bakiye": acc_open, "para_birimi": "TL"})

    # Kredi kartı: en erken ekstrenin 'önceki ekstre bakiyesi' = devreden borç
    card_open = 0.0
    if cc_files:
        def stmt_date(p):
            m = re.search(r"(\d{2})[.](\d{2})[.](\d{4})", os.path.basename(p))
            return (int(m.group(3)), int(m.group(2))) if m else (9999, 99)
        earliest = min(cc_files, key=stmt_date)
        prev = P.credit_card_opening(earliest)
        if prev is not None:
            card_open = round(-prev, 2)   # devreden borç -> negatif başlangıç
    if any(t.source == CC for t in txns):
        meta.append({"kaynak": CC, "ad": "Enpara Kredi Kartı", "tip": "credit",
                     "acilis_bakiye": card_open, "para_birimi": "TL"})
    return meta


def _balance_anchors(balance_snaps):
    """Varlık/Borç snapshot'larından hesap 'hedef bakiyeleri'.
    En güncel snapshot esas alınır. Kart için hedef = -(güncel borç) — böylece
    faturalanmamış son harcamalar tek bir uzlaştırma işlemiyle yansıtılır."""
    from .model import SOURCE_CREDIT_CARD as CC
    if not balance_snaps:
        return []
    latest = max(balance_snaps, key=lambda s: s.get("tarih") or date.min)
    anchors = []
    if latest.get("kredi_karti_borc") is not None:
        anchors.append({"kaynak": CC, "tarih": latest.get("tarih"),
                        "hedef_bakiye": round(-latest["kredi_karti_borc"], 2),
                        "kaynak_dosya": latest.get("source_file")})
    return anchors


def _dedupe_snapshots(snapshots):
    best = {}
    for s in snapshots:
        key = s.get("tarih")
        if key not in best or sum(v is not None for v in s.values()) > \
                sum(v is not None for v in best[key].values()):
            best[key] = s
    return [best[k] for k in sorted(best, key=lambda d: d or date.min)]


def _csv_dicts(path, rows, keys, headers):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            w.writerow([r.get(k, "") for k in keys])


def _write_xlsx(out, txns, monthly, cats, recurring, snapshots):
    import pandas as pd
    path = os.path.join(out, "konsolide.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        df = pd.DataFrame([t.to_row() for t in txns], columns=COLUMNS)
        df.rename(columns=COLUMNS_TR).to_excel(xl, sheet_name="İşlemler", index=False)
        pd.DataFrame(monthly).to_excel(xl, sheet_name="Aylık Nakit Akışı", index=False)
        pd.DataFrame(cats).to_excel(xl, sheet_name="Kategori Dağılımı", index=False)
        pd.DataFrame(recurring).to_excel(xl, sheet_name="Tekrar Eden", index=False)
        if snapshots:
            pd.DataFrame(snapshots).to_excel(xl, sheet_name="Bakiye-Kredi", index=False)


def _write_sqlite(out, txns, monthly, cats, recurring, snapshots, meta=None, anchors=None):
    path = os.path.join(out, "finans.db")
    if os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""CREATE TABLE islemler(
        tarih TEXT, kaynak TEXT, hareket_tipi TEXT, aciklama TEXT, kategori TEXT,
        tutar REAL, para_birimi TEXT, bakiye REAL, taksit TEXT,
        ic_transfer INTEGER, eslesme TEXT, kaynak_dosya TEXT)""")
    cur.executemany(
        "INSERT INTO islemler VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [tuple(r) for r in _rows(txns)])
    if meta:
        _sql_table(cur, "hesap_meta", meta,
                   [("kaynak", "TEXT"), ("ad", "TEXT"), ("tip", "TEXT"),
                    ("acilis_bakiye", "REAL"), ("para_birimi", "TEXT")])
    if anchors:
        _sql_table(cur, "bakiye_capa", anchors,
                   [("kaynak", "TEXT"), ("tarih", "TEXT"),
                    ("hedef_bakiye", "REAL"), ("kaynak_dosya", "TEXT")])
    _sql_table(cur, "aylik", monthly,
               [("month", "TEXT"), ("income", "REAL"), ("expense", "REAL"),
                ("net", "REAL"), ("card_spend", "REAL"), ("tx_count", "INTEGER")])
    _sql_table(cur, "kategori", cats,
               [("category", "TEXT"), ("total", "REAL"), ("count", "INTEGER")])
    _sql_table(cur, "tekrar_eden", recurring,
               [("merchant", "TEXT"), ("category", "TEXT"), ("occurrences", "INTEGER"),
                ("months", "INTEGER"), ("avg_amount", "REAL"), ("total", "REAL"),
                ("first", "TEXT"), ("last", "TEXT")])
    if snapshots:
        _sql_table(cur, "snapshot", snapshots,
                   [("tarih", "TEXT"), ("vadesiz_tl", "REAL"), ("toplam_varlik", "REAL"),
                    ("kredi_kalan", "REAL"), ("kredi_taksit", "REAL"),
                    ("kredi_sonraki_taksit", "TEXT"), ("source_file", "TEXT")])
    con.commit()
    con.close()


def _sql_table(cur, name, rows, cols):
    coldef = ", ".join(f"{c} {t}" for c, t in cols)
    cur.execute(f"CREATE TABLE {name}({coldef})")
    ph = ",".join("?" * len(cols))
    data = [tuple(_sqlval(r.get(c)) for c, _ in cols) for r in rows]
    cur.executemany(f"INSERT INTO {name} VALUES ({ph})", data)


def _sqlval(v):
    return v.isoformat() if isinstance(v, date) else v


def _write_markdown(out, txns, monthly, cats, recurring, snapshots,
                    holder, n_match, ocr):
    L = []
    a = L.append
    total_in = sum(m["income"] for m in monthly)
    total_out = sum(m["expense"] for m in monthly)
    dmin = min((t.date for t in txns), default=None)
    dmax = max((t.date for t in txns), default=None)

    a("# Enpara Finansal Özet Raporu\n")
    if holder:
        a(f"**Hesap sahibi:** {holder}  ")
    if dmin and dmax:
        a(f"**Dönem:** {dmin:%d.%m.%Y} – {dmax:%d.%m.%Y}  ")
    a(f"**Toplam işlem:** {len(txns)}  ")
    a(f"**Eşleşen kart↔hesap ödemesi:** {n_match}  ")
    a(f"**Açıklama kaynağı:** {'OCR (tesseract-tur)' if ocr else 'ham metin (bozuk)'}\n")

    a("## Genel Nakit Akışı\n")
    a(f"- Toplam giriş: **{total_in:,.2f} TL**")
    a(f"- Toplam çıkış: **{total_out:,.2f} TL**")
    a(f"- Net: **{total_in - total_out:,.2f} TL**\n")
    a("> İç transferler (kart ödemesi, kişinin kendi hesapları arası) hariçtir.\n")

    a("## Aylık Nakit Akışı\n")
    a("| Ay | Gelir | Gider | Net | Kart Harcaması | İşlem |")
    a("|---|--:|--:|--:|--:|--:|")
    for m in monthly:
        a(f"| {m['month']} | {m['income']:,.0f} | {m['expense']:,.0f} | "
          f"{m['net']:,.0f} | {m['card_spend']:,.0f} | {m['tx_count']} |")
    a("")

    a("## Kategori Dağılımı (Gider)\n")
    a("| Kategori | Toplam | Adet |")
    a("|---|--:|--:|")
    for c in cats:
        a(f"| {c['category']} | {c['total']:,.0f} | {c['count']} |")
    a("")

    a("## Tekrar Eden Ödemeler / Abonelikler\n")
    if recurring:
        a("| Satıcı | Kategori | Ay | Ort. Tutar | Toplam |")
        a("|---|---|--:|--:|--:|")
        for r in recurring[:25]:
            a(f"| {r['merchant'].title()} | {r['category']} | {r['months']} | "
              f"{r['avg_amount']:,.0f} | {r['total']:,.0f} |")
    else:
        a("_Tespit edilmedi._")
    a("")

    if snapshots:
        a("## Bakiye & Kredi Durumu (Aylık Snapshot)\n")
        a("| Tarih | Vadesiz TL | Toplam Varlık | Kredi Kalan Borç | Sonraki Taksit |")
        a("|---|--:|--:|--:|---|")
        for s in sorted(snapshots, key=lambda s: s["tarih"] or date.min):
            t = s.get("tarih")
            nt = s.get("kredi_sonraki_taksit")
            tstr = f"{t:%d.%m.%Y}" if t else ""
            ntstr = f"{nt:%d.%m.%Y}" if nt else ""
            a(f"| {tstr} | {s.get('vadesiz_tl') or 0:,.0f} | "
              f"{s.get('toplam_varlik') or 0:,.0f} | {s.get('kredi_kalan') or 0:,.0f} | "
              f"{ntstr} |")
        a("")

    a("## Notlar / Sınırlamalar\n")
    a("- Kredi kartı PDF'lerinde satıcı isimleri Enpara'nın font alt-kümeleme "
      "yöntemi nedeniyle metin katmanında bozuktur; bu yüzden açıklamalar render "
      "edilmiş sayfadan **OCR** ile (tesseract, Türkçe) okunur. Tarih ve tutarlar "
      "her zaman metinden birebir alınır.")
    a("- Tutar işareti: **+ giriş / − çıkış**. Kart harcaması gider (−), karta "
      "yapılan ödeme iç transferdir.")
    a("- Hesap kayıtları XLS / Hesap PDF / Özet PDF arasında (tarih+tutar+bakiye) "
      "ile tekilleştirilir.")

    with open(os.path.join(out, "rapor.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
