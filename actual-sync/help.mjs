#!/usr/bin/env node
/**
 * Sistem yardımı — tüm akışı ve komutları listeler.  node help.mjs  (npm run help)
 */
console.log(`
tr-bank-to-actualbudget — actual-sync komutları
================================================

Akış:
  banka PDF/XLS → python run.py <klasör> -o output → output/finans.db
                                                       │
                                                       ├─ API yolu  →  npm run apply
                                                       └─ dosya yolu →  npm run export

Komutlar (npm run <ad>  ·  her birinde --help):
  dry-run     node sync.mjs            Aktarımı önizle (yazmaz)
  apply       node sync.mjs --apply    Actual sunucusuna canlı yaz (idempotent, transfer+çapa)
  verify      node verify.mjs          Bakiyeler Actual'da tutuyor mu (çıkış kodu ≠0 = fark)
  budget      node budget.mjs          Veriye dayalı bütçe (de-lumped); --apply ile yaz
  export      node export.mjs          API'SİZ dosya importu üret (CSV/QIF, hesap başına)
  reset       node reset.mjs           Oluşturulan hesapları sil (temiz yeniden aktarım)
  help        node help.mjs            Bu yardım

İki import yolu:
  • API   (sync.mjs)   — akıllı: hesap/kategori oluşturur, transfer eşler, çapa uzlaştırır,
                         idempotent. Çalışan Actual sunucusu + config.json gerekir.
  • DOSYA (export.mjs) — taşınabilir: hesap başına CSV/QIF; Actual'da hesap → Import.
                         Sunucu/şifre gerekmez. Transferler otomatik bağlanmaz.

Yapılandırma:  actual-sync/config.json  (örnek: config.example.json) veya ACTUAL_* env.
Ayrıntı:       actual-sync/README.md
`);
