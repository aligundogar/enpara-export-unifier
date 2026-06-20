#!/usr/bin/env node
/**
 * Veriye dayalı bütçe kurucu (de-lumped / "düzenli taban").
 *
 * Gider kategorilerinin gerçek AYLIK DÜZENLİ ortalamasını hesaplar:
 *   • Tamamlanmamış güncel ayı pencereden çıkarır (yarım ay ortalamayı bozmasın).
 *   • TEK-SEFERLİK büyük alımları (>= --outlier TL) tabandan ayıklar, ayrı listeler
 *     (ör. Hepsiburada 13.599, Amazon 8.289 → Giyim ortalamasını şişirmesin).
 *   • Kalan "düzenli taban"ı kategori bütçesine setBudgetAmount ile DOĞRUDAN yazar.
 *   • Kategori notuna SABİT goal-template (#template <taban>) yazar — Actual'da
 *     "Apply budget template" dediğinde one-off'lar geri dahil OLMAZ (average değil).
 *
 * Gelir / transfer / öz-transfer kategorileri bütçelenmez.
 *
 * Kullanım:
 *   node budget.mjs                            # DRY-RUN (önizleme + one-off raporu)
 *   node budget.mjs --apply                    # güncel ay + sabit template notları
 *   node budget.mjs --apply --month 2026-06 --lookback 6 --outlier 3000
 */

import api from '@actual-app/api';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
const arg = (k, d) => { const i = process.argv.indexOf(k); return i >= 0 ? process.argv[i + 1] : d; };

const HELP = `Veriye dayalı bütçe kurucu (de-lumped "düzenli taban")

  Son N tam ayın DÜZENLİ giderini hesaplar; tek-seferlik büyük alımları ayıklar.

Kullanım:
  node budget.mjs                     DRY-RUN (önizleme + one-off raporu)
  node budget.mjs --apply             güncel aya bütçe + sabit template notları
  node budget.mjs --apply --month 2026-06 --lookback 6 --outlier 3000

Seçenekler:
  --apply            Actual'a canlı yaz (yoksa sadece önizleme)
  --month YYYY-MM    hedef ay (varsayılan: güncel ay)
  --lookback N       ortalama penceresi, tam ay (varsayılan 6)
  --outlier TL       tek işlem ≥ bu tutar = tek-seferlik, tabandan çıkar (vars. 3000)
  --help             bu yardım`;

if (process.argv.includes('--help') || process.argv.includes('-h')) { console.log(HELP); process.exit(0); }

const APPLY = process.argv.includes('--apply');
const LOOKBACK = parseInt(arg('--lookback', '6'), 10);
// Tek-seferlik "büyük alım" eşiği (TL). Bu tutarın üstündeki tekil işlemler düzenli
// tabandan çıkarılır ve ayrı raporlanır. Tek sayı ile tüm modeli ayarlarsın.
const OUTLIER_TL = parseInt(arg('--outlier', '3000'), 10);

// Bütçelenmeyecek kategoriler (gelir / transfer / iç hareket). Spesifik gelir
// adları (ör. "Maaş (Firma)") /Gelir|Maaş/ deseniyle elenir — isim gömülmez.
const NO_BUDGET = new Set([
  'Gelen Transfer', 'Öz Transfer (Gelen)', 'Öz Transfer (Giden)',
  'Gelen Havale (Gelir)', 'Maaş', 'Kredi Kartı Ödemesi',
]);
const NO_BUDGET_RE = /^Transfer:|Gelir|Maaş/;
// Az veri uyarısı: pencerede bundan az işlemi olan kategori "elle gözden geçir".
const MIN_TXN = 3;

const round50 = (x) => Math.round(x / 50) * 50;
const tl = (x) => x.toLocaleString('tr-TR');

function loadConfig() {
  let c = {};
  const cf = resolve(__dir, 'config.json');
  if (existsSync(cf)) c = JSON.parse(readFileSync(cf, 'utf8'));
  return {
    serverURL: process.env.ACTUAL_SERVER_URL || c.serverURL,
    password: process.env.ACTUAL_PASSWORD || c.password,
    syncId: process.env.ACTUAL_SYNC_ID || c.syncId,
    encryptionPassword: process.env.ACTUAL_ENCRYPTION_PASSWORD || c.encryptionPassword || undefined,
    dbPath: resolve(__dir, process.env.ACTUAL_DB_PATH || c.dbPath || '../output/finans.db'),
  };
}

function computeAverages(dbPath) {
  const db = new DatabaseSync(dbPath, { readOnly: true });
  const allMonths = db.prepare(
    "SELECT DISTINCT substr(tarih,1,7) m FROM islemler ORDER BY m DESC").all().map(r => r.m);
  // Tamamlanmamış güncel ayı pencereden çıkar (yarım ay ortalamayı düşürmesin).
  const curMonth = new Date().toISOString().slice(0, 7);
  const completed = allMonths.filter(m => m < curMonth);
  const window = completed.slice(0, LOOKBACK).reverse();
  const ph = window.map(() => '?').join(',');
  // Tek tek çıkış işlemleri — outlier ayıklamak için işlem düzeyinde okuruz.
  const txns = db.prepare(
    `SELECT kategori, -tutar AS t, tarih, aciklama
     FROM islemler
     WHERE ic_transfer=0 AND tutar<0 AND substr(tarih,1,7) IN (${ph})`).all(...window);
  db.close();

  const cats = new Map();
  for (const r of txns) {
    if (NO_BUDGET.has(r.kategori) || NO_BUDGET_RE.test(r.kategori)) continue;
    if (!cats.has(r.kategori)) cats.set(r.kategori, []);
    cats.get(r.kategori).push(r);
  }

  const out = [];
  for (const [category, list] of cats) {
    const oneoffs = list.filter(x => x.t >= OUTLIER_TL).sort((a, b) => b.t - a.t);
    const base = list.filter(x => x.t < OUTLIER_TL);
    const baseSum = base.reduce((s, x) => s + x.t, 0);
    out.push({
      category,
      avg: round50(baseSum / window.length),
      n: list.length,
      oneoffs,
      oneoffSum: oneoffs.reduce((s, x) => s + x.t, 0),
      lowData: base.length < MIN_TXN,
    });
  }
  out.sort((a, b) => b.avg - a.avg);
  return { window, rows: out };
}

async function main() {
  const cfg = loadConfig();
  const { window, rows } = computeAverages(cfg.dbPath);
  const month = arg('--month', new Date().toISOString().slice(0, 7));
  console.log(`\n📊 Bütçe önerisi — son ${window.length} TAM ay (${window[0]}…${window.at(-1)}) düzenli ortalaması`);
  console.log(`🎯 Hedef ay: ${month}   eşik: tek işlem ≥ ${tl(OUTLIER_TL)} TL = tek-seferlik (taban dışı)   (${APPLY ? 'CANLI YAZIM' : 'DRY-RUN'})\n`);
  console.log(`${'Kategori'.padEnd(30)}${'Aylık taban'.padStart(12)}   not`);
  let total = 0;
  for (const r of rows) {
    total += r.avg;
    const flags = [];
    if (r.oneoffs.length) flags.push(`+${r.oneoffs.length} tek-seferlik (${tl(r.oneoffSum)} TL hariç)`);
    if (r.lowData) flags.push('az veri — elle gözden geçir');
    console.log(`${r.category.padEnd(30)}${tl(r.avg).padStart(12)}   ${flags.join('; ')}`);
  }
  console.log(`${'— TOPLAM (aylık taban) —'.padEnd(30)}${tl(total).padStart(12)}`);

  // One-off büyük alımların dökümü — kullanıcı kararı (sinking fund / yok say / elle).
  const withOneoffs = rows.filter(r => r.oneoffs.length);
  if (withOneoffs.length) {
    console.log(`\n🧾 Taban dışı tutulan tek-seferlik büyük alımlar (≥ ${tl(OUTLIER_TL)} TL):`);
    for (const r of withOneoffs)
      for (const o of r.oneoffs)
        console.log(`   ${o.tarih}  ${tl(Math.round(o.t)).padStart(9)}  ${r.category} — ${String(o.aciklama).slice(0, 40)}`);
    console.log(`   → Bunlar düzenli bütçeye girmez. İstersen yıllık "sinking fund" zarfı kurabiliriz.`);
  }

  if (!APPLY) { console.log('\n✅ Uygulamak için:  node budget.mjs --apply   (eşik: --outlier <TL>)'); return; }

  await api.init({ dataDir: resolve(__dir, 'data'), serverURL: cfg.serverURL, password: cfg.password });
  await api.downloadBudget(cfg.syncId, cfg.encryptionPassword ? { password: cfg.encryptionPassword } : undefined);
  const cid = {};
  for (const g of await api.getCategoryGroups()) for (const c of (g.categories || [])) cid[c.name] = c.id;

  let set = 0, noted = 0, missing = [];
  for (const r of rows) {
    const id = cid[r.category];
    if (!id) { missing.push(r.category); continue; }
    await api.setBudgetAmount(month, id, Math.round(r.avg * 100));
    set++;
    // SABİT template: "Apply budget template" gelecekte one-off'ları geri dahil etmesin.
    try { await api.updateNote(id, `#template ${r.avg}`); noted++; } catch {}
  }
  await api.sync();
  await api.shutdown();
  console.log(`\n✅ ${month}: ${set} kategoriye düzenli taban bütçesi atandı, ${noted} kategoriye sabit template yazıldı.`);
  if (missing.length) console.log(`  ⚠️ Actual'da bulunamayan (önce npm run apply): ${missing.join(', ')}`);
  console.log('\nℹ️ Gelecek aylar: ya bu script\'i tekrar çalıştır, ya Budget → "Apply budget template" (sabit tabanları doldurur).');
}

main().catch(async (e) => { console.error('\n✗ HATA:', e.message); try { await api.shutdown(); } catch {} process.exit(1); });
