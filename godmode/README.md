# PROJECT GODMODE

Strategy work for a one-person, AI-leveraged technology company. The founder is pharmacy-trained and based in Bangladesh.

**Start here:** [`dossier/00_EXECUTIVE_BRIEF.md`](dossier/00_EXECUTIVE_BRIEF.md). It gives the verdict, what is built, what is not yet true, and the one next action.

| Path | What it is | Reproduce |
|---|---|---|
| `dossier/` | Missions A–F plus the validation kit, company design, portfolio and IP, security, roadmap, and strategic record | Human-written; cites `engine/evidence.toml` |
| `engine/evidence.toml` | 25 researched claims, each with a source and a strength label | — |
| `engine/opportunities.toml` | 50 opportunities × 17 fields × 10 scored dimensions plus penalties | — |
| `engine/score.py` | Dependency-free scoring engine with Monte Carlo sensitivity | `python3 godmode/engine/score.py` (`--check` in CI) |
| `engine/results/` | Generated ranking (`ranking.md` / `.json`) and full opportunity cards (`opportunities.md`) | Generated |
| `finance/assumptions.toml` | Every financial assumption, explicit and arguable | — |
| `finance/model.py` | 36-month, three-scenario model with unit economics and sensitivity | `python3 godmode/finance/model.py` (`--check` in CI) |
| `../labelledger/` | The first venture's MVP (Mission G) | `cd labelledger && uv sync --extra dev && pytest` |

All scores and all financial inputs are **judgments or assumptions**, labelled as such. Replace them with evidence as it arrives, re-run the generators, and commit the results. CI keeps the outputs honest.
