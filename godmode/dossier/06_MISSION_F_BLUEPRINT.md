# Mission F — Execution blueprint: LabelLedger

This blueprint covers the eleven parts the brief asks for. Section 8 of the brief (PRD, personas, journey, specs, schema, API, auth, UI, milestones, testing, deployment and monitoring) is folded into §5 and §6.

**Status legend:** ✅ built and tested in this repository · 🟡 proposed · ⛔ requires the founder's approval or accounts.

---

## 1. Business model

**Model:** concierge-assisted SaaS, with a free public layer, paid workspaces, and an API later.

| Layer | What | Why | Status |
|---|---|---|---|
| Free public layer | Label-history pages ("what changed in drug X, when") and a weekly "Reference label changes" digest | Distribution and trust; SEO; an email list of RA professionals | 🟡 Engine ✅, pages and newsletter 🟡 |
| Pro, $199/mo | 50 watched labels, redlines, severity triage, alerts, exports | Individual labeling specialists, small companies, consultants | 🟡 Features ✅, billing ⛔ |
| Team, $599/mo | 300 labels, drift reports against your own labels, assessment workflow with audit trail, comparison drafts, 5 seats | Mid-size generics, consultancies | 🟡 Drift ✅; workflow and seats 🟡 |
| Enterprise, from ~$1,500/mo (annual) | Unlimited labels, SSO, API, priority support, data export, DPA | Larger companies, content and AI vendors | 🟡 |
| Concierge packages, $300–$400 each (first 6–12 months) | "Portfolio drift audit": a human-reviewed drift report for up to 25 products | Earliest revenue; learning which outputs customers value; converts to subscriptions | 🟡 Ready to sell with the current engine |

**Revenue logic.** Willingness to pay is anchored to the cost of a labeling specialist's time and of a compliance miss, not to "software seats". One prevented late update, or one comparison document drafted, pays for months of subscription.

## 2. Target customers

**ICP-1 (start here).** The US-labeling or regulatory team at a generic company with roughly 10–300 ANDAs. This explicitly includes **Indian and Bangladeshi companies' US labeling teams**: Beximco, Eskayef and ACI supply the US market **[E25]**, and Indian generics hold a large share of ANDAs. They are reachable through the founder's network, share the time zone, and are price-sensitive enough that enterprise suites are a stretch.

**ICP-2 (parallel).** Regulatory consultancies and freelance labeling specialists who maintain labels for several clients. They have lighter procurement, multiply usage, and are natural early adopters.

**ICP-3 (year 2).** Export regulatory teams in emerging markets keeping package inserts aligned with reference information. This is also the bridge to product #2 (O02).

**Personas**

| Persona | Role | Job to be done | What they need to see |
|---|---|---|---|
| **Nusrat**, Labeling Specialist (user) | Maintains 120 generic labels | "Tell me which reference labels changed, what changed, and what I must update, with proof I can paste into the change record." | Word-level redline, severity with reasons, export to the change file |
| **Rahul**, Head of Regulatory Affairs (buyer) | Owns compliance; reports to QA and management | "Show me we're not drifting, and that nobody is missing updates." | Portfolio parity dashboard, audit trail, monthly report |
| **Claire**, Independent consultant (multi-client) | Runs labeling for 6 small sponsors | "Do more clients without more hours." | Client workspaces, digest, bulk drift checks, white-label exports |

**User journey (Nusrat)**

1. Receives a free drift snapshot of one of her products: "3 statements in your W&P section are missing versus the reference".
2. Books a demo.
3. Uploads or links her labels.
4. Watches the references.
5. Every Monday she gets a digest. A CRITICAL alert opens a redline with reasons.
6. She records her assessment and exports the comparison draft.
7. Rahul sees portfolio parity rise month over month.

## 3. Product architecture

```mermaid
flowchart LR
  subgraph Sources
    DM[DailyMed v2 API\n(current SPL)] ; UP[Uploads\n(SPL XML)] ; EU[EU ePI FHIR\n(roadmap)]
  end
  DM --> ING[Ingest]
  UP --> ING
  ING --> P[Hardened SPL parser\nspl.py]
  P --> A[(Version archive\nSQLite → Postgres\ngzip XML + SHA-256)]
  A --> D[Redline engine\ndiff.py]
  A --> R[Drift engine\ndrift.py]
  D --> S[Severity rules\nseverity.py]
  R --> S
  S --> X[Summaries\nai.py: deterministic +\nClaude, quote-verified]
  X --> O[Reports HTML / MD / JSON\nreport.py]
  S --> AL[Alerts + digest\nstore.py, digest.py]
  O --> W[Web app + JSON API\nweb/app.py]
  AL --> W
  W --> U[Users]
```

**Design principles**

1. **Determinism first.** Redlines and severity come from rules. AI only explains, and every quote is verified.
2. **Provenance everywhere.** Source XML checksums, engine version, generation time and rule reasons are recorded.
3. **Boring, portable infrastructure.** One container and one database.
4. **Public data first.** Customer-confidential data is added only when a workflow requires it.

**Database schema** ✅ (`labelledger/src/labelledger/store.py`)

| Table | Holds |
|---|---|
| `labels` | One row per set ID: title, labeler, application numbers, latest version |
| `versions` | One row per captured version: gzip XML plus SHA-256, source, capture time |
| `watches` | Watched labels: role (reference / own / competitor), optional follower label |
| `changes` | One per version pair: severity, counts, full JSON report, summary |
| `alerts` | Change × watch, with status new / reviewed / dismissed |

Next migration 🟡: `workspaces`, `users`, `memberships`, `assessments` (who decided what, when, why), and `api_keys`, with `workspace_id` on every row for multi-tenancy.

**API** ✅ (read-only JSON today)

- `GET /api/labels?q=`
- `GET /api/labels/{set_id}` (versions and changes)
- `GET /api/changes?since=`
- `GET /api/changes/{id}` (full redline plus summary)
- Exports: `/changes/{id}/export?format=html|md|json`, and `/drift?...&format=html|json`

Planned 🟡: token-authenticated write API (watches, uploads), webhooks on new changes, and a bulk drift endpoint.

**Authentication and authorization**
- ✅ now: HTTP Basic for a private beta; no public bind without it; CSRF on forms; strict CSP.
- 🟡 Milestone 2: email magic-link login (Resend or Postmark), workspace RBAC (owner / editor / viewer), per-workspace API tokens hashed at rest, session cookies `Secure; HttpOnly; SameSite=Lax`.
- 🟡 Enterprise: SSO via a broker (WorkOS or similar) when a contract requires it.

**UI structure** ✅: Dashboard (compare now, upload, alerts, recent changes, watchlist) → Archive (search) → Label (versions, changes, drift, watch) → Redline / Drift report pages with exports. It is server-rendered, uses no JavaScript, works on phones, and supports dark mode.

## 4. Recommended technology stack

Prices are indicative as of 2026-11 and must be verified at signup. Free tiers change often. Nothing has been purchased.

| Need | Recommendation | Why for a solo founder | Free tier / starting price | Lock-in | Alternatives considered |
|---|---|---|---|---|---|
| Language and framework | **Python 3.11+, FastAPI, Jinja2** ✅ | One language for parsing, data and web; AI coding agents excel at it | Free | None | Next.js/TypeScript: rejected for now, as a second language would double maintenance |
| XML and diff | lxml (hardened), difflib, rapidfuzz ✅ | Fast, mature, permissive licences | Free | None | — |
| Database | **SQLite** now ✅; **PostgreSQL** at multi-tenant (Neon or Supabase free tiers, or a $10–25/mo managed instance) | Zero ops now; portable SQL later | Free tiers exist | Low (standard SQL) | — |
| Hosting | **One small VM** (e.g. Hetzner, ~€4–8/mo) running the Docker image behind Caddy (automatic TLS); or Fly.io / Render / Railway | Cheapest reliable option, full control, a single binary to reason about | ~$5–10/mo | Low (Docker) | Serverless: rejected; scheduled polling and SQLite favour a long-lived process |
| Background jobs | cron or systemd timer running `labelledger poll` ✅ | No queue needed under ~10k watched labels | Free | None | Celery/RQ later if needed |
| Backups | Litestream (SQLite) or nightly dumps to Cloudflare R2 or Backblaze B2 | Point-in-time recovery for pennies | R2 has a generous free tier | Low | — |
| AI | **Claude API**, `claude-opus-5-5` for summaries ✅ (optional), with structured outputs, prompt caching and server-side fallbacks | Best quality per verified claim; summaries are rare (one per label change) | ~$4/$20 per M tokens; about $0.02–0.05 per summary (estimate) | Medium (prompt-specific) | Model is configurable by environment variable |
| AI development | Claude Code (this agent) plus GitHub | Builds, tests and maintains the code | Subscription | Low | — |
| Payments | **Paddle** (merchant of record; docs list Bangladesh sellers **[E09]**, verify) ⛔ | Handles global sales tax and VAT, invoices and payouts to a Bangladeshi seller | ~5% + $0.50 per transaction (secondary **[E24]**) | Medium | US LLC plus Stripe (Stripe Atlas or similar) once revenue warrants; Payoneer to receive; local BDT invoices for Bangladeshi customers |
| Email | Resend or Postmark (transactional, digest); Buttondown, Beehiiv or Substack for the public newsletter | Deliverability without servers | Free tiers / ~$10–20/mo | Low | Amazon SES (cheapest, more setup) |
| Errors and uptime | Sentry (free developer tier), Better Stack or UptimeRobot | Know before customers do | Free tiers | Low | — |
| Analytics | Plausible (paid) or self-hosted Umami | Privacy-friendly; no cookie banner burden | ~$9/mo or free self-hosted | Low | — |
| CRM | A spreadsheet until 50 prospects, then HubSpot free or Attio | Don't buy a CRM before you have a pipeline | Free | Low | — |
| Support | Shared inbox plus AI-drafted replies (human approves) | Volume will be low and high-value | Free | None | Intercom etc.: too early |
| Docs | Markdown in the repo, then MkDocs static site | Docs as code | Free | None | — |
| Project management | GitHub Issues and Projects | Same place as the code | Free | Low | — |
| Security tooling | Dependabot, `pip-audit` in CI, GitHub secret scanning | Supply-chain hygiene | Free | None | — |

**Estimated monthly run cost at launch:** about $50–200, including AI tools, hosting, domain and email (model assumption `../finance/assumptions.toml`).

## 5. MVP specification

**What exists** ✅ (tested; details in `labelledger/README.md`):

| Feature | User problem | Benefit | Dev cost | Priority | Acceptance criteria | Test |
|---|---|---|---|---|---|---|
| Hardened SPL parser | Labels are complex XML | Reliable structure from any SPL | Done | P0 | Parses PLR, non-PLR and flat labels; rejects DTD/entities/oversize | `test_spl.py` (real fixtures, attack payloads) |
| Section-aware redline | Manual comparison of long labels | Minutes become seconds; nothing missed | Done | P0 | Finds exactly the 5 planted edits; zero changes on identical input; exact text reconstruction | `test_diff.py` (incl. 300 property-based cases) |
| Severity triage with reasons | Alert fatigue | Read the critical change first | Done | P0 | Boxed warning, contraindications and numeric dosing → CRITICAL; editorial-only → EDITORIAL; reasons shown | `test_severity.py` |
| Drift detection | "Is our label behind?" | Prove parity or find gaps | Done | P0 | Self-compare = 100% and no findings; synthetic update = exactly 4 findings; real 2012 label misses known later warnings | `test_drift.py` |
| Verified AI summaries | Explaining changes to colleagues | Plain-language impact plus actions, with proof | Done (needs API key) | P1 | Fabricated quotes flagged; refusals and API errors degrade to deterministic | `test_ai.py` (mocked client) |
| Reports (HTML / MD / JSON) | Evidence for change records | Paste-ready, printable, machine-readable | Done | P0 | Standalone HTML, no scripts, escapes hostile text | `test_report.py`, `test_web_cli.py` |
| Archive, watchlist, alerts | Losing track of versions | History accrues automatically | Done | P0 | Checksums verified; duplicate versions ignored; alerts per watch | `test_store_watch.py` |
| DailyMed polling | Manual checking | Changes arrive by themselves | Done (live endpoint unverified) | P0 | Retries, backoff, first capture → change on new version | `test_store_watch.py` (mocked HTTP) |
| Web app and CLI | Usability | Self-serve demos and automation | Done | P0 | Auth, CSRF, CSP, uploads, exports, `--fail-on` | `test_web_cli.py` |

**Next to build** 🟡 (only when pilots ask; acceptance criteria are written now):

| Feature | Problem | Priority | Acceptance criteria |
|---|---|---|---|
| Assessment workflow (accept / not-applicable / done, with comment and user) | Audit trail for inspections | P0 for paid Team | Every change has an assessment record exportable as PDF and CSV |
| Comparison-draft export (reference vs. proposed generic text, side by side) | Required in labeling submissions **[E01]** | P0 | DOCX/HTML two-column export per changed section, with source citations |
| Multi-tenant accounts and magic-link login | Selling to more than one team | P0 before 3rd customer | Workspace isolation tested; no cross-workspace reads |
| Public label-history pages and weekly digest | Distribution | P1 | 1,000 most-prescribed reference labels published; digest sent weekly with human approval |
| Evaluation set (50+ real version pairs, hand-labeled) | Earn the right to claim accuracy | P1 | Published precision/recall per finding type; CI regression gate |
| Historical import from DailyMed archives | History before watching began | P2 | Import N past versions per set ID with checksums |
| EU SmPC / ePI source | EU generics and emerging markets referencing EU text | P2 | Parse FHIR ePI bundles into the same section model |

## 6. Development plan

| Milestone | Window | Scope | Exit criteria |
|---|---|---|---|
| **M0 Engine** ✅ | Done | Everything in §5 "exists" | 84 tests green, 91% coverage, CI |
| **M1 Live and demoable** | Days 1–7 | Run against live DailyMed; fix any endpoint drift; deploy a private instance; generate 20 real drift snapshots | `labelledger fetch` works live; snapshot gallery ready |
| **M2 Pilot-ready** | Days 8–30 | Accounts and workspaces, assessment workflow, comparison-draft export, digest email | 3 pilot workspaces running |
| **M3 Trust** | Days 31–60 | Evaluation set and published accuracy note; security posture doc; DPA/ToS; backups and monitoring | Accuracy note published; restore drill passed |
| **M4 Self-serve** | Days 61–90 | Billing (merchant of record), public label pages, onboarding flow | First self-serve paid signup |

**Testing strategy**
- Unit and property-based tests for the engine.
- Real-label fixtures with provenance.
- A planted-edit synthetic revision for exact recall.
- Mocked external services (DailyMed, Claude).
- Security tests (auth, CSRF, XSS escaping, unsafe XML).
- An evaluation set for accuracy before any marketing claim.
- CI gates: ruff, mypy --strict, coverage ≥ 88%, reproducible strategy artifacts.

**Deployment strategy:** a Docker image on one VM, with Caddy for TLS, a cron poll every 6 hours, Litestream backups, `LABELLEDGER_BASIC_AUTH` until accounts ship, and staged rollout (deploy to a staging port, smoke test, then switch).

**Monitoring and maintenance**
- `/healthz` uptime checks.
- Sentry for errors.
- Poll outcomes logged, alerting on more than 10% error rate.
- Weekly dependency audit.
- Monthly restore test.
- Quarterly review of severity rules against new FDA labeling practice.

## 7. Initial customer acquisition strategy

Channels are ranked for a solo founder. Conversion figures are **assumptions** until measured.

| Channel | Cost | Time to results | Conversion assumption | Scalability | Ops burden | Risks |
|---|---|---|---|---|---|---|
| **Personalised drift snapshots, founder-sent** (LinkedIn or email) | ~0.4 h each | Days | 5% reply-to-demo, 20% demo-to-paid (base) | Medium (automation helps) | Medium | Spam perception: only send to relevant RA people, with real value attached, never in bulk |
| **Pharmacy-school and industry network (Bangladesh/India)** | Time | Days | High trust, small pool | Low | Low | Exhausts quickly; use for first 5 pilots |
| **Regulatory consultancies as partners/resellers** | Rev-share 20–30% | 1–3 months | One partner = several clients | High | Medium | Channel conflict; partner quality |
| **Weekly "Reference label changes" digest** | ~2 h/week | 2–4 months | 0.2%/month subscriber-to-paid (base) | High | Low with automation | Must stay high-signal; human review before send |
| **Programmatic label-history pages (SEO)** | Hosting only | 3–9 months | Unknown; measure | Very high | Low | Google's scaled-content policies: each page must carry unique computed value (it does: diffs and timelines) |
| **LinkedIn content** (weekly teardown of a real label change) | ~2 h/week | 1–3 months | Builds credibility for outbound | Medium | Low | Consistency |
| **Webinars with RA associations** | Time | 2–4 months | Good for trust | Medium | Medium | Organiser gatekeeping |
| **Paid ads** | $$ | Fast | Low in this niche | — | — | Not before message–market fit |

**90-day acquisition plan**

| Window | Actions | Target |
|---|---|---|
| Days 1–14 | 30 named prospects (ICP-1 and ICP-2); 15 snapshots sent; 10 interviews | 3 pilot requests |
| Days 15–45 | Weekly LinkedIn teardown; digest v0 to 50 opt-in subscribers; 2 consultancy partnership talks | 3 paid pilots; 100 subscribers |
| Days 46–90 | Public pages for the top 300 reference labels; first webinar; referral offer (1 free month per referred team) | 8–10 paying teams (base); 300 subscribers |

**Never:** spam, fake testimonials, invented customer counts, or scraping that violates terms. Every metric we publish is real.

## 8. Pricing model

**Tiers are as in §1.** Principles:

- Annual plans at 2 months free.
- Pilot pricing at 30–40% off for the first three months to the first five customers, in exchange for feedback and a reference.
- Regional pricing for Bangladesh, India and Pakistan billed locally (about 40% lower, in BDT or INR) once a local entity exists.

**Pricing experiments**
- Van Westendorp questions in every interview.
- A three-tier page with a decoy (Team as the anchor).
- Test $149 vs. $199 for Pro on the landing page with real checkout intent ("Start pilot" clicks).
- Concierge package at $300 vs. $500.

Pricing changes only on evidence. The assumptions sit in `../finance/assumptions.toml`.

## 9. Financial assumptions (summary)

Full model: `../finance/results/model.md`. All inputs are **assumptions**.

| Scenario | Month 12 | Month 24 | Monthly break-even incl. founder draw | External cash needed |
|---|---|---|---|---|
| Conservative | ~5 teams, ~$1.4k MRR | ~16 teams, ~$5.1k MRR | ~month 11 | ~$4.3k |
| Base | ~16 teams, ~$6.6k MRR | ~48 teams, ~$21k MRR | ~month 6 | ~$1.1k |
| Aggressive | ~67 teams, ~$33k MRR | ~188 teams, ~$101k MRR | ~month 2 | $0 |

**Unit economics (base, assumption):** list ARPA ~$431/mo, gross margin ~94%, LTV ~$20k at 2% monthly churn, CAC including founder time ~$500.

**Risks that would destroy the business:**
- Close rates below 10%.
- Churn above 5% a month.
- Payment rails blocked.
- Services revenue that never converts into subscriptions.

## 10. Security requirements

| Requirement | Status |
|---|---|
| Untrusted XML parsing hardened (no DTD/entities/network, size caps) | ✅ |
| HTTPS only (Caddy/managed TLS); HSTS at the proxy | 🟡 deploy-time |
| Authentication on every non-health route; no public bind without it | ✅ (Basic auth) → 🟡 magic link and RBAC |
| CSRF on all state-changing forms; strict CSP; nosniff; frame-deny; no-referrer | ✅ |
| Output escaping of all label text (XSS) | ✅ tested |
| Least privilege: app runs as non-root in the container; read-only filesystem except the data volume | ✅ non-root; 🟡 read-only FS |
| Secrets only in environment or the platform secret store; never in git; secret scanning on | ✅ design; 🟡 enable scanning |
| Dependency monitoring (Dependabot, `pip-audit`); lockfile with two-week release cooldown | ✅ cooldown; 🟡 audit job |
| Encryption at rest (provider disk encryption / managed Postgres) and in transit | 🟡 |
| Backups with tested restore (Litestream or R2), 30-day retention | 🟡 |
| Integrity: SHA-256 on every stored source document, verified on read | ✅ |
| Logging and monitoring (Sentry, uptime, poll error rate); no secrets or customer data in logs | 🟡 |
| Incident response runbook and customer notification template | 🟡 (`09_…`) |
| Data minimisation: customer data limited to watchlists, labels they upload, assessments; no PHI ever | ✅ by design |
| AI data handling: only public label redlines sent to the API; no customer-confidential text without opt-in | ✅ by design |
| Tenant isolation tests before the second customer | 🟡 P0 |

## 11. First 30-day action plan

| Day | Action | Owner | Output |
|---|---|---|---|
| 1 | Clone repo, run `labelledger` locally, `fetch` 5 live labels, fix anything DailyMed changed | Founder + Claude | Live-verified ingestion |
| 1 | Approve working name; run trademark searches (USPTO, EUIPO, WIPO) for LabelLedger and 2 alternates | Founder ⛔ | Name decision |
| 2–3 | Build the prospect list: 30 named RA/labeling people (ICP-1 and 2) from your network and LinkedIn; note their products from public labels | Founder | `prospects.csv` |
| 3–4 | Generate 20 drift snapshots (`labelledger drift … --format html`) and review each by hand for accuracy | Founder + Claude | Snapshot gallery |
| 4 | Landing page (copy in `07_…`) with "Get a free drift snapshot" form | Claude builds; founder approves ⛔ | Live page |
| 5–14 | Send 15 personal notes (templates in `07_…`); 10 interviews using the script; log every answer the same day | Founder ⛔ (sends) | Interview log |
| 7 | Deploy a private beta instance (one VM, Basic auth, backups) | Founder ⛔ (accounts) + Claude | URL for pilots |
| 10 | Weekly LinkedIn teardown #1: "What changed in [drug] labeling this month" | Founder | Post |
| 14 | **Review**: synthesise interviews (`design:research-synthesis`), update the assumptions register | Founder + Claude | Day-14 memo |
| 15–21 | Build only what interviews demanded (likely the assessment workflow and comparison-draft export) | Claude | M2 features |
| 15–25 | Pilot proposals (template in `07_…`) to every interviewee who asked; concierge drift audits for $300–400 | Founder ⛔ | Signed pilots |
| 21 | Paddle (or alternative) account application; ToS and privacy drafts | Founder ⛔ | Billing path |
| 28 | Digest v0 to opt-in subscribers (human-approved) | Founder ⛔ | Issue #1 |
| 30 | **Gate 1** (`04_…`): ≥6/10 confirm pain and ≥3 pilot requests → continue; else re-segment | Founder | Go / no-go |
