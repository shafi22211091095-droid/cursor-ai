# LIMITATIONS

What this pipeline cannot do, what it does only approximately, and what in this
specific repository is stubbed or unverified.

This file exists because the most common failure of computational
natural-product papers is not a wrong calculation — it is a correct calculation
presented as more than it is. Read this before quoting any number the pipeline
produces.

---

## 1. Limits of molecular docking itself

These apply to every docking study and are not defects of this implementation.

### 1.1 A docking score is not a binding affinity

AutoDock Vina's score is an **empirical function fitted to a training set of
protein–ligand complexes**. It is not a free energy, and it does not become one
by being expressed in kcal/mol. The reported standard error of Vina's score
against experimental affinity is roughly **2–3 kcal/mol**, which is about three
orders of magnitude in Kd. Two compounds separated by 1–2 kcal/mol are, for
practical purposes, **not distinguishable**.

Consequences for how results may be worded:

- "Compound A (−8.2) binds more strongly than compound B (−7.4)" is **not
  supported**. That gap is well inside the method's error.
- The `pkd_estimate_from_score` column is a *unit conversion* onto a
  log-affinity scale so that LLE is comparable with published values. It is
  **not** a predicted binding constant and must never be reported as one.
- Docking is a **ranking and triage** tool. It is reasonable to say a compound
  is "predicted to occupy the ATP site with contacts to Thr315". It is not
  reasonable to assign it a potency.

### 1.2 The receptor is rigid

The pipeline docks into a rigid receptor. Side chains do not move, loops do not
rearrange, and no induced fit occurs. Natural products are frequently bulky and
irregular, exactly the ligands most likely to need pocket adjustment, so
false negatives are expected — a compound may score poorly purely because the
crystallised side-chain rotamers obstruct it.

### 1.3 Water and entropy are implicit

Explicit waters are stripped. Bridging waters that mediate real binding are
therefore absent, and the desolvation and entropic terms are lumped into the
empirical score rather than computed. Buried polar pockets are the worst case.

### 1.4 One conformation of one state of the protein

A crystal structure is one conformational snapshot, often of one functional
state, frequently with a crystallisation additive in the pocket. See
`DECISIONS.md` D-004: a favourable score against an inhibitor-bound structure is
evidence of binding **to that conformation**, never evidence of activation.

### 1.5 Docking cannot model a chemical-reactivity assay

DPPH, ABTS and FRAP measure hydrogen-atom or electron donation to a stable
radical in solution, with no protein present. The governing quantities are
bond-dissociation enthalpy and ionisation potential. **No docking score against
any protein can explain a DPPH result.** This is the reasoning behind rejecting
catalase as an antioxidant target (`DECISIONS.md` D-001), and it applies equally
to any other protein substituted for it.

---

## 2. Limits of the inputs

### 2.1 GC-MS identification is tentative, and stereochemistry is usually unknown

A GC-MS library match is a spectral similarity, not a structure determination.
Two specific consequences:

- **Isomers are frequently indistinguishable.** Library matching routinely
  cannot separate stereoisomers or close regioisomers, so the compound docked
  may not be the compound present.
- **Absolute configuration is generally not established.** The pipeline counts
  unspecified stereocentres, reports them per compound, and raises a caveat; it
  **never invents a configuration**. Where a compound has unspecified centres,
  the docked stereoisomer is one of several consistent with the identification,
  and its pose and score are correspondingly uncertain. In the sample run, four
  of five compounds carry at least one unspecified centre — this is the normal
  situation, not an edge case.

### 2.2 GC-MS area % is not concentration

Area % is detector response, which depends on ionisation efficiency and
volatility per compound. It is **not** a mole fraction or a mass fraction
without compound-specific calibration. The pipeline uses it as a weighted term
in the multi-criteria score because abundance is weak evidence of relevance, but
the weight is configurable and the sensitivity analysis shows how much the
shortlist depends on it.

### 2.3 GC-MS sees only what is volatile and thermally stable

Non-volatile and thermally labile constituents — most glycosides, tannins, many
flavonoid conjugates — do not appear in a GC-MS component list at all, or appear
only as degradation products. A GC-MS-derived screening set is therefore **not**
the extract's full chemical inventory, and the compound responsible for a
measured bioactivity may simply not be in the list.

### 2.4 Activity is measured on the extract, not on the compounds

The assays measure a **mixture**. Attributing a mixture's activity to individual
components is a hypothesis, not an observation. Additivity, synergy and
antagonism are all possible, and docking cannot distinguish them. Nothing here
substitutes for isolating the compound and assaying it.

---

## 3. Limits of the ADMET predictions

### 3.1 QSPR predictions are flagged when out of domain, and the flag matters

The bundled model is **ESOL** (Delaney 2004), a published closed-form equation
chosen because it needs no downloaded weights and runs offline. Its reported
accuracy on held-out data is roughly **0.9–1.0 log units RMSE**, so a prediction
is a one-order-of-magnitude estimate.

Every prediction carries an applicability-domain verdict. An out-of-domain value
is an **extrapolation**: it is flagged `in_domain=FALSE`, excluded from the
ranking score, and reported as a caveat. Natural-product space sits at or beyond
the edge of the drug-like space most ADMET models were fitted on, so
out-of-domain predictions are common and are the normal case, not an anomaly.

### 3.2 Rule sets are historical heuristics, not rules

Lipinski, Veber, Egan, Ghose, Muegge and Pfizer 3/75 are statistical
observations about **orally dosed synthetic drugs**. Natural products are
systematically different — more oxygen, more stereocentres, more rings, lower
Fsp3 diversity — and many approved natural-product-derived drugs violate several
of these filters. A failing compound is not thereby a bad compound. Reported
`admet_pass_rate` is a blunt, deliberately simple summary, and the rule sets are
correlated rather than independent evidence, which is why the weight sensitivity
analysis exists.

### 3.3 No metabolism, no CYP, no toxicity prediction is computed locally

The pipeline computes **no** CYP inhibition, hepatotoxicity, mutagenicity or
LD50 prediction of its own. Those require the HTML-form tools (SwissADME, pkCSM,
ProTox-III), which are handled by a manual handoff and never scraped
(`DECISIONS.md` D-010). Until you complete that handoff, those columns are
**absent, not zero**.

---

## 4. Limits of the validation

### 4.1 The decoy pool is bundled, not database-derived

Decoys come from an offline combinatorial enumeration rather than from ZINC or a
comparable database. The pool spans roughly **106–429 Da**. Specific
consequences:

- Chemotype coverage is bounded by the bundled fragments, so the pool is less
  diverse than a database-derived one and enrichment against it is an
  **optimistic-to-middling estimate**, not a hard benchmark.
- The molecules are synthetic combinatorial products, so they carry no
  information about purchasability, and none has been assayed and found
  inactive.
- Decoys are **presumed** inactive because they are property-matched and
  topologically dissimilar to the actives (Tanimoto < 0.35). They are **not**
  confirmed inactives.
- An active above roughly 430 Da cannot be property-matched from this pool.
  Tolerance relaxations are counted per active and reported, so a weaker decoy
  set is visible as one.

### 4.2 Enrichment is not computed unless you supply known actives

Without a user-supplied set of experimentally confirmed actives, ROC-AUC, BEDROC
and enrichment factors are **not defined** and are reported as not performed. No
active set is fabricated (`DECISIONS.md` D-020). Supply
`validation.known_actives_csv` — for example from ChEMBL or BindingDB — to
obtain these metrics. The redocking gate and the null distribution still run.

### 4.3 A null-set p-value has a hard resolution floor

The empirical p-value uses the `(k+1)/(n+1)` estimator, so for a null set of
size `n` the smallest value obtainable is `1/(n+1)`. With the sample config's
20-ligand null set, **no p-value below 0.048 can be reported**, regardless of
how favourable an affinity is. The floor is printed in every table as
`p_value_resolution`. For a publication run use at least 100 null ligands, and
note that the null set is the dominant cost of a run.

### 4.4 A passing redocking gate validates one pose, not the method

Reproducing a co-crystal pose shows the protocol can recover a known answer
**for that ligand in that pocket**. It does not establish that the protocol
ranks novel chemotypes correctly, and the co-crystal ligand is usually much more
drug-like than a phytochemical. The enrichment control addresses this, which is
why it should not be skipped.

### 4.5 Docking determinism is seed-fixed but not bit-guaranteed under threading

The Vina seed comes from config and is passed explicitly, and the golden
determinism test asserts byte-identical output for the deterministic stages
(ligand preparation, descriptors, rules, alerts, ranking). **Docking itself is
excluded from that guarantee**: Vina's parallel search can interleave work
differently between runs, so changing `docking.workers` or `cpu_per_worker` may
perturb results at the last decimal. Worker counts are therefore recorded in
provenance. For a byte-reproducible docking run, fix the worker and CPU counts
and use the container.

---

## 5. Limits specific to this repository right now

These are the items that are stubbed, unverified or environment-limited in this
checkout. They are the first things to fix before publication.

### 5.1 Container digest is unpinned

**Status: not done, with tooling provided.**

The `Dockerfile` base image is referenced by **tag**, not by digest. A tag is
mutable, so `make reproduce` is not reproducible across time until this is
pinned. It is not pinned here because the environment this repository was
assembled in had **no outbound access to a container registry**, so no digest
could be resolved — and the pipeline does not record a checksum it has not
verified.

Fix it with one command before any manuscript use:

```bash
make docker-pin        # resolves the digest and rewrites the FROM line
make docker-check-pinned
```

`make docker-check-pinned` fails while the image is unpinned, `make reproduce`
warns loudly, and CI emits a warning annotation.

### 5.2 `make reproduce` has not been executed in a container here

**Status: unverified.**

Docker Hub was unreachable in the assembly environment, so the image was never
built or run. The `Dockerfile` and the `reproduce` target are written and
reviewed but **not executed**. Everything else — the full pipeline, the
validation gate, the tests — was run on the host interpreter and does work. Run
`make reproduce` on a networked machine and confirm before relying on it.

### 5.3 Structure retrieval from RCSB is implemented but was never exercised

**Status: implemented, unverified.**

`files.rcsb.org` and `data.rcsb.org` were both blocked in the assembly
environment. The fetcher, its checksum caching, its corrupt-cache handling and
the RCSB REST metadata lookup are all implemented and typed, but **no live fetch
was performed**. Offline operation with `local_structure` is fully exercised.

Consequently the bundled sample receptor is a **prepared derivative** with a
stripped header, so its resolution, R-free, experimental method, deposition date
and co-crystal ligand identifiers all report as `unavailable`. That is correct
behaviour, not a bug — but it means **the resolution of the sample receptor is
unknown**, and resolution is exactly what a reviewer needs to judge whether a
structure supports docking. Re-prepare from the deposited entry with
`allow_network: true` before publishing anything from it.

### 5.4 Only one receptor is bundled, covering one of the three activities

**Status: incomplete.**

The bundled receptor (ABL1 kinase, from PDB 1IEP) maps to the **cytotoxic**
activity only. The **antioxidant** and **thrombolytic** targets are not bundled,
because selecting them requires a live structure fetch and, more importantly,
scientific decisions this pipeline should not make silently:

- **Antioxidant**: catalase is rejected outright (`DECISIONS.md` D-001). A
  coherent target is a ROS-*generating* enzyme such as xanthine oxidase, but
  that needs a matching enzyme-inhibition assay to anchor it, and the available
  high-resolution structures are bovine rather than human — a species caveat
  that must be stated.
- **Thrombolytic**: the obvious targets have a directionality problem
  (`DECISIONS.md` D-004). Plasmin inhibition is *anti*-fibrinolytic; claiming
  tPA or plasminogen *activation* from a docking score is unsupported. PAI-1
  inhibition is the coherent choice.

Add these to `config.yaml` with real PDB IDs, `grid.mode: cocrystal`, and the
`phenotype_direction` and `structure_is_inhibitor_bound` fields filled in
honestly. The pipeline will then fetch, validate and screen them.

Note also that ABL1 is a **specific oncogenic kinase**, not a general
cytotoxicity target. Whether it is the right target for your cytotoxicity assay
(which cell line? which mechanism?) is a scientific question the manuscript must
answer.

### 5.5 The hepatoprotective study has no configuration yet

**Status: not started.**

`Activity` accepts `hepatoprotective`, but no receptor panel, no sample data and
no configuration exist for the second manuscript. Target selection for
hepatoprotection has the same directionality hazard as thrombolysis and needs
the same explicit treatment.

### 5.6 Sample abundances are fabricated

**Status: by design, loudly marked.**

Every GC-MS area % and retention time in `data/sample/` is **fabricated**. The
compound names and SMILES are real and each SMILES is validated against its
published molecular formula, but the abundances are a seeded pseudo-random draw.
Every row carries `data_provenance=SYNTHETIC_PLACEHOLDER`, the pipeline detects
that column and stamps a warning on the workbook, the SI and the figure
captions, and a pre-commit hook refuses to let the marker be removed.

**No result from the sample data belongs in a manuscript.** Point
`ligands.input_csv` at your own component table.

### 5.7 No ML ADMET model beyond a closed-form QSPR

**Status: interface complete, one model bundled.**

`MLPredictor` is a pluggable interface with an enforced applicability domain, and
`ESOLSolubilityPredictor` implements it. A trained model (a Chemprop or
scikit-learn hepatotoxicity classifier, say) would need downloaded weights plus
a training set for the Tanimoto domain check, neither of which is available
offline. `TanimotoKNNDomain` is implemented and ready for a model that ships its
training SMILES.

### 5.8 Some pool molecules are very slow to dock

**Status: known, mitigated by capping flexibility.**

Highly flexible pool members (many rotatable bonds) can take minutes each at
exhaustiveness 32 in a ~12,500 Å³ box, which dominates the wall-clock of a run
through the null set. The pool caps rotatable bonds to keep the null set
drug-like and the cost bounded. If a run appears to hang, it is almost certainly
docking a flexible null ligand; the structured log reports progress per batch.

---

## 6. What would materially strengthen this work

In rough order of value per unit effort:

1. **Isolate and assay the leads.** Loliolide and spathulenol, assayed
   individually, would convert every docking result in both manuscripts from a
   hypothesis into a tested prediction. Nothing computational substitutes for
   this.
2. **Supply known actives per target** so the enrichment control actually runs.
   Without it, the only validation is the redocking gate.
3. **Add an enzyme-inhibition assay** matched to each docking target, so the
   structural rationale has an experimental anchor and the directionality is
   settled by data rather than by argument.
4. **Pin the container digest and run `make reproduce`.** Two commands, and the
   reproducibility claim becomes verified rather than asserted.
5. **Re-prepare receptors from deposited entries** so resolution and R-free are
   populated and a reviewer can judge structure quality.
6. **MD-based rescoring (MM-GBSA) of the top handful**, with the explicit caveat
   that it is a second model and needs its own validation.
7. **Resolve stereochemistry** for the leads by isolation and optical rotation or
   NMR, removing the largest single source of pose uncertainty.
