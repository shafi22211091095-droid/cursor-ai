# The one-person, AI-native company design

## Principle

**The smallest organisation that can run the business is one founder plus a few narrowly scoped automations.** Each automation earns its place by saving recurring hours or by reducing a specific risk.

The brief lists 28 possible agents. We start with **eight** and merge the rest into them or defer them. Each extra agent is something more to monitor, pay for and debug. Twenty-eight agents would make the architecture look impressive and the company slower.

## The virtual founding team (how decisions get challenged)

The 15 specialist perspectives in the brief are **review lenses**, not separate employees. Every material decision is written as a short memo, like the ones in this dossier. It then gets reviewed by the lenses that disagree with each other most:

| Decision type | Lenses that must sign off | The built-in disagreement |
|---|---|---|
| What to build next | Product, Engineer, Red team, Enterprise sales | Sales wants features that close a deal; the red team asks whether it is a one-customer feature |
| Pricing | Financial modeller, Growth, Enterprise sales | Growth wants low friction; finance wants margin and anchoring |
| Data and AI features | AI/ML scientist, Security, Red team | AI wants model freedom; security wants minimal data exposure; the red team asks how it fails silently |
| Architecture | CTO, Cloud, Security, Solo-founder feasibility | The CTO wants scale; feasibility wants the fewest moving parts |
| New product | Strategist, IP, Red team, Finance | A portfolio story versus focus |

In practice, the founder runs these as Claude sessions with explicit role prompts, and the memos plus dissent are saved in `10_STRATEGIC_RECORD.md`.

## Human roles

| When | Role | Owns |
|---|---|---|
| Now | **Founder** | Customers, judgment, approvals, sending anything external, money |
| ~$5k MRR (base month ~10–15) | Part-time **regulatory specialist** (Bangladesh, ~$700/mo assumption) | Reviews AI-drafted digests, snapshot QA, assessment support for customers |
| ~$15–20k MRR | **Full-stack engineer** (Bangladesh) | Product velocity, on-call |
| Later | Account manager / sales for enterprise | Only when inbound enterprise demand exceeds founder capacity |

## The eight automations

Every agent writes to a reviewable place: a pull request, a draft, a report file or a dashboard. **No agent sends external communications, spends money, changes production data destructively, or represents the founder.**

| # | Agent | Responsibility | Inputs | Outputs | Tools | Permissions | Failure conditions | Human approval | Metric |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Label pipeline** (the product) | Poll watched labels, capture versions, compute redlines, drift and alerts | DailyMed API, uploads | Changes, alerts, reports | `labelledger poll` on a schedule | Read public APIs; write own database | Poll error rate >10%; checksum failures; zero changes for 14 days (suspicious) | None for capture; humans review alerts | Poll success %, alert latency (hours from DailyMed publication) |
| 2 | **Digest writer** | Draft the weekly public digest and customer digests | Changes table, verified summaries | Draft digest (Markdown/HTML) | `labelledger digest`, Claude API | Read-only database; write drafts | Unverified AI claim in a draft; empty week | **Founder or RA specialist approves before every send** | Open rate, unsubscribes, replies |
| 3 | **Outreach researcher** | Build prospect rows and drift snapshots for target products | Public labels, company lists | `prospects.csv` rows, snapshot files, **draft** messages | CLI, spreadsheet | Public data only; no messaging accounts | Snapshot with product-specific noise; wrong contact | **Founder sends every message personally** | Snapshot accuracy (founder rejection rate), reply rate |
| 4 | **Support triage** | Classify support email and draft replies with links to the exact redline or doc | Shared inbox (read-only) | Labelled threads, draft replies | Email API, Claude | Read inbox; create drafts only | Wrong answer about a regulatory interpretation | **Human sends**; regulatory questions always escalate | Time to first response, CSAT |
| 5 | **Quality gate** (QA) | Run tests, lint, types, evaluation set, coverage on every change; block regressions | Pull requests | CI status, evaluation report | GitHub Actions, pytest, eval harness | Repository CI only | Flaky tests; evaluation regression | Founder merges | Evaluation precision/recall per finding type; escaped defects |
| 6 | **Security and dependency monitor** | Dependency CVEs, secret scanning, backup verification, uptime | Lockfile, repository, backup bucket, `/healthz` | Alerts, weekly security note, Dependabot PRs | pip-audit, Dependabot, restore script | Read-only except opening PRs | Critical CVE unpatched >7 days; failed restore | Founder approves upgrades | Mean time to patch; restore-test pass rate |
| 7 | **Market and competitor intelligence** | Weekly scan: competitor pricing and features, FDA/EMA labeling guidance, funding news; update the evidence register | Web search, regulator pages | Weekly brief; proposed `evidence.toml` additions with strength labels | Web search, Claude | Read public web; propose edits via PR | Unsourced claims; stale sources | Founder merges evidence changes | Decisions changed by intelligence (should be rare and real) |
| 8 | **Finance and metrics** | Monthly: replace assumptions with actuals, re-run the model, flag gaps against the gates | Billing export, app metrics | Updated `assumptions.toml` PR, monthly metrics memo | `model.py`, spreadsheets | Read-only billing exports | Assumptions left unreplaced after 60 days | Founder merges | Forecast error; runway months |

**Merged or deferred from the brief's list (and why):**
- CEO-strategy and opportunity-discovery agents: these are the founder's job, supported by #7.
- Product manager, UX, architect, frontend, backend, database and documentation: the founder plus Claude Code within the normal development loop; the quality gate (#5) enforces standards.
- Sales research and lead qualification: merged into #3.
- Marketing content and SEO: digest (#2) plus programmatic pages generated by the product.
- Analytics and retention: #8, plus usage dashboards once there are more than 10 customers.
- Privacy reviewer and access-control monitor: #6 plus a quarterly founder checklist (`09_…`).

## Operating cadence

| Rhythm | What |
|---|---|
| Daily (15 min) | Alerts health (#1), support drafts (#4), outreach drafts (#3) |
| Weekly | Approve digest (#2); intelligence brief (#7); dependency PRs (#6); the 8 review questions (`10_…`) |
| Monthly | Metrics and model refresh (#8); restore drill (#6); assumption review; kill and pivot gates |
| Quarterly | Severity-rule review against new FDA labeling practice; portfolio decision; security checklist |

## Guardrails (non-negotiable)

1. Agents draft; the founder sends, signs, spends and publishes.
2. Every AI output shown to a customer is either deterministic or quote-verified against source.
3. Agents get least-privilege credentials, scoped per agent, stored in the platform's secret store, and rotated quarterly.
4. Every agent action is logged to a reviewable place. Nothing silent.
