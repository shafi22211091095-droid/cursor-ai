# npcadd research & development roadmap

**Date:** 2026-11-05 · **Status:** living document, for human prioritisation

A prioritised, falsifiable set of research and engineering directions for
`npcadd`, derived from the pipeline's own `LIMITATIONS.md` and `DECISIONS.md`
and from the cited `docs/EVIDENCE_REVIEW.md`. This is **planning, not results**.
No item below has been executed except where it says so; each is written so that
a reviewer can see what is unknown, why closing it matters, and the cheapest
experiment that would settle it.

**Honest scale.** This is a focused portfolio of high-value items grounded in
real evidence, not an inflated list. Every item carries an evidence-status tag
(`[LIT]` literature-supported, `[DB]` database-grounded, `[CODE]` verifiable in
this repo, `[REASON]` reasoned) and a *cheapest meaningful test* — the smallest
experiment whose result would change the decision.

Priority score (1–5, higher = do sooner) weighs importance × feasibility ×
expected information gain, in the spirit of the directive's prioritisation.

---

## Portfolio A — high-probability, feasible with what is already here

### A1 · Activate the enrichment control with real ABL1 actives — **priority 5**
- **Unknown.** Does the protocol *discriminate* known ABL1 binders from
  property-matched decoys, or only reproduce one co-crystal pose? Today only the
  redocking gate and the null distribution run; ROC-AUC/BEDROC/EF are reported
  as "not performed" (`DECISIONS.md` D-020). `[CODE]`
- **Why it matters.** Self-docking is "necessary but insufficient" `[LIT]`([2]);
  enrichment is the control that tests ranking of novel chemotypes.
- **Cheapest test.** Pull ~50–200 ABL1 actives (pChEMBL ≥ 7) from ChEMBL
  **CHEMBL1862** `[DB]`, write them to `validation.known_actives_csv`, re-run
  validation. The metric machinery already exists (`docking/metrics.py`).
- **Full plan:** see the first-project dossier below.

### A2 · Pin the container digest and execute `make reproduce` — **priority 5**
- **Unknown / status.** The `Dockerfile` base is tag-pinned, not digest-pinned,
  and `make reproduce` has never been run in a container here (`LIMITATIONS.md`
  §5.1–5.2). `[CODE]`
- **Why it matters.** It converts the reproducibility claim from asserted to
  verified — the pipeline's central promise.
- **Cheapest test.** `make docker-pin && make docker-check-pinned && make
  reproduce` on a networked host; confirm byte-identical deterministic outputs.

### A3 · Re-prepare the receptor from the deposited 1IEP entry — **priority 4**
- **Unknown.** The bundled receptor is a stripped derivative, so resolution,
  R-free and experimental method report as `unavailable` (`LIMITATIONS.md` §5.3)
  — exactly what a reviewer needs to judge structure quality. `[CODE]`
- **Cheapest test.** Run the receptor stage with `allow_network: true` against
  RCSB (the fetcher is implemented but never exercised), diff the prepared
  structure, confirm metadata populates.

### A4 · Raise the null set to publication size — **priority 3**
- **Unknown.** The smoke null set (n = 10) floors the empirical p-value at
  1/11 ≈ 0.091; the sample config at n = 20 floors it at 0.048 (`LIMITATIONS.md`
  §4.3). `[CODE]`
- **Cheapest test.** Set `validation.null_set_size ≥ 100` for a real run and
  confirm the resolution line and run cost; it is the dominant wall-clock term.

---

## Portfolio B — high-impact, meaningful effort

### B1 · Cross-docking validation across multiple ABL1 structures — **priority 4**
- **Unknown.** Can the protocol recover poses in a structure *other* than the one
  a ligand was co-crystallised with? Cross-docking is the harder, more realistic
  control and is where top-score ≠ top-pose bites hardest `[LIT]`([6], [2]).
- **Why it matters.** It is named an essential complement to self-docking
  `[LIT]`([2]); ABL1 has dozens of deposited structures `[DB]` (CHEMBL1862 lists
  many PDBe cross-references), so the data exists.
- **Cheapest test.** Add a second ABL1 PDB, redock each co-crystal ligand into
  the other receptor, report the cross-RMSD matrix.

### B2 · Pocket-aware placement score (extend D-021) — **priority 4**
- **Unknown.** The new placement check (`DECISIONS.md` D-021) measures distance
  to the *box*, not to the *binding-site residues*; a pose inside the box but in
  a shallow surface groove is not flagged (`LIMITATIONS.md` §4.6). `[CODE]`
- **Cheapest test.** Define the pocket from the co-crystal ligand's contact
  residues, add a "fraction of pose atoms within N Å of any pocket residue"
  term alongside box containment; calibrate N on the redocking pose.

### B3 · MM-GBSA rescoring of the top handful — **priority 3**
- **Unknown.** Do the shortlist's rankings survive a second, physics-based
  scoring model? Only ~12.5% of audited studies did any MM-PB/GBSA `[LIT]`([1]).
- **Cheapest test.** Rescore the top ~5 compounds with an open MM-GBSA workflow;
  report as an independent model *with its own validation*, never as truth
  (`LIMITATIONS.md` §6.6). `[REASON]`

### B4 · Ship a trained ADMET model with its training SMILES — **priority 3**
- **Unknown / status.** `TanimotoKNNDomain` is implemented but unused: no bundled
  model ships a training set for the similarity domain (`LIMITATIONS.md` §5.7).
  `[CODE]`
- **Why it matters.** A single closed-form solubility model is thin ADMET
  coverage; a trained classifier with a real applicability domain would be the
  first use of the KNN domain the code already supports. AD matters most exactly
  here, in NP space `[LIT]`([A10]).
- **Cheapest test.** Add one open, citable model (e.g. a solubility or
  permeability set) with its training SMILES; verify OOD flagging on the leads.

### B5 · Optional ranking exclusion of placement-flagged poses — **priority 2**
- **Unknown.** Would excluding placement-suspect poses from ranking *improve*
  shortlist quality, or just discard large legitimate ligands? D-021 deliberately
  left this as report-only. `[CODE]` `[REASON]`
- **Cheapest test.** Behind a config flag, exclude flagged poses and compare
  enrichment (once A1 is live) with and without; adopt only if enrichment rises.

---

## Portfolio C — frontier, scientifically plausible, speculative

### C1 · Ensemble / multi-conformation docking as an induced-fit proxy — **priority 2**
- **Unknown.** How many false negatives does the rigid-receptor assumption
  (`LIMITATIONS.md` §1.2) cost on bulky terpenoids? `[REASON]`
- **Cheapest test.** Dock the leads into an ensemble of ABL1 conformations and
  compare best-of-ensemble to single-structure scores.

### C2 · Size-stratified null distribution — **priority 2**
- **Unknown.** The null set calibrates z-scores but is not matched to each
  ligand's heavy-atom count, so part of the z may still be a size signal
  (the same confound D-003 addresses for ranking). `[REASON]`
- **Cheapest test.** Bin the null by heavy-atom count; recompute z against the
  matched bin; see whether lead significance changes.

### C3 · Reference-free pose-confidence classifier — **priority 1**
- **Unknown.** Can a model trained on redocking outcomes predict pose
  correctness for screened ligands that have no crystal reference — turning the
  geometric placement heuristic (D-021) into a calibrated confidence? `[REASON]`
- **Cheapest test.** Label a redocking benchmark by RMSD, train on
  placement + interaction + score features, test whether it ranks pose quality
  better than score alone — the gap [7] showed exists. `[LIT]`([7])

---

## Priority summary

| ID | Item | Portfolio | Priority | Evidence |
|----|------|-----------|----------|----------|
| A1 | Enrichment control with real actives | A | 5 | [DB][LIT][CODE] |
| A2 | Pin digest + run reproduce | A | 5 | [CODE] |
| A3 | Re-prepare receptor from RCSB | A | 4 | [CODE] |
| B1 | Cross-docking validation | B | 4 | [LIT][DB] |
| B2 | Pocket-aware placement score | B | 4 | [CODE][LIT] |
| A4 | Publication-size null set | A | 3 | [CODE] |
| B3 | MM-GBSA rescoring | B | 3 | [LIT] |
| B4 | Trained ADMET model + AD | B | 3 | [CODE][LIT] |
| B5 | Optional placement exclusion | B | 2 | [CODE] |
| C1 | Ensemble docking | C | 2 | [REASON] |
| C2 | Size-stratified null | C | 2 | [REASON] |
| C3 | Pose-confidence classifier | C | 1 | [LIT] |

---

## First-project dossier (Mission E): activate the enrichment control (A1)

The highest-value, lowest-risk next step. It is grounded in a confirmed data
source, uses machinery already in the repository, and closes the single largest
gap in the validation story.

**Objective.** Report ROC-AUC, BEDROC(α=20) and EF at 1/5/10% for ABL1 (1IEP),
so the pipeline's ranking is shown to discriminate real binders from decoys, not
merely to reproduce one pose.

**Hypothesis (falsifiable).** With a curated ABL1 active set, the protocol
achieves ROC-AUC ≥ 0.70 ("moderate discrimination" or better, per
`metrics.py::EnrichmentReport.verdict`). **Falsified** if ROC-AUC ≤ 0.55 (no
better than random), which would itself be a reportable, publishable negative
result about rigid NP docking on this target.

**Data & provenance.**
- Source: ChEMBL v34 target **CHEMBL1862** (UniProt **P00519**), which holds
  **2,808** IC50 records with potent actives at pChEMBL ≥ 7 `[DB]`.
- Inclusion: `standard_type = IC50`, `standard_relation = '='`,
  `pchembl_value ≥ 7`, human target, no `data_validity_comment` flag. Record
  each activity's `document_chembl_id` for provenance.
- De-duplicate by parent `molecule_chembl_id`; exclude the redocking reference
  (imatinib, CHEMBL941) to avoid leakage.
- Decoys remain the pipeline's property-matched offline pool (`decoys.py`), with
  its stated limits (`LIMITATIONS.md` §4.1): they are *presumed*, not confirmed,
  inactive.

**Method.** Write actives to `validation.known_actives_csv`; the existing
validation stage property-matches decoys, docks both at the screening settings
(shared parameters, D-008), and computes the metrics. No new code is required
for the core result; A1 is primarily careful data curation.

**Controls & integrity.**
- The redocking gate must still pass at the same settings, or the enrichment
  number is uninterpretable (D-019).
- Report the decoy pool's mass span and any tolerance relaxations (already
  emitted), so an optimistic enrichment is visible as such (§4.1).
- Pre-register the ROC-AUC threshold above *before* running, so a weak result is
  reported, not buried (D-020's spirit).

**Success criteria.** A populated `VALIDATION.md` enrichment section with
ROC-AUC, BEDROC and EFs, their max-possible ceilings, and the active/decoy
counts; the config hash and the ChEMBL document IDs in provenance.

**Failure modes.** (i) Too few actives above the mass the decoy pool can match
(§4.1) → widen the pool or relax pChEMBL to ≥ 6 and report it. (ii) Actives span
multiple binding modes (type I vs II inhibitors) → stratify or state the
heterogeneity. (iii) Low ROC-AUC → report as a finding about the method on this
target, not a bug to tune away.

**Cost.** Dominated by docking the active + decoy set once at exhaustiveness 32;
bounded and resumable (`docking.resume`). **Ethics/biosafety:** none — it is a
retrospective computation on public data. **Reproducibility:** fixed seeds +
config hash + container (once A2 lands).

**What it does *not* establish.** Enrichment against a bundled, presumed-inactive
decoy pool is an optimistic-to-middling estimate, not proof the leads bind
(`LIMITATIONS.md` §4.1, §4.4). The only thing that converts the docking
hypotheses into tested predictions remains isolating and assaying the leads
(recommendation 1) — reinforced by Part 3 of the evidence review, which found
**no** curated bioactivity for loliolide or spathulenol to lean on `[DB]`.

*Reference numbers in brackets ([1], [2], [6], [7], [A10]) resolve to
`docs/EVIDENCE_REVIEW.md`.*
