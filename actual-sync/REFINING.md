# Karşı-taraf ayıklama rehberi (Diğer Kişiler, gelir, kişi hesapları)

"Diğer Kişiler" bir **çöp kutusu**dur: tanınmayan kişi-kişi transferleri oraya
düşer. İçinde gerçek **gelir** (maaş/iş), **aile**, ya da ayrı hesap açmak
istediğin kişiler olabilir. İki yöntem var — biri kalıcı (önerilen), biri tek seferlik.

## Yöntem 1 — `counterparties.py` (KALICI, önerilen)

Kuralı bir kez yaz, her yeniden aktarımda otomatik uygulanır.

**1. Neyi ayıklayacağını gör.** Aralarındaki en büyük/sık kişileri listele:

```bash
cd enpara-export-unifier
. .venv/bin/activate
python3 - <<'PY'
import sqlite3
from collections import defaultdict
con=sqlite3.connect("output/finans.db")
g=defaultdict(lambda:[0,0.0])
for ac,t in con.execute("SELECT aciklama,tutar FROM islemler WHERE kategori LIKE 'Transfer: Diğer%'"):
    k=ac.split("-")[0].split(",")[0].strip()[:30]
    g[k][0]+=1; g[k][1]+=t
for k,(n,tot) in sorted(g.items(),key=lambda x:-abs(x[1][1]))[:30]:
    print(f"{k:<32}{tot:>12,.0f}{n:>4}")
PY
```

**2. Kuralı ekle.** `enpara_unifier/counterparties.py` içinde:

- **Gelir** (maaş/iş geliri ödeyen kişi/şirket) → `INCOME_RULES`'a:
  ```python
  ("ZEYNEP KAYA", "Maaş"),            # veya "İş Geliri (Firma X)"
  ```
- **Ayrı kişi hesabı** (sürekli alacak/verecek) → önce hesabı tanımla:
  ```python
  ACC_AHMET = "person:ahmet"
  OFFBUDGET_ACCOUNTS[ACC_AHMET] = ("Ahmet (arkadaş)", "kisi")
  ```
  sonra `TRANSFER_RULES`'a: `("AHMET YILMAZ", ACC_AHMET),`
- **Yatırım** → `OFFBUDGET_ACCOUNTS` + `TRANSFER_RULES` (tip `"yatirim"`).

> Anahtarları **ASCII büyük harf** yaz: "ZEYNEP KAYA", "DÖNER"→"DONER". Sıra önemli:
> INCOME → TRANSFER → öz → Diğer Kişiler.

**3. Yeniden aktar:**
```bash
python run.py /kaynak/klasör -o output
cd actual-sync && npm run apply && npm run verify
```
`imported_id` bakiye-bazlı kararlı olduğu için **mükerrer olmaz**; sadece
kategorisi/hesabı değişen kayıtlar güncellenir. (Yeni kişi hesabı eklediysen
önce `npm run reset` gerekebilir — eski "Diğer Kişiler"deki kayıtları taşımak için.)

## Yöntem 2 — Actual arayüzü (TEK SEFERLİK)

Kalıcı değil; bir sonraki aktarımda kural yoksa geri döner. Hızlı düzeltme için.

- **Bir işlemi yeniden kategorile:** Accounts → ilgili hesap → işleme tıkla →
  Category sütunundan seç.
- **Kişiyi ayrı hesaba çevir (transfer yap):** iki işlemi seç (senin hesabındaki
  çıkış + varsa karşı giriş) → sağ tık → **"Make transfer"**; ya da işlemin
  **Payee**'sini hedef hesabın adıyla değiştir (Actual transferi otomatik kurar).
- **Yeni kişi/borç hesabı aç:** sol menü → hesap ekle → **Off-budget** seç →
  ilgili transferlerin payee'sini bu hesaba yönlendir. Hesabın bakiyesi = net
  alacak/verecek.
- **Kural yaz (UI):** More → Rules → koşul: *imported payee contains "AHMET"* →
  aksiyon: *set category/payee*. Bu da kalıcıdır ama Actual tarafında durur.

## Sonra not çıkarmak istersen

Bana şu formatta bir liste ver, kuralları ben `counterparties.py`'ye işlerim:

```
ZEYNEP KAYA      -> gelir: Maaş
AHMET YILMAZ     -> kişi hesabı: Ahmet (arkadaş)
MEHMET DEMIR     -> aile (kendi hesabında kalsın)
XYZ LTD          -> gelir: İş Geliri (XYZ)
```

İsimleri Actual'da gördüğün gibi yazman yeterli; gerisini hallederim.
