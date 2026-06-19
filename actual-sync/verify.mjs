#!/usr/bin/env node
/**
 * Doğrulama: Actual'daki hesap bakiyeleri, finans.db'den beklenen değerlerle
 * tutuyor mu? Bakiye çapasını (bakiye_capa) hesaba katar.
 *
 *   Beklenen bakiye =
 *     • çapa varsa  → çapanın hedef bakiyesi (gerçek/faturalanmamış dahil borç)
 *     • çapa yoksa  → açılış bakiyesi + tüm işlem tutarları toplamı
 *
 * Kullanım:  node verify.mjs   (npm run verify)
 */

import api from '@actual-app/api';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));

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

const fmt = (n) => n.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

async function main() {
  const cfg = loadConfig();
  const db = new DatabaseSync(cfg.dbPath, { readOnly: true });
  const meta = db.prepare('SELECT kaynak,ad,acilis_bakiye FROM hesap_meta').all();
  const sums = {};
  for (const m of meta)
    sums[m.kaynak] = db.prepare('SELECT COALESCE(SUM(tutar),0) s FROM islemler WHERE kaynak=?').get(m.kaynak).s;
  let anchors = [];
  try { anchors = db.prepare('SELECT kaynak,hedef_bakiye FROM bakiye_capa').all(); } catch {}
  db.close();
  const anchorOf = (k) => anchors.find(a => a.kaynak === k);

  await api.init({ dataDir: resolve(__dir, 'data'), serverURL: cfg.serverURL, password: cfg.password });
  await api.downloadBudget(cfg.syncId, cfg.encryptionPassword ? { password: cfg.encryptionPassword } : undefined);
  const accts = await api.getAccounts();

  console.log('\n=== BAKİYE DOĞRULAMA ===');
  let allOk = true;
  for (const m of meta) {
    const acct = accts.find(a => a.name === m.ad);
    if (!acct) { console.log(`\n  ${m.ad}: ❌ Actual'da bulunamadı`); allOk = false; continue; }
    const tx = await api.getTransactions(acct.id, '2000-01-01', '2100-01-01');
    const actualBal = tx.reduce((s, t) => s + t.amount, 0) / 100;

    const anc = anchorOf(m.kaynak);
    const expected = anc ? anc.hedef_bakiye : (m.acilis_bakiye + sums[m.kaynak]);
    const basis = anc ? 'çapa hedefi (gerçek borç)' : 'açılış + işlemler';
    const ok = Math.abs(actualBal - expected) < 0.01;
    allOk = allOk && ok;

    console.log(`\n  ${m.ad}  (${tx.length} işlem, ${tx.filter(t => t.transfer_id).length} transfer)`);
    console.log(`    Actual bakiye : ${fmt(actualBal)} TL`);
    console.log(`    Beklenen      : ${fmt(expected)} TL   [${basis}]`);
    console.log(`    ${ok ? '✅ TUTTU' : `❌ FARK: ${fmt(actualBal - expected)} TL`}`);
  }
  console.log(`\n${allOk ? '✅ Tüm bakiyeler tutuyor.' : '❌ Bazı bakiyeler tutmuyor — yukarıya bakın.'}`);
  await api.shutdown();
  process.exit(allOk ? 0 : 1);
}

main().catch(async (e) => {
  console.error('\n✗ HATA:', e.message);
  try { await api.shutdown(); } catch {}
  process.exit(2);
});
