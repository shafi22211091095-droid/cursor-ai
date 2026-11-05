# Mission D — Red team: attacking the top five

The red team's job is to kill ideas. For each finalist this page covers:

- why it fails;
- why customers refuse to pay;
- how incumbents destroy the advantage;
- the counter-move;
- the **kill criterion** that would make us stop.

Kill criteria are written in advance so that later evidence, not mood, decides.

---

## O01 — LabelLedger (recommended)

**Why it fails**

1. **The market is already served.** Dr.Evidence advertises 728,000+ labels from 11 health authorities, with automated RLD monitoring and email alerts **[E04]**. Basil Systems has AI label comparison with redlining **[E05]**. Freyr has LabelCompare and LABEL 360. IQVIA RIM Smart Labeling covers the workflow end to end. An India-based vendor (Sarjen) describes a generics-focused RLD tracker **[E06]**. A mid-size generics company may already own one of these, or have built a script.
2. **"Monitoring" is a feature, not a budget line.** RA teams already watch DailyMed as part of the job. Saving a few hours a month is not a purchase order.
3. **Accuracy liability.** One missed safety change, or one false "you're compliant", destroys trust in a market where trust is the product.
4. **Data is public, so the moat is thin.** Anyone can download DailyMed. An AI coding agent can rebuild a diff tool in a weekend.
5. **Selling to US pharma from Dhaka.** Procurement, security questionnaires, invoicing, time zones and credibility all work against a solo founder.

**Why customers refuse to pay**
- "We already have Veeva/IQVIA/Dr.Evidence."
- "My team does this in an afternoon."
- "Our QA won't accept a tool that isn't validated."
- "I can't put a vendor without a SOC 2 report into our process."

**How incumbents destroy the advantage**
- Dr.Evidence or Freyr launches a self-serve tier at $99/month.
- Veeva bundles drift checking into RIM at no extra cost.
- FDA improves SrLC to include ANDAs.
- A general AI assistant learns to "compare these two labels" well enough for occasional use.

**Counter-moves already built into the plan**

| Attack | Counter |
|---|---|
| Served market | Target the segment incumbents price out: small and mid-size ANDA holders, the US-labeling teams of South Asian generics companies, and consultancies. Use **transparent self-serve pricing** and a **free public layer** (label-history pages plus a weekly digest) that incumbents with enterprise sales motions won't copy quickly. |
| Monitoring is a feature | Monetise the painful jobs instead: drift reports against **your own** labels, assessment workflow with an audit trail, and **drafts of the side-by-side comparison** that each labeling submission requires **[E01]**. |
| Accuracy liability | Deterministic redlines are the source of truth. Every AI quote is machine-verified. Severity is rules-based and explains itself. The tool is positioned as decision support, never auto-submits, and keeps an archive of the exact source XML with checksums. A published evaluation set with precision and recall comes before any accuracy claim. |
| Thin data moat | The moat is **time**: the version archive accumulates from day one, and DailyMed's API serves only current versions. Add workflow embedding (alerts, assessments, history), an accuracy reputation, and later multi-authority coverage (EU ePI) and an API. |
| Credibility from Dhaka | Sell first to Bangladeshi and Indian companies with US portfolios **[E25]**, through the pharmacy network, and to consultancies. Use a merchant of record for payment **[E09]**. Prepare a security posture document early. Defer a US LLC until revenue requires it. |
| General AI assistants | They help with one-off comparisons. They do not keep a versioned archive, watch 300 labels, or produce an auditable assessment trail. Our product is the system, not the comparison. |

**Kill criteria**

| Day | Condition | Action |
|---|---|---|
| 30 | Fewer than 6 of 10 qualified interviewees describe recurring manual effort or a missed or late update in the past 12 months | Stop and re-segment |
| 60 | Fewer than 3 paid pilots at $200/month or more (or equivalent annual commitments) after at least 150 targeted outreaches and at least 25 conversations | **Pivot to O02** on the same codebase (parser, store, alerts, web shell carry over) |
| 90 | Pilot usage below weekly for 2 of 3 pilots | Stop and re-scope |

**Residual risk: medium-high.** That is acceptable because the test is cheap (weeks, not months) and the pivot reuses most of the code.

---

## O02 — RIM-lite for emerging-market exporters (product #2 and pivot)

**Why it fails**
- Excel is "good enough" for 50–300 registrations.
- Bangladeshi and African budgets are small and procurement is personal.
- Onboarding needs customers to type in their data.
- Indian RIM vendors bundle software with services **[E10]**.

**Why customers refuse to pay**
- "We have an Excel tracker and a person."
- "Our ERP vendor will add it."
- "We don't put registration data in the cloud."

**How incumbents destroy the advantage**
- Freyr, DDReg or ProcEdge discount aggressively.
- Global RIMs launch emerging-market editions.

**Counter-moves**
- AI import of the existing spreadsheet in minutes.
- Certificate OCR with expiry extraction.
- A WhatsApp or email reminder channel, which fits how these teams actually work.
- Pricing in BDT and INR alongside USD.
- Sharing O01's reference-label data so exporters keep package inserts current.
- Local in-person onboarding in Dhaka as an advantage remote vendors cannot copy.

**Kill criterion:** fewer than 5 of 15 exporters share their trackers and fewer than 3 pay for a pilot within 45 days of starting.

---

## O05 — Deficiency-letter response copilot

**Why it fails**
- Responses are bespoke.
- Dossiers are confidential.
- A wrong response delays approval by months.
- Consultants own this relationship.

**Why customers refuse to pay**
- "We won't upload dossiers."
- "Our consultant handles queries."
- "A chat assistant already helps us draft."

**How incumbents destroy the advantage:** general AI assistants with enterprise privacy terms do 70% of this job inside tools the customer already owns.

**Counter-move**
- Make it a module of O02, where the dossier context already lives, not a standalone product.
- Start with a library of anonymised query types and guidance citations, which needs no confidential data.

**Kill criterion (as a module):** fewer than 20 real anonymised queries obtainable from pilot customers in 60 days.

---

## O03 — Emerging-market requirements intelligence

**Why it fails**
- Maintaining 30+ authorities' requirements is a content-operations treadmill.
- Errors are costly.
- Sources are PDFs in many languages, and websites break.

**Why customers refuse to pay**
- "We ask the local agent."
- "Cortellis covers this (our parent company has a licence)."

**How incumbents destroy the advantage**
- Cortellis, RegASK or Freyr price aggressively.
- Local agents use the free summaries and never convert.

**Counter-move**
- Scope it to the 10 markets that matter most to Bangladeshi and Indian generics.
- Publish free cited summaries as SEO and lead generation for O02.
- Charge for alerts and checklists inside O02.

**Kill criterion:** under 500 monthly organic visitors to 20 published country pages after 4 months.

---

## O04 — Dossier gap-analysis copilot

**Why it fails**
- It needs customers to upload their most confidential documents to an unknown vendor.
- The quality bar is the highest of any idea.
- Funded competitors (Weave, Regara and others) and services firms target it **[E13]**.

**Why customers refuse to pay**
- Trust and NDA friction.
- "Our consultant's checklist is enough."

**How incumbents destroy the advantage**
- Enterprise suites add AI checks.
- Consultancies bundle AI into services.

**Counter-move:** defer it to year 2. Earn trust through O01 and O02 first, and add on-premise or customer-controlled deployment options.

**Kill criterion:** not attempted until O02 has 20 or more paying exporters.

---

## Pre-mortem: "It's November 2027 and LabelLedger failed. Why?"

| Story | Probability (judgment) | Early warning sign | Pre-emptive move |
|---|---|---|---|
| We built for six months and talked to five people | High without discipline | Fewer than 10 conversations by day 21 | The 14-day outreach sprint is the **only** next action; build only what pilots ask for |
| Buyers loved the demo but procurement stalled | Medium | "Send me your security questionnaire" | Security posture doc and DPA template by day 45; consultancies (lighter procurement) as a parallel segment |
| An incumbent launched self-serve at a lower price | Low–medium | Competitor pricing page changes | Weekly competitor monitor; differentiate on drift, EM coverage and the public layer |
| A false negative in a safety section hurt credibility | Low but severe | Customer-reported miss | Evaluation set before claims; second-opinion diff mode; incident runbook; candid post-mortems |
| Founder burnout running sales, product and support alone | Medium | Response times slipping | AI support drafts; scheduled office hours; first part-time hire funded at about $5k MRR |
| Payments from Bangladesh blocked or slow | Medium | Payout holds | Merchant of record from day one; US LLC as plan B by month 4 if revenue is meaningful |

## The red team's verdict

**Proceed with O01, with constraints.**

1. The next 14 days are about customer conversations, not code.
2. The paid tier must contain drift reports and comparison drafts, not just alerts.
3. Commit publicly to the kill criteria above.
4. Keep O02 warm as the pivot.
