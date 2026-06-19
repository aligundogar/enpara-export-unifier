# actual-sync — finans.db → Actual Budget

Konsolide Enpara verisini ([enpara-export-unifier](../) çıktısı `output/finans.db`)
self-hosted [Actual Budget](https://actualbudget.org)'a aktarır.

- **2 hesap** doğru açılış bakiyeleriyle: `Enpara Vadesiz TL`, `Enpara Kredi Kartı`
- **Türkçe kategori** grupları + kategoriler
- Kredi kartı ödemeleri **gerçek transfer** (her iki hesabın bakiyesi reconcile olur)
- **Idempotent**: `imported_id` ile mükerrer engelleme — tekrar çalıştırınca çift kayıt olmaz
- **Bakiye çapası**: "Varlık ve Borç Dökümü" snapshot'ı varsa, kart bakiyesini
  gerçek (faturalanmamış dahil) borca tek bir uzlaştırma işlemiyle çeker

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

```bash
npm run dry-run    # hiçbir şey yazmaz; ne olacağını raporlar
npm run apply      # canlı bütçeye aktarır (idempotent)
npm run verify     # Actual bakiyeleri finans.db ile tutuyor mu (çapa dahil)
npm run reset      # bu aracın oluşturduğu hesapları siler (--all: kategorileri de)
```

### Aylık akış (tekrar eden kullanım)

```bash
# 1) yeni ekstre/özet/dökümleri kaynak klasöre ekle
python ../run.py /kaynak/klasor -o ../output     # finans.db güncellenir
npm run apply                                     # sadece yeni işlemler eklenir
npm run verify                                    # bakiyeler tutuyor mu
```

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
