# LabelLedger

**Every version. Every change. Explained.**
Version control, section-aware redlines and drift detection for regulated drug labels.

Generic-drug labels must keep pace with their reference label. Today that work means
comparing 30–80 page labels by hand, missing updates, and rebuilding side-by-side
comparisons for every labeling submission. LabelLedger turns FDA Structured Product
Labeling (SPL) into a version-controlled archive and answers three questions in seconds:

1. **What changed between two versions of a label?** A section-aware, word-level
   redline, triaged by clinical severity with the reasons shown.
2. **Has my label drifted from its reference?** A statement-by-statement parity check
   that works even when the two labels are structured differently (PLR vs. old format).
3. **What changed this week across everything I watch?** Watchlists, alerts and a digest.

> Status: **MVP, private beta.** The engine is tested on real DailyMed labels. The
> DailyMed client is tested against recorded responses; run it once against the live
> service before relying on it (the build sandbox could not reach DailyMed).

## What it does today

| Capability | Where |
|---|---|
| Hardened SPL parser (no DTD/entities/network, size-capped): metadata, ANDA/NDA numbers, products, LOINC section tree, tables, lists, Highlights kept separate | `spl.py` |
| Section catalogue: 49 LOINC codes → 23 clinical families → 3 risk tiers; unclassified subsections inherit their parent | `sections.py` |
| Version redline: recursive section alignment (exact → fuzzy title → content, moves detected), line pairing, word-level diff with exact reconstruction (property-tested) | `diff.py` |
| Severity triage: CRITICAL / MAJOR / MODERATE / MINOR / EDITORIAL from explicit rules (boxed warning, contraindications, numeric dosing changes, risk vocabulary, negations); every rating carries its reasons | `severity.py` |
| Drift detection: reference vs. follower label, sentence coverage with fuzzy pre-filter, missing / changed / extra findings, parity per family, Recent Major Changes after the follower's date | `drift.py` |
| Summaries: deterministic by default; optional Claude summaries as structured output, **every quote verified against the redline**, unverifiable claims flagged, refusals and API errors degrade gracefully | `ai.py` |
| Reports: standalone HTML (no scripts, light/dark, print-ready), Markdown, JSON | `report.py` |
| Archive: SQLite, gzip XML with SHA-256 integrity checks, watchlist, changes, alerts | `store.py` |
| DailyMed client: polite rate limiting, retries with `Retry-After`, size caps | `dailymed.py` |
| Web app: dashboard, ad-hoc compare, archive, drift, exports, JSON API, Basic auth, CSRF, strict CSP | `web/` |
| CLI with `--fail-on` for automation | `cli.py` |

## Quickstart

```bash
cd labelledger
uv venv -p 3.11 .venv && uv sync --frozen --extra dev
source .venv/bin/activate

# Redline two versions (Markdown to stdout, or --format html/json --out FILE)
labelledger diff tests/fixtures/spl/warfarin_tya_v2_2012.xml tests/fixtures/spl/warfarin_remedyrepack_v20_2026.xml

# Drift: how far has a 2012 label fallen behind the 2026 text?
labelledger drift tests/fixtures/spl/warfarin_remedyrepack_v20_2026.xml \
                  tests/fixtures/spl/warfarin_tya_v2_2012.xml --min major

# Archive, watch, poll DailyMed, digest
labelledger ingest tests/fixtures/spl/*.xml
labelledger watch add 1244c338-9140-425a-bf2a-9da6b4ac0287 --role reference
labelledger poll
labelledger digest --days 7

# Web app on http://127.0.0.1:8000
labelledger serve
```

### What the drift check finds on real data

Comparing a 2012 warfarin label with the 2026 text (both real DailyMed SPLs) gives
**88% parity** across 4,868 days and surfaces, among others, statements the older label
lacks: the calciphylaxis warning, the acute kidney injury warning, limb ischemia in
HIT/HITTS, the renal-impairment dosing statement, and the Pregnancy and Lactation
Labeling Rule wording. A label compared with itself scores 100% with zero findings.
(These are repackager labels; this demonstrates the engine, not anyone's compliance.)

## Configuration

| Variable | Purpose |
|---|---|
| `LABELLEDGER_DB` | SQLite path (default `labelledger.db`) |
| `LABELLEDGER_BASIC_AUTH` | `user:password`; required to bind beyond loopback |
| `LABELLEDGER_AI` | `auto` (default: on if `ANTHROPIC_API_KEY` is set), `on`, `off` |
| `LABELLEDGER_AI_MODEL` | Claude model for summaries (default `claude-opus-5-5`) |
| `ANTHROPIC_API_KEY` | Enables AI summaries; never required |
| `LABELLEDGER_CONTACT` | Optional contact added to the DailyMed User-Agent |

AI summaries send only the computed redline of **public** label text to the Claude API,
use structured outputs with server-side refusal fallback, cache the system prompt, and
are verified quote-by-quote before display. Severity is never set by the model.

## Deploy

```bash
docker build -t labelledger labelledger
docker run -p 8000:8000 -v ll-data:/data -e LABELLEDGER_BASIC_AUTH='team:<long-random>' labelledger
# schedule polling, e.g. every 6 hours:
docker run --rm -v ll-data:/data labelledger labelledger poll
```

A single small VM or container host is enough for the first hundreds of customers;
see `godmode/dossier/06_execution_blueprint.md` for the scaling path (PostgreSQL,
multi-tenant auth, background workers).

## Quality gates

`ruff`, `mypy --strict`, 84 tests (property-based tests for the redline invariants,
real-label fixtures, mocked DailyMed and Claude clients, web security tests), ≥88% branch
coverage enforced in CI (currently 91%). Fixture provenance: `tests/fixtures/PROVENANCE.md`.

## Known limitations (MVP)

* Single workspace: no per-user accounts or tenant isolation yet (Basic auth for a private beta).
* DailyMed serves current versions only; history accrues from the day a label is watched.
  Bulk import of historical versions is on the roadmap.
* Highlights are captured but not diffed (they restate the full prescribing information).
* Drift thresholds were tuned on warfarin and metformin labels; an evaluation set across
  50+ drug pairs is the next quality milestone before claiming precision/recall numbers.
* US SPL only. EU SmPC/ePI (FHIR) support is the planned second source.
* Decision support only — not legal or regulatory advice, never auto-submits anything.
