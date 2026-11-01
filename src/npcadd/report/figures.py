"""Publication figures: 300 dpi, vector, colourblind-safe, self-documenting.

Every figure produced here carries a caption that states the parameters and
software versions used to make it. That is not decoration. A figure separated
from its caption is the unit that circulates in talks and slide decks, and a
reader who cannot tell whether a panel came from exhaustiveness 8 or 32 cannot
evaluate it.

Palette: Okabe-Ito, which is distinguishable under the common forms of colour
vision deficiency and reproduces legibly in greyscale.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..config import Config
from ..logging_utils import get_logger

__all__ = [
    "OKABE_ITO",
    "FigureResult",
    "apply_style",
    "plot_class_distribution",
    "plot_efficiency_vs_affinity",
    "plot_interaction_heatmap",
    "plot_null_distribution",
    "plot_property_space",
    "plot_roc",
    "plot_sensitivity",
    "plot_top_compounds",
]

log = get_logger("report.figures")

#: Okabe & Ito's colourblind-safe qualitative palette.
OKABE_ITO: tuple[str, ...] = (
    "#0072B2",  # blue
    "#E69F00",  # orange
    "#009E73",  # bluish green
    "#CC79A7",  # reddish purple
    "#56B4E9",  # sky blue
    "#D55E00",  # vermillion
    "#F0E442",  # yellow
    "#000000",  # black
)

#: Stable colour per compound class, so classes read the same across figures.
CLASS_COLOURS: dict[str, str] = {
    "phenolic": OKABE_ITO[0],
    "terpenoid": OKABE_ITO[2],
    "other": OKABE_ITO[1],
    "unclassified": "#999999",
}


@dataclass(slots=True)
class FigureResult:
    """One produced figure and its auto-generated caption."""

    name: str
    paths: list[Path] = field(default_factory=list)
    caption: str = ""
    skipped_reason: str = ""

    @property
    def produced(self) -> bool:
        return bool(self.paths)


def apply_style(cfg: Config) -> None:
    """Install the publication rcParams. Idempotent."""
    plt.rcParams.update(
        {
            "figure.dpi": 110,
            "savefig.dpi": cfg.report.dpi,
            "font.family": "sans-serif",
            "font.sans-serif": [cfg.report.font_family, "DejaVu Sans"],
            "font.size": cfg.report.base_font_size,
            "axes.titlesize": cfg.report.base_font_size + 1,
            "axes.labelsize": cfg.report.base_font_size,
            "xtick.labelsize": cfg.report.base_font_size - 1,
            "ytick.labelsize": cfg.report.base_font_size - 1,
            "legend.fontsize": cfg.report.base_font_size - 1,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linewidth": 0.5,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
            "pdf.fonttype": 42,  # embed as TrueType so text stays editable
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "axes.prop_cycle": plt.cycler(color=list(OKABE_ITO)),
        }
    )


def _provenance_suffix(cfg: Config, versions: dict[str, str]) -> str:
    """The parameter-and-version sentence appended to every caption."""
    if not cfg.report.caption_includes_versions:
        return ""
    relevant = {
        k: versions.get(k, "?") for k in ("npcadd", "rdkit", "vina", "meeko", "prolif", "numpy")
    }
    tools = ", ".join(f"{k} {v}" for k, v in relevant.items())
    return (
        f" Produced by {tools}; AutoDock Vina scoring '{cfg.docking.scoring}', "
        f"exhaustiveness {cfg.docking.exhaustiveness}, seed {cfg.docking.seed}, "
        f"{cfg.docking.n_poses} poses; ligands protonated at pH {cfg.ligands.ph} "
        f"({cfg.ligands.protonation_method}), {cfg.ligands.n_conformers} conformer(s) "
        f"per ligand from ETKDGv3 seed {cfg.ligands.embed_seed}."
    )


def _save(
    fig: plt.Figure, cfg: Config, name: str, caption: str, versions: dict[str, str]
) -> FigureResult:
    """Save a figure in every configured format and build its caption."""
    cfg.paths.figures.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for extension in cfg.report.formats:
        path = cfg.paths.figures / f"{name}.{extension}"
        fig.savefig(path, format=extension)
        paths.append(path)
    plt.close(fig)
    result = FigureResult(
        name=name, paths=paths, caption=caption + _provenance_suffix(cfg, versions)
    )
    log.info("Wrote figure %s", name, extra={"paths": [p.as_posix() for p in paths]})
    return result


def plot_efficiency_vs_affinity(
    cfg: Config, ranked: pd.DataFrame, versions: dict[str, str]
) -> FigureResult:
    """Ligand efficiency against raw affinity, coloured by class, sized by area %.

    This is the figure that justifies the whole ranking approach: it shows
    directly that raw affinity and size-corrected efficiency order the compound
    set differently.
    """
    if ranked.empty:
        return FigureResult("fig_efficiency_vs_affinity", skipped_reason="no ranked rows")

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(cfg.report.figure_width_in, cfg.report.figure_width_in * 0.44),
        constrained_layout=True,
    )

    for ax, (xcol, xlabel) in zip(
        axes,
        [
            ("best_affinity_kcal_mol", "Vina affinity (kcal/mol)"),
            ("heavy_atoms", "Heavy atoms"),
        ],
        strict=True,
    ):
        for klass, group in ranked.groupby("compound_class"):
            sizes = 12.0 + 6.0 * np.sqrt(np.maximum(group["area_percent"].to_numpy(), 0))
            ax.scatter(
                group[xcol],
                group["ligand_efficiency_kcal_mol_per_ha"],
                s=sizes,
                alpha=0.85,
                label=str(klass),
                color=CLASS_COLOURS.get(str(klass), "#777777"),
                edgecolor="white",
                linewidth=0.4,
            )
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Ligand efficiency (kcal/mol per heavy atom)")

    axes[0].legend(title="Class", frameon=False, loc="best")
    caption = (
        "Ligand efficiency versus raw docking affinity (left) and versus heavy-atom "
        "count (right). Marker area is proportional to the square root of GC-MS area %. "
        "Raw affinity improves with molecular size, so the two axes order the compound "
        "set differently; ranking is performed on efficiency, not on raw affinity."
    )
    return _save(fig, cfg, "fig_efficiency_vs_affinity", caption, versions)


def plot_top_compounds(
    cfg: Config, ranked: pd.DataFrame, versions: dict[str, str], top_n: int = 15
) -> FigureResult:
    """Horizontal bars of the top compounds by multi-criteria score, per receptor."""
    if ranked.empty:
        return FigureResult("fig_top_compounds", skipped_reason="no ranked rows")

    receptors = sorted(ranked["receptor_id"].unique())
    fig, axes = plt.subplots(
        len(receptors),
        1,
        figsize=(
            cfg.report.figure_width_in,
            max(2.2, 0.26 * top_n) * len(receptors),
        ),
        squeeze=False,
        constrained_layout=True,
    )

    for index, receptor in enumerate(receptors):
        ax = axes[index][0]
        group = (
            ranked[ranked["receptor_id"] == receptor]
            .nlargest(top_n, "multi_criteria_score")
            .iloc[::-1]
        )
        colours = [CLASS_COLOURS.get(str(c), "#777777") for c in group["compound_class"]]
        positions = np.arange(len(group))
        ax.barh(positions, group["multi_criteria_score"], color=colours, height=0.72)
        ax.set_yticks(positions)
        ax.set_yticklabels(
            [f"{n}  ({a:.2f}%)" for n, a in zip(group["name"], group["area_percent"], strict=True)]
        )
        ax.set_xlabel("Multi-criteria score (0-1)")
        ax.set_title(f"{receptor}: top {len(group)} by multi-criteria score")
        ax.grid(axis="y", visible=False)

    weights = ", ".join(f"{k} {v:g}" for k, v in sorted(cfg.ranking.weights.items()))
    caption = (
        f"Top compounds per target by multi-criteria score, a weighted sum of "
        f"min-max-normalised ligand efficiency, ADMET rule pass rate and GC-MS area % "
        f"(weights: {weights}). Bar colour encodes compound class; GC-MS area % is "
        f"given in parentheses. Normalisation is performed within each target, because "
        f"affinities from different targets are not on a common scale."
    )
    return _save(fig, cfg, "fig_top_compounds", caption, versions)


def plot_property_space(cfg: Config, admet: pd.DataFrame, versions: dict[str, str]) -> FigureResult:
    """cLogP vs MW and TPSA vs MW, with the Lipinski and Veber boundaries drawn."""
    if admet.empty:
        return FigureResult("fig_property_space", skipped_reason="no ADMET rows")

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(cfg.report.figure_width_in, cfg.report.figure_width_in * 0.44),
        constrained_layout=True,
    )

    panels = [
        ("clogp", "cLogP", 5.0, "Lipinski cLogP <= 5"),
        ("tpsa", "TPSA (A$^2$)", 140.0, "Veber TPSA <= 140"),
    ]
    for ax, (column, label, boundary, boundary_label) in zip(axes, panels, strict=True):
        for klass, group in admet.groupby("compound_class"):
            ax.scatter(
                group["mw"],
                group[column],
                s=18,
                alpha=0.85,
                label=str(klass),
                color=CLASS_COLOURS.get(str(klass), "#777777"),
                edgecolor="white",
                linewidth=0.4,
            )
        ax.axhline(boundary, color="#D55E00", linestyle="--", linewidth=0.9)
        ax.axvline(500.0, color="#555555", linestyle=":", linewidth=0.9)
        ax.set_xlabel("Molecular weight (g/mol)")
        ax.set_ylabel(label)
        ax.annotate(
            boundary_label,
            xy=(0.02, 0.95),
            xycoords="axes fraction",
            fontsize=cfg.report.base_font_size - 2,
            color="#D55E00",
            va="top",
        )
    axes[0].legend(title="Class", frameon=False, loc="lower right")

    caption = (
        "Physicochemical property space of the screened set. Dashed lines mark the "
        "Lipinski cLogP and Veber TPSA boundaries; the dotted vertical line marks "
        "MW 500. Descriptors are RDKit-computed from the docked (protonated) species."
    )
    return _save(fig, cfg, "fig_property_space", caption, versions)


def plot_class_distribution(
    cfg: Config, admet: pd.DataFrame, versions: dict[str, str]
) -> FigureResult:
    """Composition of the screened set by class, by count and by summed area %."""
    if admet.empty:
        return FigureResult("fig_class_distribution", skipped_reason="no ADMET rows")

    counts = admet.groupby("compound_class").size().sort_values(ascending=False)
    areas = admet.groupby("compound_class")["area_percent"].sum().reindex(counts.index)

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(cfg.report.figure_width_in, cfg.report.figure_width_in * 0.36),
        constrained_layout=True,
    )
    colours = [CLASS_COLOURS.get(str(c), "#777777") for c in counts.index]
    axes[0].bar(counts.index.astype(str), counts.to_numpy(), color=colours)
    axes[0].set_ylabel("Number of compounds")
    axes[1].bar(areas.index.astype(str), areas.to_numpy(), color=colours)
    axes[1].set_ylabel("Summed GC-MS area (%)")
    for ax in axes:
        ax.set_xlabel("Compound class")
        ax.grid(axis="x", visible=False)

    caption = (
        "Composition of the screened set by compound class, as compound count (left) "
        "and as summed GC-MS area % (right). The two differ because abundance and "
        "chemotype count are independent: a class may dominate the chromatogram while "
        "contributing few distinct components."
    )
    return _save(fig, cfg, "fig_class_distribution", caption, versions)


def plot_null_distribution(
    cfg: Config,
    nulls: dict[str, Any],
    ranked: pd.DataFrame,
    versions: dict[str, str],
) -> FigureResult:
    """Null affinity distribution per receptor, with the screened hits overlaid."""
    if not nulls:
        return FigureResult(
            "fig_null_distribution", skipped_reason="no null distribution was built"
        )

    receptors = sorted(nulls)
    fig, axes = plt.subplots(
        len(receptors),
        1,
        figsize=(cfg.report.figure_width_in, 2.1 * len(receptors)),
        squeeze=False,
        constrained_layout=True,
    )

    for index, receptor in enumerate(receptors):
        ax = axes[index][0]
        null = nulls[receptor]
        affinities = np.asarray(null.affinities, dtype=np.float64)
        ax.hist(
            affinities,
            bins=min(25, max(5, affinities.size // 3)),
            color=OKABE_ITO[4],
            alpha=0.75,
            edgecolor="white",
            linewidth=0.4,
            label=f"random-ligand null (n={affinities.size})",
        )
        ax.axvline(
            null.mean,
            color="#000000",
            linestyle="-",
            linewidth=1.0,
            label=f"null mean {null.mean:.2f}",
        )
        ax.axvline(
            null.mean - 2 * null.std,
            color="#D55E00",
            linestyle="--",
            linewidth=0.9,
            label="null mean - 2 SD",
        )
        hits = ranked[ranked["receptor_id"] == receptor]["best_affinity_kcal_mol"]
        if not hits.empty:
            ax.plot(
                hits.to_numpy(),
                np.full(hits.size, ax.get_ylim()[1] * 0.06),
                marker="|",
                linestyle="none",
                color=OKABE_ITO[5],
                markersize=9,
                label=f"screened compounds (n={hits.size})",
            )
        ax.set_xlabel("Vina affinity (kcal/mol)")
        ax.set_ylabel("Count")
        ax.set_title(f"{receptor}: screened affinities against the random-ligand null")
        ax.legend(frameon=False, fontsize=cfg.report.base_font_size - 2)

    caption = (
        "Empirical null distribution of docking affinities per target, built by docking "
        "a random-ligand set under identical parameters, with the screened compounds "
        "overlaid as ticks. The null is what converts an affinity into a z-score and a "
        "one-sided empirical p-value; a compound whose affinity falls inside the bulk "
        "of the null is not distinguishable from a ligand with no reason to bind."
    )
    return _save(fig, cfg, "fig_null_distribution", caption, versions)


def plot_roc(
    cfg: Config,
    curves: Sequence[tuple[str, np.ndarray, np.ndarray, float]],
    versions: dict[str, str],
) -> FigureResult:
    """ROC curves for actives against property-matched decoys."""
    if not curves:
        return FigureResult(
            "fig_roc",
            skipped_reason="enrichment was not performed (no known actives supplied)",
        )

    fig, ax = plt.subplots(
        figsize=(cfg.report.figure_width_in * 0.55, cfg.report.figure_width_in * 0.5),
        constrained_layout=True,
    )
    for index, (receptor, fpr, tpr, auc) in enumerate(curves):
        ax.plot(
            fpr,
            tpr,
            color=OKABE_ITO[index % len(OKABE_ITO)],
            linewidth=1.4,
            label=f"{receptor} (AUC {auc:.3f})",
        )
    ax.plot([0, 1], [0, 1], color="#999999", linestyle="--", linewidth=0.9, label="random")
    ax.set_xlabel("False positive rate")
    ax.set_ylabel("True positive rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, loc="lower right")

    caption = (
        "ROC curves for the retrospective discrimination of known actives from "
        "property-matched decoys, per target. Decoys are presumed inactive by "
        "construction (property-matched and topologically dissimilar to the actives), "
        "not experimentally confirmed inactives."
    )
    return _save(fig, cfg, "fig_roc", caption, versions)


def plot_interaction_heatmap(
    cfg: Config, matrix: pd.DataFrame, versions: dict[str, str], max_columns: int = 30
) -> FigureResult:
    """Binary ligand x contact occupancy heatmap."""
    if matrix.empty:
        return FigureResult("fig_interaction_heatmap", skipped_reason="no interaction fingerprints")

    trimmed = matrix.iloc[:, :max_columns]
    fig, ax = plt.subplots(
        figsize=(
            cfg.report.figure_width_in,
            max(2.2, 0.20 * len(trimmed.index) + 1.6),
        ),
        constrained_layout=True,
    )
    ax.imshow(
        trimmed.to_numpy(),
        aspect="auto",
        cmap=matplotlib.colors.ListedColormap(["#F2F2F2", OKABE_ITO[0]]),
        vmin=0,
        vmax=1,
        interpolation="nearest",
    )
    ax.set_xticks(np.arange(len(trimmed.columns)))
    ax.set_xticklabels(trimmed.columns, rotation=90, fontsize=cfg.report.base_font_size - 3)
    ax.set_yticks(np.arange(len(trimmed.index)))
    ax.set_yticklabels(trimmed.index, fontsize=cfg.report.base_font_size - 3)
    ax.grid(visible=False)

    caption = (
        f"Protein-ligand interaction fingerprints of the top-ranked pose per compound, "
        f"showing the {len(trimmed.columns)} most frequently occupied "
        f"residue-interaction pairs (ProLIF; filled = detected). Fingerprints are "
        f"geometric assertions that a distance-and-angle criterion is satisfied; they "
        f"are not energetic contributions, and at resolutions worse than ~2.5 A "
        f"side-chain positions do not support residue-level interpretation."
    )
    return _save(fig, cfg, "fig_interaction_heatmap", caption, versions)


def plot_sensitivity(
    cfg: Config, sensitivity: dict[str, Any], versions: dict[str, str]
) -> FigureResult:
    """How often each compound stays in the top-5 under resampled weights."""
    per_receptor = sensitivity.get("per_receptor") or {}
    if not per_receptor:
        return FigureResult("fig_weight_sensitivity", skipped_reason="no sensitivity data")

    receptors = sorted(per_receptor)
    fig, axes = plt.subplots(
        len(receptors),
        1,
        figsize=(cfg.report.figure_width_in, max(2.0, 1.9 * len(receptors))),
        squeeze=False,
        constrained_layout=True,
    )
    for index, receptor in enumerate(receptors):
        ax = axes[index][0]
        frequencies = per_receptor[receptor].get("top_k_appearance_frequency", {})
        if not frequencies:
            continue
        names = list(frequencies)[::-1]
        values = [frequencies[n] for n in names]
        ax.barh(np.arange(len(names)), values, color=OKABE_ITO[3], height=0.72)
        ax.set_yticks(np.arange(len(names)))
        ax.set_yticklabels(names)
        ax.set_xlim(0, 1)
        ax.set_xlabel("Fraction of resampled weightings in which the compound is top-5")
        stability = per_receptor[receptor].get("mean_jaccard_vs_baseline")
        ax.set_title(f"{receptor}: top-5 stability (mean Jaccard {stability})")
        ax.grid(axis="y", visible=False)

    caption = (
        f"Sensitivity of the shortlist to the multi-criteria weighting. Weights were "
        f"resampled {sensitivity.get('n_samples')} times from a Dirichlet distribution "
        f"centred on the configured values (alpha={sensitivity.get('dirichlet_alpha')}, "
        f"seed={sensitivity.get('seed')}); bars show how often each compound remains in "
        f"the top five. A compound appearing in a small fraction of resamplings owes its "
        f"position to the weighting rather than to the data."
    )
    return _save(fig, cfg, "fig_weight_sensitivity", caption, versions)
