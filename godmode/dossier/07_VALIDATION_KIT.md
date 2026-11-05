# Validation kit: turning hypotheses into evidence

Everything here is **proposed**. Nothing has been sent, and nobody has been contacted. **You send every message yourself.** Agents may draft them, but they never send in your name.

Rules we follow:

- No spam.
- No fake urgency.
- No invented customers or testimonials.
- Only contact people for whom the snapshot is genuinely relevant.

---

## 1. Build the prospect list (public data only)

1. **Pick products.** For each target company, list its US generic products. Public labels on DailyMed show the labeler name and the ANDA number (the parser extracts both: `labelledger parse file.xml`).
2. **Find the reference label for each product.** Use the RLD or Reference Standard listed for that product in FDA's Orange Book.
3. **Find people.** On LinkedIn, search for: "labeling specialist", "regulatory affairs labeling", "US regulatory affairs", "artwork and labeling", plus the company name. In Bangladesh and India, start with your pharmacy-school network.
4. **Log every prospect** in `prospects.csv`:
   `name, company, role, channel, products, reference_setids, snapshot_file, sent_on, replied, interview_on, outcome`

## 2. Generate a drift snapshot (≈5 minutes each once automated)

```bash
labelledger fetch <reference-set-id>      # reference (RLD) label
labelledger fetch <their-set-id>          # the prospect's generic label
labelledger drift ref.xml theirs.xml --min major --format html --out snapshot_<company>_<drug>.html
```

**Review every snapshot yourself before sending it.** Remove findings that are plainly product-specific, such as inactive ingredients or supply details. Never send a snapshot you would not defend line by line.

## 3. Outreach templates

**LinkedIn connection note** (≤300 characters)
> Hi {first name} — I'm a pharmacist building a tool that tracks reference-label changes for generic labels. I ran a free comparison of {product}'s current label against its reference and found {n} statements worth a look. Happy to share — no strings.

**Follow-up after they accept**
> Thanks for connecting. Here's the snapshot for {product}: {link or attachment}. It compares your current DailyMed label with {reference} statement by statement. The {section} findings are the ones I'd check first. I'd value 20 minutes to learn how your team tracks reference updates today. I'm trying to understand the work, not to pitch. Would {two time options} work?

**Email (where appropriate)**
> Subject: {product} label vs. reference: {n} differences in {section}
>
> {First name}, I compared the current {product} label from {company} with its reference listed drug's label (both public on DailyMed). Most of it matches, but {n} statements in {section} don't appear in yours, e.g. "{one short quoted statement}". The full one-page comparison is attached.
>
> I'm a pharmacist from {city} building software for generic labeling teams, and I'm interviewing labeling professionals about how reference updates are tracked. Could I have 20 minutes next week? If this isn't your area, a pointer to the right colleague would be very kind.

**Tone rules**
- Lead with their product and a concrete finding.
- Never say "you are non-compliant". Differences can be legitimate (carve-outs, product-specific text).
- Make it easy to say no.

## 4. Interview script (≈20 minutes, past behaviour over opinions)

1. **Context (2 min)**
   - "What does your role cover? How many labels does your team maintain? For which markets?"
2. **Last time (6 min)**
   - "Tell me about the last time a reference label changed and you had to update yours. How did you find out? How long from the reference change to your submission? What took the most time?"
   - "Have you ever found out about a reference change late? What happened?"
3. **Current tools and spend (4 min)**
   - "What do you use today: DailyMed alerts, a vendor tool, a spreadsheet, a consultant? What does it cost?"
   - "Who signs off on a labeling change? What evidence do they want in the change record?"
4. **Quantify (3 min)**
   - "Roughly how many reference updates a month across your portfolio? Hours per update, end to end?"
   - "If those hours dropped by 80%, what would your team do with them?"
5. **Snapshot reaction (3 min)**
   - "Looking at this snapshot: which findings are useful? Which are noise? What's missing?"
6. **Pricing (2 min, Van Westendorp)**
   - At what monthly price would this be so cheap you'd doubt the quality?
   - At what price is it a bargain?
   - At what price is it getting expensive?
   - At what price is it too expensive to consider?
7. **Close**
   - "Would you run a 6-week pilot on 25 of your products at {pilot price}? Who else must agree?"
   - "Who else should I talk to?"

**After each interview, log within 1 hour:**
- pain (0–10, with the quote that justifies it);
- current solution and cost;
- frequency and hours;
- decision-maker;
- price answers;
- next step.

Use Claude's research-synthesis skill (`design:research-synthesis`) on the log at day 14.

## 5. Demo script (10 minutes)

1. Their product's drift snapshot. Open on the most important real finding.
2. Version redline of a recent reference change. Show severity, reasons and word-level redline.
3. Watchlist and Monday digest: "this arrives by itself".
4. Export the evidence pack for the change record (HTML, Markdown, JSON).
5. Ask: "What would you need to see to run a pilot?"

## 6. Landing page copy

**Headline:** Know the moment a reference label changes, and exactly what it means for yours.

**Sub-headline:** LabelLedger keeps a version-controlled ledger of FDA labels. It shows every change section by section, ranks it by clinical severity, and checks your labels for drift against their reference. Built for generic labeling teams.

**Three benefits**
- **See every change, word by word.** Section-aware redlines of every new version of the labels you watch. No more side-by-side PDFs.
- **Know what matters first.** Severity triage with the reason shown: boxed warning, contraindication, numeric dosing change, new risk language.
- **Prove your labels are in sync.** Statement-level drift reports against the reference, ready for your change record.

**How it works:** Watch your reference labels → we capture each new version → you get a ranked redline and a drift check → you record your assessment and export the evidence.

**Proof (real, not testimonial):**
> We compared a real 2012 warfarin label with the 2026 text: 88% parity, and the older label lacks the calciphylaxis and acute-kidney-injury warnings that were added since.

**Trust line:** Deterministic redlines from the official SPL. Any AI explanation is checked word for word against the source. Decision support; your experts decide.

**CTA:** Get a free drift snapshot for one of your products →

**FAQ**
- *Where does the data come from?* Public FDA labels on DailyMed, plus files you upload.
- *Do you use AI?* Only to explain changes. Every quoted sentence is verified against the redline, and severity is rule-based.
- *Is my data safe?* Your watchlists and assessments stay in your workspace. Label text is public.
- *Do you replace our RA review?* No. We make it faster and auditable.

No fabricated logos, customer counts or quotes, ever. The proof section uses only our own reproducible analyses.

## 7. Pilot proposal (one page)

> **LabelLedger pilot — {Company}**
>
> **Scope:** up to 25 of your US generic products and their reference labels, for 6 weeks.
>
> **You get:**
> - a drift report for each product at the start;
> - weekly change alerts with redlines;
> - a digest;
> - an assessment log;
> - an exit report: changes detected, hours saved (your estimate), findings acted on.
>
> **We ask:** a named owner, a 20-minute check-in every two weeks, and honest feedback. A reference call if you are satisfied.
>
> **Price:** ${pilot price} for the 6 weeks (credited to the first year if you continue).
>
> **Success criteria, agreed upfront:**
> 1. At least one actionable reference change surfaced that your team had not yet logged.
> 2. Time per reference update reduced by at least 50% (your estimate).
> 3. Weekly use by the owner.
>
> **Data:** public label data plus your watchlist and assessments. No patient data. Delete-on-request at any time.

## 8. Feedback form (pilot week 3 and exit)

1. How often did you open LabelLedger this week? (never / 1–2 / 3+)
2. Which finding was most useful? Paste it.
3. Any false or irrelevant findings? Paste them; we use them to improve rules.
4. Time spent per reference update before vs. now (minutes).
5. What would make you pay for this at {price}? What would stop you?
6. How disappointed would you be if you could no longer use it? (very / somewhat / not)
7. Who else at {company} or elsewhere should see this?

## 9. Validation thresholds (decided in advance)

| Stage | Metric | Threshold to continue | Evidence type |
|---|---|---|---|
| Problem | Qualified interviewees describing recurring manual effort or a late/missed update in the last 12 months | ≥ 6 of 10 | Interview log with quotes |
| Problem | Independent companies describing the same pain | ≥ 5 companies | Log |
| Solution | Snapshot findings rated "useful" by interviewees | ≥ 60% of reviewed findings | Snapshot reactions |
| Willingness to pay | Paid pilots | ≥ 3 at ≥ $200/month by day 60 | Invoices or Paddle receipts |
| Usage | Pilot owners active weekly | ≥ 2 of 3 by week 4 | App logs |
| Retention | Pilots converting to paid plans | ≥ 2 of 3 | Contracts |
| Pull | "Very disappointed" answer (Sean Ellis test) | ≥ 40% of active users | Feedback form |
| Revenue | MRR at day 90 | ≥ $1,500 (between conservative and base) | Billing |

**If customers reject the idea, find out why before pivoting:**
- wrong segment (already served);
- wrong job (monitoring isn't painful, drafting is);
- wrong price;
- wrong trust signals.

Log the reason in `10_STRATEGIC_RECORD.md`.

## 10. Objection handling

| Objection | Response (truthful) |
|---|---|
| "We already use {vendor}." | "Great. Is it covering your whole portfolio, including drift against your own labels? Many teams use us for the workflows their suite doesn't reach. If you're fully covered, I'd still love to learn what works." |
| "My team does it manually, it's fine." | "How long did the last update take, end to end? Was anything ever caught late?" (let the numbers speak; walk away if it truly is fine) |
| "Is it validated?" | "It's decision support with deterministic, auditable output and source checksums. We publish our evaluation results. Your SOP decides how it's used. We can provide a validation-support pack as we grow." |
| "You're a small company." | "True. That's why everything is transparent: open methodology, exportable data, no lock-in, and a pilot priced so you risk little." |
