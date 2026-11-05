# Strategic record (living document)

This file is the company's memory, in four parts:

- §1 Assumptions register
- §2 Decision log
- §3 Intelligence log
- §4 Review questions and metrics

It is append-only: corrections are new entries, not deletions. The same rule applies to `DECISIONS.md` in this repository.

**Status words:**

| Status | Meaning |
|---|---|
| **proposed** | Suggested, not yet acted on |
| **in progress** | Being worked on |
| **completed** | Done |
| **tested** | Checked by automated tests or a measurement we ran |
| **independently verified** | Confirmed by a customer, the market, or a third party |

## 1. Assumptions register

| ID | Assumption | Status | Current evidence | How we test it | Review by |
|---|---|---|---|---|---|
| A-01 | Generic labeling teams spend meaningful recurring time tracking reference-label changes | untested | FDA obligation [E01]; discrepancy study [E02] | 10 interviews (hours per update) | Day 30 |
| A-02 | They have missed or late updates that matter | untested | E02 (dated); our 2012-vs-2026 sample (E23, illustrative) | Interview stories; drift snapshots on real portfolios | Day 30 |
| A-03 | Small and mid-size ANDA holders and consultancies are under-served by current tools | untested | Enterprise, quote-only pricing [E04, E19] | Ask "what do you use and what does it cost" | Day 30 |
| A-04 | They will pay ≥ $199/mo (Pro) or ≥ $599/mo (Team) | untested | None | Paid pilots; Van Westendorp | Day 60 |
| A-05 | Drift snapshots from public data are accurate enough to impress, not embarrass | **tested on 4 labels** | Self-compare 100% / 0 findings; synthetic recall exact; real 2012 gaps match known label history | Founder review of 20 live snapshots; evaluation set | Day 7 / M3 |
| A-06 | DailyMed v2 endpoints behave as implemented | untested live | Implemented from documentation; mocked tests | `labelledger fetch` on the founder's machine | Day 1 |
| A-07 | Paddle will onboard a Bangladesh-based seller | untested | Docs list BD with `allow_sales` [E09] | Application | Day 21 |
| A-08 | Monthly churn ≤ 2% for paying teams | untested | None | Cohorts | Month 6 |
| A-09 | Reply-to-demo 5%, demo-to-paid 20% (base) | untested | None (assumption) | Outreach log | Day 45 |
| A-10 | AI summaries add value over deterministic summaries | untested | Verification pipeline tested (mocked) | A/B in pilots: which summary do users read and keep | Day 60 |
| A-11 | Bangladeshi and Indian companies with US portfolios are reachable through the founder's network | untested | Companies exist [E25] | Prospect list with warm paths | Day 3 |
| A-12 | Incumbents won't launch a self-serve generics tier within 6 months | untested | — | Weekly competitor monitor | Monthly |

## 2. Decision log

| ID | Date | Decision | Alternatives rejected | Reason | Revisit if |
|---|---|---|---|---|---|
| D-001 | 2026-11-05 | First venture: **LabelLedger (O01)** | O02, O05, O03, O04, O50 and 44 others | Score 6.95, #1 in 94% of perturbed runs; public-data demo; founder fit; low capital (`05_…`) | Kill criteria (`04_…`) |
| D-002 | 2026-11-05 | Opportunity scores committed **before** MVP code | Scoring after building | Limits sunk-cost bias (commit `9145802` precedes `603fb3d`) | — |
| D-003 | 2026-11-05 | **Deterministic-first AI**: rules compute redlines and severity; Claude only explains; every quote machine-verified | LLM-generated diffs and severity | Regulated domain; trust is the product; hallucinations are unacceptable | Never weakened; may be extended |
| D-004 | 2026-11-05 | Python + FastAPI + server-rendered HTML, SQLite now and Postgres at multi-tenancy | Next.js/TypeScript; serverless | One language; no JS needed; zero ops now; portable SQL | >10k watched labels or >20 tenants |
| D-005 | 2026-11-05 | Default AI model `claude-opus-5-5` with structured outputs, prompt caching, `fallbacks: "default"`; effort `medium` | Cheaper models by default | Quality per verified claim; low call volume; model is configurable | Cost per summary > $0.10 or quality evals |
| D-006 | 2026-11-05 | Dependency lock with a two-week release cooldown (`exclude-newer`) | Always-latest | Supply-chain hygiene | Security patch needed sooner (manual bump) |
| D-007 | 2026-11-05 | Venture code lives in this repository for now (`godmode/`, `labelledger/`) | New repository | The session is scoped to this repository | **Move to a private repository before any customer data** (recommended) |
| D-008 | 2026-11-05 | Product #2 = O02 RIM-lite for exporters, gated on O01 traction or O01 failure | Building O02 in parallel | Focus; shared infrastructure | Gate in `09_…` |

## 3. Intelligence log (what changed)

| Date | Change | Source | Impact |
|---|---|---|---|
| 2026-10-29 | UN General Assembly deferred Bangladesh's LDC graduation to 24 Nov 2029 | E08 | TRIPS patent cliff moves out three years; patent intelligence is a 2027–28 opportunity, not now |
| FY2025-26 | Bangladesh pharma exports $238.5M (+12%), 130+ countries | E07 | Growing export regulatory workload supports O02 timing |
| 2026 | AI regulatory start-ups funded (Weave Bio, Entvin, Regara, Klaris, Validfor) | E13 | Category validated; they target big pharma, so the mid-market gap remains |
| 2026-03 / Q4 2026 | EU ePI draft roadmap; FHIR standard; phased rollout of centrally authorised products reported from Q4 2026 | E14 | Second structured label source for LabelLedger |
| mid-2026 | FDA PMI rule still proposed, not final | E15 | Watch; a final rule would be a demand spike |
| 2026 | Competitors confirmed: Dr.Evidence, Basil, Freyr/Freya, Sarjen, IQVIA | E04–E06 | Positioning must be self-serve, generics-first, with a public layer |

## 4. Review questions and metrics

**Weekly review (30 minutes; answers appended below each week)**

1. What changed?
2. What did we learn?
3. What is working?
4. What is failing?
5. What should we stop?
6. What should we automate?
7. What should we build next?
8. What is the single highest-value next action?

**North-star metric:** reference labels under active watch by paying teams, which means captured versions × engaged customers. **Supporting metrics:**

| Funnel stage | Metrics |
|---|---|
| Acquisition | Snapshots sent; replies; demos; digest subscribers |
| Activation | Labels watched in week 1; first assessment recorded |
| Retention | Weekly active pilot owners; month-3 cohort retention |
| Revenue | MRR; ARPA; months of runway |
| Quality | Poll success rate; alert latency; escaped false negatives (target 0); evaluation precision and recall |

### Week of 2026-11-05 (first entry)

1. **What changed?** Bangladesh's LDC deferral to 2029; competitor landscape mapped.
2. **What did we learn?** Label intelligence is a validated category with enterprise incumbents. Our opening is the self-serve, generics-first segment.
3. **What is working?** On four real labels and one synthetic revision, the engine finds exactly the planted edits and recognisable real label updates.
4. **What is failing?** Nothing has been tested with customers yet. DailyMed and the Claude API were not reachable from the build sandbox.
5. **What should we stop?** Building more before talking to users.
6. **What should we automate?** Snapshot generation for the outreach list (agent #3).
7. **What should we build next?** Only what the first 10 interviews demand. Likely candidates: the assessment workflow and comparison-draft export.
8. **What is the single highest-value next action?** Live-verify DailyMed, then put 15 personalised drift snapshots in front of 15 labeling professionals within 14 days.
