# Evidence review: is npcadd's methodology supported by the literature?

**Date:** 2026-11-05 · **Author:** automated literature + database review, for human verification

This document does for the pipeline's *methodological choices* what `VALIDATION.md`
does for a run's *numbers*: it checks them against external evidence instead of
asserting them. Every claim below is tied to a real, retrievable source — a
peer-reviewed paper located through Consensus (semantic search over Semantic
Scholar / OpenAlex / PubMed) or a record in ChEMBL v34. Nothing here is a
citation from memory.

## How this review was produced, and its limits

- **Literature** was retrieved with the Consensus search tool and **bioactivity
  data** from the ChEMBL MCP interface. Both were reachable from the build
  environment.
- **What was *not* reachable:** the PubMed E-utilities backend returned a
  server-side error throughout, and direct web access to NCBI, OpenAlex,
  Espacenet and patent databases is blocked by this environment's egress policy
  (HTTP 403 at the proxy). **No patent search was performed.** Novelty
  statements below are therefore literature-only and are phrased as *potentially
  novel*, per the standard this repository already applies to its own claims.
- This is a **reporting-and-reasoning** document. It introduces no experimental
  result and no new computation beyond the database lookups quoted.

**Evidence-status tags** (used throughout):
`[LIT]` supported by cited literature · `[DB]` grounded in a named database
record · `[CODE]` verifiable in this repository · `[REASON]` reasoned argument,
not independently verified.

---

## Part 1 — The core design decisions, checked against the literature

### 1.1 The mandatory redocking gate is not optional rigor; its absence is the norm

`npcadd` refuses to screen until a co-crystal ligand is redocked below an RMSD
threshold (`DECISIONS.md` D-019). A 2026 methodological audit of **1,127**
natural-product docking publications found redocking reported in only **7.5%**
of a full-text sample, and a *qualifying* redocking RMSD below 2 Å in only
**6.2%** `[LIT]`([1]). Positive controls appeared in 87.5% but the pose-level
control — the one that actually tests whether the protocol can reproduce a known
answer — was essentially absent. The pipeline's hard gate places it in the top
few per cent of published practice, and the audit's numbers are the quantitative
case for why the gate is a *hard stop* rather than a warning.

### 1.2 Ranking on ligand efficiency, not raw score, is the mainstream correction

The pipeline forbids ranking by raw Vina score (`DECISIONS.md` D-003), because
the score is approximately extensive in molecular size. That docking scores are
**not** reliable binding affinities, and should drive *ranking and triage* rather
than potency claims, is the explicit conclusion of two highly-cited analyses
`[LIT]`([8], [6]) and the premise of the "ten quick tips" guidance for
reproducible docking `[LIT]`([3]). Ligand efficiency itself is independently
validated by ChEMBL, which curates LE/LLE for its activities: imatinib's
38 nM ABL1 IC50 record carries **LE = 0.27, LLE = 2.83** `[DB]`(ChEMBL
CHEMBL941 / CHEMBL1862). The pipeline's *docking-derived* LE for imatinib on the
same target is ≈0.36 `[CODE]` (−13.28 kcal/mol ÷ 37 heavy atoms). The two are
the same order but not equal: the docking value is the more favourable, exactly
as §1.1 of `LIMITATIONS.md` warns, which is why the pipeline uses LE to *order*
compounds and never to assign potency.

### 1.3 Validation and screening must share parameters

`DECISIONS.md` D-008 forces one `docking:` block to drive both validation and
screening, after the finding that the bundled system passes at exhaustiveness 32
(0.24 Å) and fails at 8 (12.3 Å) while the *failing* run scores *better*. The
general form of this — that the top-scoring pose is frequently not the
crystallographic one, acutely so in cross-docking — is established `[LIT]`([6]),
and the modern reframing of docking as "conditional modelling" whose reliability
depends on search-space design and deployment-matched validation is the thesis
of a 2026 review `[LIT]`([2]).

### 1.4 Applicability domain on ADMET predictions is an OECD-level requirement

Every QSPR value carries an applicability-domain verdict and out-of-domain
values are excluded from ranking (`LIMITATIONS.md` §3.1). Defining an
applicability domain is **OECD validation principle 3**, and the reliability of
a QSAR prediction is generally limited to chemicals similar to the training set
`[LIT]`([A1], [A6]). More pointedly for natural products: a 2026 topological
study of a 426,850-molecule lipophilicity model found mean absolute error
**2.73× higher** for "pharmacologically extreme" peripheral molecules than for
the drug-like core `[LIT]`([A10]) — a direct, quantitative justification for
flagging terpenoids and highly-oxygenated lactones as out-of-domain rather than
quoting their predictions. Recent work also cautions that AD verdicts are
themselves imperfect and should be paired with error analysis `[LIT]`([A3]); the
pipeline's descriptor-box domain is deliberately a coarse, auditable first line,
not a reliability guarantee.

### 1.5 Natural-product docking has recognised, specific pitfalls

The pipeline's whole posture — multi-target promiscuity, the gap between an
extract assay and a single-compound claim, the need for experimental anchoring —
matches the published catalogue of NP-docking pitfalls `[LIT]`([4]) and the
general challenges of scoring, flexibility and benchmarking `[LIT]`([9], [10]).

**Part 1 conclusion.** Every major methodological decision in `npcadd` is
corroborated by current peer-reviewed sources. The pipeline is a faithful
implementation of 2018–2026 best-practice guidance, and in the case of the
redocking gate it enforces what the literature shows ~94% of published studies
omit `[LIT]`([1]).

---

## Part 2 — The new placement check: prior art and what is potentially novel

The screened-pose **box-containment check** added in this work (`DECISIONS.md`
D-021, `docking/grid.py::assess_placement`) rests on a specific, recent finding:
across eleven targets, Vina's ranking metrics barely moved between a
site-directed and a blind box (mean ROC-AUC 0.69 → 0.62) while correct placement
collapsed from 96% to 48% of actives, the two were uncorrelated (r = −0.03), and
doubling exhaustiveness fixed only two of eight gross misplacements `[LIT]`([7]).
That paper explicitly proposes "a simple check to catch" placement failure. That
the best-scoring pose is not reliably the correct one is older and well cited
`[LIT]`([6], [8]).

**What is prior art:** the *diagnosis* (ranking hides placement error) and the
*idea* of a placement check `[LIT]`([7]); pose-to-reference geometry for the one
ligand that has a crystal pose (the redocking gate) `[CODE]`.

**What appears potentially novel `[REASON]`:** wiring a **reference-free**
per-screened-ligand placement verdict (atom containment + centroid wall-proximity
against the *actual search box*) into the pipeline's standard output, so that the
check Agboola et al. propose for a benchmark runs automatically on every
production screen and is stamped in `docking_results.csv`. The published check is
validated against known-site distance on benchmark sets; applying the
box-geometry proxy to unknown screened ligands and surfacing it as routine
provenance is the differentiated contribution. This is a **potentially novel
engineering contribution, not a new scientific finding**, and the prior-art
search was literature-only (no patents) — so the claim is bounded accordingly.

---

## Part 3 — The sample system and leads, as the databases actually have them

- **ABL1 is richly annotated.** The target is ChEMBL **CHEMBL1862** (UniProt
  **P00519**, the identifier in `config.sample.yaml`), with **2,808** curated
  IC50 records and many sub-100 nM actives at pChEMBL ≥ 7 `[DB]`. This matters
  operationally: `LIMITATIONS.md` §4.2 and `DECISIONS.md` D-020 note the
  enrichment control cannot run without known actives, and ChEMBL supplies them
  for this exact target. See the roadmap's first-project dossier.
- **The two leads are absent from curated bioactivity.** A ChEMBL search for
  **loliolide** and for **spathulenol** returns **zero** compound records `[DB]`.
  Their names and SMILES are real, but neither has curated target-activity data —
  concrete support for `LIMITATIONS.md` §2.4 and for recommendation 1 ("isolate
  and assay the leads"): there is no existing bioactivity record to lean on.
- **Imatinib carries a natural-product flag.** ChEMBL marks CHEMBL941
  `natural_product = 1` with an NP-likeness score of −1.8 `[DB]` — a reminder
  that the "natural product vs synthetic" line the rule-set caveats (§3.2) worry
  about is genuinely blurry even for the reference inhibitor.

---

## Part 4 — Gaps the literature flags that npcadd does *not* yet close

These are evidence-backed entry points for the roadmap, not deficiencies hidden
from the user (each is already acknowledged in `LIMITATIONS.md`):

1. **No cross-docking or out-of-distribution test.** Self-docking is "necessary
   but insufficient"; cross-docking, apo/predicted structures and OOD tests are
   called essential complements `[LIT]`([2]). npcadd does only self-(re)docking.
2. **No physics-based rescoring.** MM-PB(GB)SA appeared in ~12.5% of audited
   studies `[LIT]`([1]); npcadd offers none (noted in `LIMITATIONS.md` §6.6).
3. **The enrichment control ships dormant.** It is defined but unpopulated;
   Part 3 shows real actives exist to populate it `[DB]`.
4. **AD verdicts are coarse.** A descriptor box is a first line; error-aware and
   density-based AD methods are more reliable `[LIT]`([A3], [A1]).

---

## References

All links are the exact Consensus record URLs; follow them for DOI and
full text.

1. Makhmutova et al. (2026). *Molecular Docking of Natural Products: Critical Appraisal of Current Methodology and Practical Guidelines.* Molecules. [link](https://consensus.app/papers/details/b3c381195fa75425b8a52fd66e7fff12/?utm_source=claude_desktop)
2. Kittelson et al. (2026). *Reproducibility, validation, and failure modes across classical and AI-driven molecular docking.* J. Comput.-Aided Mol. Des. [link](https://consensus.app/papers/details/add14303af7a5063b08bee0ef02af33a/?utm_source=claude_desktop)
3. Martis & Téletchéa (2025). *Ten quick tips to perform meaningful and reproducible molecular docking calculations.* PLOS Comput. Biol. [link](https://consensus.app/papers/details/8d8bf8f066015d7690ce5cd37260928b/?utm_source=claude_desktop)
4. Temml et al. (2021). *Molecular Docking for Natural Product Investigations: Pitfalls and Ways to Overcome Them.* [link](https://consensus.app/papers/details/65c45fa16b3356698fc982f922c4d01a/?utm_source=claude_desktop)
6. Ramírez & Caballero (2018). *Is It Reliable to Take the Molecular Docking Top Scoring Position as the Best Solution without Considering Available Structural Data?* Molecules. [link](https://consensus.app/papers/details/044afb21428b54d5a59d16b1ffae219e/?utm_source=claude_desktop)
7. Agboola et al. (2026). *Ranking metrics conceal pose errors in blind docking with AutoDock Vina, a cross-target benchmark.* Results in Engineering. [link](https://consensus.app/papers/details/16e03941ef9e59059772ed92f4ddd33d/?utm_source=claude_desktop)
8. Pantsar & Poso (2018). *Binding Affinity via Docking: Fact and Fiction.* Molecules. [link](https://consensus.app/papers/details/6bb0421cc9c257f58d180ea65e007a0a/?utm_source=claude_desktop)
9. Torres et al. (2019). *Key Topics in Molecular Docking for Drug Design.* Int. J. Mol. Sci. [link](https://consensus.app/papers/details/9e85ec9474ba521296d4dc8a0a160fcd/?utm_source=claude_desktop)
10. Huang (2018). *Comprehensive assessment of flexible-ligand docking algorithms.* Brief. Bioinform. [link](https://consensus.app/papers/details/49a116a403165b929a1c6cca2f9479d4/?utm_source=claude_desktop)

Applicability-domain sources:

- A1. Sahigara et al. (2012). *Comparison of Different Approaches to Define the Applicability Domain of QSAR Models.* Molecules. [link](https://consensus.app/papers/details/7ecb2d66867a5aacb5ac5102764d0c86/?utm_source=claude_desktop)
- A3. Mora et al. (2024). *Rethinking the applicability domain analysis in QSAR models.* J. Comput.-Aided Mol. Des. [link](https://consensus.app/papers/details/fce46692937b5dddb7365f57b0ce0b04/?utm_source=claude_desktop)
- A6. Roy et al. (2015). *On a simple approach for determining applicability domain of QSAR models.* Chemom. Intell. Lab. Syst. [link](https://consensus.app/papers/details/765b559a88df55bb937ce706ca4fb28f/?utm_source=claude_desktop)
- A10. Malikussaid et al. (2026). *Mapping the Applicability Domain of Lipophilicity Models Through Chemical Space Topology.* [link](https://consensus.app/papers/details/70f0bc81a0ba5ac1b854ef4e6af0d9c3/?utm_source=claude_desktop)

Database records (ChEMBL v34): CHEMBL941 (imatinib), CHEMBL1862 (ABL1 / UniProt P00519).

Primary ligand-efficiency literature (already cited in `stages/rank.py`): Hopkins
et al. 2004 *Drug Discov. Today* 9:430; Reynolds et al. 2008 *J. Med. Chem.*
51:2432; Leeson & Springthorpe 2007 *Nat. Rev. Drug Discov.* 6:881.
