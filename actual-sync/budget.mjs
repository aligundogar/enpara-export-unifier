#!/usr/bin/env node
/**
 * Veriye dayalı bütçe kurucu.
 *
 * Gider kategorilerinin SON N AYLIK gerçek ortalamasını hesaplar ve:
 *   • kategori notuna goal-template yazar (#template average N months) — gelecek
 *     aylarda Budget → "Apply budget template" ile kendini günceller
 *   • hedef ay(lar) için setBudgetAmount ile bütçeyi DOĞRUDAN doldurur (UI gerekmez)
 *
 * Gelir / transfer / öz-transfer kategorileri bütçelenmez.
 *
 * Kullanım:
 *   node budget.mjs                 # DRY-RUN (önizleme)
 *   node budget.mjs --apply         # güncel ay + template notları
 *   node budget.mjs --apply --month 2026-06 --lookback 6
 */

import api from '@actual-app/api';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
const APPLY = process.argv.includes('--apply');
const arg = (k, d) => { const i = process.argv.indexOf(k); return i >= 0 ? process.argv[i + 1] : d; };
const LOOKBACK = parseInt(arg('--lookback', '6'), 10);

// Bütçelenmeyecek kategoriler (gelir / transfer / iç hareket). Spesifik gelir
// adları (ör. "Maaş (Firma)") /Gelir|Maaş/ deseniyle elenir — isim gömülmez.
const NO_BUDGET = new Set([
  'Gelen Transfer', 'Öz Transfer (Gelen)', 'Öz Transfer (Giden)',
  'Gelen Havale (Gelir)', 'Maaş', 'Kredi Kartı Ödemesi',
]);
const NO_BUDGET_RE = /^Transfer:|Gelir|Maaş/;
// Sabit tutarlı kategoriler → ortalama yerine sabit #template
const FIXED_TEMPLATE = { 'Kredi Taksiti': null };  // null → son ay tutarını kullan

const round50 = (x) => Math.round(x / 50) * 50;

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
  // veride bulunan son N ay (gün-içi değil ay bazında)
  const months = db.prepare(
    "SELECT DISTINCT substr(tarih,1,7) m FROM islemler ORDER BY m DESC").all().map(r => r.m);
  const window = months.slice(0, LOOKBACK).reverse();
  const ph = window.map(() => '?').join(',');
  // kategori bazında aylık gider ortalaması (iç transfer hariç, sadece çıkışlar)
  const rows = db.prepare(
    `SELECT kategori, ROUND(-SUM(tutar),2) tot, COUNT(*) n
     FROM islemler
     WHERE ic_transfer=0 AND tutar<0 AND substr(tarih,1,7) IN (${ph})
     GROUP BY kategori`).all(...window);
  db.close();
  const out = [];
  for (const r of rows) {
    if (NO_BUDGET.has(r.kategori) || NO_BUDGET_RE.test(r.kategori)) continue;
    out.push({ category: r.kategori, avg: round50(r.tot / window.length), n: r.n });
  }
  out.sort((a, b) => b.avg - a.avg);
  return { window, rows: out };
}

async function main() {
  const cfg = loadConfig();
  const { window, rows } = computeAverages(cfg.dbPath);
  const month = arg('--month', new Date().toISOString().slice(0, 7));
  console.log(`\n📊 Bütçe önerisi — son ${window.length} ay (${window[0]}…${window.at(-1)}) ortalaması`);
  console.log(`🎯 Hedef ay: ${month}  (${APPLY ? 'CANLI YAZIM' : 'DRY-RUN'})\n`);
  console.log(`${'Kategori'.padEnd(30)}${'Aylık bütçe'.padStart(12)}  template`);
  let total = 0;
  for (const r of rows) {
    total += r.avg;
    const tmpl = (r.category in FIXED_TEMPLATE) ? `#template ${r.avg}` : `#template average ${window.length} months`;
    console.log(`${r.category.padEnd(30)}${r.avg.toLocaleString('tr-TR').padStart(12)}  ${tmpl}`);
  }
  console.log(`${'— TOPLAM —'.padEnd(30)}${total.toLocaleString('tr-TR').padStart(12)}`);

  if (!APPLY) { console.log('\n✅ Uygulamak için:  node budget.mjs --apply'); return; }

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
    const tmpl = (r.category in FIXED_TEMPLATE) ? `#template ${r.avg}` : `#template average ${window.length} months`;
    try { await api.updateNote(id, tmpl); noted++; } catch {}
  }
  await api.sync();
  await api.shutdown();
  console.log(`\n✅ ${month}: ${set} kategoriye bütçe atandı, ${noted} kategoriye template notu yazıldı.`);
  if (missing.length) console.log(`  ⚠️ Actual'da bulunamayan (önce sync.mjs --apply): ${missing.join(', ')}`);
  console.log('\nℹ️ Gelecek aylarda: Budget sayfası → "Apply budget template" (notlardan otomatik doldurur).');
}

main().catch(async (e) => { console.error('\n✗ HATA:', e.message); try { await api.shutdown(); } catch {} process.exit(1); });
