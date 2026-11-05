# Mission C — Opportunity scoring

Full generated output: `../engine/results/ranking.md`, with machine-readable data in `ranking.json`. To regenerate:

```bash
python3 godmode/engine/score.py
python3 godmode/engine/score.py --check   # CI fails if results are stale
```

## Engine

**Score.** Each opportunity is scored 1–10 on the ten dimensions in the brief, using your weights (15/15/10/10/10/10/10/10/5/5). The score anchors are written at the top of `opportunities.toml`, so every number can be argued with.

**Penalties.** The eight penalties from the brief come from a catalogue with a maximum deduction for each:

| Penalty | Max | Penalty | Max |
|---|---|---|---|
| Heavy regulation | 0.6 | Hard acquisition | 0.5 |
| Unstable dependency | 0.3 | Expensive infrastructure | 0.3 |
| High support | 0.4 | Weak differentiation | 0.6 |
| Unproven demand | 0.4 | Security exposure | 0.5 |

Each opportunity takes a fraction of each penalty's maximum, so penalties are graded rather than all-or-nothing.

**Final score** = weighted score − penalties.

**Sensitivity.** We ran 4,000 Monte Carlo trials, seeded so they are reproducible. Each trial perturbs everything at once:

- every weight (Dirichlet, concentration 60)
- every score (±1)
- every penalty (±0.25)

The output is how often each opportunity ranks first or in the top five. This separates rankings that are robust from rankings that are noise.

**Validation.** The engine refuses bad data: unknown tags or penalties, scores outside 1–10, weights that don't sum to 1, and citations to evidence that isn't registered.

## Top 10

| # | ID | Opportunity | Weighted | Penalties | **Final** | P(#1) | P(top-5) |
|---|---|---|---|---|---|---|---|
| 1 | O01 | LabelLedger: reference-label change intelligence and drift detection | 7.35 | −0.40 | **6.95** | 94% | 100% |
| 2 | O02 | RIM-lite registration and renewal tracker for emerging-market exporters | 6.80 | −0.56 | **6.24** | 2% | 76% |
| 3 | O05 | Deficiency-letter (query) response copilot | 6.85 | −0.65 | **6.20** | 2% | 73% |
| 4 | O03 | Emerging-market registration requirements intelligence | 6.65 | −0.45 | **6.20** | 1% | 73% |
| 5 | O04 | AI dossier gap-analysis and country adaptation (CTD/ACTD) | 6.80 | −0.77 | **6.03** | 1% | 48% |
| 6 | O50 | AI-native regulatory-affairs productized service | 6.65 | −0.65 | **6.00** | 0% | 43% |
| 7 | O25 | AI tutor for Gulf licensing exams (South Asian pharmacists) | 6.55 | −0.70 | **5.85** | 0% | 25% |
| 8 | O26 | Cosmetics and supplements compliance checker (e-commerce app) | 6.50 | −0.66 | **5.84** | 0% | 21% |
| 9 | O06 | Pharmacovigilance literature screening for small MAHs | 6.75 | −0.95 | **5.80** | 0% | 16% |
| 10 | O07 | PSUR/PBRER drafting assistant for generics | 6.45 | −0.80 | **5.65** | 0% | 6% |

## Selection

* **Top five, red-teamed in `04_…`:** O01, O02, O05, O03, O04.
* **Finalists:** O01, O02, O05.
* **Recommended first venture: O01 (LabelLedger).**

## Reading the results honestly

* **The winner is robust; the runners-up are not.** O01 leads by 0.71 points and wins 94% of perturbed trials. O02, O05, O03, O04 and O50 swap places under perturbation, so choose among them on strategic fit, not on decimal points. We do exactly that: O02 becomes product #2 because it shares O01's customers and uses the founder's Bangladesh network.
* **The highest raw scores are not always the best bets.** O04 has the highest problem severity and willingness to pay among the runners-up (8/8), but the largest penalties: confidential dossiers, accuracy liability, hard sales. Penalties, not hype, decide its rank. That is the "do not choose glamour over viability" rule working.
* **Possible bias, disclosed.** The same analyst scored the opportunities and then built O01. To limit sunk-cost bias:
  - the scores were committed **before** the MVP code (git history: commit `9145802` precedes `603fb3d`);
  - the kill criteria are written down in advance (`04_…`, `10_…`);
  - O02 stays ready as the pivot.
* **Glamorous ideas that ranked low, and why:**
  - EU HTA evidence synthesis (O22, rank 35): enterprise sales the founder cannot run solo yet.
  - US AI medical billing (O43, rank 47): HIPAA plus crowded VC-funded competitors.
  - Bangla AI agents (O33): no moat.
