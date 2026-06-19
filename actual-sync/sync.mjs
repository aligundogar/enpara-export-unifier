#!/usr/bin/env node
/**
 * Enpara → Actual Budget aktarıcı.
 *
 * finans.db (enpara-export-unifier çıktısı) okur ve Actual Budget'a aktarır:
 *   • 2 hesap (Vadesiz TL + Kredi Kartı) doğru açılış bakiyeleriyle
 *   • Türkçe kategori grupları + kategoriler
 *   • Tüm işlemler; kart↔hesap "kredi kartı ödemesi" eşleşmeleri GERÇEK TRANSFER
 *   • imported_id ile idempotent (tekrar çalıştırınca çift kayıt olmaz)
 *
 * Varsayılan: DRY-RUN (hiçbir şey yazmaz). Yazmak için:  node sync.mjs --apply
 *
 * Yapılandırma: config.json (bkz config.example.json) veya ortam değişkenleri
 *   ACTUAL_SERVER_URL  ACTUAL_PASSWORD  ACTUAL_SYNC_ID
 *   ACTUAL_ENCRYPTION_PASSWORD (ops.)  ACTUAL_DB_PATH (ops.)
 */

import api from '@actual-app/api';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dir = dirname(fileURLToPath(import.meta.url));
const APPLY = process.argv.includes('--apply');

// --- Kategori grup haritası (Türkçe set) -----------------------------------
const GROUPS = [
  { group: 'Gelir', is_income: true, cats: ['Gelen Transfer', 'Öz Transfer (Gelen)'] },
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

function catGroupOf(catName) {
  for (const g of GROUPS) if (g.cats.includes(catName)) return g;
  return GROUPS.find(g => g.group === 'Diğer');
}

// --- yapılandırma -----------------------------------------------------------
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
    if (!cfg[k]) throw new Error(`Eksik yapılandırma: ${k} (config.json veya ortam değişkeni)`);
  cfg.dbPath = resolve(__dir, cfg.dbPath);
  return cfg;
}

// --- finans.db okuma --------------------------------------------------------
function readData(dbPath) {
  if (!existsSync(dbPath)) throw new Error(`finans.db bulunamadı: ${dbPath}\n  Önce: python run.py <klasör> -o output`);
  const db = new DatabaseSync(dbPath, { readOnly: true });
  const meta = db.prepare('SELECT kaynak,ad,tip,acilis_bakiye FROM hesap_meta').all();
  const rows = db.prepare(
    `SELECT rowid, tarih,kaynak,hareket_tipi,aciklama,kategori,tutar,bakiye,taksit,ic_transfer,eslesme,kaynak_dosya
     FROM islemler ORDER BY tarih, rowid`).all();
  let anchors = [];
  try {
    anchors = db.prepare('SELECT kaynak,tarih,hedef_bakiye FROM bakiye_capa').all();
  } catch { /* bakiye_capa yoksa sorun değil */ }
  db.close();
  return { meta, rows, anchors };
}

const cents = (tl) => Math.round(Number(tl) * 100);

// --- işlem planı (dry-run + apply ortak) -----------------------------------
function buildPlan(rows) {
  const seen = new Map();
  // imported_id KARARLI olmalı: hangi kaynak dosyanın açıklaması dedup'ı kazanırsa
  // kazansın aynı kalmalı (yoksa yeni dosya ekleyince mükerrer oluşur).
  // Hesap işlemlerinde 'bakiye' kaynaktan bağımsız + benzersizdir → en sağlam anahtar.
  // Bakiyesi olmayan satırlarda (kredi kartı) tarih+tutar+açıklama+sıra kullanılır.
  const importedId = (r) => {
    if (r.bakiye !== null && r.bakiye !== undefined && r.bakiye !== '')
      return `${r.kaynak}:${r.tarih}:${cents(r.tutar)}:B${cents(r.bakiye)}`;
    const base = `${r.kaynak}:${r.tarih}:${cents(r.tutar)}:${(r.aciklama || '').slice(0, 28).replace(/\s+/g, ' ').trim()}`;
    const n = (seen.get(base) || 0) + 1; seen.set(base, n);
    return n === 1 ? base : `${base}#${n}`;
  };
  const plan = { normal: [], transferFromChecking: [], skippedCardSide: 0 };
  for (const r of rows) {
    const iid = importedId(r);              // her satır için kararlı kimlik (sırayla)
    if (r.kaynak === 'kredi_karti' && r.eslesme) { plan.skippedCardSide++; continue; }
    const t = {
      _src: r.kaynak, iid,
      date: r.tarih, amount: cents(r.tutar),
      payee_name: (r.aciklama || '').trim() || '(açıklama yok)',
      category: r.kategori, notes: r.hareket_tipi || undefined,
      cleared: true, imported_id: iid,
    };
    if (r.kaynak === 'vadesiz_hesap' && r.eslesme) plan.transferFromChecking.push(t);
    else plan.normal.push(t);
  }
  return plan;
}

// --- ana --------------------------------------------------------------------
async function main() {
  const cfg = loadConfig();
  const { meta, rows, anchors } = readData(cfg.dbPath);
  console.log(`\n📊 finans.db: ${rows.length} işlem, ${meta.length} hesap`);
  console.log(`🎯 Hedef: ${cfg.serverURL}  (mod: ${APPLY ? 'CANLI YAZIM' : 'DRY-RUN'})\n`);

  mkdirSync(resolve(__dir, 'data'), { recursive: true });
  await api.init({ dataDir: resolve(__dir, 'data'), serverURL: cfg.serverURL, password: cfg.password });
  await api.downloadBudget(cfg.syncId, cfg.encryptionPassword ? { password: cfg.encryptionPassword } : undefined);

  // 1) HESAPLAR
  const existingAccts = await api.getAccounts();
  const acctIdByKaynak = {};
  console.log('— Hesaplar —');
  for (const m of meta) {
    const found = existingAccts.find(a => a.name === m.ad);
    if (found) {
      acctIdByKaynak[m.kaynak] = found.id;
      console.log(`  = var: ${m.ad}`);
    } else if (APPLY) {
      // Actual'da kredi kartı = normal on-budget hesap (negatif bakiyeyle çalışır);
      // ayrı 'type' alanı sürümlere göre değiştiğinden gönderilmez.
      const id = await api.createAccount(
        { name: m.ad, offbudget: false },
        cents(m.acilis_bakiye));
      acctIdByKaynak[m.kaynak] = id;
      console.log(`  + oluşturuldu: ${m.ad}  (açılış ${m.acilis_bakiye} TL)`);
    } else {
      acctIdByKaynak[m.kaynak] = `<yeni:${m.kaynak}>`;
      console.log(`  + oluşturulacak: ${m.ad}  (açılış ${m.acilis_bakiye} TL)`);
    }
  }

  // 2) KATEGORİLER
  const neededCats = [...new Set(rows.map(r => r.kategori).filter(Boolean))];
  const groups0 = await api.getCategoryGroups();
  const groupIdByName = {}; const catIdByName = {};
  for (const g of groups0) { groupIdByName[g.name] = g.id; for (const c of (g.categories || [])) catIdByName[c.name] = c.id; }

  // ihtiyaç duyulan grupları belirle
  const usedGroups = new Map();
  for (const cat of neededCats) { const g = catGroupOf(cat); if (!usedGroups.has(g.group)) usedGroups.set(g.group, g); }
  console.log('\n— Kategoriler —');
  let createdG = 0, createdC = 0;
  for (const [gname, g] of usedGroups) {
    let gid = groupIdByName[gname];
    if (!gid) {
      if (APPLY) { gid = await api.createCategoryGroup({ name: gname, is_income: !!g.is_income }); groupIdByName[gname] = gid; }
      createdG++;
    }
    for (const cat of neededCats.filter(c => catGroupOf(c).group === gname)) {
      if (!catIdByName[cat]) {
        if (APPLY) { const id = await api.createCategory({ name: cat, group_id: gid, is_income: !!g.is_income }); catIdByName[cat] = id; }
        createdC++;
      }
    }
  }
  console.log(`  ${createdG} grup + ${createdC} kategori ${APPLY ? 'oluşturuldu' : 'oluşturulacak'} (mevcut korunur)`);

  // 3) İŞLEMLER
  const plan = buildPlan(rows);
  console.log('\n— İşlemler —');
  console.log(`  normal: ${plan.normal.length}  |  transfer (kart ödemesi): ${plan.transferFromChecking.length}  |  atlanan kart-tarafı: ${plan.skippedCardSide}`);

  if (anchors?.length) {
    console.log('\n— Bakiye çapaları (faturalanmamış uzlaştırma) —');
    for (const a of anchors)
      console.log(`  ${a.kaynak}: hedef bakiye ${a.hedef_bakiye} TL (${a.tarih})`);
  }

  if (!APPLY) {
    console.log('\n🔎 DRY-RUN — örnek 5 işlem:');
    for (const t of plan.normal.slice(0, 5))
      console.log(`   ${t.date}  ${(t.amount / 100).toFixed(2).padStart(11)} TL  [${t.category}]  ${t.payee_name.slice(0, 32)}`);
    console.log('\n   Hesap açılışları:');
    for (const m of meta) console.log(`     ${m.ad}: ${m.acilis_bakiye} TL`);
    console.log('\n✅ Yazım için tekrar:  node sync.mjs --apply');
    await api.shutdown(); return;
  }

  // --- CANLI YAZIM ---
  // Transfer payee'si (kart hesabının) — importTransactions, payee bir transfer
  // payee'si olduğunda KARŞI TARAFI (kart işlemini) otomatik oluşturur.
  const payees = await api.getPayees();
  const cardTransferPayee = payees.find(p => p.transfer_acct === acctIdByKaynak['kredi_karti']);
  if (plan.transferFromChecking.length && !cardTransferPayee)
    throw new Error('Kart hesabının transfer payee\'si bulunamadı — kart hesabı oluştu mu?');

  const toImport = { vadesiz_hesap: [], kredi_karti: [] };
  for (const t of plan.normal) {
    toImport[t._src].push({
      date: t.date, amount: t.amount,
      payee_name: t.payee_name, category: catIdByName[t.category], notes: t.notes,
      cleared: t.cleared, imported_id: t.imported_id,
    });
  }
  // transferler: vadesiz tarafa payee=kart transfer payee → kart tarafı otomatik oluşur
  for (const t of plan.transferFromChecking) {
    toImport.vadesiz_hesap.push({
      date: t.date, amount: t.amount,
      payee: cardTransferPayee.id, notes: t.notes || 'Kredi kartı ödemesi',
      cleared: true, imported_id: t.imported_id,
    });
  }

  // importTransactions: imported_id ile MÜKERRER ENGELLEME (idempotent) + transfer üretimi
  let added = 0, updated = 0;
  for (const kaynak of ['vadesiz_hesap', 'kredi_karti']) {
    const batch = toImport[kaynak];
    if (!batch.length) continue;
    const res = await api.importTransactions(acctIdByKaynak[kaynak], batch, { defaultCleared: true });
    added += res.added?.length || 0;
    updated += res.updated?.length || 0;
    if (res.errors?.length) console.log(`  ! ${kaynak} hataları:`, res.errors.slice(0, 3));
    console.log(`  → ${kaynak}: +${res.added?.length || 0} yeni, ${res.updated?.length || 0} güncellendi`);
  }
  console.log(`\n  toplam: +${added} yeni, ${updated} güncellendi (mükerrerler imported_id ile atlandı)`);

  // --- BAKİYE ÇAPASI: gerçek (faturalanmamış dahil) bakiyeye uzlaştır ---
  for (const a of (anchors || [])) {
    const acctId = acctIdByKaynak[a.kaynak];
    if (!acctId) continue;
    const iid = `ANCHOR-${a.kaynak}`;
    const tx = await api.getTransactions(acctId, '2000-01-01', '2100-01-01');
    const existing = tx.find(t => t.imported_id === iid);
    const curBal = tx.reduce((s, t) => s + t.amount, 0);
    const residual = curBal - (existing ? existing.amount : 0);     // çapasız bakiye
    const want = cents(a.hedef_bakiye);
    const adj = want - residual;                                    // gereken uzlaştırma
    if (Math.abs(adj) < 1) {                                        // <1 kuruş: gerek yok
      if (existing) { await api.deleteTransaction(existing.id); console.log(`  ⚓ ${a.kaynak}: çapa kaldırıldı (artık gerekmiyor)`); }
      continue;
    }
    if (existing) {
      await api.updateTransaction(existing.id, { amount: adj, date: a.tarih });
      console.log(`  ⚓ ${a.kaynak}: çapa güncellendi → ${(adj / 100).toFixed(2)} TL (hedef bakiye ${a.hedef_bakiye})`);
    } else {
      await api.importTransactions(acctId, [{
        date: a.tarih, amount: adj, payee_name: 'Faturalanmamış harcamalar (uzlaştırma)',
        notes: 'Varlık/Borç dökümü ile uzlaştırma — sonraki ekstre gelince küçülür',
        cleared: false, imported_id: iid,
      }], {});
      console.log(`  ⚓ ${a.kaynak}: çapa eklendi → ${(adj / 100).toFixed(2)} TL (hedef bakiye ${a.hedef_bakiye})`);
    }
  }

  await api.sync();
  await api.shutdown();
  console.log('\n✅ Aktarım tamam ve sunucuyla senkronize edildi.');
}

main().catch(async (e) => {
  console.error('\n✗ HATA:', e.message);
  try { await api.shutdown(); } catch {}
  process.exit(1);
});
