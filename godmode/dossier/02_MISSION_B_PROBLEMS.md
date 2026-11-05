# Mission B — 20 neglected or underserved customer problems

**Evidence grades**

| Grade | Meaning |
|---|---|
| **A** | Primary source: a regulator or a peer-reviewed study |
| **B** | Secondary source, or a vendor's own claim that still points to demand |
| **C** | Indirect workflow evidence, such as job postings |
| **H** | Hypothesis, not yet evidenced |

Evidence IDs point to `../engine/evidence.toml`.

**What none of these entries proves.** A recorded problem does not show that people will pay to solve it. Willingness to pay is proven only by money changing hands. `07_VALIDATION_KIT.md` is designed to get that proof. The threshold for each problem's paid demand is written into its row.

| # | Problem | Who feels it | Evidence | Grade | What would prove paid demand | Maps to |
|---|---|---|---|---|---|---|
| 1 | Generic labels drift out of sync with their reference label | Generic labeling/RA teams; prescribers and patients downstream | FDA puts monitoring on ANDA holders [E01]; 68% of multi-source generics had safety-labeling discrepancies, some from outdated text [E02]; labels from different labelers span 2012–2026 in our own sample [E23] | **A** | 3 paid pilots ≥ $200/mo for drift reports | O01 |
| 2 | Comparing long labels by hand, and building the side-by-side comparison every labeling submission needs | Labeling specialists | Submission content requires a comparison with RLD labeling [E01] | **A** (process) | Measured hours per comparison in 5 interviews; pilots that replace the manual step | O01 |
| 3 | Free FDA tooling leaves out generics, non-safety sections and pre-2016 history | Anyone tracking label history | SrLC scope [E03] | **A** | Search demand and sign-ups for public label-history pages | O01 |
| 4 | Label-intelligence software is enterprise-priced and quote-only | Small and mid-size generics companies, consultancies | Vendors' scale and quote-only pricing [E04, E19] | **B** | Prospects who say a current tool is "too expensive" or "too big" | O01 |
| 5 | Enterprise RIM is unaffordable below large-pharma scale | Emerging-market and small companies | Enterprise RIM per-seat pricing claim [E10] | **B** (vendor) | 5 exporters share their spreadsheet trackers and accept a paid pilot | O02 |
| 6 | Emerging-market registration requirements are fragmented and undocumented | Export RA teams, BD teams | Harmonisation only within regional blocs [E11] | **B** | Search demand for country-requirement queries; 20 paid self-serve users | O03 |
| 7 | Export RA teams juggle registrations, renewals and variations across 28+ countries | Bangladeshi and Indian exporters | Job postings describing exactly this work [E12] | **C** | Time-tracking from 3 teams; renewal misses in the last year | O02 |
| 8 | Export regulatory workload is growing in Bangladesh | Bangladeshi exporters | Exports +12% to $238.5M, 130+ countries [E07] | **B** | — (context, not a product) | O02, O03 |
| 9 | Patent-regime change after LDC graduation (now Nov 2029) | Bangladeshi manufacturers | Deferral to 2029 [E08] | **A** (timeline) | Policy and industry demand for patent landscaping by 2027–28 | Future (patent intelligence) |
| 10 | Deficiency-letter queries with hard deadlines | Export RA teams | Workflow described in postings [E12] | **C/H** | 20 anonymised real queries collected; drafting time measured | O05 |
| 11 | Nitrosamine risk assessments for every product | CMC/QA teams | FDA CPCA framework and deadlines [E16] | **A** (mandate) | Exporters receiving nitrosamine queries from regulated markets | O08 |
| 12 | Mandatory weekly literature monitoring in pharmacovigilance | Small MAHs | AI tools already sold for this [E17] | **B** | — (crowded; low priority) | O06 |
| 13 | Periodic safety reports are long and repetitive | PV teams | Vendors sell automated PSUR/PBRER [E18] | **B** | PV vendors willing to pilot | O07 |
| 14 | Moving product information to structured EU ePI (FHIR) | EU MAHs, labeling teams | EMA ePI roadmap and FHIR standard [E14] | **A** (trend) | EU generics asking for SmPC version tracking | O01 (EU source) |
| 15 | A one-page patient medication information format may become mandatory in the US | All US Rx manufacturers | Proposed rule, not final [E15] | **A** (uncertain timing) | Final rule published, then a demand spike | O01 adjacency |
| 16 | New continental and reliance procedures in Africa | Exporters to Africa | AMA operational; continental listing [E20] | **A** | Exporters asking which products qualify for reliance routes | O03 |
| 17 | Safety teams need continuously updated label-derived safety data | Large-pharma PV, safety researchers | GSK's PVLens built for exactly this [E22] | **A** | API interest from safety-analytics teams | O01 (API) |
| 18 | AI regulatory tooling funded mostly for big pharma, leaving mid-market gaps | Mid-market generics | Funding round targets [E13] | **B** | Mid-market buyers lacking AI tools in interviews | O01–O05 |
| 19 | Computational natural-product papers often lack validated methods | Academic researchers, reviewers, journals | Founder's documented analysis in `DECISIONS.md` / `LIMITATIONS.md` | **H** | 100 sign-ups from one tutorial; 5% paid | O18, O19 |
| 20 | Small beauty and CPG brands launch without regulatory checks | Indie brands selling cross-border | $4M seed for AI compliance review in this space [E21] | **B** | App-store installs and paid conversion | O26 |

## What this table tells us

* **Problems 1–4 are the only cluster graded A on both the obligation and the gap.** They all point at O01. That is the strongest evidence-based reason to start there.
* **Problems 5–8 and 10 are real workflow pain with weak willingness-to-pay evidence.** They are where the founder's Bangladesh network gives an edge. They point at product #2.
* **Problems 11–13 have real mandates but are crowded or past their urgency peak.** They are adjacencies, not starting points.
* **Problems 14–17 are tailwinds.** They make O01's data asset more valuable over time: EU ePI, PMI, safety-data APIs.
