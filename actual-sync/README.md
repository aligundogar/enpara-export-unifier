# actual-sync — finans.db → Actual Budget

Konsolide çok-bankalı veriyi ([repo kökü](../) çıktısı `output/finans.db`)
self-hosted [Actual Budget](https://actualbudget.org)'a aktarır.

- **Tüm hesaplar** doğru açılış bakiyeleriyle: bankalar (on-budget) + kişi/yatırım (off-budget)
- **Türkçe kategori** grupları + kategoriler; maaş/iş geliri gelir olarak tanınır
- Kart↔hesap ve banka↔banka **gerçek transfer** (her iki bakiye reconcile olur)
- **Idempotent**: `imported_id` ile mükerrer engelleme — tekrar çalıştırınca çift kayıt olmaz
- **Bakiye çapası**: snapshot'tan gerçek (faturalanmamış/nakit-dışı) borç/bakiyeye uzlaştırır
- **İki import yolu**: API (akıllı) veya hesap-başına CSV/QIF dosyası (taşınabilir)
- **Veriye dayalı bütçe**: de-lumped kategori ortalamaları + goal-template'ler

## Kurulum

```bash
cd actual-sync
npm install
cp config.example.json config.json   # düzenleyin (gitignore'lu)
```

`config.json`:

```json
{
  "serverURL": "http://SUNUCU:5006",
  "password": "ACTUAL_SUNUCU_SIFRENIZ",
  "syncId": "Settings → Advanced → Sync ID",
  "encryptionPassword": null,
  "dbPath": "../output/finans.db"
}
```

> Aynı değerler ortam değişkenleriyle de verilebilir: `ACTUAL_SERVER_URL`,
> `ACTUAL_PASSWORD`, `ACTUAL_SYNC_ID`, `ACTUAL_ENCRYPTION_PASSWORD`, `ACTUAL_DB_PATH`.
> **Sync ID**'yi programatik bulmak için: `POST /account/login` → token →
> `GET /sync/list-user-files` (`groupId` alanı).

## Kullanım

İki içe-aktarma yolu vardır:

- **API yolu** (`sync.mjs`) — akıllı: hesap/kategori oluşturur, transferleri eşler,
  bakiye çapalarını uzlaştırır, idempotenttir. Çalışan Actual sunucusu + `config.json` ister.
- **Dosya yolu** (`export.mjs`) — taşınabilir: hesap başına CSV/QIF üretir, Actual'da
  hesap → **Import** ile yüklersin. Sunucu/şifre gerekmez (aşağıda).

```bash
npm run dry-run    # API: hiçbir şey yazmaz; ne olacağını raporlar
npm run apply      # API: canlı bütçeye aktarır (idempotent)
npm run verify     # Actual bakiyeleri finans.db ile tutuyor mu (çapa dahil)
npm run budget     # veriye dayalı bütçe önerisi (--apply ile yazar)
npm run export     # API'siz: hesap başına CSV/QIF import dosyaları üretir
npm run reset      # bu aracın oluşturduğu hesapları siler (--all: kategorileri de)
npm run help       # tüm komutların özeti
```

> Her script `--help` destekler: `node sync.mjs --help`, `node budget.mjs --help`, …
> npm üzerinden bayrak geçmek için çift tire: `npm run budget -- --apply --outlier 3000`.

### Aylık akış (tekrar eden kullanım)

```bash
# 1) yeni ekstre/özet/dökümleri kaynak klasöre ekle
python ../run.py /kaynak/klasor -o ../output     # finans.db güncellenir
npm run apply                                     # sadece yeni işlemler eklenir
npm run verify                                    # bakiyeler tutuyor mu
```

### Dosya-tabanlı import (API'siz)

Sunucuya programatik bağlanmadan, Actual'ın kendi içe-aktarma özelliğiyle yüklemek için:

```bash
npm run export                 # CSV → ../output/actual-import/  (+ _OKU.md rehberi)
node export.mjs --format qif   # QIF üret
node export.mjs --out <dizin>  # çıktı dizinini değiştir
```

Üretilen her dosya **bir hesaba** aittir. Actual'da: hesabı aç → ⋯ → **Import** →
dosyayı seç (CSV'de sütun eşlemesi: Date/Payee/Notes/Category/Amount, tarih `YYYY-MM-DD`).
Bakiyeler `verify.mjs` modeliyle uyumlu üretilir (açılış + çapa satırları dahil).

> ⚠️ Düz dosya importu **transferi otomatik bağlamaz** ve **idempotent değildir**
> (aynı dosyayı iki kez içe aktarma). Akıllı transfer + tekrar-güvenli aktarım için API yolu (`npm run apply`).

### Bütçe kurma (`budget.mjs`)

Gider kategorilerinin son N **tam** ayının düzenli ortalamasını hesaplar; tek-seferlik
büyük alımları (≥ `--outlier` TL) tabandan ayıklar ve ayrı raporlar:

```bash
npm run budget                            # önizleme + one-off raporu (yazmaz)
npm run budget -- --apply                 # güncel aya bütçe + sabit template notları
node budget.mjs --apply --month 2026-06 --lookback 6 --outlier 3000
```

De-lumped tabanlar `setBudgetAmount` ile yazılır; kategori notuna **sabit**
`#template <taban>` düşülür — böylece "Apply budget template" gelecekte one-off'ları
geri dahil etmez. Gelir/transfer kategorileri bütçelenmez.

## Tasarım notları (önemli)

- **İşaret**: `+` giriş / `−` çıkış (kuruş tamsayı). Kart harcaması `−`, karta ödeme
  iç transfer.
- **`imported_id` kararlıdır**: hesap satırlarında değişken açıklama yerine
  **`bakiye`** alanından üretilir (`kaynak:tarih:tutar:Bbakiye`). Böylece farklı
  kaynak dosyalar aynı işlemi farklı kelimelerle anlatsa bile id değişmez ve
  yeni dosya ekleyince mükerrer oluşmaz.
- **Transferler** `importTransactions` + transfer payee ile kurulur (Actual karşı
  tarafı otomatik yaratır); `addTransactions`'ın `runTransfers`'ı sürümlere göre
  güvenilmezdir.
- **Bakiye çapası** sabit `imported_id` (`ANCHOR-<hesap>`) ile tutulur; her
  çalıştırmada güncellenir, yeni ekstreler geldikçe küçülür.

## Budget sekmesi "garip" mi görünüyor?

Actual varsayılan **zarf (envelope)** bütçeleme yapar; 2 yıllık geçmişi bütçe
atamadan yüklediğinizde "overspent / negatif available" görünür — bu normaldir,
hesap bakiyeleri yine doğrudur (`npm run verify`). Geçmiş analizi için
**Settings → Budget type → Tracking** moduna geçmek daha uygundur; ya da Budget
sekmesini yok sayıp **Reports**'u kullanın.

## Gereksinimler

Node 22+ (yerleşik `node:sqlite` için), çalışan bir Actual sync server.
`config.json`, `data/`, `node_modules/` gitignore'ludur — kişisel veri/şifre
repoya girmez.
