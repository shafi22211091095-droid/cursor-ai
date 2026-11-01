# Contributing

Thanks for working on this. The guidance below is mostly about one thing:
**this repository's value is that a reviewer can trust its numbers.** Changes
are judged by whether they preserve that.

## Setup

```bash
make install        # .venv from the pinned uv.lock
make hooks          # pre-commit hooks (same checks CI runs)
source .venv/bin/activate
```

## Before you push

```bash
make check          # lint + mypy --strict + tests with coverage
```

A clean `make check` should mean a green CI. If it does not, that is a bug in
the setup worth reporting.

## Non-negotiables

These are enforced in code and in CI. A change that breaks one needs a very good
argument and a `DECISIONS.md` entry.

1. **No GUI tool in the critical path.** If a number in a manuscript could come
   from a tool you click, it is not reproducible. See `DECISIONS.md` D-005.
2. **No magic numbers in code.** Every scientific parameter comes from
   `config.yaml`. If you find yourself typing a threshold into a function, add a
   config field instead.
3. **Never invent data.** If a value is unavailable, report it as unavailable.
   `StructureMetadata` renders `unavailable`; the protonation backends publish a
   `limitation()`; the enrichment control reports "not performed" rather than
   fabricating actives. Follow those patterns.
4. **Never silently substitute a method.** `get_protonator` raises on an unknown
   method rather than falling back to a default. Unknown rule sets and unknown
   alert catalogues raise. Do the same for anything you add.
5. **No bare `except`.** Catch specific exceptions. Where a third-party library
   raises types you cannot enumerate, catch `Exception` with a `# noqa: BLE001`
   and a comment saying why.
6. **Caveats travel with the numbers.** A new caveat belongs in
   `_collect_caveats` so it reaches the workbook README, the SI header and the
   figure captions — not in a docstring only.

## Adding things

### A new ADMET rule set

Add a function to `src/npcadd/chem/rules.py` returning per-criterion verdicts
(not just a boolean), with the primary citation in its docstring. Register it in
`RULE_SETS` and add the name to the `RuleSetName` literal in `config.py`. Add a
test with a compound that passes and one that fails on a *named* criterion.

### A new ML/QSPR predictor

Implement the `MLPredictor` protocol in `src/npcadd/chem/admet_ml.py`. A
prediction must be inseparable from its applicability-domain verdict — return a
`PredictionSet` of `PropertyPrediction`, each carrying an `ADVerdict`. If you
ship training SMILES, use `TanimotoKNNDomain`; if you only have published
descriptor ranges, use `DescriptorBoxDomain`. Register in `ML_PREDICTORS`.

### A new protonation backend

Implement the `Protonator` protocol in `src/npcadd/chem/protonation.py`,
including `limitation()` and `state_is_uncertain()`. The `limitation()` string
is propagated verbatim into the run report, so write it for a reviewer, not for
a developer. Register in `PROTONATORS`.

### A new figure

Add a function to `src/npcadd/report/figures.py` returning a `FigureResult`.
Use the Okabe-Ito palette and `CLASS_COLOURS` so classes read consistently
across figures. The caption must state what the figure shows **and** what it
does not license the reader to conclude; `_save` appends the parameters and
software versions automatically.

## Testing expectations

- Pure functions need unit tests. Coverage gate is 80% and CI enforces it.
- Scientific functions should be tested against an independent implementation
  where one exists (ROC-AUC is checked against `sklearn` to 1e-12) or against
  analytically known values (BEDROC at 1.0 / 0.0 for perfect / worst rankings).
- Property-based tests (Hypothesis) are used for descriptors and
  standardisation; invariants beat examples for those.
- Determinism changes must keep `tests/test_determinism.py` green. Note that
  the golden test has a companion asserting a *changed seed changes the output*,
  so it cannot pass vacuously — keep that property if you touch it.
- Mark slow tests `@pytest.mark.slow` and network tests `@pytest.mark.network`.

## Methodological changes

Any change to *how a number is computed* needs a `DECISIONS.md` entry: the
decision, the alternatives rejected, the reasoning, and where it is enforced.
Append; do not rewrite. If you reverse an earlier decision, add a new entry that
says so and references the old one.

If you make something less rigorous for speed — a smaller null set, fewer
conformers — say so in the entry and make sure the consequence surfaces in the
output (e.g. the p-value resolution floor is already printed in every table).

## Commits

Logical units, imperative subject line, and a body that explains *why*. If a
change was driven by a measurement, put the measurement in the message — several
decisions in `DECISIONS.md` are traceable to numbers recorded in commit bodies.

## Archiving a release on Zenodo

For a manuscript, the archived artefact must let a reader reproduce the numbers.

1. **Pin the container digest** and commit it:
   ```bash
   make docker-pin && make docker-check-pinned
   ```
   Record the digest and date in `DECISIONS.md`. An unpinned image means the
   archive is not reproducible across time.

2. **Run the pipeline cleanly and verify it:**
   ```bash
   make clean && make reproduce
   npcadd verify-provenance runs/<run-id>/provenance.json
   ```

3. **Check what is in the tree.** `data/raw/` is read-only by convention and a
   pre-commit hook rejects modifications to it. Confirm you have the right to
   redistribute every input; PDB coordinate data carries no copyright
   restriction, but your own raw spectra may have constraints.

4. **Confirm no synthetic data is being passed off as real.** If `data/sample/`
   is still wired into your config, the outputs carry a `SYNTHETIC_PLACEHOLDER`
   banner — that banner must not reach a manuscript.

5. **Link GitHub to Zenodo** (zenodo.org → GitHub → enable this repository),
   then publish a GitHub release. Zenodo archives the tag and mints a DOI.

6. **Update `CITATION.cff`** with the DOI and version, commit, and make a
   follow-up release so the citation metadata in the archive is correct.

7. **Deposit alongside the code:** `provenance.json` from the reported run, the
   exact `config.yaml`, `uv.lock`, and `reports/VALIDATION.md`. Those four files
   are what make the archived result checkable rather than merely available.

## Reporting a problem

Most valuable: a case where the pipeline produces a number that is wrong, or
presents an uncertain number as certain. Include the config, the run ID and the
`provenance.json`.
