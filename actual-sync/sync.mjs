#!/usr/bin/env node
/**
 * Çok-bankalı → Actual Budget aktarıcı.
 *
 * finans.db (enpara-export-unifier çıktısı) okur:
 *   • Tüm hesaplar: bankalar (on-budget) + kişi/yatırım (off-budget), doğru açılış
 *   • Türkçe kategori + gelir grupları (maaş/iş geliri)
 *   • Transferler GERÇEK transfer: kart↔hesap, banka↔banka, hesap↔kişi/yatırım
 *   • imported_id (hesap+bakiye bazlı) ile idempotent
 *   • Bakiye çapası: kart bakiyesini gerçek (faturalanmamış) borca uzlaştırır
 *
 * Varsayılan DRY-RUN. Yazmak için:  node sync.mjs --apply
 * Yapılandırma: config.json veya ACTUAL_* ortam değişkenleri.
 */

import api from '@actual-app/api';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dir = dirname(fileURLToPath(import.meta.url));
const APPLY = process.argv.includes('--apply');

// Gelir kategorileri (is_income grubuna girer)
const INCOME_CATS = [
  'Gelen Transfer', 'Öz Transfer (Gelen)', 'Gelen Havale (Gelir)',
  'İş Geliri (***REMOVED***)', 'Maaş', 'Maaş (***REMOVED***)',
];
const GROUPS = [
  { group: 'Gelir', is_income: true, cats: INCOME_CATS },
  { group: 'Gıda & Market', cats: ['Market & Gıda'] },
  { group: 'Yeme-İçme', cats: ['Yeme-İçme'] },
  { group: 'Faturalar & Abonelikler', cats: ['Faturalar & Abonelikler', 'Online & Dijital Servisler'] },
  { group: 'Ulaşım', cats: ['Ulaşım & Akaryakıt'] },
  { group: 'Sağlık', cats: ['Sağlık & Eczane', 'Sağlık & Kişisel Bakım'] },
  { group: 'Alışveriş', cats: ['Giyim & Mağaza', 'Elektronik & Teknoloji'] },
  { group: 'Eğitim & Kültür', cats: ['Eğitim & Kırtasiye', 'Eğlence & Kültür'] },
  { group: 'Finansal', cats: ['Kredi Taksiti', 'Vergi & Kesinti', 'Faiz & Ücret', 'Kredi Kartı Ödemesi', 'Nakit & ATM', 'Diğer Ödeme'] },
  { group: 'Transferler (Giden)', cats: ['Giden Transfer', 'Öz Transfer (Giden)'] },
  { group: 'Diğer', cats: ['Diğer'] },
];

function catGroupOf(cat) {
  for (const g of GROUPS) if (g.cats.includes(cat)) return g;
  if (/Gelir|Maaş|Gelen Havale/.test(cat)) return GROUPS[0];   // güvenlik: gelir
  return GROUPS.find(g => g.group === 'Diğer');
}

function loadConfig() {
  let c = {};
  const cf = resolve(__dir, 'config.json');
  if (existsSync(cf)) c = JSON.parse(readFileSync(cf, 'utf8'));
  const cfg = {
    serverURL: process.env.ACTUAL_SERVER_URL || c.serverURL,
    password: process.env.ACTUAL_PASSWORD || c.password,
    syncId: process.env.ACTUAL_SYNC_ID || c.syncId,
    encryptionPassword: process.env.ACTUAL_ENCRYPTION_PASSWORD || c.encryptionPassword || undefined,
    dbPath: process.env.ACTUAL_DB_PATH || c.dbPath || '../output/finans.db',
  };
  for (const k of ['serverURL', 'password', 'syncId'])
    if (!cfg[k]) throw new Error(`Eksik yapılandırma: ${k}`);
  cfg.dbPath = resolve(__dir, cfg.dbPath);
  return cfg;
}

function readData(dbPath) {
  if (!existsSync(dbPath)) throw new Error(`finans.db yok: ${dbPath}\n  Önce: python run.py <klasör> -o output`);
  const db = new DatabaseSync(dbPath, { readOnly: true });
  const meta = db.prepare('SELECT kaynak,ad,tip,acilis_bakiye,offbudget FROM hesap_meta').all();
  const rows = db.prepare(
    `SELECT rowid, tarih,hesap,kaynak,hareket_tipi,aciklama,kategori,tutar,bakiye,transfer_to,eslesme
     FROM islemler ORDER BY tarih, rowid`).all();
  let anchors = [];
  try { anchors = db.prepare('SELECT kaynak,tarih,hedef_bakiye FROM bakiye_capa').all(); } catch {}
  db.close();
  return { meta, rows, anchors };
}

const cents = (tl) => Math.round(Number(tl) * 100);

// Kararlı imported_id: hesap+bakiye (kaynaktan bağımsız, benzersiz). Bakiyesizlerde
// (kredi kartı) hesap+tarih+tutar+açıklama+sıra.
function makeImportedId() {
  const seen = new Map();
  return (r) => {
    if (r.bakiye !== null && r.bakiye !== undefined && r.bakiye !== '')
      return `${r.hesap}:${r.tarih}:${cents(r.tutar)}:B${cents(r.bakiye)}`;
    const base = `${r.hesap}:${r.tarih}:${cents(r.tutar)}:${(r.aciklama || '').slice(0, 28).replace(/\s+/g, ' ').trim()}`;
    const n = (seen.get(base) || 0) + 1; seen.set(base, n);
    return n === 1 ? base : `${base}#${n}`;
  };
}

function buildPlan(rows) {
  const iid = makeImportedId();
  const items = [];   // {account, kind:'normal'|'transfer', target?, ...}
  let skipped = 0;
  for (const r of rows) {
    if (r.transfer_to === '__skip__') { skipped++; continue; }
    const id = iid(r);
    const base = {
      account: r.hesap, date: r.tarih, amount: cents(r.tutar),
      imported_id: id, cleared: true,
      notes: (r.aciklama || '').trim().slice(0, 80) || undefined,
    };
    if (r.transfer_to && r.transfer_to !== '') {
      items.push({ ...base, kind: 'transfer', target: r.transfer_to });
    } else {
      items.push({ ...base, kind: 'normal', payee_name: (r.aciklama || '').trim() || '(açıklama yok)', category: r.kategori });
    }
  }
  return { items, skipped };
}

async function main() {
  const cfg = loadConfig();
  const { meta, rows, anchors } = readData(cfg.dbPath);
  const { items, skipped } = buildPlan(rows);
  const nTransfer = items.filter(i => i.kind === 'transfer').length;
  const nNormal = items.filter(i => i.kind === 'normal').length;

  console.log(`\n📊 finans.db: ${rows.length} satır → ${nNormal} normal + ${nTransfer} transfer (+${skipped} eşleşen taraf atlandı)`);
  console.log(`🏦 ${meta.length} hesap  |  🎯 ${cfg.serverURL}  (${APPLY ? 'CANLI YAZIM' : 'DRY-RUN'})\n`);

  mkdirSync(resolve(__dir, 'data'), { recursive: true });
  await api.init({ dataDir: resolve(__dir, 'data'), serverURL: cfg.serverURL, password: cfg.password });
  await api.downloadBudget(cfg.syncId, cfg.encryptionPassword ? { password: cfg.encryptionPassword } : undefined);

  // 1) HESAPLAR (on/off-budget)
  const existing = await api.getAccounts();
  const idByAcc = {};
  console.log('— Hesaplar —');
  for (const m of meta) {
    const found = existing.find(a => a.name === m.ad);
    const tag = m.offbudget ? 'off-budget' : 'on-budget';
    if (found) { idByAcc[m.kaynak] = found.id; console.log(`  = ${m.ad}`); }
    else if (APPLY) {
      idByAcc[m.kaynak] = await api.createAccount({ name: m.ad, offbudget: !!m.offbudget }, cents(m.acilis_bakiye));
      console.log(`  + ${m.ad}  (${tag}, açılış ${m.acilis_bakiye} TL)`);
    } else { idByAcc[m.kaynak] = `<yeni:${m.kaynak}>`; console.log(`  + ${m.ad}  (${tag}, açılış ${m.acilis_bakiye} TL)`); }
  }

  // 2) KATEGORİLER (sadece normal işlemlerden; transferler kategorisiz)
  const neededCats = [...new Set(items.filter(i => i.kind === 'normal').map(i => i.category).filter(Boolean))];
  const groups0 = await api.getCategoryGroups();
  const gidByName = {}, cidByName = {};
  for (const g of groups0) { gidByName[g.name] = g.id; for (const c of (g.categories || [])) cidByName[c.name] = c.id; }
  const usedGroups = new Map();
  for (const cat of neededCats) { const g = catGroupOf(cat); usedGroups.set(g.group, g); }
  let cg = 0, cc = 0;
  for (const [gname, g] of usedGroups) {
    let gid = gidByName[gname];
    if (!gid) { if (APPLY) { gid = await api.createCategoryGroup({ name: gname, is_income: !!g.is_income }); gidByName[gname] = gid; } cg++; }
    for (const cat of neededCats.filter(c => catGroupOf(c).group === gname)) {
      if (!cidByName[cat]) { if (APPLY) { cidByName[cat] = await api.createCategory({ name: cat, group_id: gid, is_income: !!g.is_income }); } cc++; }
    }
  }
  console.log(`\n— Kategoriler — ${cg} grup + ${cc} kategori ${APPLY ? 'oluşturuldu' : 'oluşturulacak'}`);

  if (anchors?.length) {
    console.log('\n— Bakiye çapaları —');
    for (const a of anchors) console.log(`  ${a.kaynak}: hedef ${a.hedef_bakiye} TL (${a.tarih})`);
  }

  if (!APPLY) {
    console.log('\n🔎 DRY-RUN — hesap başına işlem:');
    const byAcc = {};
    for (const i of items) (byAcc[i.account] ??= { normal: 0, transfer: 0 })[i.kind]++;
    for (const [a, c] of Object.entries(byAcc)) console.log(`   ${a}: ${c.normal} normal, ${c.transfer} transfer`);
    console.log('\n✅ Yazım için:  node sync.mjs --apply');
    await api.shutdown(); return;
  }

  // --- CANLI YAZIM ---
  // hesap → transfer payee id (Actual transfer için)
  const payees = await api.getPayees();
  const transferPayeeOf = {};
  for (const [key, id] of Object.entries(idByAcc)) {
    const p = payees.find(p => p.transfer_acct === id);
    if (p) transferPayeeOf[key] = p.id;
  }

  // hesap başına işlemleri grupla
  const batches = {};
  for (const i of items) {
    (batches[i.account] ??= []).push(
      i.kind === 'transfer'
        ? { date: i.date, amount: i.amount, payee: transferPayeeOf[i.target], notes: i.notes, cleared: true, imported_id: i.imported_id }
        : { date: i.date, amount: i.amount, payee_name: i.payee_name, category: cidByName[i.category], notes: i.notes, cleared: true, imported_id: i.imported_id }
    );
  }
  // transfer payee eksikse uyar
  for (const i of items) if (i.kind === 'transfer' && !transferPayeeOf[i.target])
    throw new Error(`Transfer payee yok: ${i.target} (hesap oluştu mu?)`);

  let added = 0, updated = 0;
  for (const [acc, batch] of Object.entries(batches)) {
    const res = await api.importTransactions(idByAcc[acc], batch, { defaultCleared: true });
    added += res.added?.length || 0; updated += res.updated?.length || 0;
    if (res.errors?.length) console.log(`  ! ${acc}:`, res.errors.slice(0, 2));
    console.log(`  → ${acc}: +${res.added?.length || 0} yeni, ${res.updated?.length || 0} güncel`);
  }
  console.log(`\n  toplam: +${added} yeni, ${updated} güncellendi`);

  // --- BAKİYE ÇAPASI ---
  for (const a of (anchors || [])) {
    const acctId = idByAcc[a.kaynak]; if (!acctId) continue;
    const iid = `ANCHOR-${a.kaynak}`;
    const tx = await api.getTransactions(acctId, '2000-01-01', '2100-01-01');
    const ex = tx.find(t => t.imported_id === iid);
    const residual = tx.reduce((s, t) => s + t.amount, 0) - (ex ? ex.amount : 0);
    const adj = cents(a.hedef_bakiye) - residual;
    if (Math.abs(adj) < 1) { if (ex) { await api.deleteTransaction(ex.id); console.log(`  ⚓ ${a.kaynak}: çapa kaldırıldı`); } continue; }
    if (ex) { await api.updateTransaction(ex.id, { amount: adj, date: a.tarih }); console.log(`  ⚓ ${a.kaynak}: çapa güncellendi → ${(adj / 100).toFixed(2)} TL`); }
    else {
      await api.importTransactions(acctId, [{ date: a.tarih, amount: adj, payee_name: 'Faturalanmamış (uzlaştırma)', notes: 'Varlık/Borç dökümü ile uzlaştırma', cleared: false, imported_id: iid }], {});
      console.log(`  ⚓ ${a.kaynak}: çapa eklendi → ${(adj / 100).toFixed(2)} TL`);
    }
  }

  await api.sync();
  await api.shutdown();
  console.log('\n✅ Aktarım tamam.');
}

main().catch(async (e) => {
  console.error('\n✗ HATA:', e.message);
  try { await api.shutdown(); } catch {}
  process.exit(1);
});
