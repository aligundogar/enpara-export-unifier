"""enpara-export-unifier — Enpara.com hesap/kart ekstrelerini tek bir veri setinde birleştirir.

Modüller:
  normalize   — tarih/para/metin normalizasyonu (Türkçe + ASCII katlama)
  categorize  — işlemleri kategoriye ayıran (düzenlenebilir) kural seti
  parsers     — 3 kaynak: kredi kartı PDF (OCR), hesap hareketleri PDF, Enpara XLS
  analyze     — tekrar eden ödemeler, kart↔hesap mükerrer eşleştirme, nakit akışı
  consolidate — tüm kaynakları birleştirip CSV / XLSX / Markdown / SQLite üretir
"""

__version__ = "0.1.0"

from .model import Transaction  # noqa: F401
