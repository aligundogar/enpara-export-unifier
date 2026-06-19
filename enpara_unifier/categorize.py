"""İşlem kategorilendirme — DÜZENLENEBİLİR kural seti.

Eşleştirme `normalize.ascii_fold()` ile ASCII'ye katlanmış BÜYÜK harf metin
üzerinde yapılır; anahtar kelimeleri de ASCII büyük harf yazın ("MİGROS"→"MIGROS").

Ödeme aracıları (IYZICO/PARAM/PAYCELL/MOKA/HEPSIPAY/N KOLAY/GOOGLE *) gerçek
satıcıyı gizler; bu yüzden önce "/" veya "*" sonrasındaki asıl satıcı çözülür
(_unwrap) ve kategori ona göre belirlenir.

Kurallar yukarıdan aşağıya; ilk eşleşen kazanır (spesifik/resmi olan üstte).
"""

from __future__ import annotations

import re
from .normalize import ascii_fold

# Hesap "Hareket tipi" alanına göre kesin kategoriler
HAREKET_TIPI_MAP = {
    "GELEN TRANSFER": "Gelen Transfer",
    "GIDEN TRANSFER": "Giden Transfer",
    "VERGI KESINTISI": "Vergi & Kesinti",
    "FAIZ": "Faiz & Ücret",
    "KART ODEMESI": "Kredi Kartı Ödemesi",
}

# Ödeme aracısı önekleri — asıl satıcıyı "/" veya "*" sonrasında ara
_PROCESSORS = ["IYZICO", "PARAM", "PAYCELL", "MOKA UNITED", "HEPSIPAY",
               "N KOLAY", "GOOGLE *", "ODEAL", "MASTERPASS/"]


def _unwrap(text: str) -> str | None:
    if any(p in text for p in _PROCESSORS):
        parts = re.split(r"[/*]", text)
        if len(parts) > 1:
            return parts[-1].strip()
    return None


# (kategori, [anahtar kelimeler]) — sıra önemli
RULES: list[tuple[str, list[str]]] = [
    ("Kredi Kartı Ödemesi", [
        "KREDI KARTI ODEMESI", "ODEME - ENPARA.COM CEP", "CEP SUBESI",
        "K.KARTI ODEME", "KARTI ODEME", "KART ODEMESI",
    ]),
    ("Kredi Taksiti", ["IHTIYAC KREDI", "VADELI IHTIYAC", "TAKSITI", "KREDI TAKSIT"]),
    ("Resmi & Harç", [
        "HARC", "M.T.V", " MTV", "RUHSAT", "TRAFIK CEZA", "PARA CEZA", "IDARI PARA",
        "NVI", "NUFUS", "PASAPORT", "E-DEVLET", "EDEVLET", "GIB ", "VERGI DAIRE",
        "TAPU", "ADLIYE", "ICRA", "BELEDIYE",
    ]),
    ("Vergi & Kesinti", ["KKDF", "BSMV", "VERGI", "STOPAJ", "DAMGA", "KESINTI VE EKLER"]),
    ("Yatırım & Tasarruf", [
        "HISSE", "NEMA", " FON ", "BIST", "ALTIN HESAP", "VADELI HESAP",
        "DOVIZ ALIS", "DOVIZ SATIS", "KIYMETLI MADEN",
    ]),
    ("Bağış & Yardım", ["DERNEK", "VAKIF", "DOSTLARI", "YARDIMLAS", "BAGIS", "AHBAP"]),
    ("Sağlık & Eczane", ["ECZANE", "ECZ ", "HASTANE", "TIP MERKEZI", "DIS KLINIK",
                         "SAGLIK", "POLIKLINIK", "LABORATUVAR", "MEDIKAL"]),
    ("Eğitim & Kırtasiye", [
        "KIRTASIYE", "KITAP", "YAYINCILIK", "YAYINEVI", "KYK", "YURT TAHSILATI",
        "UNIVERSITE", "OKUL", "KURS", "METUNIC", "METU", "EHLIYE", "SURUCU",
        "EGITIM", "DERSHANE", "AKADEMI",
    ]),
    ("Faturalar & Abonelikler", [
        "VODAFONE", "TURKCELL", "AVEA", "TURK TELEKOM", "KONTOR", "FATURA",
        "ELEKTRIK", "DOGALGAZ", "SU FATURA", "ISKI", "ASKI", "BEDAS", "EWE",
        "SPOTIFY", "NETFLIX", "YOUTUBE PREMIUM", "AMAZONPRIME", "AMAZON PRIME",
        "DISNEY", "BLUTV", "EXXEN", "GAIN",
    ]),
    ("Online & Dijital Servisler", [
        "GOOGLE", "TWITCH", "LINKEDIN", "KASPERSKY", "STRIPE", "VIPERNEWS",
        "LIVEUAMAP", "APPLE.COM", "ICLOUD", "MICROSOFT", "OPENAI", "Z.AI",
        "STEAM", "NEXWAY", "PATREON", "GITHUB", "NAMECHEAP", "CLOUDFLARE", "OVH",
    ]),
    ("Ulaşım & Akaryakıt", [
        "TOPLU TASIMA", "EGO KART", "EGO ", "UBER", "YANDEX", "OBILET", "BILET",
        "OTOBUS", "TRAMVAY", "TAKSI", "OTEL", "GEZGIN", "TURIZM", "BILETALL",
        "OPET", "SHELL", "PETROL", " BP ", "TOTAL", "AYTEMIZ", "AKARYAKIT",
        "ARAC ICI GECIS", "HGS", "OGS", "GAR ", "PEGASUS", "THY", "TURKISH",
    ]),
    ("Yeme-İçme", [
        "CAFE", "COFFEE", "KAFE", "KAHVE", "CAYEVI", "RESTORAN", "RESTAURANT",
        "DONER", "PIZZA", "PIDE", "BUFE", "FIRIN", "SEKERLEME", "PASTA", "YEMEK",
        "KOMAGENE", "SUBWAY", "POPOYES", "CIGKOFTE", "SOSISCI", "KANTIN",
        "HMBRGR", "BURGER", "NERO", "CASTELLO", "ARABICA", "COLOMBIA", "COLAMBIA",
        "MADRID", "MOJO", "SAMKO", "BELPA", "DANTEL", "KENNEDY", "WINSTON",
        "MAYDONOZ", "ADIYAMAN", "BEKA", "HACIBABA", "DONUS", "LIZBON", "ANITTA",
        "YEMEKSEPETI", "TRENDYOL - YEMEK", "TRENDYOL- YEMEK", "FC UP", "HOT DONER",
        "MEZE", "OCAKBASI", "KEBAP", "TATLI", "BISTRO", "LOKANTA",
    ]),
    ("Market & Gıda", [
        "A101", "BIM ", "BIM A.S", "MIGROS", "MGROS", "MACROCENTER", "MARKET",
        "GIDA", "BAKKAL", "MANAV", "KASAP", "SARKUTERI", "SOK-", "SOK ", "CARREFOUR",
        "HAKMAR", "HAPPY CENTER", "MRDIY", "MR DIY", "TARIM KREDI", "FILE ",
    ]),
    ("Sağlık & Kişisel Bakım", ["GRATIS", "WATSONS", "KUAFOR", "BERBER", "KOZMETIK", "ROSSMANN"]),
    ("Giyim & Alışveriş", [
        "BOYNER", "LCW", "LC WAIKIKI", "DEFACTO", "KOTON", "ZARA", "MAVI",
        "HEDIYELIK", "EL SANATLARI", "AYLAK DUKKAN", "MAGAZA", "MAG ", "CICEK",
        "AMAZON", "HEPSIBURADA", "TRENDYOL", "N11", "MORHIPO", "GITTIGIDIYOR",
        "PTTAVM", "DECATHLON", "IKEA", "KOCTAS", "BAUHAUS",
    ]),
    ("Elektronik & Teknoloji", [
        "MEDIA MARKT", "MEDIAMARKT", "TEKNOSA", "VATAN", "ILETISIM", "TEKNOLOJI",
        "BILISIM", "ELEKTRONIK", "FOTO", "COLOR MEDYA", "INCEHESAP", "ITOPYA",
    ]),
    ("Eğlence & Kültür", [
        "SINEMA", "ICE ARENA", "PARK DINLEN", "TIYATRO", "KONSER", "MUZE",
        "OYUN", "WORLD OF", "SPOR SALON", "FITNESS", "MACFIT", "GYM",
    ]),
    ("Nakit & ATM", ["ATM", "NAKIT AVANS", "PARA CEKME", "QNB ATM", "PARA YATIRMA"]),
]


def categorize_strict(description: str, hareket_tipi: str | None = None) -> str | None:
    """Anahtar kelimeyle eşleşirse kategori, yoksa None (kişi-yönlendirmeden önce
    çalıştırılır → bilinen satıcı/resmi ödeme kişi hesabı olmaz)."""
    text = ascii_fold(description)
    inner = _unwrap(text)
    for probe in ([inner, text] if inner else [text]):
        if not probe:
            continue
        for category, keywords in RULES:
            for kw in keywords:
                if kw in probe:
                    return category
    if hareket_tipi:
        ht = ascii_fold(hareket_tipi)
        if ht in ("KART ODEMESI", "VERGI KESINTISI", "FAIZ"):
            return HAREKET_TIPI_MAP[ht]
    return None


def categorize(description: str, source: str, hareket_tipi: str | None = None) -> str:
    """Kesin kategori; bulunamazsa hareket tipine, o da yoksa 'Diğer'e düşer."""
    cat = categorize_strict(description, hareket_tipi)
    if cat:
        return cat
    if hareket_tipi:
        ht = ascii_fold(hareket_tipi)
        if ht in HAREKET_TIPI_MAP:
            return HAREKET_TIPI_MAP[ht]
        if ht == "ODEME":
            return "Diğer Ödeme"
    return "Diğer"
