"""İşlem kategorilendirme — DÜZENLENEBİLİR kural seti.

Eşleştirme `normalize.ascii_fold()` ile ASCII'ye katlanmış BÜYÜK harf metin
üzerinde yapılır; yani buradaki anahtar kelimeleri de ASCII büyük harf yazın
("MİGROS" yerine "MIGROS", "DÖNER" yerine "DONER").

Kurallar yukarıdan aşağıya denenir; ilk eşleşen kazanır. Yeni satıcı eklemek
için ilgili kategorinin listesine bir anahtar kelime eklemeniz yeterli.
"""

from __future__ import annotations

from .normalize import ascii_fold

# Hesap "Hareket tipi" alanına göre kesin kategoriler (satıcı metninden önce gelir)
HAREKET_TIPI_MAP = {
    "GELEN TRANSFER": "Gelen Transfer",
    "GIDEN TRANSFER": "Giden Transfer",
    "VERGI KESINTISI": "Vergi & Kesinti",
    "FAIZ": "Faiz & Ücret",
}

# (kategori, [anahtar kelimeler])  — sıra önemli: spesifik olan üstte
RULES: list[tuple[str, list[str]]] = [
    ("Kredi Kartı Ödemesi", [
        "KREDI KARTI ODEMESI", "ODEME - ENPARA.COM CEP", "CEP SUBESI",
    ]),
    ("Kredi Taksiti", [
        "IHTIYAC KREDI", "VADELI IHTIYAC", "TAKSITI", "KREDI TAKSIT",
    ]),
    ("Vergi & Kesinti", ["KKDF", "BSMV", "VERGI", "STOPAJ", "DAMGA"]),
    ("Sağlık & Eczane", ["ECZANE", "ECZ ", "HASTANE", "TIP MERKEZI", "DIS KLINIK", "SAGLIK"]),
    ("Online & Dijital Servisler", [
        "GOOGLE", "SPOTIFY", "NETFLIX", "TWITCH", "LINKEDIN", "AMAZON", "AMAZONPRIME",
        "IYZICO", "HEPSIPAY", "HEPSIBURADA", "PARAM/", "PAYCELL", "MASTERPASS",
        "STRIPE", "KASPERSKY", "NEXWAY", "VIPERNEWS", "LIVEUAMAP", "APPLE.COM",
        "MOKA UNITED", "MICROSOFT", "OPENAI", "Z.AI", "STEAM",
    ]),
    ("Faturalar & Abonelikler", [
        "VODAFONE", "TURKCELL", "AVEA", "TURK TELEKOM", "KONTOR", "FATURA",
        "ELEKTRIK", "DOGALGAZ", "SU FATURA", "ISKI", "ASKI", "BEDAS", "EWE",
    ]),
    ("Ulaşım & Akaryakıt", [
        "TOPLU TASIMA", "EGO KART", "EGO ", "UBER", "YANDEX", "OBILET", "BILET",
        "OTOBUS", "METRO ", "TRAMVAY", "TAKSI", "OTEL", "GEZGIN", "TURIZM",
        "OPET", "SHELL", "PETROL", " BP ", "TOTAL", "AYTEMIZ", "AKARYAKIT",
        "ARAC ICI GECIS", "GAR ",
    ]),
    ("Yeme-İçme", [
        "CAFE", "COFFEE", "KAFE", "KAHVE", "RESTORAN", "RESTAURANT", "DONER",
        "PIZZA", "PIDE", "BUFE", "FIRIN", "SEKERLEME", "PASTA", "YEMEK",
        "KOMAGENE", "SUBWAY", "POPOYES", "CIGKOFTE", "SOSISCI", "KANTIN",
        "HMBRGR", "BURGER", "NERO", "CASTELLO", "ARABICA", "COLOMBIA", "COLAMBIA",
        "MADRID", "MOJO", "SAMKO", "BELPA", "DANTEL", "KENNEDY", "WINSTON",
        "MAYDONOZ", "ADIYAMAN", "BEKA", "HACIBABA", "DONUS", "LIZBON", "ANITTA",
        "YEMEKSEPETI", "TRENDYOL - YEMEK", "TRENDYOL- YEMEK", "FC UP",
    ]),
    ("Market & Gıda", [
        "A101", "BIM ", "BIM A.S", "MIGROS", "MGROS", "MACROCENTER", "MARKET",
        "GIDA", "BAKKAL", "MANAV", "KASAP", "SARKUTERI", "SOK ", "CARREFOUR",
        "HAKMAR", "HAPPY CENTER", "MRDIY", "MR DIY",
    ]),
    ("Sağlık & Kişisel Bakım", ["GRATIS", "WATSONS", "KUAFOR", "BERBER", "KOZMETIK"]),
    ("Giyim & Mağaza", [
        "BOYNER", "LCW", "LC WAIKIKI", "DEFACTO", "KOTON", "ZARA", "MAVI",
        "HEDIYELIK", "EL SANATLARI", "AYLAK DUKKAN", "MAGAZA", "MAG ",
    ]),
    ("Elektronik & Teknoloji", [
        "MEDIA MARKT", "MEDIAMARKT", "TEKNOSA", "VATAN", "ILETISIM", "TEKNOLOJI",
        "BILISIM", "ELEKTRONIK", "FOTO", "COLOR MEDYA",
    ]),
    ("Eğitim & Kırtasiye", [
        "KIRTASIYE", "KITAP", "YAYINCILIK", "YAYINEVI", "KYK", "YURT TAHSILATI",
        "UNIVERSITE", "OKUL", "KURS", "METUNIC", "METU",
    ]),
    ("Eğlence & Kültür", [
        "SINEMA", "ICE ARENA", "PARK DINLEN", "TIYATRO", "KONSER", "MUZE",
        "OYUN", "WORLD OF", "SPOR",
    ]),
    ("Nakit & ATM", ["ATM", "NAKIT AVANS", "PARA CEKME", "QNB ATM"]),
]


def categorize(description: str, source: str, hareket_tipi: str | None = None) -> str:
    """Bir işlem için kategori döndürür."""
    if hareket_tipi:
        ht = ascii_fold(hareket_tipi)
        # "kredi kartı ödemesi" gibi açıklamalar 'Ödeme' tipindedir; önce metne bak
        if ht in HAREKET_TIPI_MAP and ht not in ("GELEN TRANSFER", "GIDEN TRANSFER"):
            # vergi/faiz gibi kesin tipler
            if ht in ("VERGI KESINTISI", "FAIZ"):
                # yine de kredi taksiti açıklaması varsa onu koru
                pass
    text = ascii_fold(description)

    for category, keywords in RULES:
        for kw in keywords:
            if kw in text:
                return category

    # metinden bulunamadıysa hesap hareket tipine düş
    if hareket_tipi:
        ht = ascii_fold(hareket_tipi)
        if ht in HAREKET_TIPI_MAP:
            return HAREKET_TIPI_MAP[ht]
        if ht == "ODEME":
            return "Diğer Ödeme"

    return "Diğer"
