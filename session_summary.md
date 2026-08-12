# Session Summary — 2026-08-10 (KA-code catalog + Accurate export)

## What happened

1. Ran the Flask app locally to verify it works.
2. Bulk-added new tempered-glass catalog records to `hp_data.db` for KA-codes
   KA-A02, A06, A07, A08, A09, A11–A16, A19, A21, A22, plus TG Camera B05/B06,
   transcribed directly from supplier receipt photos (not assumed groupings —
   several early assumptions were wrong and had to be corrected against receipts).
3. Two serious data-integrity incidents during this work (see below), both
   traced back to `hp.kode` having no UNIQUE constraint.
4. Renumbered `kode` values to stay sequential/gapless per user's standing rule.
5. Generated two Accurate (Indonesian ERP) Excel import files from the final
   `hp_data.db` state:
   - `~/Downloads/item-import-id (1) - TG Baru.xlsx` — item-master import, 338 rows
   - `~/Downloads/purchase-order-import-file - TG Baru.xlsx` — PO import to
     supplier V.00027, dated 01/08/2026, qty 3/item, prices per KA-code
6. Briefly explored `/Users/kevin/Documents/GitHub/bigseller-auto-uploader`
   (separate repo, read-only) — no changes made there.

## Data incidents (both accepted/resolved, not reversible)

- **Overwrite bug**: a full-block replace of KA-A06 computed a bloated old-range
  query and deleted/overwrote 54 unrelated legacy kode values (TG0339–TG0392).
  User explicitly accepted the loss ("yg penting saya mau apa yang saya kirim
  sesuai") rather than attempting recovery.
- **Cross-code collision bug**: "reuse freed kode block" logic only checked the
  touched KA-code's own old range, not the whole table, so 40 kode values ended
  up shared between KA-A06 and KA-A08/A09/A11. Fixed by recomputing new ranges
  from `max(existing)` across the entire table, then re-running the gapless
  renumbering sweep. Verified 0 collisions afterward, row count unchanged (2287).

**Standing rule going forward**: any kode reassignment must (a) compute new
ranges from `max()` over the whole `hp` table, never a per-KA-code subrange,
and (b) be followed by a full-table `(kode) -> {kode_merek_tg}` collision check
plus a gap check on the sorted distinct kode sequence.

## Final KA-code kode ranges (hp_data.db, as of last verification)

```
KA-A08: 12 rows, TG0339–TG0350
KA-A09: 12 rows, TG0351–TG0362
KA-A11: 16 rows, TG0363–TG0378
KA-A13: 18 rows, TG0379–TG0396
KA-A12: 16 rows, TG0397–TG0412
KA-A14: 18 rows, TG0413–TG0430
KA-A15: 18 rows, TG0431–TG0448
KA-A16: 18 rows, TG0449–TG0466
KA-A21: 15 rows, TG0467–TG0481
KA-A22: 15 rows, TG0482–TG0496
KA-A07: 53 rows, TG0497–TG0549
KA-A19: 4 rows,  TG0550–TG0553
KA-A02: 18 rows, TG0554–TG0571
B05:    3 rows,  TG0572–TG0574
B06:    3 rows,  TG0575–TG0577
KA-A06: 99 rows, TG0578–TG0676
```

Total DB rows: 2287. All `merek_tg` = "Monkey King" (loosely confirmed, not fully verified).
jenis_tg per code: A02="Spy + Blue", A06="5D", A07="Clear", A08="5D", A09="Anti Spy",
A11="5D", A12="Spy", A13="Matte", A14="Spy Matte", A15="Spy 360", A16="Clear 300C",
A19="Superfit Clear Premium", A21="Superfit Clear", A22="Superfit Spy", B05/B06="TG Camera".

**Note**: TG0319–TG0338 and TG0339–TG0392 (legacy "5D" batch) are permanently empty/lost —
accepted by user, do not attempt to backfill.

## Accurate export details

- Nama Barang format (both files, final agreed version):
  `"{merek_tg} {kode_merek_tg} Tempered Glass {jenis_tg} - {tipe_hp}"`
  e.g. `"Monkey King KA-A08 Tempered Glass 5D - SAMSUNG S21"`
- Item-master file: Kategori Barang="TEMPERED GLASS", Kode Barang=dash format
  (e.g. "TG-0339"), Jenis Barang="INV", Satuan="PCS". Only rows with
  `kode >= 'TG0319'` (i.e. not already in `backups/TG-Accurate.xlsx`, which
  covers TG-0001–TG-0318) were included as new.
- PO file: 1 HEADER row (No Pemasok V.00027, Tgl Pesanan 01/08/2026, Alamat
  Kirim="-", no Nama Cabang) + 338 ITEM rows, Kuantitas=3 each, prices from:
  ```
  KA-A06=15000  B06=25000   KA-A07=17500  KA-A19=70000
  B05=27500     KA-A08=22500 KA-A09=30000  KA-A11=20000
  KA-A12=22500  KA-A13=25000 KA-A14=30000  KA-A15=25000
  KA-A16=22500  KA-A21=27500 KA-A22=32500  KA-A02=35000
  ```

## Open items (not yet acted on, need user confirmation before touching)

- `tempered-glass-persamaan/CLAUDE.md` was not updated after the KA-code work —
  still describes the pre-existing `kode_merek_tg` migration as "uncommitted,
  in progress" and doesn't mention the kode-uniqueness gotcha or the two
  incidents above. Should be refreshed next time there's a natural pause.
- `bigseller-auto-uploader`: login still not completed end-to-end; all
  `uploader.py` selectors still placeholder. No action requested by user beyond
  the read-only exploration ("ok ckup" closed that thread).
