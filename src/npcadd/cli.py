"""Typer CLI.

Stage state is passed between commands through the run directory, so each
command can be run independently and ``run-all`` is simply all of them in
order. The run directory is keyed by a run ID derived from the config hash, so
re-running with an unchanged config resumes rather than duplicating work.

One rule is enforced at the CLI level and not merely documented: ``dock``
refuses to run unless validation has passed for this config, because a
screening result produced without a validation gate is not interpretable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from . import __version__
from .config import Config, load_config
from .hashing import hash_file
from .logging_utils import configure_logging, get_logger, new_run_id
from .provenance import Provenance

app = typer.Typer(
    name="npcadd",
    help=(
        "Reproducible natural-product computer-aided drug discovery pipeline.\n\n"
        "Every parameter comes from config.yaml; nothing is hard-coded. Run "
        "'npcadd run-all --config config.yaml' for the full pipeline."
    ),
    no_args_is_help=True,
    add_completion=False,
)

log = get_logger("cli")

ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        "-c",
        help="Path to config.yaml, the single source of truth for the run.",
        exists=False,
        dir_okay=False,
    ),
]
RunIdOption = Annotated[
    str | None,
    typer.Option(
        "--run-id",
        help="Reuse an existing run directory. Defaults to a config-derived ID.",
    ),
]

STATE_FILE = "stage_state.json"


class StageStateError(RuntimeError):
    """A command was run before a stage it depends on."""


def _setup(config: Path, run_id: str | None) -> tuple[Config, str, Path, Provenance]:
    """Load config, create the run directory, start logging and provenance."""
    try:
        cfg = load_config(config)
    except (FileNotFoundError, ValueError) as exc:
        typer.secho(f"Configuration error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    config_hash = cfg.config_hash()
    resolved_run_id = run_id or f"cfg-{config_hash[:12]}"
    out_dir = cfg.run_dir(resolved_run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    configure_logging(resolved_run_id, log_file=out_dir / "run.jsonl")

    # The resolved config and its hash are copied into the run directory, so an
    # output directory is self-describing even if config.yaml later changes.
    (out_dir / "resolved_config.yaml").write_text(cfg.to_yaml(), encoding="utf-8")
    (out_dir / "config.sha256").write_text(f"{config_hash}\n", encoding="utf-8")

    provenance = Provenance.start(resolved_run_id, cfg)
    provenance.add_input(cfg.ligands.input_csv)
    return cfg, resolved_run_id, out_dir, provenance


def _load_state(out_dir: Path) -> dict[str, Any]:
    path = out_dir / STATE_FILE
    if not path.is_file():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data


def _save_state(out_dir: Path, **updates: Any) -> dict[str, Any]:
    state = _load_state(out_dir)
    state.update(updates)
    (out_dir / STATE_FILE).write_text(
        json.dumps(state, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return state


def _fail(message: str, code: int = 1) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


def _resolve_run_dir(config: Path, run_id: str | None) -> tuple[Config, str, Path]:
    """Load config and locate its run directory, with no side effects."""
    cfg = load_config(config)
    resolved = run_id or f"cfg-{cfg.config_hash()[:12]}"
    return cfg, resolved, cfg.run_dir(resolved)


@app.callback()
def main() -> None:
    """npcadd command-line interface."""
    from rdkit import RDLogger

    # Third-party imports re-enable RDKit's C++ logger; npcadd reports
    # rejections through its own structured log with reasons attached.
    RDLogger.DisableLog("rdApp.*")


@app.command()
def version() -> None:
    """Print the npcadd version and the versions of the scientific stack."""
    from .provenance import package_versions

    typer.echo(f"npcadd {__version__}")
    for name, value in package_versions().items():
        typer.echo(f"  {name}: {value}")


_ACTIVES_GUIDE = """# Known actives for the enrichment control

`npcadd` reports ROC-AUC, BEDROC and enrichment factors only when you supply a
set of experimentally confirmed actives for a target (DECISIONS.md D-020); it
never fabricates one. Fill in `known_actives_template.csv` in this directory.

## Columns

Only `name` and `smiles` are read; the rest are recorded for your own provenance
and ignored by the pipeline.

| column | required | meaning |
|---|---|---|
| `name` | yes | your label for the active |
| `smiles` | yes | its structure (standardised on load) |
| `chembl_id` | no | source identifier, for traceability |
| `pchembl_value` | no | -log10(IC50/Ki/Kd in M); 7 = 100 nM |
| `source` | no | database / publication the value came from |

## How to populate it (example: ABL1, the bundled target)

ABL1 is ChEMBL target **CHEMBL1862** (UniProt P00519). Pull confirmed actives,
for example `standard_type = IC50`, `pchembl_value >= 7`, no data-validity flag,
de-duplicated by parent molecule, and **excluding the redocking reference
(imatinib)** to avoid leakage. Two real example rows:

```csv
name,smiles,chembl_id,pchembl_value,source
Imatinib,Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1,CHEMBL941,7.42,ChEMBL
CHEMBL29197,COc1cc2ncnc(Nc3cccc(Br)c3)c2cc1OC,CHEMBL29197,7.52,ChEMBL
```

Then set in your config:

```yaml
validation:
  known_actives_csv: reports/known_actives.csv   # your filled-in file
```

and re-run `npcadd validate-docking`. See `docs/RESEARCH_ROADMAP.md` (project A1)
for the full dossier, and LIMITATIONS.md 4.1 for what an enrichment number
against the bundled decoy pool does and does not establish.
"""


@app.command()
def doctor(config: ConfigOption) -> None:
    """Preflight: check config, environment and inputs before a long run."""
    import importlib.util
    import shutil

    from .chem.np_likeness import np_model_available
    from .provenance import package_versions

    try:
        cfg = load_config(config)
    except (FileNotFoundError, ValueError) as exc:
        _fail(f"Configuration error: {exc}", code=2)
        return

    checks: list[tuple[str, str, str]] = []

    def add(status: str, label: str, detail: str = "") -> None:
        checks.append((status, label, detail))

    add("PASS", "config loads", f"{len(cfg.receptors)} receptor(s), study '{cfg.study}'")
    add(
        "PASS" if cfg.ligands.input_csv.is_file() else "FAIL",
        "ligand input CSV",
        str(cfg.ligands.input_csv),
    )
    if importlib.util.find_spec("vina") is not None:
        import vina

        add("PASS", "AutoDock Vina", str(getattr(vina, "__version__", "present")))
    else:
        add("FAIL", "AutoDock Vina", "not importable")
    np_ok = np_model_available()
    add(
        "PASS" if np_ok else "WARN",
        "NP-likeness model",
        "available" if np_ok else "NP_Score model not found; np-likeness will be skipped",
    )
    for rc in cfg.receptors:
        if rc.local_structure is not None:
            if rc.local_structure.is_file():
                add("PASS", f"receptor {rc.id} structure", str(rc.local_structure))
            else:
                add(
                    "WARN" if cfg.allow_network else "FAIL",
                    f"receptor {rc.id} structure",
                    "will fetch from RCSB" if cfg.allow_network else "missing, network disabled",
                )
        else:
            add(
                "PASS" if cfg.allow_network else "FAIL",
                f"receptor {rc.id} structure",
                f"will fetch {rc.pdb_id} from RCSB"
                if cfg.allow_network
                else "no local_structure, network disabled",
            )
        if rc.cocrystal_ligand is None:
            add(
                "WARN",
                f"receptor {rc.id} redocking gate",
                "no cocrystal_ligand: the mandatory gate cannot run",
            )
        if rc.directionality_conflict:
            add(
                "WARN",
                f"receptor {rc.id} directionality",
                "activation phenotype vs inhibitor-bound structure",
            )
    if cfg.validation.known_actives_csv is None:
        add(
            "WARN",
            "enrichment control",
            "no known_actives_csv; run 'npcadd actives-template' to populate it",
        )
    else:
        add(
            "PASS" if cfg.validation.known_actives_csv.is_file() else "FAIL",
            "known actives CSV",
            str(cfg.validation.known_actives_csv),
        )
    cfg.paths.runs.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(cfg.paths.runs).free / 1e9
    add(
        "PASS" if free_gb > 1.0 else "WARN",
        "disk headroom",
        f"{free_gb:.1f} GB free under {cfg.paths.runs}",
    )
    versions = list(package_versions().items())
    add("PASS", "scientific stack", ", ".join(f"{k} {v}" for k, v in versions[:4]))

    colour = {"PASS": typer.colors.GREEN, "WARN": typer.colors.YELLOW, "FAIL": typer.colors.RED}
    for status, label, detail in checks:
        typer.secho(f"  {status:4} {label}" + (f" - {detail}" if detail else ""), fg=colour[status])
    n_fail = sum(1 for status, _, _ in checks if status == "FAIL")
    n_warn = sum(1 for status, _, _ in checks if status == "WARN")
    typer.echo(f"\n{len(checks)} checks: {n_fail} FAIL, {n_warn} WARN.")
    if n_fail:
        raise typer.Exit(code=1)


@app.command("actives-template")
def actives_template_cmd(config: ConfigOption) -> None:
    """Write a known-actives CSV template and guide to populate the enrichment control."""
    try:
        cfg = load_config(config)
    except (FileNotFoundError, ValueError) as exc:
        _fail(f"Configuration error: {exc}", code=2)
        return
    cfg.paths.reports.mkdir(parents=True, exist_ok=True)
    template = cfg.paths.reports / "known_actives_template.csv"
    template.write_text("name,smiles,chembl_id,pchembl_value,source\n", encoding="utf-8")
    guide = cfg.paths.reports / "known_actives_README.md"
    guide.write_text(_ACTIVES_GUIDE, encoding="utf-8")
    typer.secho("Wrote known-actives template.", fg=typer.colors.GREEN)
    typer.echo(f"  Template: {template}")
    typer.echo(f"  Guide:    {guide}")
    typer.echo("  Fill it, set validation.known_actives_csv in your config, then re-validate.")


@app.command()
def scaffolds(
    config: ConfigOption,
    run_id: RunIdOption = None,
    cutoff: Annotated[float, typer.Option(help="Butina Tanimoto-distance cutoff.")] = 0.35,
) -> None:
    """Report Murcko-scaffold and Butina-cluster diversity of the ranked shortlist."""
    import pandas as pd

    from .chem.scaffolds import analyse_scaffolds

    try:
        cfg, _resolved, out_dir = _resolve_run_dir(config, run_id)
    except (FileNotFoundError, ValueError) as exc:
        _fail(f"Configuration error: {exc}", code=2)
        return
    ranked_path = out_dir / "ranked_compounds.csv"
    admet_path = out_dir / "admet_results.csv"
    if not ranked_path.is_file() or not admet_path.is_file():
        _fail("Need ranked_compounds.csv and admet_results.csv; run 'npcadd rank' first.", code=3)
        return
    ranked = pd.read_csv(ranked_path, keep_default_na=False)
    admet = pd.read_csv(admet_path, keep_default_na=False)
    smiles_by = {str(r["ligand_id"]): str(r["smiles"]) for _, r in admet.iterrows()}
    smiles = [smiles_by[str(lid)] for lid in ranked.get("ligand_id", []) if str(lid) in smiles_by]
    analysis = analyse_scaffolds(smiles, cluster_cutoff=cutoff)
    typer.secho(
        f"Scaffold diversity of {analysis.n_parsed} ranked compound(s):", fg=typer.colors.GREEN
    )
    for key, value in analysis.as_record().items():
        typer.echo(f"  {key}: {value}")
    cfg.paths.reports.mkdir(parents=True, exist_ok=True)
    out = cfg.paths.reports / "scaffold_diversity.csv"
    counts = analysis.scaffold_counts
    ordered = sorted(counts, key=lambda name: -counts[name])
    pd.DataFrame(
        [(name, counts[name]) for name in ordered],
        columns=["murcko_scaffold", "count"],
    ).to_csv(out, index=False, lineterminator="\n")
    typer.echo(f"  Scaffold counts: {out}")


@app.command("html-report")
def html_report_cmd(
    config: ConfigOption,
    run_id: RunIdOption = None,
    top: Annotated[int, typer.Option(help="How many top-ranked rows to table.")] = 10,
) -> None:
    """Render a single self-contained HTML summary of a finished run."""
    from .report.html import render_html_summary

    try:
        cfg, resolved, out_dir = _resolve_run_dir(config, run_id)
    except (FileNotFoundError, ValueError) as exc:
        _fail(f"Configuration error: {exc}", code=2)
        return
    try:
        path = render_html_summary(cfg, resolved, out_dir, cfg.paths.reports, top_n=top)
    except FileNotFoundError as exc:
        _fail(str(exc), code=3)
        return
    typer.secho(f"Wrote HTML summary: {path}", fg=typer.colors.GREEN)


@app.command()
def dossiers(
    config: ConfigOption,
    run_id: RunIdOption = None,
    top: Annotated[int, typer.Option(help="How many top-ranked compounds to card.")] = 5,
) -> None:
    """Write a per-compound markdown evidence card for the top-ranked hits."""
    from .report.dossier import write_dossiers

    try:
        cfg, _resolved, out_dir = _resolve_run_dir(config, run_id)
    except (FileNotFoundError, ValueError) as exc:
        _fail(f"Configuration error: {exc}", code=2)
        return
    try:
        paths = write_dossiers(cfg, out_dir, cfg.paths.reports, top_n=top)
    except FileNotFoundError as exc:
        _fail(str(exc), code=3)
        return
    typer.secho(f"Wrote {len(paths)} dossier card(s).", fg=typer.colors.GREEN)
    for path in paths:
        typer.echo(f"  {path}")


@app.command("prepare-ligands")
def prepare_ligands_cmd(config: ConfigOption, run_id: RunIdOption = None) -> None:
    """Stage 1: standardise, protonate, embed and export ligands to PDBQT."""
    from .stages.ligands import prepare_ligands

    cfg, _resolved, out_dir, prov = _setup(config, run_id)
    try:
        ligands, summary = prepare_ligands(cfg, out_dir)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        _fail(f"Ligand preparation failed: {exc}")
        return

    prov.add_stage("prepare-ligands", summary.as_metrics())
    prov.add_outputs([summary.manifest_path, summary.rejected_path])
    prov.write(out_dir / "provenance.json")
    _save_state(out_dir, ligands_manifest=summary.manifest_path)

    typer.secho(
        f"Prepared {summary.n_accepted}/{summary.n_input} ligands ({summary.n_rejected} rejected).",
        fg=typer.colors.GREEN,
    )
    if summary.n_rejected:
        typer.echo(f"  Rejection reasons: {summary.rejection_counts}")
        typer.echo(f"  Details: {summary.rejected_path}")
    typer.echo(f"  Manifest: {summary.manifest_path}")
    _ = ligands


@app.command("prepare-receptor")
def prepare_receptor_cmd(config: ConfigOption, run_id: RunIdOption = None) -> None:
    """Stage 2: fetch, clean, protonate and export receptors to PDBQT."""
    from .stages.receptor import ReceptorPrepError, prepare_all_receptors

    cfg, _resolved, out_dir, prov = _setup(config, run_id)
    try:
        receptors = prepare_all_receptors(cfg, out_dir)
    except ReceptorPrepError as exc:
        _fail(f"Receptor preparation failed:\n{exc}")
        return

    import pandas as pd

    table = out_dir / "receptor_provenance.csv"
    pd.DataFrame([r.as_record() for r in receptors]).to_csv(table, index=False, lineterminator="\n")

    prov.add_stage(
        "prepare-receptor",
        {
            "n_receptors": len(receptors),
            "receptors": [r.receptor_id for r in receptors],
            "n_warnings": sum(len(r.warnings) for r in receptors),
        },
    )
    prov.add_outputs([r.pdbqt_path for r in receptors] + [table])
    prov.write(out_dir / "provenance.json")
    _save_state(out_dir, receptor_provenance=table.as_posix())

    typer.secho(f"Prepared {len(receptors)} receptor(s).", fg=typer.colors.GREEN)
    for receptor in receptors:
        typer.echo(
            f"  {receptor.receptor_id} ({receptor.pdb_id}, {receptor.activity}): "
            f"box centre ({', '.join(f'{c:.2f}' for c in receptor.box.center)}), "
            f"size ({', '.join(f'{s:.2f}' for s in receptor.box.size)})"
        )
        for warning in receptor.warnings:
            typer.secho(f"    ! {warning}", fg=typer.colors.YELLOW)
    typer.echo(f"  Provenance table: {table}")


@app.command("validate-docking")
def validate_docking_cmd(config: ConfigOption, run_id: RunIdOption = None) -> None:
    """Stage 3: MANDATORY validation. Redocking gate, decoy enrichment, null set."""
    from .stages.receptor import ReceptorPrepError, prepare_all_receptors
    from .stages.report import write_validation_report
    from .stages.validate import ValidationFailure, run_validation

    cfg, resolved, out_dir, prov = _setup(config, run_id)
    try:
        receptors = prepare_all_receptors(cfg, out_dir)
    except ReceptorPrepError as exc:
        _fail(f"Receptor preparation failed:\n{exc}")
        return

    try:
        report = run_validation(cfg, receptors, out_dir)
    except ValidationFailure as exc:
        prov.add_stage("validate-docking", {"passed": False})
        prov.add_warning("Validation gate failed; screening was not run.")
        prov.write(out_dir / "provenance.json")
        _save_state(out_dir, validation_passed=False)
        typer.secho(f"\n{exc}\n", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc

    write_validation_report(cfg, report, [], cfg.paths.reports / "VALIDATION.md", resolved)
    prov.add_stage("validate-docking", report.as_metrics())
    for warning in report.warnings:
        prov.add_warning(warning)
    prov.write(out_dir / "provenance.json")
    _save_state(
        out_dir,
        validation_passed=report.all_redocks_passed,
        validation_config_hash=cfg.config_hash(),
    )

    typer.secho("Validation complete.", fg=typer.colors.GREEN)
    for item in report.redock:
        colour = (
            typer.colors.GREEN
            if item.passed
            else (typer.colors.YELLOW if not item.performed else typer.colors.RED)
        )
        typer.secho(
            f"  redock {item.receptor_id}: "
            f"{'RMSD ' + str(item.rmsd_angstrom) + ' A' if item.performed else 'NOT PERFORMED'}"
            f" -> {item.diagnosis}",
            fg=colour,
        )
    for enrichment in report.enrichment:
        typer.echo(
            f"  enrichment {enrichment.receptor_id}: ROC-AUC "
            f"{enrichment.roc_auc:.3f} ({enrichment.verdict})"
        )
    for warning in report.warnings:
        typer.secho(f"  ! {warning}", fg=typer.colors.YELLOW)
    typer.echo(f"  Report: {cfg.paths.reports / 'VALIDATION.md'}")


@app.command()
def dock(
    config: ConfigOption,
    run_id: RunIdOption = None,
    skip_validation_check: Annotated[
        bool,
        typer.Option(
            "--skip-validation-check",
            help=(
                "Dock without a passing validation gate. Recorded in provenance and "
                "stamped on every output. Do not use for anything you intend to "
                "publish."
            ),
        ),
    ] = False,
) -> None:
    """Stage 4: dock every ligand into every receptor."""
    from .stages.dock import run_docking

    cfg, _resolved, out_dir, prov = _setup(config, run_id)
    state = _load_state(out_dir)

    if not skip_validation_check and not state.get("validation_passed"):
        _fail(
            "Refusing to screen: validation has not passed for this configuration.\n"
            "Run 'npcadd validate-docking --config <config>' first.\n\n"
            "A screening result produced without a redocking gate cannot be "
            "interpreted: there is no evidence the protocol reproduces a pose it was "
            "given, so there is no basis for trusting poses it was not.\n"
            "If you understand that and want to proceed anyway, pass "
            "--skip-validation-check; the override is recorded in provenance and "
            "stamped on every output.",
            code=3,
        )
        return

    ligands, receptors, validation = _rehydrate(cfg, out_dir)
    try:
        rows, summary = run_docking(cfg, ligands, receptors, out_dir, nulls=validation.nulls)
    except RuntimeError as exc:
        _fail(f"Docking failed: {exc}")
        return

    if skip_validation_check:
        prov.add_warning(
            "Screening was run with --skip-validation-check: no passing redocking "
            "gate. Results are uninterpretable as evidence of binding."
        )
    prov.add_stage("dock", summary.as_metrics())
    prov.add_output(summary.results_path)
    prov.write(out_dir / "provenance.json")
    _save_state(out_dir, docking_results=summary.results_path)

    typer.secho(
        f"Docked {summary.n_ok}/{summary.n_jobs} jobs "
        f"({summary.n_failed} failed, {summary.n_from_cache} from cache).",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  Results: {summary.results_path}")
    _ = rows


@app.command()
def admet(config: ConfigOption, run_id: RunIdOption = None) -> None:
    """Stage 5: offline descriptors, rule sets, alerts and QSPR predictions."""
    from .stages.admet import run_admet
    from .stages.ligands import LigandRecord

    cfg, _resolved, out_dir, prov = _setup(config, run_id)
    ligands = _read_ligand_manifest(out_dir, LigandRecord)
    try:
        rows, summary = run_admet(cfg, ligands, out_dir)
    except (KeyError, RuntimeError, ValueError) as exc:
        _fail(f"ADMET failed: {exc}")
        return

    prov.add_stage("admet", summary.as_metrics())
    prov.add_output(summary.results_path)
    prov.write(out_dir / "provenance.json")
    _save_state(out_dir, admet_results=summary.results_path)

    typer.secho(f"Profiled {summary.n_ligands} ligand(s).", fg=typer.colors.GREEN)
    typer.echo(
        f"  Lipinski pass: {summary.n_lipinski_pass}; all rules: "
        f"{summary.n_all_rules_pass}; PAINS flagged: {summary.n_pains_flagged}; "
        f"Brenk flagged: {summary.n_brenk_flagged}"
    )
    if summary.n_ml_out_of_domain:
        typer.secho(
            f"  ! {summary.n_ml_out_of_domain} QSPR prediction(s) are outside the "
            "model's applicability domain and are flagged, not reported as fact.",
            fg=typer.colors.YELLOW,
        )
    if summary.external_batch_paths:
        typer.echo(
            f"  Manual-tool handoff written for "
            f"{len(cfg.admet.external_tools)} tool(s) under "
            f"{out_dir / 'external_tools'} (npcadd does not scrape these)."
        )
    _ = rows


@app.command()
def rank(config: ConfigOption, run_id: RunIdOption = None) -> None:
    """Stage 6: rank on ligand efficiency and the multi-criteria score."""
    from .stages.rank import rank_compounds

    cfg, _resolved, out_dir, prov = _setup(config, run_id)
    screen_rows, admet_rows = _read_screen_and_admet(out_dir)
    try:
        ranked, summary = rank_compounds(cfg, screen_rows, admet_rows, out_dir)
    except ValueError as exc:
        _fail(f"Ranking failed: {exc}")
        return

    prov.add_stage("rank", summary.as_metrics())
    prov.add_output(summary.results_path)
    prov.write(out_dir / "provenance.json")
    _save_state(out_dir, ranked_results=summary.results_path)

    typer.secho(f"Ranked {summary.n_rows} compound-target pair(s).", fg=typer.colors.GREEN)
    typer.echo(f"  Spearman vs raw-affinity ranking: {summary.spearman_vs_raw_affinity}")
    typer.echo(f"  Top-5 weight stability: {summary.sensitivity.get('top5_stability')}")
    typer.echo(f"  Results: {summary.results_path}")
    for row in ranked[:5]:
        typer.echo(
            f"    {row.rank}. {row.name} ({row.compound_class}, "
            f"{row.area_percent:.2f}%) vs {row.receptor_id}: "
            f"LE {row.efficiency.ligand_efficiency:.3f}, "
            f"score {row.multi_criteria_score:.3f}"
        )


@app.command()
def report(config: ConfigOption, run_id: RunIdOption = None) -> None:
    """Stage 7: workbook, figures, VALIDATION.md and supplementary information."""
    from .stages.report import run_report

    cfg, resolved, out_dir, prov = _setup(config, run_id)
    ligands, receptors, validation = _rehydrate(cfg, out_dir)
    screen_rows, admet_rows = _read_screen_and_admet(out_dir)

    from .stages.ligands import prepare_ligands

    _, ligand_summary = prepare_ligands(cfg, out_dir)
    from .stages.rank import rank_compounds

    ranked, ranking_summary = rank_compounds(cfg, screen_rows, admet_rows, out_dir)

    bundle = run_report(
        cfg,
        resolved,
        ligand_summary,
        receptors,
        validation,
        screen_rows,
        admet_rows,
        ranked,
        ranking_summary,
        out_dir,
    )
    prov.add_stage("report", bundle.as_metrics())
    prov.add_outputs(
        [bundle.workbook_path, bundle.validation_md_path, bundle.si_md_path, *bundle.figure_paths]
    )
    for caveat in bundle.caveats:
        prov.add_warning(caveat)
    prov.write(out_dir / "provenance.json")

    typer.secho("Report complete.", fg=typer.colors.GREEN)
    typer.echo(f"  Workbook: {bundle.workbook_path}")
    typer.echo(f"  Validation: {bundle.validation_md_path}")
    typer.echo(f"  Supplementary information: {bundle.si_md_path}")
    typer.echo(f"  Figures: {sum(1 for f in bundle.figures if f.produced)} produced")
    if bundle.caveats:
        typer.secho(
            f"\n  {len(bundle.caveats)} caveat(s) are stamped on the outputs:",
            fg=typer.colors.YELLOW,
        )
        for caveat in bundle.caveats:
            typer.secho(f"    - {caveat}", fg=typer.colors.YELLOW)
    _ = ligands


@app.command("run-all")
def run_all(
    config: ConfigOption,
    run_id: RunIdOption = None,
    skip_validation_check: Annotated[
        bool, typer.Option("--skip-validation-check", help="See 'dock --help'.")
    ] = False,
) -> None:
    """Run every stage in order: prepare, validate, dock, admet, rank, report."""
    from .stages.admet import run_admet
    from .stages.dock import run_docking
    from .stages.ligands import prepare_ligands
    from .stages.rank import rank_compounds
    from .stages.receptor import ReceptorPrepError, prepare_all_receptors
    from .stages.report import run_report
    from .stages.validate import ValidationFailure, run_validation

    cfg, resolved, out_dir, prov = _setup(config, run_id)
    started = new_run_id(cfg.config_hash())
    typer.secho(
        f"npcadd {__version__} — run {resolved} (started {started})",
        fg=typer.colors.CYAN,
    )

    try:
        ligands, ligand_summary = prepare_ligands(cfg, out_dir)
        prov.add_stage("prepare-ligands", ligand_summary.as_metrics())
        typer.echo(
            f"[1/7] ligands: {ligand_summary.n_accepted} accepted, "
            f"{ligand_summary.n_rejected} rejected"
        )

        receptors = prepare_all_receptors(cfg, out_dir)
        prov.add_stage("prepare-receptor", {"n_receptors": len(receptors)})
        typer.echo(f"[2/7] receptors: {len(receptors)} prepared")

        validation = run_validation(cfg, receptors, out_dir)
        prov.add_stage("validate-docking", validation.as_metrics())
        typer.echo(
            f"[3/7] validation: "
            f"{sum(1 for r in validation.redock if r.passed)}/"
            f"{sum(1 for r in validation.redock if r.performed)} redock gates passed"
        )
        if not validation.all_redocks_passed and not skip_validation_check:
            unperformed = [r for r in validation.redock if not r.performed]
            if len(unperformed) == len(validation.redock):
                _fail(
                    "No redocking control could be performed for any receptor, so the "
                    "mandatory validation gate did not run. Screening has not been "
                    "started. See reports/VALIDATION.md.",
                    code=3,
                )
                return

        screen_rows, docking_summary = run_docking(
            cfg, ligands, receptors, out_dir, nulls=validation.nulls
        )
        prov.add_stage("dock", docking_summary.as_metrics())
        typer.echo(f"[4/7] docking: {docking_summary.n_ok}/{docking_summary.n_jobs} ok")

        admet_rows, admet_summary = run_admet(cfg, ligands, out_dir)
        prov.add_stage("admet", admet_summary.as_metrics())
        typer.echo(f"[5/7] admet: {admet_summary.n_ligands} profiled")

        ranked, ranking_summary = rank_compounds(cfg, screen_rows, admet_rows, out_dir)
        prov.add_stage("rank", ranking_summary.as_metrics())
        typer.echo(f"[6/7] ranking: {ranking_summary.n_rows} rows")

        bundle = run_report(
            cfg,
            resolved,
            ligand_summary,
            receptors,
            validation,
            screen_rows,
            admet_rows,
            ranked,
            ranking_summary,
            out_dir,
        )
        prov.add_stage("report", bundle.as_metrics())
        typer.echo(f"[7/7] report: {bundle.workbook_path}")

    except ValidationFailure as exc:
        prov.add_warning("Validation gate failed; screening was not run.")
        prov.write(out_dir / "provenance.json")
        typer.secho(f"\n{exc}\n", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=3) from exc
    except (ReceptorPrepError, FileNotFoundError, ValueError, RuntimeError) as exc:
        prov.add_warning(f"Pipeline aborted: {exc}")
        prov.write(out_dir / "provenance.json")
        typer.secho(f"\nPipeline failed: {exc}\n", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    for warning in validation.warnings + bundle.caveats:
        prov.add_warning(warning)
    prov.add_inputs([r.pdbqt_path for r in receptors])
    prov.add_outputs(
        [
            ligand_summary.manifest_path,
            ligand_summary.rejected_path,
            docking_summary.results_path,
            admet_summary.results_path,
            ranking_summary.results_path,
            bundle.workbook_path,
            bundle.validation_md_path,
            bundle.si_md_path,
            *bundle.figure_paths,
        ]
    )
    provenance_path = prov.write(out_dir / "provenance.json")
    _save_state(out_dir, validation_passed=validation.all_redocks_passed, complete=True)

    typer.secho("\nPipeline complete.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Run directory: {out_dir}")
    typer.echo(f"  Provenance:    {provenance_path}")
    typer.echo(f"  Workbook:      {bundle.workbook_path}")
    typer.echo(f"  Validation:    {bundle.validation_md_path}")
    typer.echo(f"  SI:            {bundle.si_md_path}")
    if bundle.caveats:
        typer.secho(
            f"\n  {len(bundle.caveats)} caveat(s) stamped on every output:",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        for caveat in bundle.caveats:
            typer.secho(f"    - {caveat}", fg=typer.colors.YELLOW)


@app.command("verify-provenance")
def verify_provenance(
    provenance_json: Annotated[
        Path, typer.Argument(help="Path to a provenance.json from a previous run.")
    ],
) -> None:
    """Re-hash every recorded input and output and report any drift."""
    if not provenance_json.is_file():
        _fail(f"No such file: {provenance_json}", code=2)
        return
    record = json.loads(provenance_json.read_text(encoding="utf-8"))

    problems: list[str] = []
    checked = 0
    for section in ("inputs", "outputs"):
        for path_str, expected in (record.get(section) or {}).items():
            path = Path(path_str)
            if expected == "<missing>":
                continue
            if not path.is_file():
                problems.append(f"{section}: {path} is missing (was {expected[:12]})")
                continue
            actual = hash_file(path)
            checked += 1
            if actual != expected:
                problems.append(
                    f"{section}: {path} has changed\n    recorded {expected}\n    actual   {actual}"
                )

    typer.echo(f"Run ID:      {record.get('run_id')}")
    typer.echo(f"Config hash: {(record.get('config') or {}).get('hash')}")
    typer.echo(f"Git commit:  {(record.get('git') or {}).get('commit')}")
    typer.echo(f"Git dirty:   {(record.get('git') or {}).get('dirty')}")
    typer.echo(f"Checked:     {checked} file(s)")

    if problems:
        typer.secho(f"\n{len(problems)} discrepancy/ies:", fg=typer.colors.RED)
        for problem in problems:
            typer.secho(f"  - {problem}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho("\nAll recorded files match their checksums.", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------
# State rehydration helpers
# --------------------------------------------------------------------------


def _read_ligand_manifest(out_dir: Path, record_cls: Any) -> list[Any]:
    """Rebuild ligand records from the manifest written by stage 1."""
    import pandas as pd

    path = out_dir / "ligand_manifest.csv"
    if not path.is_file():
        raise StageStateError(f"No ligand manifest at {path}. Run 'npcadd prepare-ligands' first.")
    frame = pd.read_csv(path, keep_default_na=False)
    fields = set(record_cls.__dataclass_fields__)
    records: list[Any] = []
    for raw in frame.to_dict(orient="records"):
        kwargs = {str(k): v for k, v in raw.items() if str(k) in fields}
        records.append(record_cls(**kwargs))
    return records


def _read_screen_and_admet(out_dir: Path) -> tuple[list[Any], list[Any]]:
    """Rebuild screening rows and ADMET rows from their CSVs."""
    import pandas as pd

    from .chem.descriptors import DESCRIPTOR_FIELDS
    from .stages.admet import AdmetRow
    from .stages.dock import ScreenRow

    screen_path = out_dir / "docking_results.csv"
    admet_path = out_dir / "admet_results.csv"
    if not screen_path.is_file():
        raise StageStateError(f"No docking results at {screen_path}. Run 'npcadd dock' first.")
    if not admet_path.is_file():
        raise StageStateError(f"No ADMET results at {admet_path}. Run 'npcadd admet' first.")

    screen_frame = pd.read_csv(screen_path, keep_default_na=False)
    screen_rows: list[ScreenRow] = []
    for raw in screen_frame.to_dict(orient="records"):
        row = {str(k): v for k, v in raw.items()}
        affinities = [float(x) for x in str(row.get("all_affinities_kcal_mol", "")).split(";") if x]
        best = row.get("best_affinity_kcal_mol")
        screen_rows.append(
            ScreenRow(
                ligand_id=str(row["ligand_id"]),
                name=str(row["name"]),
                compound_class=str(row["compound_class"]),
                area_percent=float(row["area_percent"] or 0.0),
                receptor_id=str(row["receptor_id"]),
                activity=str(row["activity"]),
                target_name=str(row["target_name"]),
                ok=bool(row["docking_ok"]),
                best_affinity_kcal_mol=float(best) if best not in ("", None) else None,
                all_affinities=affinities,
                z_score=_optional_float(row.get("z_score_vs_null")),
                empirical_p=_optional_float(row.get("empirical_p_vs_null")),
                p_resolution=_optional_float(row.get("p_value_resolution")),
                n_contacts=int(row.get("n_contacts") or 0),
                contact_residues=str(row.get("contact_residues", "")),
                contacts=str(row.get("contacts", "")),
                placement_ok=_optional_bool(row.get("placement_ok")),
                placement_note=str(row.get("placement_note", "")),
                frac_atoms_in_box=_optional_float(row.get("frac_atoms_in_box")),
                n_atoms_in_box=_optional_int(row.get("n_atoms_in_box")),
                pose_centroid_offset_a=_optional_float(row.get("pose_centroid_offset_a")),
                pose_centroid_offset_frac=_optional_float(row.get("pose_centroid_offset_frac")),
                pose_max_atom_offset_frac=_optional_float(row.get("pose_max_atom_offset_frac")),
                n_poses_returned=_optional_int(row.get("n_poses_returned")),
                mean_top_affinity_kcal_mol=_optional_float(row.get("mean_top_affinity_kcal_mol")),
                affinity_spread_kcal_mol=_optional_float(row.get("affinity_spread_kcal_mol")),
                affinity_std_kcal_mol=_optional_float(row.get("affinity_std_kcal_mol")),
                consensus_ok=_optional_bool(row.get("consensus_ok")),
                top2_pose_rmsd_a=_optional_float(row.get("top2_pose_rmsd_a")),
                pose_path=str(row.get("pose_path", "")),
                error=str(row.get("error", "")),
            )
        )

    admet_frame = pd.read_csv(admet_path, keep_default_na=False)
    admet_rows: list[AdmetRow] = []
    for raw_row in admet_frame.to_dict(orient="records"):
        row = {str(k): v for k, v in raw_row.items()}
        descriptors = {f: float(row[f]) for f in DESCRIPTOR_FIELDS if f in row and row[f] != ""}
        rule_results = {
            k[: -len("_pass")]: bool(v)
            for k, v in row.items()
            if k.endswith("_pass") and k != "admet_pass_rate"
        }
        alerts = {
            k: v
            for k, v in row.items()
            if k.endswith(("_flagged", "_filters")) or k == "n_structural_alerts"
        }
        ml = {
            k: v
            for k, v in row.items()
            if "_pred" in k or "_in_domain" in k or k.startswith("n_ml")
        }
        np_like = {k: v for k, v in row.items() if k.startswith("np_likeness")}
        admet_rows.append(
            AdmetRow(
                ligand_id=str(row["ligand_id"]),
                name=str(row["name"]),
                compound_class=str(row["compound_class"]),
                area_percent=float(row["area_percent"] or 0.0),
                smiles=str(row["smiles"]),
                descriptors=descriptors,
                rule_results=rule_results,
                admet_pass_rate=float(row.get("admet_pass_rate") or 0.0),
                alerts=alerts,
                ml_predictions=ml,
                np_likeness=np_like,
            )
        )
    return screen_rows, admet_rows


def _optional_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    parsed = _optional_float(value)
    return None if parsed is None else int(parsed)


def _optional_bool(value: Any) -> bool | None:
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "1.0", "yes"):
        return True
    if text in ("false", "0", "0.0", "no"):
        return False
    return None


def _rehydrate(cfg: Config, out_dir: Path) -> tuple[list[Any], list[Any], Any]:
    """Re-derive ligands, receptors and validation state for a later stage.

    Receptor preparation and validation are re-run rather than deserialised.
    Both are resumable and cheap on a second pass (structures are cached and
    docking is fingerprint-skipped), and re-running them guarantees the later
    stage sees exactly the state the current config implies rather than a stale
    snapshot.
    """
    from .stages.ligands import LigandRecord
    from .stages.receptor import prepare_all_receptors
    from .stages.validate import run_validation

    ligands = _read_ligand_manifest(out_dir, LigandRecord)
    receptors = prepare_all_receptors(cfg, out_dir)
    validation = run_validation(cfg, receptors, out_dir)
    return ligands, receptors, validation


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
