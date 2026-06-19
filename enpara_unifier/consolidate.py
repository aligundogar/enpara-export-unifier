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
from .categorize import categorize, categorize_strict
from . import counterparties as CP
from .model import (Transaction, COLUMNS, COLUMNS_TR, SOURCE_CREDIT_CARD,
                    SOURCE_ACCOUNT, ACC_ENPARA_VADESIZ, ACC_ENPARA_KART, ACC_GARANTI)


def _route_txn(t: Transaction, derived: dict) -> None:
    """Bir işlemi yönlendir: yapısal kart ödemesi / karşı-taraf (gelir/transfer/
    kişi) / normal kategori. `derived`: otomatik üretilen kişi hesaplarının
    {anahtar: (ad, tip)} kaydı (tam parse — her kişi ayrı hesap)."""
    d = t.description_ascii or ""
    # 1) kart ödemesi (yapısal iç transfer)
    if t.source == SOURCE_CREDIT_CARD and t.internal_transfer:   # karttaki "Cep Şubesi"
        t.transfer_to = ACC_ENPARA_VADESIZ
        t.category = "Kredi Kartı Ödemesi"
        return
    if t.account == ACC_ENPARA_VADESIZ and "KREDI KARTI ODEMESI" in d:
        t.transfer_to = ACC_ENPARA_KART
        t.internal_transfer = True
        t.category = "Kredi Kartı Ödemesi"
        return
    # 2) açık karşı-taraf kuralları (gelir / belirli kişi / yatırım / öz-transfer)
    r = CP.route(d, t.account, t.hareket_tipi)
    if r.get("transfer_to"):
        t.transfer_to = r["transfer_to"]
        t.internal_transfer = True
        if r["transfer_to"] in CP.OFFBUDGET_ACCOUNTS:
            t.category = "Transfer: " + CP.OFFBUDGET_ACCOUNTS[r["transfer_to"]][0]
        else:
            t.category = "Öz Transfer (Gelen)" if t.amount > 0 else "Öz Transfer (Giden)"
        return
    if r.get("category"):       # gelir
        t.category = r["category"]
        return
    # 3) BİLİNEN satıcı/resmi ödeme/abonelik → kategori (kişi-yönlendirmeden ÖNCE,
    #    yoksa 'İtimat Eğitim ... ehliyet' gibi kurumlar kişi hesabı olur)
    strict = categorize_strict(t.description, t.hareket_tipi)
    if strict:
        t.category = strict
        return
    # 4) TAM PARSE: adı çıkarılabilen kişi → kendi alacak/verecek hesabı
    if CP.looks_personal(d, t.hareket_tipi):
        name = CP.party_name(t.description) or "Bilinmeyen Kişi"
        key = CP.party_key(name)
        derived.setdefault(key, (name, "kisi"))
        t.transfer_to = key
        t.internal_transfer = True
        t.category = "Transfer: " + name
        return
    # 5) isimsiz transfer (ör. MOBIL-FAST-...) → Diğer Kişiler çöp kutusu
    if CP.is_transfer_like(d, t.hareket_tipi):
        t.transfer_to = CP.ACC_DIGER
        t.internal_transfer = True
        t.category = "Transfer: Diğer Kişiler"
        return
    # 6) normal kategorize (gider) → bulunamazsa 'Diğer'
    t.category = categorize(t.description, t.source, t.hareket_tipi)


# --- dosya sınıflandırma ----------------------------------------------------

def classify(path: str) -> str:
    low = path.lower()
    if low.endswith((".xls", ".xlsx")):
        # Garanti mi Enpara mı? İlk hücrelere bak.
        try:
            import pandas as pd
            head = " ".join(str(v) for v in pd.read_excel(path, header=None, nrows=5)
                            .fillna("").values.ravel())
        except Exception:
            head = ""
        if "GARANT" in head.upper():
            return "garanti_xls"
        return "account_xls"   # Enpara
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
    """İlk sayfada 'Ad Soyad' gibi Başlık-Düzeni iki-dört kelimelik ilk
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
            elif kind == "garanti_xls":
                t = P.parse_garanti_xls(f)
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

    # zenginleştirme — yönlendirme: gelir / transfer / kişi / normal kategori
    derived = {}     # otomatik üretilen kişi hesapları {anahtar: (ad, tip)}
    for t in txns:
        _route_txn(t, derived)
    txns = A.dedupe_account(txns)
    snapshots = _dedupe_snapshots(snapshots)
    n_match = A.match_transfers(txns)
    txns.sort(key=lambda t: (t.date, t.account))

    monthly = A.monthly_cashflow(txns)
    cats = A.category_breakdown(txns)
    recurring = A.detect_recurring(txns)
    meta = _account_meta(txns, cc_files, derived)
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


def _opening_from_balance(txns, acc_key):
    """açılış = son_bakiye − Σ(tutarlar) — gün-içi sıralamadan bağımsız, kesin."""
    atx = [t for t in txns if t.account == acc_key and t.balance is not None]
    if not atx:
        return None
    final = max(atx, key=lambda t: t.date)        # en güncel tarihli satır
    total = sum(t.amount for t in txns if t.account == acc_key)
    return round(final.balance - total, 2)


def _account_meta(txns, cc_files, derived=None):
    """Tüm hesap tanımları + açılış bakiyeleri (Actual aktarımı için).
    Bankalar on-budget; kişi/yatırım hesapları off-budget (açılış 0, transferlerle dolar)."""
    meta = []
    BANKS = [(ACC_ENPARA_VADESIZ, "Enpara Vadesiz TL"), (ACC_GARANTI, "Garanti TL")]
    for acc_key, name in BANKS:
        if not any(t.account == acc_key for t in txns):
            continue
        meta.append({"kaynak": acc_key, "ad": name, "tip": "checking",
                     "acilis_bakiye": _opening_from_balance(txns, acc_key) or 0.0,
                     "para_birimi": "TL", "offbudget": 0})

    # Kredi kartı: en erken ekstrenin 'önceki ekstre bakiyesi' = devreden borç
    if any(t.account == ACC_ENPARA_KART for t in txns):
        card_open = 0.0
        if cc_files:
            def stmt_date(p):
                m = re.search(r"(\d{2})[.](\d{2})[.](\d{4})", os.path.basename(p))
                return (int(m.group(3)), int(m.group(2))) if m else (9999, 99)
            prev = P.credit_card_opening(min(cc_files, key=stmt_date))
            if prev is not None:
                card_open = round(-prev, 2)
        meta.append({"kaynak": ACC_ENPARA_KART, "ad": "Enpara Kredi Kartı",
                     "tip": "credit", "acilis_bakiye": card_open,
                     "para_birimi": "TL", "offbudget": 0})

    # Off-budget hesaplar: transferlerde geçen (kişi/yatırım/şirket) + elle tanımlı
    derived = derived or {}
    OFF = ("person:", "inv:", "company:")
    refs = {t.transfer_to for t in txns
            if t.transfer_to and str(t.transfer_to).startswith(OFF)}
    refs |= set(CP.MANUAL_BALANCES.keys())
    for k in sorted(refs):
        name, tip = (CP.OFFBUDGET_ACCOUNTS.get(k) or derived.get(k) or (k, "kisi"))
        meta.append({"kaynak": k, "ad": name, "tip": tip,
                     "acilis_bakiye": 0.0, "para_birimi": "TL", "offbudget": 1})
    return meta


def _balance_anchors(balance_snaps):
    """Hesap 'hedef bakiyeleri' (bakiye çapası):
    • kart: Varlık/Borç snapshot'ından (faturalanmamış borç dahil)
    • elle: counterparties.MANUAL_BALANCES (nakit-dışı alacak/verecek)."""
    anchors = []
    if balance_snaps:
        latest = max(balance_snaps, key=lambda s: s.get("tarih") or date.min)
        if latest.get("kredi_karti_borc") is not None:
            anchors.append({"kaynak": ACC_ENPARA_KART, "tarih": latest.get("tarih"),
                            "hedef_bakiye": round(-latest["kredi_karti_borc"], 2),
                            "aciklama": "Varlık/Borç dökümü ile uzlaştırma"})
    today = date.today()
    for acc, (bal, note) in CP.MANUAL_BALANCES.items():
        anchors.append({"kaynak": acc, "tarih": today,
                        "hedef_bakiye": round(float(bal), 2), "aciklama": note})
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
    # islemler tablosu COLUMNS'a göre (account, transfer_to dahil)
    _col_sql = {
        "date": "tarih TEXT", "account": "hesap TEXT", "source": "kaynak TEXT",
        "hareket_tipi": "hareket_tipi TEXT", "description": "aciklama TEXT",
        "category": "kategori TEXT", "amount": "tutar REAL",
        "currency": "para_birimi TEXT", "balance": "bakiye REAL",
        "installment": "taksit TEXT", "internal_transfer": "ic_transfer INTEGER",
        "transfer_to": "transfer_to TEXT", "match_id": "eslesme TEXT",
        "source_file": "kaynak_dosya TEXT",
    }
    cur.execute("CREATE TABLE islemler(" + ", ".join(_col_sql[c] for c in COLUMNS) + ")")
    ph = ",".join("?" * len(COLUMNS))
    cur.executemany(f"INSERT INTO islemler VALUES ({ph})", [tuple(r) for r in _rows(txns)])
    if meta:
        _sql_table(cur, "hesap_meta", meta,
                   [("kaynak", "TEXT"), ("ad", "TEXT"), ("tip", "TEXT"),
                    ("acilis_bakiye", "REAL"), ("para_birimi", "TEXT"),
                    ("offbudget", "INTEGER")])
    if anchors:
        _sql_table(cur, "bakiye_capa", anchors,
                   [("kaynak", "TEXT"), ("tarih", "TEXT"),
                    ("hedef_bakiye", "REAL"), ("aciklama", "TEXT")])
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
