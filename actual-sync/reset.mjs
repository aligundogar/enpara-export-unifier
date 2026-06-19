#!/usr/bin/env node
/**
 * Sıfırlama: bu aracın Actual'da oluşturduğu Enpara hesaplarını (ve isteğe bağlı
 * kategori gruplarını) siler. Temiz bir yeniden aktarım yapmadan önce işe yarar.
 *
 *   node reset.mjs            → sadece hesapları sil (işlemleri de gider)
 *   node reset.mjs --all      → kategori gruplarını da sil
 *
 * DİKKAT: deleteAccount, hesabın TÜM işlemlerini de siler. Bütçedeki diğer
 * hesaplara/kategorilere (bu araç dışında oluşturulanlara) dokunmaz.
 */

import api from '@actual-app/api';
import { readFileSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
const ALL = process.argv.includes('--all');

const MY_ACCOUNTS = ['Enpara Vadesiz TL', 'Enpara Kredi Kartı'];
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
  };
}

async function main() {
  const cfg = loadConfig();
  await api.init({ dataDir: resolve(__dir, 'data'), serverURL: cfg.serverURL, password: cfg.password });
  await api.downloadBudget(cfg.syncId, cfg.encryptionPassword ? { password: cfg.encryptionPassword } : undefined);

  console.log('— Hesaplar siliniyor —');
  for (const a of await api.getAccounts())
    if (MY_ACCOUNTS.includes(a.name)) { await api.deleteAccount(a.id); console.log('  - silindi:', a.name); }

  if (ALL) {
    console.log('— Kategori grupları siliniyor —');
    for (const g of await api.getCategoryGroups()) {
      if (!MY_GROUPS.includes(g.name)) continue;
      for (const cat of (g.categories || [])) { try { await api.deleteCategory(cat.id); } catch {} }
      try { await api.deleteCategoryGroup(g.id); console.log('  - silindi:', g.name); } catch {}
    }
  }

  await api.sync();
  console.log(`\n— Kalan — hesap: ${(await api.getAccounts()).length}`);
  await api.shutdown();
  console.log('✅ Sıfırlama tamam.');
}

main().catch(async (e) => {
  console.error('\n✗ HATA:', e.message);
  try { await api.shutdown(); } catch {}
  process.exit(1);
});
