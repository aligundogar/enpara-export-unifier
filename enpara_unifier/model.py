"""Birleşik işlem (transaction) şeması.

İşaret (sign) kuralı — nakit akışı bakış açısı:
    amount > 0  -> hesaba/ kişiye para GİRİŞİ (gelir, gelen transfer, artı bakiye)
    amount < 0  -> para ÇIKIŞI (harcama, giden transfer, ödeme, vergi)

Kredi kartı harcamaları gerçek bir gider olduğu için NEGATİF saklanır.
Kredi kartına yapılan ödemeler ("Cep Şubesi" ödemesi) ise kişinin hesabından
karta giden iç transferdir -> internal_transfer=True ile işaretlenir ve
hesap tarafındaki "kredi kartı ödemesi" satırıyla eşleştirilir (analyze.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


SOURCE_CREDIT_CARD = "kredi_karti"
SOURCE_ACCOUNT = "vadesiz_hesap"


@dataclass
class Transaction:
    date: date
    source: str                      # SOURCE_CREDIT_CARD | SOURCE_ACCOUNT
    source_file: str
    description: str                 # temizlenmiş açıklama (Türkçe, OCR'lı)
    amount: float                    # işaretli — yukarıdaki kurala göre
    currency: str = "TL"
    balance: Optional[float] = None  # işlem sonrası bakiye (varsa)
    hareket_tipi: Optional[str] = None   # hesap hareket tipi (Ödeme, Gelen Transfer...)
    installment: Optional[str] = None    # kredi kartı taksiti, örn "2/3"
    category: str = "Diğer"
    internal_transfer: bool = False  # kart<->hesap iç transferi mi
    match_id: Optional[str] = None   # eşleşen iç transferin kimliği
    description_ascii: str = ""      # eşleştirme için ASCII-katlanmış büyük harf

    def to_row(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d


# Birleşik tablo kolon sırası (çıktılarda kullanılır)
COLUMNS = [
    "date", "source", "hareket_tipi", "description", "category",
    "amount", "currency", "balance", "installment",
    "internal_transfer", "match_id", "source_file",
]

# Türkçe kolon başlıkları (Excel/CSV için)
COLUMNS_TR = {
    "date": "Tarih",
    "source": "Kaynak",
    "hareket_tipi": "Hareket Tipi",
    "description": "Açıklama",
    "category": "Kategori",
    "amount": "Tutar",
    "currency": "Para Birimi",
    "balance": "Bakiye",
    "installment": "Taksit",
    "internal_transfer": "İç Transfer",
    "match_id": "Eşleşme",
    "source_file": "Kaynak Dosya",
}
