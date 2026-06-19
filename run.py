#!/usr/bin/env python3
"""Enpara Export Unifier — komut satırı arayüzü.

Kullanım:
    python run.py <girdi_klasörü> [-o çıktı_klasörü] [--no-ocr]

Örnek:
    python run.py ./ekstreler -o ./output
"""

import argparse
import sys

from enpara_unifier.consolidate import consolidate


def main():
    ap = argparse.ArgumentParser(
        description="Enpara hesap/kart ekstrelerini tek veri setinde birleştirir.")
    ap.add_argument("input_dir", help="PDF/XLS dosyalarının bulunduğu klasör")
    ap.add_argument("-o", "--output", default="output", help="Çıktı klasörü (vars: output)")
    ap.add_argument("--no-ocr", action="store_true",
                    help="Kredi kartı açıklamalarını OCR'lama (hızlı ama bozuk metin)")
    args = ap.parse_args()

    print(f"📂 Girdi: {args.input_dir}")
    res = consolidate(args.input_dir, args.output, ocr=not args.no_ocr)

    print("\n" + "=" * 50)
    print(f"✅ Tamam — {res['transactions']} işlem konsolide edildi")
    print(f"   Dosyalar: {res['files']}  {res['by_kind']}")
    if res["holder"]:
        print(f"   Hesap sahibi: {res['holder']}")
    print(f"   Kart↔hesap eşleşmesi: {res['matches']}  |  Ay: {res['months']}")
    print(f"\n📁 Çıktılar → {res['output_dir']}/")
    print("   • csv/*.csv        (5 ayrı tablo)")
    print("   • konsolide.xlsx   (çok sayfalı Excel)")
    print("   • finans.db        (SQLite)")
    print("   • rapor.md         (özet rapor)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
