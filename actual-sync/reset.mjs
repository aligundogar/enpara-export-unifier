#!/usr/bin/env node
/**
 * Sıfırlama: bu aracın oluşturduğu hesapları (finans.db/hesap_meta'daki adlar)
 * ve isteğe bağlı kategori gruplarını siler. Temiz yeniden aktarımdan önce.
 *
 *   node reset.mjs         → sadece hesaplar (işlemleriyle birlikte)
 *   node reset.mjs --all   → kategori gruplarını da sil
 */

import api from '@actual-app/api';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
const ALL = process.argv.includes('--all');

const MY_GROUPS = ['Gelir', 'Gıda & Market', 'Yeme-İçme', 'Faturalar & Abonelikler',
  'Ulaşım', 'Sağlık', 'Alışveriş', 'Eğitim & Kültür', 'Finansal',
  'Transferler (Giden)', 'Diğer'];

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

async function main() {
  const cfg = loadConfig();
  const db = new DatabaseSync(cfg.dbPath, { readOnly: true });
  const names = db.prepare('SELECT ad FROM hesap_meta').all().map(r => r.ad);
  db.close();

  await api.init({ dataDir: resolve(__dir, 'data'), serverURL: cfg.serverURL, password: cfg.password });
  await api.downloadBudget(cfg.syncId, cfg.encryptionPassword ? { password: cfg.encryptionPassword } : undefined);

  console.log('— Hesaplar siliniyor —');
  for (const a of await api.getAccounts())
    if (names.includes(a.name)) { await api.deleteAccount(a.id); console.log('  -', a.name); }

  if (ALL) {
    console.log('— Kategori grupları —');
    for (const g of await api.getCategoryGroups()) {
      if (!MY_GROUPS.includes(g.name)) continue;
      for (const cat of (g.categories || [])) { try { await api.deleteCategory(cat.id); } catch {} }
      try { await api.deleteCategoryGroup(g.id); console.log('  -', g.name); } catch {}
    }
  }
  await api.sync();
  console.log(`\n— Kalan hesap: ${(await api.getAccounts()).length}`);
  await api.shutdown();
  console.log('✅ Sıfırlama tamam.');
}

main().catch(async (e) => { console.error('✗', e.message); try { await api.shutdown(); } catch {} process.exit(1); });
