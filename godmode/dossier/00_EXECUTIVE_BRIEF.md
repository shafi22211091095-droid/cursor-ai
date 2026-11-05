# PROJECT GODMODE — Executive brief

*Prepared 2026-11-05 by the virtual founding team, which in practice is Claude working for the founder. Sources are in `../engine/evidence.toml` (25 claims, each labelled for strength). Figures marked **assumption** are not evidence.*

---

## The verdict

**Build LabelLedger first: version control, explained redlines and drift detection for regulated drug labels.**

Every generic medicine label has to keep up with its reference label. In January 2024 FDA finalised guidance that puts the job of monitoring reference-label changes, and submitting conforming labeling with a side-by-side comparison, on the generic company **[E01]**. Today that is a manual job: someone compares 30–80 page labels by eye. Published research found that 68% of multi-source generics had safety-labeling discrepancies, some caused by out-of-date text **[E02]**. FDA's free safety-change database leaves out generics, every non-safety section, and all history before 2016 **[E03]**.

LabelLedger turns the public FDA label archive (SPL XML on DailyMed) into a version-controlled ledger. It then answers three questions in seconds:

1. **What changed in this reference label?** A word-level redline organised by section, triaged by clinical severity, with the reasons shown.
2. **Has our label drifted from its reference?** A statement-by-statement parity check. It works even when the two labels are structured differently.
3. **What changed this week across everything we watch?** Watchlists, alerts and a digest.

### Why this one and not the other 49

| Test | LabelLedger |
|---|---|
| Score (weighted, with penalties) | **6.95**, #1 of 50. Margin 0.71 over #2 |
| Robustness | Ranked #1 in **94%** of 4,000 Monte Carlo runs perturbing every weight, score and penalty. #2–#6 are statistically tied |
| Founder fit | Uses your pharmaceutical-sciences training (reading labels is pharmacy work) and your rigor ethos (npcadd's validation-first design carries straight over) |
| Capital | Under $1k to first revenue. Fully public data, no data licences, no regulated product |
| Proof before permission | A personalised drift report can be built from public data **before** a prospect shares anything. That is the sales hook |
| Upside | It is a wedge into a "regulatory operating system for generic-medicine makers", and into an API for health-AI products that need current, change-aware label data **[E22]** |

**What would have beaten it:** a registration-and-renewal tracker for emerging-market exporters (O02). It scores lower because buyers have lower budgets and heavier onboarding. It is planned as **product #2**, sold to overlapping customers. See `05_MISSION_E_DECISION.md`.

### What the red team says, in one line each

* Competitors exist: Dr.Evidence advertises 728k labels across 11 authorities with RLD alerts **[E04]**, and Basil, Freyr/Freya, Sarjen and IQVIA sell adjacent tools **[E05, E06]**. We win only as the **self-serve, transparent-price, generics-first** option, with a public layer that markets itself. If interviews show target buyers are already served, we pivot within 60 days.
* "Monitoring" alone may not be worth paying for. So the paid tier carries the jobs that hurt most: drift reports against your own labels, assessments with an audit trail, and drafts of the submission comparison.
* Selling to US companies from Bangladesh is harder. So we target the US-labeling teams of **Indian and Bangladeshi generics companies** first (same time zone, same professional networks), collect payment through a merchant of record **[E09]**, and form a US entity only once revenue justifies it.

---

## What exists now (status board)

| Item | Status | Evidence |
|---|---|---|
| 50 opportunities, 17 fields each, scored, classified | **Completed** | `../engine/results/opportunities.md`, `ranking.md` |
| Scoring engine with sensitivity analysis | **Completed, reproducible** | `python3 godmode/engine/score.py --check` (CI) |
| 20 underserved problems with evidence grades | **Completed** | `02_MISSION_B_PROBLEMS.md` |
| Red team of top 5, winner memo | **Completed** | `04_…`, `05_…` |
| Execution blueprint (11 parts), validation kit, AI-company design, portfolio/IP/security, 365-day roadmap | **Completed (proposed plans)** | `06_…` to `10_…` |
| Three-scenario financial model | **Completed, reproducible** | `../finance/results/model.md` |
| LabelLedger MVP: parser, redline, severity, drift, verified-AI summaries, reports, archive, DailyMed client, web app, CLI, Docker, CI | **Built and tested** | 84 tests, 91% branch coverage, ruff + mypy --strict clean |
| Engine run on real FDA labels | **Tested** on 4 real DailyMed SPLs | `labelledger/tests/fixtures/PROVENANCE.md` |
| DailyMed live polling | **Not verified.** DailyMed is blocked from the build sandbox. Tested against recorded responses only | Run `labelledger fetch <set-id>` once on your machine |
| AI summaries against the live Claude API | **Not run.** No API key in the sandbox. Request shape and failure handling are tested with a mocked client | Set `ANTHROPIC_API_KEY` and run `labelledger diff … --ai` |
| Customers, revenue, pricing validation | **None yet.** Every commercial number is an assumption | `07_VALIDATION_KIT.md` is how we get evidence |
| Deployment | **Not deployed.** That needs your accounts and approval | `06_…` §4 and `labelledger/README.md` |

### What the engine already shows on real data

Comparing a real 2012 warfarin label with the real 2026 text gives **88% parity across a 4,868-day gap**. Among the statements the older label lacks, it surfaces:

* the calciphylaxis warning
* the acute-kidney-injury warning
* limb ischemia in HIT/HITTS
* the renal-impairment dosing statement
* the Pregnancy and Lactation Labeling Rule wording

All of these are real labeling evolutions. A label compared with itself scores 100% with zero findings. On a synthetic revision with five planted edits, the engine finds exactly those five, with the right severities.

---

## Facts that changed while we worked (as of 2026-11-05)

* **Bangladesh's LDC graduation is deferred to 24 Nov 2029.** The UN General Assembly adopted the deferral on 29 Oct 2026 **[E08]**. The TRIPS pharmaceutical-patent cliff moves out three years. That gives three years of preparation demand (patent landscapes, regulated-market registrations), not an emergency next month.
* Bangladesh pharma exports reached about **$238.5M in FY2025-26 (+12%) to 130+ countries [E07]**. Export regulatory workload is growing, and that is the opening for product #2.
* Investors are funding AI regulatory tools: Weave Bio $20M, Entvin (YC), Regara, Klaris, Validfor **[E13]**. They mostly target large pharma, which leaves the generics mid-market open.
* The EU is moving product information to structured FHIR "ePI", with rollout reported from Q4 2026 **[E14]**. That is a natural second data source.

---

## The one highest-leverage next action

**Within 14 days, put 15 personalised drift snapshots in front of 15 labeling or regulatory-affairs professionals, and book the conversations.**

The product is ready enough to do this today. Each snapshot is built from public data: their product's current label against the reference's current label. Interviews tell us whether the pain is real and paid for. That decides everything else, and no amount of extra building can substitute for it.

How (details in `07_VALIDATION_KIT.md`):

1. **Today:** run LabelLedger on your machine. DailyMed is reachable outside this sandbox. Fetch 20 reference labels and the matching generic labels for products made by companies you can reach. Bangladeshi and Indian firms with US ANDAs come first: Beximco, Eskayef and ACI supply the US market **[E25]**.
2. **Days 1–3:** list 30 named people (labeling or RA leads) through your pharmacy-school network and LinkedIn, and generate their snapshots.
3. **Days 3–14:** send personal notes (templates in the kit; **you send them, not an agent**). Run interviews with the script. Log every answer.
4. **Gate at day 30:** at least 6 of 10 interviewees describe recurring manual effort or missed updates, and at least 3 ask for a pilot. **Gate at day 60:** at least 3 paid pilots at $200/month or more. Miss both gates and we pivot to O02 using the same codebase. The kill and pivot criteria are written in advance in `04_…` and `10_STRATEGIC_RECORD.md`.

### Decisions only you can make (I have not done these)

* Approve outreach in your name, and approve any spending. Nothing has been bought, registered or sent.
* Choose and register the product name. "LabelLedger" is a **working codename** and needs a trademark search first.
* Choose the payment route: Paddle as merchant of record (docs list Bangladesh as supported; verify) or a US LLC.
* Provide an Anthropic API key if you want AI summaries switched on.
* Decide whether this venture lives in this repository or moves to its own private repository (recommended before any customer data exists).

---

## Map of this dossier

| File | Mission |
|---|---|
| `01_MISSION_A_GLOBAL_INTELLIGENCE.md` | Landscape, method, the 50 opportunities |
| `02_MISSION_B_PROBLEMS.md` | 20 underserved problems with evidence grades |
| `03_MISSION_C_SCORING.md` | Scoring engine, top 10, sensitivity |
| `04_MISSION_D_RED_TEAM.md` | Attack on the top 5, pre-mortem, kill criteria |
| `05_MISSION_E_DECISION.md` | The decision memo |
| `06_MISSION_F_BLUEPRINT.md` | The 11-part execution blueprint, including the 30-day plan |
| `07_VALIDATION_KIT.md` | Scripts, templates, landing copy, pilot proposal, thresholds |
| `08_AI_NATIVE_COMPANY.md` | Virtual founding team and the lean agent architecture |
| `09_PORTFOLIO_IP_SECURITY_ROADMAP.md` | Portfolio, defensibility, IP, security and legal, 365-day roadmap |
| `10_STRATEGIC_RECORD.md` | Assumptions register, decision log, review cadence |
