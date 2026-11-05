# npcadd

**A reproducible computational pipeline for natural-product drug discovery.**

`npcadd` takes a GC-MS-derived phytochemical set from SMILES to a ranked,
validated, publication-ready result — ligand preparation, receptor preparation,
**mandatory docking validation**, screening, ADMET, efficiency-corrected
ranking, and auto-generated figures and workbooks. Every parameter lives in one
validated config file, every run records its own provenance, and no GUI tool
appears anywhere in the critical path.

```bash
make install
make smoke        # full pipeline, ~10 min, bundled sample data
```

---

## Why this exists

Computational natural-product papers fail in a small number of recurring,
avoidable ways. This pipeline is built so that each of those failures is either
impossible or loudly reported.

| Common failure | What npcadd does |
|---|---|
| Docking with no validation | **Refuses to screen** until a co-crystal ligand is redocked and reproduces its crystallographic pose. `npcadd dock` exits with an error otherwise. |
| Ranking by raw docking score | **Refuses to rank by raw affinity.** Vina's score scales with molecular size, so ranking a C6→C30 set by it ranks largely by molecular weight. Ranking is on ligand efficiency. |
| Validating at one setting, screening at another | One `docking:` config block drives both. On the bundled system this is the difference between a pose at **0.24 Å** and one at **12.3 Å**. |
| A good ranking hiding a wrong pose | Every screened pose is checked for **search-box containment**: a pose with heavy atoms outside the box, or a centroid hugging a wall, is flagged in `docking_results.csv` and counted in the run log. Vina's ranking can stay healthy while correct placement collapses (Agboola et al. 2026, *Results in Engineering*), so the score alone is never trusted to confirm placement. |
| Affinities quoted without context | Every affinity carries a z-score and an empirical p-value against a random-ligand null distribution. |
| Scraped, unversioned ADMET numbers | HTML-form tools get a paste-ready batch and a validated paste-back schema, with tool version and retrieval date in provenance. |
| ML predictions on out-of-domain molecules | Every QSPR value carries an applicability-domain verdict; out-of-domain values are flagged and excluded from ranking. |
| Hidden methodological choices | `DECISIONS.md` logs every choice, the alternatives rejected, and where it is enforced in code. |
| Caveats buried in an appendix | Caveats are stamped on the workbook README, the SI header **and every figure caption**. |
| GUI steps that cannot be re-run | Excluded by design. `make reproduce` runs headless in a container. |

---

## Pipeline

```
                  config.yaml  ──(Pydantic-validated, hashed into every output)
                       │
   ┌───────────────────┼────────────────────────────────────────────┐
   ▼                   ▼                                            ▼
1 PREPARE-LIGANDS   2 PREPARE-RECEPTOR                     provenance.json
   SMILES CSV          fetch / cached PDB (checksummed)       git SHA, config hash
   standardise         normalise atom order                   SHA256 of every file
   pKa @ pH 7.4        strip solvent, keep cofactors          versions, OS, CPU
   ETKDGv3 + MMFF94    protonate (PDB2PQR + PROPKA)           all random seeds
   prune, → PDBQT      → PDBQT, derive box from co-crystal
   ↓                   ↓
   rejected_ligands.csv  receptor_provenance.csv
          │                   │
          └─────────┬─────────┘
                    ▼
        3 VALIDATE-DOCKING   ◀── MANDATORY, blocks everything downstream
             ├ redock co-crystal ligand → in-place RMSD → HALT if > 2.0 Å
             ├ property-matched decoys  → ROC-AUC, BEDROC, EF
             └ random-ligand null set   → z-score, empirical p
                    ▼
                4 DOCK  (Vina, seeded, parallel, resumable, ProLIF fingerprints)
                    ▼
                5 ADMET (RDKit descriptors, 6 rule sets, PAINS/Brenk, QSPR + AD)
                    ▼
                6 RANK  (LE, LLE, LELP, fit quality, multi-criteria + sensitivity)
                    ▼
                7 REPORT (XLSX, 300 dpi vector figures, VALIDATION.md, SI.md)
```

A Mermaid source for this diagram is in [`docs/pipeline.mmd`](docs/pipeline.mmd).

---

## 5-minute quickstart

**Requirements:** Linux or macOS, Python 3.11, [uv](https://docs.astral.sh/uv/).
Docker only for `make reproduce`.

```bash
git clone <this-repo> && cd cursor-ai
make install                 # creates .venv from the pinned uv.lock
source .venv/bin/activate

npcadd version               # confirm the scientific stack is present
make smoke                   # full pipeline on 5 bundled compounds (~10 min)
```

Outputs land in `reports/`:

| File | What it is |
|---|---|
| `reports/npcadd_smoke.xlsx` | Multi-sheet workbook; the **README sheet** explains every other sheet |
| `reports/VALIDATION.md` | The three validation controls and their verdicts |
| `reports/SUPPLEMENTARY_INFORMATION.md` | Methods, ranked table, figure captions |
| `reports/figures/*.pdf`, `*.png` | 300 dpi, vector, colourblind-safe |
| `runs/<run-id>/provenance.json` | Git SHA, config hash, every file checksum, versions, seeds |
| `data/interim/rejected_ligands.csv` | Every molecule that did not enter screening, and why |

Then verify nothing has drifted:

```bash
npcadd verify-provenance runs/<run-id>/provenance.json
```

### Further analysis commands

These run against a finished run directory (or, for `doctor`, before a run) and
need no re-docking:

| Command | What it produces |
|---|---|
| `npcadd doctor -c config.yaml` | Pre-flight checklist: config, AutoDock Vina, receptors, inputs, disk headroom, and whether the enrichment control can run. |
| `npcadd html-report -c config.yaml` | A single self-contained `reports/<run-id>_summary.html` — top hits, caveats and provenance in one portable page. |
| `npcadd dossiers -c config.yaml --top 5` | One markdown evidence card per top compound in `reports/dossiers/`, aggregating docking, efficiency, ADMET and every caveat. |
| `npcadd scaffolds -c config.yaml` | Bemis-Murcko scaffold and Butina-cluster diversity of the shortlist (`reports/scaffold_diversity.csv`). |
| `npcadd actives-template -c config.yaml` | A known-actives CSV template plus a guide for populating the enrichment control from ChEMBL/BindingDB. |

Additional per-compound columns now flow through the tables, all **diagnostic**
— none changes the ranking:

- **Natural-product-likeness** (Ertl & Schuffenhauer): `np_likeness_*` in the ADMET table — the NP-trained complement to the synthetic-drug rule sets.
- **Efficiency indices** `binding_efficiency_index_bei`, `surface_efficiency_index_sei`, `size_independent_le_sile` in the ranked table (the metrics ChEMBL curates).
- **Pose consensus and sampling stability** `affinity_spread_kcal_mol`, `consensus_ok`, `top2_pose_rmsd_a` in the docking table — reference-free confidence signals beside the single best score.

---

## Worked example: the bundled sample run

`config.smoke.yaml` screens five compounds — including the two lead compounds,
**loliolide** and **spathulenol** — against **ABL1 kinase** (PDB 1IEP), the
target of imatinib.

> **The abundances in `data/sample/` are fabricated.** Compound names and SMILES
> are real and formula-validated; the GC-MS area percentages are a seeded
> pseudo-random draw so the pipeline has a realistic distribution to run on.
> Every row is marked `SYNTHETIC_PLACEHOLDER`, and the pipeline stamps a warning
> on every output when it sees that marker. See
> [`data/sample/README.md`](data/sample/README.md).
>
> The **receptor is real**, so the validation gate is a genuine test.

### What the validation gate actually does

Redocking imatinib into its own receptor, through the full preparation chain
(PDB2PQR/PROPKA protonation at pH 7.4, Meeko PDBQT, box derived from the
co-crystal ligand):

| Exhaustiveness | In-place RMSD | Affinity | Gate at 2.0 Å |
|---|---|---|---|
| 8 | 12.29 Å | −10.96 kcal/mol | **FAIL** |
| **32** | **0.24 Å** | **−13.28 kcal/mol** | **PASS** |

At exhaustiveness 8 the search finds the right pocket (centroid offset 1.8 Å)
but the wrong orientation — and the failing run's score is *less* favourable, so
**the failure is invisible from the score alone**. Only the pose comparison
reveals it. This is why `config.smoke.yaml` reduces the ligand count for CI but
never the exhaustiveness.

As an independent check that the preparation chain is chemically right, the
interaction fingerprint of the crystallographic imatinib pose recovers the
textbook ABL1 contacts: hydrogen bonds to **Thr315** (the gatekeeper whose T315I
mutation causes clinical imatinib resistance), **Met318**, **Glu286** and the
DFG **Asp381**, plus hydrophobic contacts to Leu248, Tyr253, Lys271, Met290,
Ile313, Phe317, Leu370 and Phe382.

### Why ranking by raw score would have been wrong

| Compound | Heavy atoms | Vina score | Ligand efficiency |
|---|---|---|---|
| Loliolide | 14 | −7.58 | **0.542** |
| Spathulenol | 16 | −5.15 | 0.322 |
| Imatinib (reference) | 37 | **−13.28** | 0.359 |

*Numbers are from the `config.smoke.yaml` run (exhaustiveness 32, seed 20240617, 2 conformers per ligand); imatinib is the redocking control. Docking scores of this precision vary with conformer set and seed, which is itself a reminder that differences of ~1 kcal/mol are noise.*

Imatinib wins on raw score — deservedly, it is a real nanomolar inhibitor of
this target. But loliolide, less than half its size, is the more efficient
binder per heavy atom. In the smoke run the raw-score order is campesterol, loliolide, glycerol monostearate, vanillin, spathulenol, but the efficiency order is vanillin, loliolide, spathulenol, campesterol, glycerol monostearate: the largest molecules fall and the small ones rise. See [`DECISIONS.md`](DECISIONS.md) D-003.

---

## Using it on your own data

### 1. Your component table

A CSV with these columns (names configurable under `ligands:`):

```csv
name,smiles,compound_class,area_percent
Loliolide,CC1(C)CC(O)CC2(C)OC(=O)C=C12,terpenoid,2.81
Spathulenol,CC1(C)C2CCC(=C)C3CCC(C)(O)C3C12,terpenoid,1.35
```

`compound_class` should be `phenolic`, `terpenoid` or `other`.

### 2. Your targets

Copy `config.sample.yaml` and edit the `receptors:` block:

```yaml
receptors:
  - id: abl1
    pdb_id: 1IEP
    activity: cytotoxic          # must be an activity you actually assayed
    target_name: "ABL1 tyrosine-protein kinase"
    uniprot: P00519
    chain: A
    cocrystal_ligand: STI        # enables the redocking gate
    grid:
      mode: cocrystal            # box derived from the ligand; no magic numbers
      padding: 5.0
    phenotype_direction: inhibition
    structure_is_inhibitor_bound: true
```

`phenotype_direction` and `structure_is_inhibitor_bound` are required. If you
declare an *activation* phenotype against an *inhibitor-bound* structure, the
pipeline flags the directionality conflict on every output rather than letting
it pass silently ([`DECISIONS.md`](DECISIONS.md) D-004).

### 3. Run

```bash
npcadd run-all --config my_config.yaml
```

Or stage by stage — each is independently runnable and resumable:

```bash
npcadd prepare-ligands   --config my_config.yaml
npcadd prepare-receptor  --config my_config.yaml
npcadd validate-docking  --config my_config.yaml   # must pass before dock
npcadd dock              --config my_config.yaml
npcadd admet             --config my_config.yaml
npcadd rank              --config my_config.yaml
npcadd report            --config my_config.yaml
```

### 4. Get the enrichment control running

Without known actives, ROC-AUC/BEDROC/EF are reported as **not performed** — no
active set is fabricated. Supply one (from ChEMBL or BindingDB) to enable them:

```yaml
validation:
  known_actives_csv: data/raw/abl1_actives.csv   # columns: name,smiles
```

---

## Reproducibility

```bash
make docker-pin      # pin the base image by digest (do this once)
make reproduce       # regenerate every table and figure inside the container
```

`make reproduce` rebuilds everything from raw inputs with `PYTHONHASHSEED=0` and
single-threaded BLAS. `data/raw` is mounted read-only.

**What is guaranteed:** the deterministic stages — ligand preparation (including
3D coordinates), descriptors, rule sets, alerts and ranking — are **byte-identical**
between runs. A golden-file test asserts this on file hashes, and a companion
test confirms that changing the embedding seed *does* change the output, so the
test cannot pass vacuously.

**What is not:** AutoDock Vina's parallel search can interleave differently, so
changing worker counts may perturb the last decimal. Worker counts are recorded
in provenance. See [`LIMITATIONS.md`](LIMITATIONS.md) §4.5.

Each run writes `provenance.json`: git commit and dirty flag, config hash and
the verbatim config, SHA256 of every input and output, resolved versions of
every scientifically relevant package, OS, CPU, UTC timestamp, and every seed.

---

## Development

```bash
make check      # lint + mypy --strict + tests with coverage
make test-all   # including slow tests
make hooks      # install pre-commit hooks
```

The codebase is `mypy --strict` clean and `ruff` clean. Tests include
property-based tests (Hypothesis) for descriptors and standardisation,
agreement-with-`sklearn` tests for ROC-AUC, and the golden-file determinism
test. CI runs lint, types, tests, a sample-data regeneration check and a full
end-to-end smoke run on every push.

---

## Read these before quoting any number

- **[`LIMITATIONS.md`](LIMITATIONS.md)** — what docking cannot do, what is
  stubbed in this checkout, and what would most strengthen the work. Start here.
- **[`DECISIONS.md`](DECISIONS.md)** — every methodological choice, the
  alternatives rejected, and where each is enforced in code.
- **[`reports/VALIDATION.md`](reports/VALIDATION.md)** — generated per run; the
  evidence that the protocol reproduces a known answer.

Three things worth internalising:

1. **A docking score is not an affinity.** Vina's error against experiment is
   ~2–3 kcal/mol, roughly three orders of magnitude in Kd. Two compounds within
   1–2 kcal/mol are not distinguishable.
2. **Docking cannot explain a DPPH result.** Radical scavenging is a chemical
   property measured without protein. This is why human catalase is rejected as
   an antioxidant docking target ([`DECISIONS.md`](DECISIONS.md) D-001).
3. **The assays measured a mixture.** Attributing a mixture's activity to
   individual components is a hypothesis. Isolate and assay the leads.

---

## Citation

If you use this pipeline, cite it via [`CITATION.cff`](CITATION.cff), and cite
the underlying tools — they did the scientific work:

- **AutoDock Vina** — Eberhardt, Santos-Martins, Tillack & Forli, *J. Chem. Inf.
  Model.* 2021, 61, 3891–3898; Trott & Olson, *J. Comput. Chem.* 2010, 31, 455–461.
- **RDKit** — Landrum et al., open-source cheminformatics, https://www.rdkit.org
- **Meeko** — Forli Lab, https://github.com/forlilab/Meeko
- **ProLIF** — Bouysset & Fiorucci, *J. Cheminform.* 2021, 13, 72.
- **PDB2PQR / PROPKA** — Dolinsky et al., *Nucleic Acids Res.* 2004, 32, W665;
  Olsson et al., *J. Chem. Theory Comput.* 2011, 7, 525–537.
- **Dimorphite-DL** — Ropp et al., *J. Cheminform.* 2019, 11, 14.
- **ESOL** — Delaney, *J. Chem. Inf. Comput. Sci.* 2004, 44, 1000–1005.
- **Structures** — cite each PDB entry and its primary publication.

Archiving for a manuscript: see [`CONTRIBUTING.md`](CONTRIBUTING.md) §Zenodo.

## Licence

MIT — see [`LICENSE`](LICENSE). Bundled PDB-derived coordinates carry no
copyright restriction; see
[`data/sample/receptors/PROVENANCE.md`](data/sample/receptors/PROVENANCE.md).
