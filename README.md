# Enpara Export Unifier

> Enpara.com'un dağınık hesap/kredi kartı ekstrelerini (PDF + XLS) **tek bir
> birleşik veri setine** dönüştürür; kategorize eder, tekrar eden ödemeleri ve
> kart↔hesap mükerrer kayıtlarını bulur, nakit akışını çıkarır.
>
> *Unifies Enpara.com's scattered account & credit-card statements (PDF + XLS)
> into one consolidated dataset with categorization, recurring-payment & internal-
> transfer detection, and cash-flow analysis.*

Çıktı 4 formatta: **CSV**, **çok sayfalı Excel (.xlsx)**, **SQLite (.db)** ve
okunabilir **Markdown rapor**.

---

## Neden?

Enpara birden çok belge türü verir ve hiçbiri tek başına yeterli değildir:

| Belge | İçerik | Sorun |
|---|---|---|
| `... tarihli ekstreniz.pdf` | Kredi kartı harcamaları | **Satıcı isimleri bozuk** (aşağıya bkz.) |
| `1- Enpara Hesap Hareketleri.pdf` | Tüm hesap hareketleri | Temiz, ama karttan ayrı |
| `... ayı hesap ve ihtiyaç kredisi özetiniz.pdf` | Aylık bakiye + kredi durumu | Sadece snapshot |
| `Enpara hesap hareketleriniz.xls` | Hesap hareketleri (eski .xls) | Karttan ayrı, çakışıyor |

Bu araç hepsini okuyup tek tabloda birleştirir ve çakışan kayıtları teke indirir.

### Kredi kartı PDF'lerindeki "bozuk Türkçe" sorunu

Enpara'nın kredi kartı ekstreleri, gömülü fontları **alt-kümeleyip** `ToUnicode`
haritası koymadan üretir. Sonuç: PDF'in metin katmanında Türkçe'ye özel harfler
(ş, ç, ö, ü, ı, ğ ve büyükleri) ya düşer ya da kontrol kodlarına eşlenir — ve bu
eşleme **her ay farklıdır**. Yani bu PDF'lerden kopyala-yapıştır da bozuk çıkar.

**Çözüm:** Tarih ve tutarlar saf ASCII olduğu için metinden birebir okunur;
satıcı **açıklamaları ise sayfa görüntüsünden OCR (tesseract, Türkçe)** ile
geri kazanılır. Doğrulama: çıkarılan harcama/ödeme toplamları ekstrenin kendi
özet rakamlarıyla kuruşu kuruşuna tutar.

---

## Kurulum

**1) Sistem bağımlılığı — Tesseract OCR (Türkçe dil paketi ile):**

```bash
# Debian / Ubuntu / Kali
sudo apt install tesseract-ocr tesseract-ocr-tur

# macOS
brew install tesseract tesseract-lang

# Windows: https://github.com/UB-Mannheim/tesseract/wiki  (kurulumda Turkish seçin)
```

**2) Python paketleri:**

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> OCR'sız da çalışır (`--no-ocr`): tarih/tutar/analiz tam doğru olur, sadece
> kredi kartı satıcı isimleri bozuk kalır. Hesap PDF/XLS açıklamaları zaten temizdir.

---

## Kullanım

Tüm ekstreleri bir klasöre koyun ve çalıştırın:

```bash
python run.py /ekstrelerin/olduğu/klasör -o output
```

Seçenekler:

| Bayrak | Açıklama |
|---|---|
| `-o, --output` | Çıktı klasörü (varsayılan: `output`) |
| `--no-ocr` | OCR'ı atla (hızlı; kart satıcı isimleri bozuk kalır) |

OCR sonuçları `output/.ocr_cache.json` içinde önbelleğe alınır; tekrar
çalıştırmalar anında biter. Gelecek ayların ekstrelerini aynı klasöre ekleyip
yeniden çalıştırmanız yeterli — format aynı olduğu sürece otomatik işlenir.

---

## Çıktılar

```
output/
├── csv/
│   ├── islemler.csv               # tüm birleşik işlemler
│   ├── aylik_nakit_akisi.csv      # ay bazında gelir/gider/net
│   ├── kategori_dagilimi.csv      # kategori bazında gider
│   ├── tekrar_eden_odemeler.csv   # abonelik/düzenli ödemeler
│   └── bakiye_kredi_snapshot.csv  # aylık bakiye + kredi durumu
├── konsolide.xlsx                 # yukarıdakilerin hepsi (çok sayfalı)
├── finans.db                      # SQLite — sorgulanabilir
└── rapor.md                       # okunabilir özet rapor
```

### Birleşik işlem şeması (`islemler`)

| Kolon | Anlam |
|---|---|
| Tarih | ISO tarih |
| Kaynak | `kredi_karti` / `vadesiz_hesap` |
| Hareket Tipi | Ödeme / Gelen Transfer / Giden Transfer / Vergi Kesintisi… |
| Açıklama | Temizlenmiş açıklama (kart için OCR'lı) |
| Kategori | Otomatik kategori (bkz. `categorize.py`) |
| Tutar | **İşaretli: + giriş / − çıkış** |
| Bakiye | İşlem sonrası bakiye (varsa) |
| Taksit | Kredi kartı taksiti (örn `2/3`) |
| İç Transfer | Kart↔hesap / öz transfer mi |
| Eşleşme | Eşleşen iç transferin kimliği |
| Kaynak Dosya | Kaydın geldiği dosya |

---

## Analizler

- **Tekilleştirme** — XLS / Hesap PDF / Özet PDF arasında çakışan hesap
  satırları `(tarih + tutar + bakiye)` ile teke indirilir.
- **Kart ↔ hesap eşleştirme** — karttaki "Cep Şubesi" ödemesi ile hesaptaki
  "kredi kartı ödemesi" tutar + yakın tarihe göre eşleştirilir (`match_id`).
- **Öz transfer tespiti** — kişinin kendi adına gelen/giden transferleri iç
  transfer sayılır ve gelir/gider toplamından dışlanır (hesap sahibi adı
  belgelerden otomatik bulunur).
- **Tekrar eden ödemeler** — aynı satıcıda ≥3 farklı ayda görülen ödemeler.
- **Nakit akışı** — aylık gelir / gider / net (iç transferler hariç; gider =
  hesap çıkışları + kart harcamaları, çift sayım olmadan).

---

## Actual Budget'a aktarım (`actual-sync/`)

`output/finans.db`'yi self-hosted [Actual Budget](https://actualbudget.org)'a
aktaran Node aracı. 2 hesap (doğru açılış bakiyeleriyle) + Türkçe kategoriler +
işlemler; kart ödemeleri **gerçek transfer**, **idempotent** (mükerrer engelleme),
ve "Varlık ve Borç Dökümü" varsa kart bakiyesini gerçek borca çeken **bakiye
çapası**.

```bash
cd actual-sync && npm install && cp config.example.json config.json   # düzenle
npm run dry-run   # önizleme   ·   npm run apply   # aktar   ·   npm run verify
```

Ayrıntılar: [`actual-sync/README.md`](actual-sync/README.md).

---

## Kategorileri özelleştirme

Kategori kuralları `enpara_unifier/categorize.py` içinde basit anahtar-kelime
listeleridir. Eşleştirme Türkçe karakterler ASCII'ye katlanmış BÜYÜK harf metin
üzerinde yapılır — yeni bir satıcı eklemek için ilgili kategoriye bir kelime
eklemeniz yeterli (örn `"MIGROS"`, `"DONER"`).

---

## Proje yapısı

```
enpara_unifier/
├── model.py        # birleşik Transaction şeması + işaret kuralı + hesap anahtarları
├── normalize.py    # tarih / para / metin (TR↔ASCII) normalizasyonu
├── categorize.py   # düzenlenebilir kategori kuralları (gider)
├── counterparties.py # karşı-taraf yönlendirme: gelir/kişi/yatırım/öz-transfer
├── parsers.py      # parser'lar: kart PDF+OCR, hesap PDF, özet PDF, Enpara XLS, Garanti XLS
├── analyze.py      # dedup, genel transfer eşleştirme, tekrar eden, nakit akışı
└── consolidate.py  # orkestratör + 4 format yazıcı + hesap_meta/bakiye_capa
run.py              # CLI
actual-sync/        # finans.db → Actual Budget aktarıcı (Node) — kendi README'si
```

### Çok bankalı

Enpara dışında başka bankaların (ör. **Garanti** XLS) hesap hareketleri de eklenebilir.
Her banka Actual'da ayrı **hesap** olur; bankalar arası kendi transferlerin (ör.
Garanti↔Enpara) **gerçek transfer** olarak eşleşir. Kişiler (alacak/verecek) ve
yatırım platformları **off-budget hesap** olur; maaş/iş geliri ödeyenler **gelir**
sayılır. Yönlendirme kuralları `enpara_unifier/counterparties.py` içinde düzenlenebilir.

---

## Gizlilik

Bu araç tamamen **yerel** çalışır; hiçbir veri dışarı gönderilmez. `.gitignore`
tüm `*.pdf`, `*.xls`, `*.xlsx` ve `output/` klasörünü hariç tutar — kişisel
finansal verinizi yanlışlıkla commit etmezsiniz.

## Lisans

MIT — kişisel ve resmî olmayan bir araçtır; Enpara.com / QNB ile ilişiği yoktur.
