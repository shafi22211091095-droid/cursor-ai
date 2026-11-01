"""Stage 7: reporting.

Produces the workbook, the figures, ``reports/VALIDATION.md`` and
``reports/SUPPLEMENTARY_INFORMATION.md``.

A deliberate choice runs through this module: **caveats are not collected in an
appendix**. If any input row is flagged as synthetic, if a redocking control was
not performed, if a receptor's resolution is unknown, if a QSPR prediction is
out of domain, or if the weight sensitivity is low, that fact is written into
the workbook README, the figure captions and the top of the SI document — the
places a reader actually looks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import Config
from ..docking.metrics import roc_curve
from ..logging_utils import get_logger
from ..provenance import package_versions
from ..report.figures import (
    FigureResult,
    apply_style,
    plot_class_distribution,
    plot_efficiency_vs_affinity,
    plot_interaction_heatmap,
    plot_null_distribution,
    plot_property_space,
    plot_roc,
    plot_sensitivity,
    plot_top_compounds,
)
from ..report.workbook import SheetSpec, write_workbook
from .admet import AdmetRow
from .dock import ScreenRow
from .ligands import LigandPrepSummary
from .rank import RankedRow, RankingSummary
from .receptor import PreparedReceptor
from .validate import ValidationReport

__all__ = ["ReportBundle", "run_report", "write_validation_report"]

log = get_logger("stages.report")

SYNTHETIC_FLAG = "SYNTHETIC_PLACEHOLDER"

SYNTHETIC_BANNER = (
    "THIS RUN USED SYNTHETIC PLACEHOLDER DATA. The GC-MS abundances in the input "
    "are fabricated, not measured. Every number downstream of them -- including the "
    "GC-MS area term in the multi-criteria score and therefore the compound ranking "
    "-- is a demonstration that the software runs, NOT a scientific finding. Do not "
    "report any of it."
)


@dataclass(slots=True)
class ReportBundle:
    """Everything the reporting stage produced."""

    workbook_path: str = ""
    validation_md_path: str = ""
    si_md_path: str = ""
    figures: list[FigureResult] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def figure_paths(self) -> list[str]:
        return [p.as_posix() for f in self.figures for p in f.paths]

    def as_metrics(self) -> dict[str, Any]:
        return {
            "n_figures": sum(1 for f in self.figures if f.produced),
            "n_figures_skipped": sum(1 for f in self.figures if not f.produced),
            "n_caveats": len(self.caveats),
            "workbook": self.workbook_path,
        }


def _input_is_synthetic(cfg: Config) -> bool:
    """Whether any input row is flagged as synthetic placeholder data."""
    try:
        frame = pd.read_csv(cfg.ligands.input_csv, dtype=str, keep_default_na=False)
    except (OSError, ValueError):  # pragma: no cover - already read upstream
        return False
    if "data_provenance" not in frame.columns:
        return False
    return bool((frame["data_provenance"].str.strip() == SYNTHETIC_FLAG).any())


def _collect_caveats(
    cfg: Config,
    receptors: list[PreparedReceptor],
    validation: ValidationReport,
    ligand_summary: LigandPrepSummary,
    admet_rows: list[AdmetRow],
    ranking_summary: RankingSummary,
) -> list[str]:
    """Assemble every methodological caveat that must surface in the outputs."""
    caveats: list[str] = []

    if _input_is_synthetic(cfg):
        caveats.append(SYNTHETIC_BANNER)

    caveats.extend(validation.warnings)
    for receptor in receptors:
        caveats.extend(f"[{receptor.receptor_id}] {w}" for w in receptor.warnings)

    if ligand_summary.protonation_limitation:
        caveats.append(f"Protonation: {ligand_summary.protonation_limitation}")

    n_out_of_domain = sum(
        int(r.ml_predictions.get("n_ml_out_of_domain", 0) or 0) for r in admet_rows
    )
    if n_out_of_domain:
        caveats.append(
            f"{n_out_of_domain} QSPR prediction(s) fell outside the model's "
            "applicability domain. These are extrapolations, are flagged "
            "in_domain=FALSE in the tables, and are excluded from the ranking score."
        )

    if ligand_summary.n_uncertain_protonation:
        caveats.append(
            f"{ligand_summary.n_uncertain_protonation} compound(s) have an ionisable "
            f"group with a pKa within 1 unit of pH {cfg.ligands.ph}, so their dominant "
            "protonation state is not well determined. The state docked is the "
            "model's best single choice; both states should be docked before drawing "
            "conclusions about those compounds. Affected compounds are flagged in the "
            "ligand manifest."
        )

    n_unspecified = sum(
        1 for r in admet_rows if r.descriptors.get("n_unspecified_stereocentres", 0) > 0
    )
    if n_unspecified:
        caveats.append(
            f"{n_unspecified} compound(s) have at least one unspecified stereocentre. "
            "GC-MS library identification does not establish absolute configuration, "
            "so the docked stereoisomer is one of several consistent with the "
            "identification, and its pose is correspondingly uncertain."
        )

    stability = ranking_summary.sensitivity.get("top5_stability")
    if isinstance(stability, (int, float)) and stability < 0.5:
        caveats.append(
            f"The shortlist is weight-sensitive (mean top-5 Jaccard {stability:.2f} "
            "under resampled weights). The ranking is substantially a product of the "
            "chosen weighting rather than of the data, and must be reported as such."
        )

    if not cfg.validation.halt_on_failure:
        caveats.append(
            "validation.halt_on_failure was set to false, so a failing redocking "
            "control would not have stopped this run."
        )

    for receptor in receptors:
        if receptor.directionality_conflict:
            caveats.append(
                f"[{receptor.receptor_id}] The desired phenotype is activation but the "
                "structure is an inhibitor-bound complex. A favourable score is "
                "evidence of binding to the inhibitor-bound conformation, not evidence "
                "of activation."
            )

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for caveat in caveats:
        if caveat not in seen:
            seen.add(caveat)
            unique.append(caveat)
    return unique


def write_validation_report(
    cfg: Config,
    validation: ValidationReport,
    figures: list[FigureResult],
    out_path: Path,
    run_id: str,
) -> Path:
    """Write ``reports/VALIDATION.md``."""
    lines: list[str] = [
        "# Docking validation report",
        "",
        f"- **Run ID**: `{run_id}`",
        f"- **Config SHA256**: `{cfg.config_hash()}`",
        f"- **Study**: {cfg.study}",
        "",
        "This report is generated by `npcadd validate-docking`. It is the evidence "
        "that the docking protocol used for screening can reproduce a known answer. "
        "Screening results should not be read without it.",
        "",
    ]

    if _input_is_synthetic(cfg):
        lines += ["> [!WARNING]", f"> {SYNTHETIC_BANNER}", ""]

    if not cfg.validation.enabled:
        lines += [
            "## Validation was disabled",
            "",
            "`validation.enabled` was false for this run. **No control was performed.** "
            "Any affinity reported from this run is uncalibrated and unverified.",
            "",
        ]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path

    # ---- Control 1: redocking -----------------------------------------
    lines += [
        "## Control 1 — Redocking the co-crystal ligand (gate)",
        "",
        "The co-crystal ligand is docked back into its own receptor and the RMSD to "
        "the crystallographic pose is measured **in place, without superposition**. "
        "Alignment-based RMSD (`GetBestRMS`) is not used: it answers whether the "
        "conformation is similar, not whether docking reproduced the placement, and it "
        "will pass a pose that is flipped end-for-end in the pocket.",
        "",
        f"Threshold: **{cfg.validation.redock_rmsd_threshold} A**. "
        f"Halt on failure: **{cfg.validation.halt_on_failure}**.",
        "",
        "| Receptor | PDB | RMSD (A) | Centroid offset (A) | Affinity | Best-of-N RMSD | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in validation.redock:
        if item.performed:
            verdict = "**PASS**" if item.passed else "**FAIL**"
            lines.append(
                f"| `{item.receptor_id}` | {item.pdb_id} | {item.rmsd_angstrom} | "
                f"{item.centroid_distance_angstrom} | {item.affinity_kcal_mol} | "
                f"{item.best_of_n_rmsd} (rank {item.best_rmsd_pose_rank}) | {verdict} |"
            )
        else:
            lines.append(
                f"| `{item.receptor_id}` | {item.pdb_id} | — | — | — | — | **NOT PERFORMED** |"
            )
    lines.append("")
    for item in validation.redock:
        lines.append(f"- `{item.receptor_id}`: {item.diagnosis}")
    lines.append("")

    # ---- Control 2: enrichment ----------------------------------------
    lines += ["## Control 2 — Enrichment against property-matched decoys", ""]
    if not validation.enrichment_performed:
        lines += [
            "> [!IMPORTANT]",
            f"> **Not performed.** {validation.enrichment_skip_reason}",
            "",
        ]
    else:
        lines += [
            "Decoys are selected from an offline, deterministically enumerated pool to "
            "match each active on MW, cLogP, TPSA, rotatable bonds, HBD, HBA and formal "
            "charge, while remaining topologically dissimilar to every active "
            "(Tanimoto < 0.35). They are **presumed** inactive by construction, not "
            "experimentally confirmed inactives.",
            "",
            "| Receptor | Actives | Decoys | ROC-AUC | BEDROC | EF1% (max) | Verdict |",
            "|---|---|---|---|---|---|---|",
        ]
        for enrichment in validation.enrichment:
            ef1 = enrichment.enrichment_factors.get("1pct")
            ef1_max = enrichment.max_enrichment_factors.get("1pct")
            lines.append(
                f"| `{enrichment.receptor_id}` | {enrichment.n_actives} | "
                f"{enrichment.n_decoys} | {enrichment.roc_auc:.3f} | "
                f"{enrichment.bedroc:.3f} | "
                f"{ef1:.2f} ({ef1_max:.2f})"
                if ef1 is not None
                else "—"
            )
        lines.append("")
        for enrichment in validation.enrichment:
            lines.append(
                f"- `{enrichment.receptor_id}`: {enrichment.verdict} "
                f"(BEDROC alpha={enrichment.bedroc_alpha:g})"
            )
        lines += [
            "",
            "Decoy set construction, per active:",
            "",
            "| Active | Requested | Selected | Shortfall | Tolerance relaxations |",
            "|---|---|---|---|---|",
        ]
        for decoy_set in validation.decoy_sets:
            record = decoy_set.as_record()
            lines.append(
                f"| {record['active_id']} | {record['n_decoys_requested']} | "
                f"{record['n_decoys_selected']} | {record['shortfall']} | "
                f"{record['tolerance_relaxations']} |"
            )
        lines += [
            "",
            "A decoy set that required tolerance relaxations is a weaker control than "
            "one that did not, because its decoys are less closely property-matched.",
            "",
        ]

    # ---- Control 3: null distribution ---------------------------------
    lines += [
        "## Control 3 — Random-ligand null distribution",
        "",
        "A random ligand set is docked under identical parameters to establish what "
        "score this receptor gives a ligand with no reason to bind. This is what turns "
        "a reported affinity into a z-score and a one-sided empirical p-value.",
        "",
    ]
    if not validation.nulls:
        lines += ["**Not available.** No null distribution could be built.", ""]
    else:
        lines += [
            "| Receptor | n | Mean | SD | Min | 5th pct | Median | Smallest supportable p |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for receptor_id in sorted(validation.nulls):
            s = validation.nulls[receptor_id].summary()
            lines.append(
                f"| `{receptor_id}` | {s['n_null_ligands']} | "
                f"{s['mean_affinity_kcal_mol']} | {s['sd_affinity_kcal_mol']} | "
                f"{s['min_affinity_kcal_mol']} | {s['p05']} | {s['median']} | "
                f"{s['smallest_supportable_p']} |"
            )
        lines += [
            "",
            "The empirical p-value uses the (k+1)/(n+1) estimator, which never returns "
            "zero: with a null set of size n the smallest defensible claim is "
            "p < 1/(n+1), shown in the last column.",
            "",
        ]

    if validation.pool_summary:
        lines += [
            "### Decoy and null pool",
            "",
            "```",
            *[f"{k}: {v}" for k, v in sorted(validation.pool_summary.items())],
            "```",
            "",
        ]

    if validation.warnings:
        lines += ["## Warnings", ""]
        lines.extend(f"- {w}" for w in validation.warnings)
        lines.append("")

    produced = [f for f in figures if f.produced]
    if produced:
        lines += ["## Figures", ""]
        for figure in produced:
            relative = [
                p.relative_to(cfg.paths.reports).as_posix()
                if cfg.paths.reports in p.parents
                else p.as_posix()
                for p in figure.paths
            ]
            lines.append(f"### `{figure.name}`")
            lines.append("")
            lines.append(f"Files: {', '.join(f'`{r}`' for r in relative)}")
            lines.append("")
            lines.append(f"*{figure.caption}*")
            lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote validation report", extra={"path": out_path.as_posix()})
    return out_path


def _write_si(
    cfg: Config,
    out_path: Path,
    run_id: str,
    receptors: list[PreparedReceptor],
    ligand_summary: LigandPrepSummary,
    ranked: list[RankedRow],
    ranking_summary: RankingSummary,
    figures: list[FigureResult],
    caveats: list[str],
    versions: dict[str, str],
) -> Path:
    """Write the Markdown supplementary-information document."""
    lines: list[str] = [
        "# Supplementary Information",
        "",
        f"**{cfg.study}**",
        "",
        f"- Run ID: `{run_id}`",
        f"- Config SHA256: `{cfg.config_hash()}`",
        f"- Generated by npcadd {versions.get('npcadd', '?')}",
        "",
    ]
    if any(c == SYNTHETIC_BANNER for c in caveats):
        lines += ["> [!CAUTION]", f"> {SYNTHETIC_BANNER}", ""]

    if caveats:
        lines += [
            "## Caveats a reader must have before the results",
            "",
            "These are stated here, before the tables, rather than in an appendix.",
            "",
        ]
        lines.extend(f"{i}. {c}" for i, c in enumerate(caveats, start=1))
        lines.append("")

    lines += [
        "## S1. Methods summary",
        "",
        "### Ligand preparation",
        "",
        f"- Input: `{cfg.ligands.input_csv.name}` ({ligand_summary.n_input} components)",
        f"- Accepted: **{ligand_summary.n_accepted}**; rejected: "
        f"{ligand_summary.n_rejected} (per-molecule reasons in "
        "`data/interim/rejected_ligands.csv`)",
        f"- Standardisation: RDKit MolStandardize — cleanup, "
        f"{'largest-fragment selection, ' if cfg.ligands.strip_salts else ''}"
        f"{'neutralisation, ' if cfg.ligands.neutralise else ''}"
        + (
            "canonical tautomer"
            if cfg.ligands.canonical_tautomer
            else "no tautomer canonicalisation"
        ),
        f"- Protonation: {cfg.ligands.protonation_method} at pH {cfg.ligands.ph}",
        f"- Conformers: ETKDGv3 seed {cfg.ligands.embed_seed}, "
        f"{cfg.ligands.n_conformers} requested, MMFF94 optimised, "
        f"{cfg.ligands.energy_window_kcal} kcal/mol energy window, "
        f"{cfg.ligands.prune_rms_thresh} A RMSD pruning",
        f"- Compounds with unspecified stereocentres: {ligand_summary.n_with_unspecified_stereo}",
        f"- Compounds carrying a formal charge at pH {cfg.ligands.ph}: "
        f"{ligand_summary.n_charged_at_ph}",
        "",
        "### Receptors",
        "",
        "| ID | PDB | Activity | Target | Resolution | R-free | UniProt | "
        "Protonation | Box centre | Box size |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for receptor in receptors:
        meta = receptor.metadata.as_record()
        lines.append(
            f"| `{receptor.receptor_id}` | {receptor.pdb_id} | {receptor.activity} | "
            f"{receptor.target_name} | {meta['resolution_angstrom']} | "
            f"{meta['r_free']} | {meta['uniprot_accession']} | "
            f"{receptor.protonation_tool} | "
            f"({', '.join(f'{c:.2f}' for c in receptor.box.center)}) | "
            f"({', '.join(f'{s:.2f}' for s in receptor.box.size)}) |"
        )
    lines += [
        "",
        "Search boxes are derived from the co-crystal ligand rather than transcribed; "
        "each receptor's derivation string is recorded in the workbook.",
        "",
        "### Docking",
        "",
        f"- Engine: AutoDock Vina {versions.get('vina', '?')}, scoring `{cfg.docking.scoring}`",
        f"- Exhaustiveness {cfg.docking.exhaustiveness}, seed {cfg.docking.seed}, "
        f"{cfg.docking.n_poses} poses retained",
        f"- Interaction fingerprints: ProLIF {versions.get('prolif', '?')} "
        f"({', '.join(cfg.docking.prolif_interactions)})",
        "- Validation used identical docking parameters to screening; see `VALIDATION.md`.",
        "",
        "### Ranking",
        "",
        "Compounds are **not** ranked by raw docking affinity. Vina's score is "
        "approximately extensive in molecular size, so ranking a set spanning C6 "
        "phenolics to C30 triterpenes by raw score would order it largely by molecular "
        "weight. Ranking is on ligand efficiency and a multi-criteria score:",
        "",
        "- `LE = -affinity / heavy_atoms`",
        "- `LLE = pKd_estimate - cLogP`",
        "- `LELP = cLogP / LE`",
        "- `FQ` = LE relative to the maximum achievable at that heavy-atom count "
        "(Reynolds et al. 2008)",
        "",
        "Multi-criteria score = "
        + " + ".join(f"{v:g} x normalised({k})" for k, v in sorted(cfg.ranking.weights.items()))
        + ", min-max normalised within each target.",
        "",
        f"Spearman correlation between the multi-criteria ranking and the raw-affinity "
        f"ranking: {ranking_summary.spearman_vs_raw_affinity}. A low correlation is "
        "expected and is the point of the correction.",
        "",
        f"Weight sensitivity: {ranking_summary.sensitivity.get('n_samples')} Dirichlet "
        f"resamplings, mean top-5 Jaccard overlap "
        f"{ranking_summary.sensitivity.get('top5_stability')}. "
        f"{ranking_summary.sensitivity.get('interpretation', '')}",
        "",
        "## S2. Ranked compounds",
        "",
        f"Compounds at or above {cfg.ranking.area_highlight_threshold}% GC-MS area are "
        "sorted into the upper rows within each target.",
        "",
    ]

    frame = pd.DataFrame([r.as_record() for r in ranked])
    if not frame.empty:
        columns = [
            "rank",
            "name",
            "compound_class",
            "area_percent",
            "receptor_id",
            "best_affinity_kcal_mol",
            "heavy_atoms",
            "ligand_efficiency_kcal_mol_per_ha",
            "fit_quality",
            "lipophilic_efficiency_lle",
            "admet_pass_rate",
            "z_score_vs_null",
            "empirical_p_vs_null",
            "multi_criteria_score",
        ]
        present = [c for c in columns if c in frame.columns]
        top = frame.nsmallest(cfg.report.top_n_table, "rank")[present]
        lines.append("| " + " | ".join(present) + " |")
        lines.append("|" + "|".join("---" for _ in present) + "|")
        for _, row in top.iterrows():
            lines.append("| " + " | ".join(str(row[c]) for c in present) + " |")
        lines.append("")

    produced = [f for f in figures if f.produced]
    if produced:
        lines += ["## S3. Figures", ""]
        for index, figure in enumerate(produced, start=1):
            lines += [
                f"**Figure S{index}.** {figure.caption}",
                "",
                f"Files: {', '.join(f'`{p.name}`' for p in figure.paths)}",
                "",
            ]
    skipped = [f for f in figures if not f.produced]
    if skipped:
        lines += ["### Figures not produced", ""]
        lines.extend(f"- `{f.name}`: {f.skipped_reason}" for f in skipped)
        lines.append("")

    lines += [
        "## S4. Software versions",
        "",
        "```",
        *[f"{k}: {v}" for k, v in sorted(versions.items())],
        "```",
        "",
        "## S5. Reproducing this",
        "",
        "```bash",
        "make reproduce",
        "```",
        "",
        "Run-level provenance — git commit, config hash, SHA256 of every input and "
        "output, package versions, OS, CPU and all random seeds — is written to "
        "`provenance.json` in the run directory.",
        "",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Wrote supplementary information", extra={"path": out_path.as_posix()})
    return out_path


def run_report(
    cfg: Config,
    run_id: str,
    ligands_summary: LigandPrepSummary,
    receptors: list[PreparedReceptor],
    validation: ValidationReport,
    screen_rows: list[ScreenRow],
    admet_rows: list[AdmetRow],
    ranked: list[RankedRow],
    ranking_summary: RankingSummary,
    out_dir: Path,
) -> ReportBundle:
    """Produce the workbook, figures, VALIDATION.md and the SI document."""
    versions = package_versions()
    apply_style(cfg)

    ranked_frame = pd.DataFrame([r.as_record() for r in ranked])
    admet_frame = pd.DataFrame([r.as_record() for r in admet_rows])
    screen_frame = pd.DataFrame([r.as_record() for r in screen_rows])

    matrix_path = out_dir / "interaction_matrix.csv"
    matrix = pd.read_csv(matrix_path, index_col=0) if matrix_path.is_file() else pd.DataFrame()

    caveats = _collect_caveats(
        cfg, receptors, validation, ligands_summary, admet_rows, ranking_summary
    )

    figures: list[FigureResult] = [
        plot_efficiency_vs_affinity(cfg, ranked_frame, versions),
        plot_top_compounds(cfg, ranked_frame, versions, top_n=cfg.report.top_n_table),
        plot_property_space(cfg, admet_frame, versions),
        plot_class_distribution(cfg, admet_frame, versions),
        plot_null_distribution(cfg, validation.nulls, ranked_frame, versions),
        plot_interaction_heatmap(cfg, matrix, versions),
        plot_sensitivity(cfg, ranking_summary.sensitivity, versions),
    ]

    curves: list[tuple[str, np.ndarray, np.ndarray, float]] = []
    for enrichment in validation.enrichment:
        stored = out_dir / "validation" / enrichment.receptor_id / "labels_scores.npz"
        if stored.is_file():
            data = np.load(stored)
            fpr, tpr = roc_curve(data["labels"], data["scores"])
            curves.append((enrichment.receptor_id, fpr, tpr, enrichment.roc_auc))
    figures.append(plot_roc(cfg, curves, versions))

    sheets = [
        SheetSpec(
            "ranked_compounds",
            ranked_frame,
            "Primary results: compounds ranked by multi-criteria score within each "
            "target. Ranking is on ligand efficiency, never on raw affinity.",
        ),
        SheetSpec(
            "docking_results",
            screen_frame,
            "Per ligand-receptor docking result: affinities for all retained poses, "
            "z-score and empirical p against the null, and detected interactions.",
        ),
        SheetSpec(
            "admet_descriptors",
            admet_frame,
            "RDKit descriptors, the six drug-likeness rule sets with per-criterion "
            "verdicts, PAINS/Brenk alerts, and QSPR predictions with their "
            "applicability-domain verdicts.",
        ),
        SheetSpec(
            "receptor_provenance",
            pd.DataFrame([r.as_record() for r in receptors]),
            "Per receptor: resolution, R-free, method, UniProt accession, co-crystal "
            "ligand, checksums, derived search box, and warnings. 'unavailable' means "
            "the structure does not state it.",
        ),
        SheetSpec(
            "validation_redocking",
            pd.DataFrame([r.as_record() for r in validation.redock]),
            "The redocking gate: in-place RMSD to the crystallographic pose per "
            "receptor, with a diagnosis of any failure.",
        ),
        SheetSpec(
            "validation_enrichment",
            pd.DataFrame([e.as_record() for e in validation.enrichment]),
            "ROC-AUC, BEDROC and enrichment factors against property-matched decoys, "
            "with the attainable EF ceiling alongside each value.",
        ),
        SheetSpec(
            "validation_null",
            pd.DataFrame([validation.nulls[k].summary() for k in sorted(validation.nulls)]),
            "Random-ligand null affinity distribution per receptor, including the "
            "smallest p-value the null set can support.",
        ),
        SheetSpec(
            "interaction_matrix",
            matrix.reset_index() if not matrix.empty else pd.DataFrame(),
            "Binary ligand x (residue, interaction) occupancy for the top-ranked pose, "
            "columns ordered by how many ligands make the contact.",
        ),
        SheetSpec(
            "rejected_ligands",
            pd.read_csv(ligands_summary.rejected_path)
            if Path(ligands_summary.rejected_path).is_file()
            else pd.DataFrame(),
            "Every input molecule that did not enter screening, with a typed reason.",
        ),
        SheetSpec(
            "weight_sensitivity",
            pd.DataFrame(
                [
                    {"receptor_id": k, **{kk: str(vv) for kk, vv in v.items()}}
                    for k, v in (ranking_summary.sensitivity.get("per_receptor") or {}).items()
                ]
            ),
            "Stability of the top-5 shortlist under Dirichlet-resampled multi-criteria weights.",
        ),
    ]

    workbook_path = write_workbook(
        cfg,
        sheets,
        cfg.paths.reports / cfg.report.xlsx_name,
        versions,
        run_id,
        cfg.config_hash(),
        caveats,
    )

    validation_md = write_validation_report(
        cfg, validation, figures, cfg.paths.reports / "VALIDATION.md", run_id
    )
    si_md = _write_si(
        cfg,
        cfg.paths.reports / "SUPPLEMENTARY_INFORMATION.md",
        run_id,
        receptors,
        ligands_summary,
        ranked,
        ranking_summary,
        figures,
        caveats,
        versions,
    )

    bundle = ReportBundle(
        workbook_path=workbook_path.as_posix(),
        validation_md_path=validation_md.as_posix(),
        si_md_path=si_md.as_posix(),
        figures=figures,
        caveats=caveats,
    )
    log.info("Reporting complete", extra=bundle.as_metrics())
    return bundle
