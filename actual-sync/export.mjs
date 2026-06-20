#!/usr/bin/env node
/**
 * Dosya-tabanlı Actual import üreticisi (API/SUNUCU GEREKTİRMEZ).
 *
 * finans.db'yi okur ve Actual'ın kendi "Import" özelliğiyle (hesap sayfası →
 * Import) içe aktarılabilen HESAP BAŞINA dosya üretir:
 *   • CSV  (varsayılan) — Date,Payee,Notes,Category,Amount
 *   • QIF  (--format qif)
 *
 * Bakiyeler verify.mjs modeliyle birebir uyumlu üretilir:
 *   • on-budget banka → kendi işlemleri (+ açılış bakiyesi satırı)
 *   • off-budget kişi/yatırım → gelen transferler (ters işaretle)
 *   • bakiye çapası olan hesap → sonu hedef bakiyeye uzlaştıran "çapa" satırı
 *
 * NOT: Düz dosya importunda transferler OTOMATİK BAĞLANMAZ (Actual CSV/QIF
 * importu transfer eşlemez); her bacak kendi hesabında normal işlem olur, karşı
 * hesap adı "payee" olarak yazılır. Akıllı transfer/çapa için API yolu: sync.mjs.
 *
 * Kullanım:
 *   node export.mjs                      # CSV → ../output/actual-import/
 *   node export.mjs --format qif         # QIF üret
 *   node export.mjs --out <dizin>        # çıktı dizinini değiştir
 *   node export.mjs --help
 */

import { DatabaseSync } from 'node:sqlite';
import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dir = dirname(fileURLToPath(import.meta.url));
const arg = (k, d) => { const i = process.argv.indexOf(k); return i >= 0 ? process.argv[i + 1] : d; };

const HELP = `Dosya-tabanlı Actual import üreticisi (API gerektirmez)

  finans.db → hesap başına CSV/QIF dosyası. Actual'da hesap → "Import" ile yükle.

Kullanım:
  node export.mjs                  CSV üret  (../output/actual-import/)
  node export.mjs --format qif     QIF üret
  node export.mjs --out <dizin>    çıktı dizini
  node export.mjs --help           bu yardım

Notlar:
  • Bakiyeler verify.mjs modeliyle uyumludur (açılış + çapa satırları dahil).
  • Düz dosya importu transferi BAĞLAMAZ; akıllı transfer için: node sync.mjs --apply`;

if (process.argv.includes('--help') || process.argv.includes('-h')) { console.log(HELP); process.exit(0); }

const FORMAT = (arg('--format', 'csv') || 'csv').toLowerCase();
if (!['csv', 'qif'].includes(FORMAT)) { console.error(`✗ Bilinmeyen format: ${FORMAT} (csv|qif)`); process.exit(1); }

function loadConfig() {
  let c = {};
  const cf = resolve(__dir, 'config.json');
  if (existsSync(cf)) c = JSON.parse(readFileSync(cf, 'utf8'));
  return {
    dbPath: resolve(__dir, process.env.ACTUAL_DB_PATH || c.dbPath || '../output/finans.db'),
  };
}

const round2 = (x) => Math.round(Number(x) * 100) / 100;
// dosya adı güvenli (ad'ı koru, / ve denetim karakterlerini sadeleştir)
const safeName = (s) => s.replace(/[\/\\:*?"<>|]+/g, '-').replace(/\s+/g, ' ').trim();
const csvCell = (s) => {
  const v = String(s ?? '');
  return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
};

function buildAccounts(dbPath) {
  if (!existsSync(dbPath)) throw new Error(`finans.db yok: ${dbPath}\n  Önce: python run.py <klasör> -o output`);
  const db = new DatabaseSync(dbPath, { readOnly: true });
  const meta = db.prepare('SELECT kaynak,ad,tip,acilis_bakiye,offbudget FROM hesap_meta').all();
  const all = db.prepare(
    'SELECT tarih,hesap,aciklama,kategori,tutar,transfer_to FROM islemler ORDER BY tarih, rowid').all();
  let anchors = [];
  try { anchors = db.prepare('SELECT kaynak,tarih,hedef_bakiye,aciklama FROM bakiye_capa').all(); } catch {}
  db.close();

  const nameOf = Object.fromEntries(meta.map(m => [m.kaynak, m.ad]));
  const anchorOf = (k) => anchors.find(a => a.kaynak === k);
  const out = [];

  for (const m of meta) {
    const rows = [];
    if (m.offbudget) {
      // off-budget kişi/yatırım: bakiye = gelen transferler (ters işaret)
      for (const r of all.filter(r => r.transfer_to === m.kaynak))
        rows.push({ date: r.tarih, amount: round2(-r.tutar), payee: nameOf[r.hesap] || (r.aciklama || '').trim(), notes: (r.aciklama || '').trim(), category: '' });
    } else {
      // on-budget banka: kendi işlemleri
      for (const r of all.filter(r => r.hesap === m.kaynak)) {
        const isTransfer = r.transfer_to && r.transfer_to !== '' && r.transfer_to !== '__skip__';
        const payee = isTransfer ? (nameOf[r.transfer_to] || (r.aciklama || '').trim()) : ((r.aciklama || '').trim() || '(açıklama yok)');
        rows.push({ date: r.tarih, amount: round2(r.tutar), payee, notes: (r.aciklama || '').trim(), category: isTransfer ? '' : (r.kategori || '') });
      }
    }
    rows.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));

    // açılış bakiyesi satırı (en erken tarihten)
    const firstDate = rows.length ? rows[0].date : new Date().toISOString().slice(0, 10);
    if (Number(m.acilis_bakiye) !== 0)
      rows.unshift({ date: firstDate, amount: round2(m.acilis_bakiye), payee: 'Açılış Bakiyesi', notes: 'Starting balance', category: '' });

    // bakiye çapası → sonu hedef bakiyeye uzlaştır
    const anc = anchorOf(m.kaynak);
    if (anc) {
      const sum = rows.reduce((s, r) => s + r.amount, 0);
      const adj = round2(anc.hedef_bakiye - sum);
      if (Math.abs(adj) >= 0.01)
        rows.push({ date: anc.tarih || firstDate, amount: adj, payee: 'Uzlaştırma (çapa)', notes: anc.aciklama || 'Bakiye çapası', category: '' });
    }

    out.push({ name: m.ad, offbudget: !!m.offbudget, rows });
  }
  return out;
}

function toCSV(rows) {
  const head = 'Date,Payee,Notes,Category,Amount';
  const body = rows.map(r => [r.date, r.payee, r.notes, r.category, r.amount.toFixed(2)].map(csvCell).join(','));
  return [head, ...body].join('\n') + '\n';
}

function toQIF(rows) {
  const lines = ['!Type:Bank'];
  for (const r of rows) {
    lines.push(`D${r.date}`, `T${r.amount.toFixed(2)}`, `P${r.payee}`);
    if (r.notes) lines.push(`M${r.notes}`);
    if (r.category) lines.push(`L${r.category}`);
    lines.push('^');
  }
  return lines.join('\n') + '\n';
}

function main() {
  const cfg = loadConfig();
  const accounts = buildAccounts(cfg.dbPath);
  const outDir = resolve(__dir, arg('--out', '../output/actual-import'));
  mkdirSync(outDir, { recursive: true });

  console.log(`\n📤 Dosya-tabanlı Actual import (${FORMAT.toUpperCase()})  →  ${outDir}\n`);
  const index = [];
  let totalRows = 0;
  for (const a of accounts) {
    if (!a.rows.length) { console.log(`   (atlandı, boş) ${a.name}`); continue; }
    const fname = `${safeName(a.name)}.${FORMAT}`;
    const content = FORMAT === 'qif' ? toQIF(a.rows) : toCSV(a.rows);
    writeFileSync(join(outDir, fname), content, 'utf8');
    const bal = a.rows.reduce((s, r) => s + r.amount, 0);
    totalRows += a.rows.length;
    index.push({ fname, name: a.name, tag: a.offbudget ? 'off-budget' : 'on-budget', n: a.rows.length, bal });
    console.log(`   ✓ ${fname.padEnd(34)} ${String(a.rows.length).padStart(4)} işlem   bakiye ${bal.toLocaleString('tr-TR', { minimumFractionDigits: 2 })} TL  [${a.offbudget ? 'off' : 'on'}-budget]`);
  }

  // import rehberi
  const guide = [
    '# Actual Budget — dosya importu rehberi',
    '',
    `Format: ${FORMAT.toUpperCase()}  |  ${index.length} hesap, ${totalRows} işlem`,
    '',
    'Her dosya BİR hesaba aittir. Actual\'da:',
    '  1) Hesabı aç (yoksa önce oluştur; off-budget olanları "Off budget" işaretle).',
    '  2) Hesap sayfası → ⋯ menü → **Import**. İlgili dosyayı seç.',
    '  3) CSV ise sütun eşlemesi: Date=Date, Payee=Payee, Notes=Notes,',
    '     Category=Category, Amount=Amount. Tarih biçimi: YYYY-MM-DD.',
    '  4) Tekrar import zararsız değildir (dosya importu idempotent DEĞİL) —',
    '     aynı dosyayı iki kez içe aktarma; gerekirse önce hesabı temizle.',
    '',
    '> Transferler bu yolda otomatik bağlanmaz. Akıllı transfer + idempotent',
    '> aktarım istiyorsan API yolunu kullan:  npm run apply',
    '',
    '## Hesaplar',
    ...index.map(i => `- **${i.name}** (${i.tag}) → \`${i.fname}\` — ${i.n} işlem, bakiye ${i.bal.toLocaleString('tr-TR', { minimumFractionDigits: 2 })} TL`),
    '',
  ].join('\n');
  writeFileSync(join(outDir, '_OKU.md'), guide, 'utf8');

  console.log(`\n✅ ${index.length} dosya yazıldı (+ _OKU.md rehberi).`);
  console.log('ℹ️  Actual\'da: hesap → Import → dosyayı seç. Detay: _OKU.md');
}

try { main(); } catch (e) { console.error('\n✗ HATA:', e.message); process.exit(1); }
