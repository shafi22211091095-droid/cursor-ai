# Mission A — Global intelligence: 50 opportunities

**The full catalogue is generated:** `../engine/results/opportunities.md` has 50 cards with all 17 required fields, in ranked order. Its source is the dataset `../engine/opportunities.toml`. This page explains how the 50 were found and what the landscape looks like.

## Method

1. **Founder-asset scan.** The founder has:
   - pharmaceutical-sciences training
   - research rigor (the `npcadd` pipeline in this repository: validation gates, provenance, honest limitations)
   - a Bangladesh base, with a pharmacy-graduate network across roughly 200+ manufacturers and very low living costs
   - access to modern AI tools
   - little assumed capital

   Opportunities that use these assets were weighted in the search, not in the scores.

2. **Problem-first search** across the 25 industries listed in the brief. Each candidate needed:
   - an identifiable buyer
   - recurring, expensive, under-served pain
   - an automation angle

   "Popular category" alone was not enough.

3. **Evidence pass.** Twenty-five claims were researched on the web on 2026-11-05 and registered with strength labels (`../engine/evidence.toml`). Where no evidence was gathered, the card says *judgment only*.

4. **Deliberate breadth.** About half the list is outside pharma: Bangladesh business software, horizontal developer and compliance tools, academic software, agritech and climate. This tests whether domain advantage is real or just familiarity.

## The landscape in one table

| Cluster | Count | Best | Why the cluster scores the way it does |
|---|---|---|---|
| Pharma regulatory operations | 9 | O01 LabelLedger (6.95) | Regulator-defined, recurring obligations; public structured data; founder fit; buyers already pay consultants |
| Pharma safety and quality (PV, QMS, CMC) | 10 | O06 PV literature (5.80) | Real mandates, but crowded with funded tools and heavy on GxP validation |
| Pharma commercial / medical | 5 | O16 Tenders (5.60) | Revenue-linked pain; fragile data sources or enterprise sales |
| Research software | 6 | O19 Rigor checker (5.30) | Strong founder fit (npcadd), weak budgets |
| Bangladesh business software | 7 | O32 Export docs (5.40) | Big local need, low willingness to pay, no edge for this founder |
| Horizontal dev/compliance tools | 6 | O36 API diff (5.50) | Commodity markets; the same engine with none of the domain moat |
| Consumer, edtech, health, agritech | 7 | O25 Gulf exam prep (5.85) | Fast to start, weak moats, small ceilings or slow B2B2C |

## Technology shifts that open these opportunities now

* **Structured product information is becoming machine-readable worldwide.** US SPL is already structured XML. The EU is moving to FHIR-based ePI, with rollout reported from Q4 2026 **[E14]**. FDA's one-page Patient Medication Information rule is still only proposed **[E15]**. Diffing, monitoring and drift detection become software problems instead of reading problems.
* **LLMs can explain regulatory text, if they are kept honest.** The winning pattern is deterministic computation, then an LLM explanation, then automatic verification of every quote. That is the architecture LabelLedger ships.
* **Regulatory AI is funded, mostly for big pharma** **[E13]**. The mid-market and emerging-market generics segments are left to consultancies and spreadsheets.
* **African and Asian regulatory consolidation.** The African Medicines Agency is now operational, reliance pathways are spreading, and ASEAN uses ACTD **[E11, E20]**. That creates new procedures and new intelligence needs for exporters.
* **Bangladesh's LDC graduation is deferred to Nov 2029 [E08].** Patent-regime and regulatory upgrades move from emergency to a three-year preparation window.

## Where the strongest ideas cluster

The top six are all regulatory operations for generic-medicine makers (O01, O02, O05, O03, O04, O50). That is not a coincidence. This is where:

* a regulator defines the obligation, so willingness to pay rests on compliance rather than convenience;
* the inputs are public or semi-public, so AI leverage is high and data costs are near zero;
* buyers are global but under-served below enterprise scale;
* the founder's training lets them judge correctness, which matters most when a mistake carries safety consequences.

Because the top six share a customer, they form a **portfolio**, not six separate bets. See `09_PORTFOLIO_IP_SECURITY_ROADMAP.md`.
