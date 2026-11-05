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
