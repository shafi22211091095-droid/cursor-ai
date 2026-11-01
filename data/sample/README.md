# `data/sample/` — SYNTHETIC demonstration data

> ## Nothing in this directory may appear in a manuscript.
>
> The compound **abundances here are fabricated**. They are not measurements of
> anything. They exist so that the pipeline has a realistic-shaped dataset to
> run on before your real GC-MS data is available.

## What is real and what is not

| Item | Status |
|---|---|
| Compound **names** | **Real.** Published natural products. |
| Compound **SMILES** | **Real.** Each one is validated against its published molecular formula by `scripts/make_sample_data.py`, which fails loudly on a mismatch. |
| **`area_percent`** (GC-MS area %) | **FABRICATED.** A seeded log-normal draw. Not a measurement. |
| **`retention_time_min`** | **FABRICATED.** Uniform draw, present only because real GC-MS exports carry the column. |
| `molecular_formula`, `monoisotopic_mass` | **Computed** from the SMILES by RDKit. Real, but derived, not measured. |
| `receptors/` | **Real** experimental structure. See `receptors/PROVENANCE.md`. |

Every fabricated row carries `data_provenance=SYNTHETIC_PLACEHOLDER`. The
pipeline reads that column and stamps a prominent warning on every figure
caption, every report and the run provenance whenever any input row is flagged,
so a synthetic-data run cannot be mistaken for a real one.

## The files

| File | Rows | Purpose |
|---|---|---|
| `gcms_components_SYNTHETIC.csv` | 69 | Full demonstration set: 20 terpenoids, 20 phenolics, 29 others. Includes loliolide and spathulenol. |
| `gcms_smoke_SYNTHETIC.csv` | 5 | The 5-ligand CI smoke run and the golden determinism test. |
| `gcms_with_rejects_SYNTHETIC.csv` | 9 | The 5 smoke rows plus 4 deliberately bad rows, to exercise every rejection path: a salt, an unparseable SMILES, an organometallic, and a duplicate structure under a second library name. |
| `receptors/` | — | ABL1 kinase + imatinib co-crystal pose, for the redocking validation gate. |

## Replacing it with your own data

Point `ligands.input_csv` in your config at your own CSV. Required columns
(names are configurable under the `ligands:` section):

```
name,smiles,compound_class,area_percent
```

`compound_class` should be one of `phenolic`, `terpenoid` or `other` to match the
manuscript's classification scheme; any other value is carried through verbatim
and reported as-is.

Drop the `data_provenance` column, or set it to something other than
`SYNTHETIC_PLACEHOLDER`, and the synthetic-data warnings disappear.

## Regenerating

```bash
python scripts/make_sample_data.py
```

The seed is fixed (`0xA11CE`), so the fabricated numbers are byte-identical on
every machine. The script re-validates every SMILES against its published
molecular formula and exits non-zero on any mismatch.
