# Portfolio, defensibility and IP, security and legal, and the 365-day roadmap

## 1. Portfolio strategy

**Rule:** do not launch a second product until the first has paying, retained customers.

Every product added later must share at least two of the following with an existing one:

- customers
- data
- infrastructure
- distribution

| Class | Asset | Strategic reason | Gate to start |
|---|---|---|---|
| **Core** | LabelLedger (O01) | First revenue; builds the label archive; earns trust with generics regulatory teams | Now |
| **Complementary #1** | Registration & renewal tracker for exporters (O02), with requirements intelligence (O03) as its free SEO layer | Same buyers (RA teams), same app shell, alerts and store; founder's Bangladesh network; O01 keeps package inserts in sync | ≥10 paying O01 teams, **or** O01 kill criteria triggered (then it becomes the pivot) |
| **Complementary #2** | Label data API (version history, change events, drift) for health-AI, clinical-content and safety-analytics teams [E22] | Same archive, new buyers; infrastructure-grade revenue | Archive covers the ~2,000 most-used reference labels; evaluation published |
| **Complementary #3** | Query-response module (O05) inside O02 | Uses O02's dossier context; highest value per use | O02 has 20+ paying exporters |
| **Internal tools** | GODMODE scoring engine, financial model, prospect and snapshot generator, digest writer | Make one person operate like a team | Exists (scoring, model) / build as needed |
| **Experimental** | `npcadd` (open source), possibly npcadd Cloud (O18) | Scientific credibility; recruiting signal; low cost to keep alive | Keep public; no paid push before 2027 |
| **Avoid / discontinue** | Consumer edtech, horizontal compliance monitors, US medical billing, bank document AI | Weak moat or wrong risk profile for this founder (see ranking) | — |

**Shared platform, built once:**

- authentication and workspaces
- billing
- alerts and digests
- document store with checksums
- an AI layer with quote verification
- audit trail
- exports

**Cross-product data rule:** customer data is never pooled across customers. Only public-source data (labels, regulations) is shared between products.

## 2. Defensibility (and what is *not* defensible)

**Not defensible on its own:**

- calling an LLM API;
- downloading DailyMed;
- a diff algorithm. Any of these can be cloned in weeks.

**Defensible, compounding over time:**

| Moat | How it compounds | Status |
|---|---|---|
| Version archive in time | DailyMed serves current versions; every week we capture builds a history competitors can't backfill cheaply | Starts on first deploy |
| Evaluation set and accuracy record | Hand-labelled version pairs with published precision/recall; trust is earned slowly and lost fast | Roadmap (M3) |
| Workflow embedding | Assessments, audit trail and comparison drafts live in the system of record that inspections ask for | Roadmap (M2) |
| Distribution | Public label-history pages and a high-signal digest that RA professionals subscribe to | Roadmap (M4) |
| Multi-authority coverage | US SPL, then EU ePI, then key emerging markets; harder to replicate with each source | Roadmap (year 1–2) |
| Founder-market fit in South Asia | Network, language, time zone, price levels; remote vendors can't copy in-person trust | Now |
| Integrations | Export formats and connectors into RIM/QMS systems customers already use | Year 2 |

## 3. Intellectual-property strategy

| Area | Plan |
|---|---|
| Software ownership | Today the code belongs to the founder. **On incorporation, assign all IP to the company** by written assignment, before any investor, co-founder or contractor joins. |
| Licensing | LabelLedger is proprietary ("all rights reserved" in `pyproject.toml`). `npcadd` stays open (MIT) as a credibility asset. Consider source-available or open-core for the parser later; decide on evidence, not ideology. |
| Open-source obligations | Current dependencies use permissive licences: lxml (BSD), rapidfuzz (MIT), FastAPI (MIT), Starlette (BSD), Jinja2 (BSD), httpx (BSD), Typer (MIT), Pydantic (MIT), uvicorn (BSD), python-multipart (Apache-2.0), anthropic SDK (Apache-2.0 for 2.x). **Keep a licence inventory in CI** (`pip-licenses`) and avoid GPL/AGPL in the hosted service unless deliberate. |
| Trade secrets | Severity rules, drift thresholds, evaluation set, prospect lists. Keep them in private repos, give contractors limited access, and mark them confidential. |
| Trademarks | "LabelLedger" is a working name. **Before any public use:** search USPTO (TESS), EUIPO, WIPO Global Brand Database and Bangladesh DPDT; check domain and handle availability; file in the main market when revenue allows (USPTO base filing fees apply per class; verify current fees). Keep 2 alternates ready. |
| Data rights | US labels on DailyMed are public FDA labeling. Confirm NLM DailyMed terms of use before redistributing at scale. EU ePI and EMA documents follow EMA's reuse policy, to be checked before ingesting. Respect robots and rate limits; never bypass access controls. |
| Contractor agreements | Every contractor signs an IP assignment, confidentiality and data-processing terms **before** access. Templates must be reviewed by a lawyer in the founder's jurisdiction. |
| Copyright | Our report text, docs and digest content are ours; label text quoted in reports is public regulatory text, always attributed to its source. |
| Patents | Low value for this software (prior art; cost; enforcement). Do not spend on patents. Publish methodology selectively for credibility. |
| Never | Use proprietary data without authorisation; scrape behind logins; reuse a competitor's content. |

## 4. Cybersecurity, privacy and legal safety

**Data classification**

| Class | Examples | Handling |
|---|---|---|
| Public | Label text, FDA documents | Stored and checksummed |
| Customer-confidential | Watchlists, uploaded drafts, assessments, comments | Workspace-isolated; encrypted at rest and in transit; never sent to AI without opt-in; deletable on request |
| Personal | User names and emails | Minimal; never sold; used only for the service |
| Never collected | Patient data (PHI) | — |

**Controls:** see the security table in `06_MISSION_F_BLUEPRINT.md` §10. In summary: built ✅ hardened parsing, auth, CSRF, CSP, escaping, non-root container, checksums; planned 🟡 TLS, backups, monitoring, audit job, tenant isolation tests.

**Incident response (runbook outline)**

1. Detect (alert or customer report).
2. Contain (disable the affected feature or revoke keys).
3. Assess scope (which workspaces, what data).
4. Notify affected customers within 72 hours with facts, impact and remediation.
5. Fix and verify.
6. Blameless post-mortem published to customers when they are affected.
7. Track the corrective actions.

**Legal and privacy**

- **Terms of service**: decision-support positioning; no regulatory or legal advice; customer remains responsible for submissions; liability cap; acceptable use.
- **Privacy policy** and a **DPA template** before the first EU or US enterprise customer.
- **GDPR** applies to EU personal data (contact details) if we sell into the EU.
- Check the **current status of Bangladesh's personal-data-protection legislation** before handling Bangladeshi customers' personal data.
- **Payments:** use a merchant of record to handle sales tax and VAT; foreign-exchange receipt rules in Bangladesh need checking with a bank or accountant.
- **AI vendor terms:** use the Claude API under commercial terms (API inputs not used for training by default; verify current terms). Send only public label text unless the customer opts in.

**Never:**

- access systems without authorisation;
- scrape in violation of terms;
- exfiltrate private information;
- circumvent controls;
- impersonate customers;
- claim certifications (SOC 2, ISO 27001, GxP validation) we do not hold;
- claim compliance without evidence.

## 5. The 365-day roadmap (gates, not dates)

A phase starts only when the previous gate is passed. If a gate fails, apply the decision written in advance in `04_…`.

| Phase | Window | Goals | Gate to exit |
|---|---|---|---|
| **Intelligence and selection** | Days 1–30 | ✅ Markets researched, 50 opportunities, venture selected, MVP engine built. Next: live DailyMed check, 20 snapshots, 10 interviews | ≥6/10 confirm pain; ≥3 pilot requests |
| **Construction** | Days 31–60 | Accounts and workspaces, assessment workflow, comparison-draft export, security posture doc, private deployment with backups and monitoring | 3 pilots live; restore drill passed |
| **Launch** | Days 61–90 | Paid pilots → plans; pricing tests; digest weekly; first public label pages; fix product weaknesses found in pilots | ≥3 paying teams; ≥$1.5k MRR; weekly usage by ≥2 of 3 pilots |
| **Product–market evidence** | Months 4–6 | Retention and onboarding; consultancy partner channel; evaluation set published; infrastructure cost review | Month-3 cohort retention ≥80%; "very disappointed" ≥40% |
| **Expansion** | Months 7–9 | High-value features (portfolio dashboard, API beta); integrations (export to RIM/QMS); start O02 discovery with Bangladeshi exporters; EU ePI parser spike | ≥10 paying teams **or** documented pivot |
| **Scale** | Months 10–12 | Strengthen recurring revenue (annual plans); operational resilience (on-call, runbooks); decide on the first hire; evaluate O02 build; decide whether to stay lean, hire or raise | Base-case trajectory (~$6–7k MRR) or a revised plan |

**Do not force a milestone whose prerequisites are not met.** A missed gate is information, not failure, if we act on it.
