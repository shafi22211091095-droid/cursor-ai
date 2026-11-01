# DECISIONS

An append-only log of every methodological choice in this pipeline, the
alternatives rejected, and why. **Append; do not rewrite.** If a decision is
reversed, add a new entry that supersedes the old one and say so — the history
of what was considered is itself part of the method.

Format: each entry states the decision, the alternatives rejected, the
reasoning, and where it is enforced in code. A decision that is not enforced
anywhere is a preference, not a decision, and is marked as such.

---

## D-001 — Human catalase rejected as a docking target for antioxidant activity

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `config.yaml` receptor panel; `DECISIONS.md` check in `scripts/check_docs.py`

**Decision.** Human catalase is **not** used as a docking target to rationalise
antioxidant activity, despite its frequent appearance in the natural-product
docking literature.

**Why this is scientifically indefensible.**

1. **Wrong direction of effect.** Catalase is itself an antioxidant enzyme: it
   decomposes H₂O₂ to water and oxygen. A small molecule that *binds* catalase
   is, if anything, an inhibitor, and inhibiting catalase would **reduce**
   antioxidant capacity. A favourable docking score against catalase is
   therefore evidence for the opposite of the claimed phenotype. The common
   framing — "compound X docks well into catalase, therefore X is an
   antioxidant" — inverts the pharmacology.

2. **The assay does not measure enzyme binding.** DPPH, ABTS and FRAP assays
   measure a *chemical* property: the compound's ability to donate a hydrogen
   atom or an electron to a stable radical, in a cuvette, with no protein
   present. Radical-scavenging capacity is governed by bond-dissociation
   enthalpy and ionisation potential, not by shape complementarity to a protein
   pocket. Docking cannot model the quantity the assay reports, so no docking
   score against any protein can explain a DPPH result.

3. **The active site is inaccessible to typical ligands.** Catalase's haem is
   buried at the end of a long, narrow channel sized for H₂O₂. Most
   GC-MS-derived phytochemicals are far too large to reach it, so a reported
   pose is usually at a surface site of no catalytic relevance — a result that
   a reviewer who knows the structure will identify immediately.

4. **No experimental anchor.** The study has no catalase inhibition or
   activation data, so there is nothing to validate the docking against and no
   way to detect that the model is wrong.

**Alternatives considered and rejected.**

| Alternative | Why rejected |
|---|---|
| Keep catalase, frame it as "possible interaction" | Hedged language does not fix an inverted mechanism; the figure still implies antioxidant action. |
| Superoxide dismutase / glutathione peroxidase | Same inversion: these are antioxidant enzymes, so binding is not a pro-antioxidant mechanism. |
| Dock against DPPH radical itself | DPPH is not a protein; the relevant quantity is a reaction thermodynamic, not a binding pose. Addressed properly by DFT bond-dissociation enthalpy, which is out of scope here. |
| Use a ROS-*generating* enzyme (e.g. xanthine oxidase, NADPH oxidase) | **Accepted in principle** — inhibiting a ROS source is a coherent antioxidant mechanism and the direction of effect is right. Requires an enzyme-inhibition assay to anchor it. See D-002 and LIMITATIONS.md. |

**Consequence.** If antioxidant activity is to be rationalised structurally at
all, it must be via a ROS-generating target with a matching enzyme-inhibition
assay. Absent that assay, the honest treatment of a DPPH result is to report it
as a chemical property and **not** to attach a docking figure to it.

---

## D-002 — Docking targets restricted to the three assayed activities

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `Config._activities_are_assayed`; the `Activity` literal in `src/npcadd/config.py` admits only the assayed phenotypes

**Decision.** The docking panel covers only the activities actually measured in
this work — **antioxidant, thrombolytic, cytotoxic** (plus
**hepatoprotective** for the second manuscript). No broader panel of
"interesting" targets is screened.

**Reasoning.** Every docking result that has no corresponding assay is an
unfalsifiable claim. Adding an antidiabetic or antimicrobial target because a
structure is available produces a table row that no experiment in the
manuscript can support or refute, and it invites the reader to infer a
therapeutic claim the data cannot carry. It also inflates the multiple-comparison
burden: screening 102 compounds against 10 targets gives 1020 scores, and the
best of those is a maximum of a large sample, not a discovery.

Restricting the panel to assayed activities means each docking result is a
structural *hypothesis about a measured effect*, which is a claim the manuscript
can actually defend.

**Alternatives rejected.**

| Alternative | Why rejected |
|---|---|
| Broad target panel for "mechanistic insight" | Produces claims no assay can support; maximum-of-many-scores is not a finding. |
| Broad panel, report everything, let the reader judge | A reader cannot distinguish a pre-specified hypothesis from a post-hoc maximum; that is the author's job. |
| Broad panel with multiple-testing correction | Correction does not supply the missing experiment; the claim remains unanchored. |

---

## D-003 — Raw docking-score comparison across different-sized ligands is invalid

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `rank_compounds` raises on any attempt to sort by a raw-score column (`FORBIDDEN_SORT_KEYS` in `src/npcadd/stages/rank.py`)

**Decision.** Compounds are **never** ranked by raw docking affinity. Comparing
raw docking scores across ligands of different size is invalid without
**efficiency normalisation**, so ranking is on ligand efficiency (LE) and a
multi-criteria score, with LLE, LELP and fit quality reported alongside.

**Reasoning.** AutoDock Vina's scoring function is approximately **extensive in
molecular size**: it sums pairwise atom-type contact terms, so adding heavy
atoms generally improves the score whether or not the added atoms contribute
anything specific. A GC-MS component list spans C6 phenolics to C30
triterpenoids and sterols — roughly 6 to 50 heavy atoms. Ranking that set by raw
score therefore ranks it substantially **by molecular weight**, and the
resulting "top hits" are largely the largest molecules in the chromatogram.

Measured on this repository's own sample run, at identical settings:

| Compound | Heavy atoms | Vina score (kcal/mol) | LE (kcal/mol/HA) |
|---|---|---|---|
| Loliolide | 14 | −7.58 | **0.542** |
| Spathulenol | 16 | −5.15 | 0.322 |
| Imatinib (reference) | 37 | −13.28 | 0.359 |

Imatinib wins decisively on raw score — and it is a genuine nanomolar inhibitor
of this target, so in that case the score is earned. But loliolide, a molecule
less than half its size, is the **more efficient** binder per heavy atom. Rank
by raw score and loliolide disappears; rank by efficiency and it moves from the bottom of the raw-score order to second of five. The
ordering is not a detail, it is the result.

**Metrics used, and why each.**

- `LE = −affinity / heavy_atoms` — removes the first-order size dependence.
- `LLE = pKd − cLogP` — penalises affinity bought with lipophilicity, which
  matters acutely for terpenoids, where a greasy C15–C30 skeleton can score well
  on hydrophobic contact alone.
- `LELP = cLogP / LE` — lipophilicity per unit efficiency.
- `FQ` — LE divided by the maximum LE achievable at that heavy-atom count
  (Reynolds et al., *J. Med. Chem.* 2008, 51, 2432). LE itself still declines
  with size; FQ corrects for that known decline and is the fairest single number
  for comparing a size-diverse set.

**Note on the pKd column.** `pkd_estimate_from_score` is a *unit conversion* of
the Vina score onto a log-affinity scale so that LLE is comparable with
published values. Vina's score is an empirical function, not a free energy, and
this column must never be reported as a predicted binding constant.

**Alternatives rejected.**

| Alternative | Why rejected |
|---|---|
| Report raw scores only; note the size caveat in the text | The table is what gets read, cited and tabulated into reviews. A caveat in the Methods does not travel with it. |
| Normalise by MW instead of heavy-atom count | Heavy-atom count is the established LE denominator and is insensitive to halogen mass. |
| Rescore with MM-GBSA | Better in principle, but it needs force-field parameterisation per ligand plus solvation sampling; it is not available offline here and would introduce a second, unvalidated model. Noted in LIMITATIONS.md as future work. |

---

## D-004 — Docking an inhibitor-bound structure while claiming activation is a directionality problem

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `ReceptorConfig.phenotype_direction` and `structure_is_inhibitor_bound`; `ReceptorConfig.directionality_conflict`; the conflict is written into receptor warnings, the workbook README, the SI caveats and every figure caption

**Decision.** Each receptor must **declare** whether the desired phenotype is
inhibition or activation, and whether the deposited structure is an
inhibitor-bound complex. Where those conflict, the pipeline refuses to let the
conflict stay implicit: it is stamped on the outputs.

**Reasoning.** Protein conformation is state-dependent. A kinase crystallised
with a type-II inhibitor is in a DFG-out, inactive conformation; a receptor
crystallised with an antagonist is in its inactive state. Docking into that
conformation and scoring well means *this ligand is compatible with the
inhibitor-bound state*. It does **not** mean the ligand activates the protein —
if anything it is weak evidence for the opposite, since the pocket being
occupied is the one an inhibitor occupies.

This matters concretely for the thrombolytic activity in this work.
Thrombolysis is the *dissolution* of a formed clot, mediated by plasmin acting
on fibrin. So:

- Docking into **plasmin's** active site and scoring well indicates plasmin
  **inhibition**, which is *anti*-fibrinolytic — the opposite of a thrombolytic
  effect.
- Docking into **tPA** or **plasminogen** and claiming the compound activates
  them is a directionality claim that a docking score cannot support: an
  activator must stabilise an active conformation or an allosteric site, and a
  score against the deposited (usually inhibited or zymogen) form says nothing
  about that.
- A coherent inhibition-direction target for enhancing fibrinolysis is
  **PAI-1** (SERPINE1): inhibiting the inhibitor of plasminogen activation
  promotes fibrinolysis. The direction of effect is then right.

**Rule adopted.** A favourable score against an inhibitor-bound structure may be
reported as *evidence of binding to that conformation*, never as evidence of
activation. Where the phenotype is activation, the manuscript must either use a
structure in the relevant active or allosteric state, or state plainly that the
structural rationale is for binding only and the activation mechanism is
untested.

**Alternatives rejected.**

| Alternative | Why rejected |
|---|---|
| Dock anyway and describe the result as "modulation" | Vague wording does not resolve a directionality error; readers read "modulation" as "it works". |
| Use an apo structure to stay neutral | Apo pockets are frequently collapsed or non-native; this trades a directionality problem for a sampling one without acknowledging either. |
| Use an agonist-bound or active-state structure | **Accepted where one exists.** Requires an explicit statement that the conformation was chosen to match the claimed direction. |

---

## D-005 — GUI tools excluded from the critical path

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** no GUI tool appears in any `src/npcadd` code path, in `Dockerfile`, or in `Makefile`; `make reproduce` runs headless in a container

**Decision.** **PyRx, Discovery Studio Visualizer and Swiss-PdbViewer are
excluded from the critical path**, along with every other interactive tool. They
may be used for *inspecting* or *illustrating* a result, never for producing one.

**Reasoning.** A GUI step is not reproducible, for reasons that are structural
rather than a matter of care:

1. **No executable record.** A sequence of clicks cannot be committed, diffed,
   reviewed or re-run. There is no artefact a reviewer could inspect to see
   what was done.
2. **Hidden defaults.** These tools apply undisclosed defaults — protonation
   rules, charge models, which waters are kept, how the grid box is placed.
   Those choices change the numbers and are not recorded anywhere in the output.
3. **Version drift is invisible.** A GUI result carries no record of the tool
   version, so a number cannot be attributed to a specific implementation.
4. **Hand-placed grid boxes are not reproducible.** A box centred by dragging is
   a different box on every attempt. npcadd derives the box from the co-crystal
   ligand's coordinates instead (`GridBox(mode="cocrystal")`), so it is
   recomputed identically on every run and no coordinate is hand-typed.
5. **It cannot run in CI or in a container**, so `make reproduce` would be
   impossible — and `make reproduce` is the whole point of this repository.

Every function those tools would serve has a scripted equivalent here: Meeko for
PDBQT preparation, PDB2PQR/PROPKA for pH-dependent protonation, RDKit for
ligand handling, derived grid boxes for the search space, and ProLIF for
interaction analysis.

**Alternatives rejected.**

| Alternative | Why rejected |
|---|---|
| Use PyRx for convenience, document the settings | Documentation of a GUI run is a claim about what was clicked, not a record; it cannot be verified or re-executed. |
| GUI for preparation, scripts for docking | Preparation is exactly where the hidden defaults bite — protonation, charges, retained waters all change the scores. |
| GUI only for figures | **Accepted.** Visual inspection and illustration are fine, provided no number in the manuscript originates there. |

---

## D-006 — Protonation uses a pKa-aware method, not generic rules

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `src/npcadd/chem/protonation.py`; `get_protonator` raises on an unknown method rather than falling back

**Decision.** Ligand protonation at the configured pH uses **dimorphite-dl**, a
pKa-aware method, behind a pluggable `Protonator` interface. Open Babel's `-p`
flag is **not** used.

**Reasoning.** Open Babel's `-p` applies a small set of generic SMARTS rules
with hard-coded, molecule-independent behaviour. It does not estimate the pKa of
the actual molecule, is insensitive to electronic environment, and its output is
not traceable to any pKa value. Formal charge strongly affects Vina's
electrostatic and hydrogen-bonding terms, so a generic rule set is not an
acceptable basis for a published affinity.

**Honest statement of what dimorphite-dl is.** It matches ionisable
substructures against curated *empirical pKa ranges*. It is deterministic,
offline, and far better than generic rules, but it is a substructure-lookup
method, not a per-molecule pKa calculation: it does not model long-range
electronic effects, intramolecular hydrogen bonding, or the binding-site
microenvironment. It gives the dominant macrostate in bulk water, not
site-specific protonation. Each backend must publish its own `limitation()`
string, which is propagated verbatim into the run report — the limitation
travels with the numbers.

**Borderline states are detected, not assumed.** `state_is_uncertain` probes the
dominant state at pH ± 1 and flags any compound whose formal charge differs
between them, meaning some group has a pKa inside that window and the state at
the working pH is not well determined. Vanillin is the worked example in the
sample data: its phenol pKa is near 7.4, so its deprotonation at pH 7.4 is a
modelling choice rather than a fact, and it is flagged accordingly. Both states
should be docked for such compounds.

**Alternatives rejected.**

| Alternative | Why rejected |
|---|---|
| Open Babel `-p 7.4` | Generic rules, no pKa estimate, untraceable output. Explicitly excluded by the project brief. |
| Dock the neutral form throughout | Available as `protonation_method: none`, with a loud limitation string. Wrong for every carboxylic acid and aliphatic amine at pH 7.4. |
| Enumerate all states and dock each | Supported via `max_protonation_variants`, but multiplies docking cost and needs a defensible rule for combining per-state scores. Default is the dominant state with borderline cases flagged. |
| A commercial pKa predictor | Not reproducible without a licence; would break the offline guarantee. |

---

## D-007 — Redocking RMSD is computed in place, never after superposition

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `in_place_rmsd` in `src/npcadd/docking/poses.py` uses `CalcRMS`; the module docstring forbids `GetBestRMS` for this purpose

**Decision.** The redocking control measures **in-place, symmetry-corrected
RMSD** (RDKit `CalcRMS`) between the docked pose and the crystallographic pose.
`GetBestRMS` is **not** used.

**Reasoning.** `GetBestRMS` *aligns* the probe onto the reference before
measuring, which answers "is the internal conformation similar?" — not "did
docking reproduce the crystallographic placement?". A pose that is in the right
pocket but rotated end-for-end has a similar conformation and will score well
under the aligned metric.

Measured on this repository's bundled ABL1/imatinib system, for a pose produced
at exhaustiveness 8:

| Metric | Value | Verdict against a 2.0 Å gate |
|---|---|---|
| In-place RMSD (`CalcRMS`) | **12.29 Å** | FAIL — correctly |
| Aligned RMSD (`GetBestRMS`) | **2.09 Å** | within rounding distance of PASSING |

The aligned metric would have come close to certifying a pose that is simply
wrong. Centroid offset is reported alongside RMSD because the pair distinguishes
"wrong pocket" (large offset) from "right pocket, wrong orientation" (small
offset, large RMSD) — a distinction a single number hides, and one that changes
what the fix is.

---

## D-008 — Validation and screening must share docking parameters

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `run_validation` and `run_docking` both read `cfg.docking`; there is no separate validation-parameter block in the config schema

**Decision.** The redocking control, the decoy enrichment, the null distribution
and the screen all use **one** `docking:` configuration block. It is not
possible to validate at one exhaustiveness and screen at another.

**Reasoning.** A validation result describes a *protocol*. Validate at
exhaustiveness 32 and screen at 8 and the validation describes a protocol that
was never used on the real compounds — the reported RMSD is then evidence about
a different experiment.

This is not hypothetical. On the bundled system, with everything else identical:

| Exhaustiveness | Top-pose in-place RMSD | Top-pose affinity | Gate at 2.0 Å |
|---|---|---|---|
| 8 | 12.29 Å | −10.96 kcal/mol | **FAIL** |
| 32 | **0.24 Å** | −13.28 kcal/mol | **PASS** |
| 64 | 0.24 Å | −13.28 kcal/mol | PASS |

At exhaustiveness 8 the search finds the correct pocket (centroid offset 1.8 Å)
but not the correct orientation. Note also that the failing run's best score was
*less* favourable, so the failure is not detectable from the score alone — only
the pose comparison reveals it.

This is why `config.sample.yaml` pins exhaustiveness to 32 with a comment
recording the measurement, and why `config.smoke.yaml` reduces the ligand count
and null-set size for CI speed but **does not** reduce exhaustiveness.

---

## D-009 — Decoys are generated offline by combinatorial enumeration

**Date:** 2026-11-01 · **Status:** Decided, with a documented limitation · **Enforced:** `src/npcadd/docking/decoys.py`, `src/npcadd/resources/building_blocks.py`

**Decision.** Property-matched decoys and the random-ligand null set are drawn
from a pool enumerated combinatorially from bundled medicinal-chemistry
fragments, with no randomness in the pool's construction.

**Reasoning.** DUD-E-style decoys are selected from a large external database
(ZINC, millions of compounds). That conflicts directly with the requirement that
the pipeline run offline and deterministically, and it makes the decoy set
depend on a database snapshot that is itself not archived with the paper.

Two ordering decisions were necessary to make a size-capped pool actually span
the library, and both were found by measurement rather than reasoning:

1. **Round-robin across cores**, not core-major. Core-major enumeration
   truncated at 2500 molecules produced a pool whose maximum MW was 290 Da,
   which could not property-match a 494 Da active **at all** (0 decoys selected
   after 4 tolerance relaxations).
2. **Bit-reversal (van der Corput) ordering of substituent pairs.** Iterating
   substituents in declaration order holds the first substituent fixed through
   its whole inner loop, so large×large products are never reached before the
   cap and the pool's upper mass range stays empty.

Together these took the pool from 106–290 Da to **106–429 Da**, after which all
test actives obtained full decoy sets. Any prefix of the ordering is now a
near-uniform sample of the full combination space, so the pool is sized to what
a run needs rather than to a fixed ceiling.

**Limitations, recorded honestly.** The pool's chemotype coverage is bounded by
the bundled fragments, so it is less diverse than a database-derived pool, and
enrichment computed against it is an optimistic-to-middling estimate rather than
a hard benchmark. The molecules are synthetic combinatorial products, not
catalogue compounds, so they carry no information about purchasability or about
having been assayed and found inactive. Decoys are **presumed** inactive because
they are property-matched and topologically dissimilar to the actives
(Tanimoto < 0.35); they are not confirmed inactives. Tolerance relaxations are
counted per active and reported, so a decoy set that needed widening is visible
as the weaker control it is.

---

## D-010 — HTML-form-only ADMET tools are handed off manually, never scraped

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `write_external_tool_batches` and `read_external_tool_csv` in `src/npcadd/stages/admet.py`; no HTTP client targets these hosts anywhere in the codebase

**Decision.** SwissADME, pkCSM and ProTox-III are **not** scraped. The pipeline
generates a paste-ready input batch and a strict paste-back CSV schema,
validates the pasted values on read, and records the tool name, version and
retrieval date in provenance.

**Reasoning.** Scraping is fragile, generally against these services' terms,
and — decisively for a manuscript — **unversioned**. A scraped number cannot be
attributed to a specific model version on a specific date, and an unattributable
number does not belong in a paper. The manual handoff costs an afternoon and
buys attributable numbers.

Validation on read is strict: a cell that cannot be parsed is an error, not a
silently coerced default. A quietly defaulted hepatotoxicity prediction is worse
than a missing one.

---

## D-011 — Structural alerts are reported as flags, not verdicts

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** per-filter names are reported in the output tables; `rank.py` adds an explanatory flag rather than excluding the compound

**Decision.** A PAINS or Brenk match is reported as a **flag with the matched
filter names**, never collapsed to a pass/fail, and never used to drop a
compound from the results.

**Reasoning.** PAINS filters identify *assay-interference liability*, not
inactivity or worthlessness. Several privileged natural-product scaffolds match
them and are nonetheless genuine, well-characterised bioactives — catechols,
quinones, curcuminoids, many flavonoids. In a phytochemistry study, silently
filtering PAINS matches would remove a large part of the phenolic fraction,
which is frequently the fraction the assay implicates. Reporting which filter
matched lets a reader judge whether the liability is relevant to the specific
assay used.

---

## D-012 — Every rule set is implemented from the primary literature

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `src/npcadd/chem/rules.py`, one function per rule set with its citation in the docstring; each returns per-criterion verdicts

**Decision.** Lipinski, Veber, Egan, Ghose, Muegge and Pfizer 3/75 are
implemented in code from their original papers. No drug-likeness verdict is
taken from a web tool.

**Reasoning.** Every verdict in the output tables must be traceable to an
inequality a reader can inspect, and reproducible without network access. Web
tools also differ in their implementations — notably in how many Lipinski
violations are tolerated and whether Ghose's bounds are applied two-sided — so
"Lipinski: pass" from an unnamed tool is not a well-defined statement.

Each rule returns the individual criteria, not just a boolean, because the
question a reviewer asks is *which* criterion failed. Ghose's two-sided bounds
matter specifically for natural products: small terpenoids routinely fail its
*lower* MW and atom-count bounds, and a one-sided check would hide that.

---

## D-013 — Search boxes are derived from the co-crystal ligand, not transcribed

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `GridBox` defaults to `mode: cocrystal`; `mode: explicit` requires both centre and size and demands provenance in `notes`

**Decision.** The docking box is computed from the co-crystal ligand's
coordinates — centroid for the centre, ligand extent plus padding for the size,
floored at a minimum — and the derivation string is recorded in the output.

**Reasoning.** A transcribed box centre is a magic number: it cannot be checked
against the structure it describes, it drifts if the structure is revised or a
different chain is used, and typos in it are invisible. Deriving it removes the
hand-typed coordinate entirely and makes the box recomputed-identical on every
run. The box is also validated: an edge over 40 Å is rejected because a box that
large makes the search effectively blind and the scores non-comparable, and an
edge under 10 Å is rejected because it clips most ligands.

`mode: explicit` remains available for receptors with no co-crystal ligand, but
then the provenance of the numbers must be written in `notes`.

---

## D-014 — Receptor metadata is reported as "unavailable" rather than guessed

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `StructureMetadata` fields are all optional and render as `unavailable`; `prepare_receptor` warns when resolution is unknown or worse than 2.5 Å

**Decision.** Resolution, R-work, R-free, experimental method, deposition date
and UniProt accession are read from the structure file and the RCSB API where
available, and reported as `unavailable` where they genuinely are not. They are
never inferred or filled from memory.

**Reasoning.** Resolution and R-free are exactly the fields a reviewer uses to
judge whether a structure supports docking at all. A plausible-looking guess is
worse than a blank, because a blank prompts the check and a guess prevents it.
The bundled sample receptor is a worked example: it is a prepared derivative
whose header has been stripped, so the pipeline reports its resolution as
`unavailable` and attaches a warning that it must be re-prepared from the
deposited entry before publication.

---

## D-015 — Atom ordering is normalised to true sequence order before protonation

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `normalise_atom_order` in `src/npcadd/stages/receptor.py`

**Decision.** Receptor atoms are regrouped so each residue's atoms are
contiguous, with residues emitted in `(chain, residue number, insertion code)`
order, before any protonation or PDBQT export.

**Reasoning.** This began as a workaround and turned out to matter chemically.
The bundled sample structure has one atom of SER A 438 misplaced near the top of
the file, which makes polymer parsers reject the whole file with "interrupted
residues". Grouping by *first appearance* fixes the parse but creates a worse
problem: SER 438 becomes the first residue in the file, PDB2PQR identifies it as
the chain's N-terminus, and builds a **charged NH₃⁺ group on a residue in the
middle of the kinase domain** — inventing a formal charge that is not in the
protein and producing a molecule RDKit rejects for impossible hydrogen valence.

Ordering by residue number makes the real termini the termini. No coordinate is
altered; the transformation is a pure permutation plus serial renumbering.

---

## D-016 — Interaction fingerprints read the receptor through RDKit, not MDAnalysis

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `load_protein` in `src/npcadd/docking/interactions.py`

**Decision.** ProLIF's protein is built from `Chem.MolFromPDBFile(...,
proximityBonding=True)` rather than from an MDAnalysis `AtomGroup`.

**Reasoning.** MDAnalysis infers bonds purely by interatomic distance. On the
bundled receptor, PROPKA assigns His361 as doubly protonated (HIP) at pH 7.4,
which places its HD1 **1.08 Å** from the backbone amide hydrogen of Asp363. The
distance guesser bonds those two hydrogens to each other, and RDKit then rejects
the molecule for impossible hydrogen valence, so fingerprinting fails outright.
RDKit's reader applies a comparable distance criterion but refuses
hydrogen–hydrogen bonds, and reads the identical file cleanly.
`proximityBonding=False` is not an option either: with no CONECT records every
residue comes out fragmented.

**Validation of this choice.** With this reader, the fingerprint of the
crystallographic imatinib pose reproduces the textbook ABL1 contacts: hydrogen
bonds to **Thr315** (the gatekeeper whose T315I mutation causes clinical
imatinib resistance), **Met318**, **Glu286** and the DFG **Asp381**, plus
hydrophobic contacts to Leu248, Tyr253, Lys271, Met290, Ile313, Phe317, Leu370
and Phe382. Recovering the known interaction pattern is independent evidence
that the receptor preparation chain is chemically correct.

**Interpretation limit.** A fingerprint is a geometric assertion that a
distance-and-angle criterion is satisfied. It is not an energetic contribution,
and at resolutions worse than ~2.5 Å side-chain positions do not support
residue-level interpretation. That caveat is attached to the receptor and
printed in the figure caption.

---

## D-017 — Output tables carry run-relative paths, not absolute ones

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `LigandRecord.pdbqt_path` is documented as run-relative; `tests/test_determinism.py` asserts byte-identical manifests across run directories

**Decision.** File paths written into output tables are relative to the run
directory.

**Reasoning.** Found by the golden determinism test, which failed on
`ligand_manifest.csv` while every PDBQT matched. Absolute paths embed the run
directory and the machine's filesystem layout into the table, so two otherwise
identical runs produce different bytes — and the reproducibility claim is
precisely that they do not. Relative paths make the manifest byte-identical
across run directories and machines, and the absolute path is recoverable by
joining with the run directory.

---

## D-018 — Weight sensitivity is reported, because the shortlist depends on it

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `sensitivity_analysis` in `src/npcadd/stages/rank.py`; a low stability becomes a caveat stamped on every output

**Decision.** The multi-criteria weights are resampled from a Dirichlet
distribution centred on the configured values, and the stability of the top-5
shortlist is reported as the mean Jaccard overlap with the baseline top-5.

**Reasoning.** Any weighted score is a choice, and a reader is entitled to know
whether the shortlist is a property of the data or of that choice. Above roughly
0.8 overlap the shortlist is robust; below roughly 0.5 it is substantially an
artefact of the weighting, and the pipeline says so in the caveats rather than
presenting one weighting as the answer.

---

## D-019 — The pipeline refuses to screen without a passing validation gate

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `npcadd dock` checks stage state and exits 3; `run_validation` raises `ValidationFailure` when `halt_on_failure` is true

**Decision.** `npcadd dock` will not run unless validation has passed for the
current configuration. The override, `--skip-validation-check`, is recorded in
provenance and stamped on every output.

**Reasoning.** A screening result produced without a redocking gate is not
interpretable: there is no evidence the protocol reproduces a pose it was given,
so there is no basis for trusting poses it was not. Making this a hard stop
rather than a warning means the failure mode is a loud error at run time instead
of a figure in a manuscript.

The failure message lists the things that actually fix a failing gate, ordered
by how often they are the cause: raise exhaustiveness; check the box (a centroid
offset above ~4 Å means the box or site assignment is wrong, not the sampling);
check receptor protonation and any stripped cofactor; check that the co-crystal
ligand's protonation state matches the deposit.

---

## D-020 — Enrichment is reported as "not performed" when no actives are supplied

**Date:** 2026-11-01 · **Status:** Decided · **Enforced:** `run_validation` sets `enrichment_skip_reason`; `VALIDATION.md` prints it prominently

**Decision.** Without a user-supplied set of experimentally confirmed actives,
ROC-AUC, BEDROC and enrichment factors are reported as **not defined and not
computed**. No active set is fabricated.

**Reasoning.** These metrics require known actives. Inventing them — by treating
the top-scoring screening compounds as actives, say — would make the enrichment
circular and the number meaningless. The honest output is an explicit statement
that the control was not performed, together with instructions for supplying
actives (for example from ChEMBL or BindingDB) to obtain it. The redocking gate
and the null distribution still run, since neither needs known actives.
