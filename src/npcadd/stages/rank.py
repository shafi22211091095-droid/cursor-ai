"""Stage 6: ranking. Scientific correctness is enforced here, in code.

The rule this module exists to enforce
--------------------------------------
**Compounds are never ranked by raw docking affinity.** Vina's score is
approximately extensive in molecular size: add heavy atoms and the score
improves, almost regardless of whether the added atoms do anything useful.
Ranking a GC-MS component list — which spans C6 phenols to C30 triterpenes — by
raw score therefore ranks it largely by molecular weight, and the "top hits"
are simply the biggest molecules present.

Measured on the bundled sample: loliolide (14 heavy atoms) scores
-7.58 kcal/mol and spathulenol (16 heavy atoms) scores -5.15. Imatinib (37
heavy atoms) scores -13.28. Imatinib is a real nanomolar inhibitor of this
target so its score is earned, but the ordering of the two terpenoids inverts
once size is accounted for, and that is the general case rather than the
exception.

So the ranking key is **ligand efficiency** and its relatives:

* ``LE   = -affinity / heavy_atoms``                  (kcal/mol per heavy atom)
* ``LLE  = pKd_estimate - cLogP``                     (lipophilicity-corrected)
* ``LELP = cLogP / LE``                               (lipophilicity per efficiency)
* ``FQ``  — fit quality, LE normalised for the known
  size-dependence of achievable LE, after Reynolds et al.

:func:`rank_compounds` refuses to produce a table at all if asked to sort by
raw affinity, and the multi-criteria score combines normalised LE, ADMET pass
rate and GC-MS area % with weights from config, accompanied by a sensitivity
analysis over those weights.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import Config
from ..logging_utils import get_logger
from .admet import AdmetRow
from .dock import ScreenRow

__all__ = [
    "EfficiencyMetrics",
    "RankedRow",
    "RankingSummary",
    "binding_efficiency_index",
    "fit_quality",
    "ligand_efficiency",
    "lipophilic_efficiency",
    "rank_compounds",
    "sensitivity_analysis",
    "size_independent_ligand_efficiency",
    "surface_efficiency_index",
]

log = get_logger("stages.rank")

#: Gas constant in kcal/(mol K).
R_KCAL = 1.987204259e-3

#: Columns that must never be used as the primary sort key.
FORBIDDEN_SORT_KEYS = frozenset(
    {"best_affinity_kcal_mol", "affinity", "docking_score", "vina_score"}
)


def ligand_efficiency(affinity_kcal_mol: float, heavy_atoms: int) -> float:
    """Ligand efficiency: binding energy per heavy atom.

    ``LE = -affinity / heavy_atoms``, so a more negative affinity gives a larger
    (better) LE. Hopkins, Groom & Alex, *Drug Discov. Today* 2004, 9, 430-431.

    Raises:
        ValueError: if ``heavy_atoms`` is not positive.
    """
    if heavy_atoms <= 0:
        raise ValueError(f"heavy_atoms must be positive; got {heavy_atoms}")
    return float(-affinity_kcal_mol / heavy_atoms)


def pkd_estimate(affinity_kcal_mol: float, temperature_k: float = 298.15) -> float:
    """Convert a docking affinity to a notional pKd.

    ``dG = -RT ln(Ka)`` gives ``pKd = -affinity / (2.303 RT)``.

    This is a **unit conversion, not a measurement**. Vina's score is an
    empirical function fitted to binding data; it is not a free energy, and the
    resulting pKd should be read as "the score expressed on a log-affinity
    scale" so that LLE is comparable to published values. It must never be
    reported as a predicted binding constant.
    """
    if temperature_k <= 0:
        raise ValueError(f"temperature_k must be positive; got {temperature_k}")
    return float(-affinity_kcal_mol / (2.302585093 * R_KCAL * temperature_k))


def lipophilic_efficiency(
    affinity_kcal_mol: float, clogp: float, temperature_k: float = 298.15
) -> float:
    """Lipophilic ligand efficiency: ``LLE = pKd - cLogP``.

    Penalises affinity bought with greasiness, which matters acutely for
    terpenoid natural products: a C15-C30 hydrocarbon skeleton can score well
    on hydrophobic contact alone while being a poor starting point.
    Leeson & Springthorpe, *Nat. Rev. Drug Discov.* 2007, 6, 881-890.
    """
    return float(pkd_estimate(affinity_kcal_mol, temperature_k) - clogp)


def lelp(affinity_kcal_mol: float, clogp: float, heavy_atoms: int) -> float:
    """Ligand-efficiency-dependent lipophilicity: ``LELP = cLogP / LE``.

    Smaller magnitude is better. Returns NaN when LE is zero rather than raising,
    because a zero-affinity row is a legitimate (if useless) screening result.
    """
    efficiency = ligand_efficiency(affinity_kcal_mol, heavy_atoms)
    if efficiency == 0:
        return float("nan")
    return float(clogp / efficiency)


def _max_achievable_le(heavy_atoms: int) -> float:
    """Reynolds' empirical size-dependent maximal ligand efficiency.

    Reynolds, Tounge & Bembenek, *J. Med. Chem.* 2008, 51, 2432-2438, fit the
    maximum observed LE across ChEMBL as a decaying function of heavy-atom
    count. Fit used here: ``LE_max = 0.0715 + 7.5328/HA + 25.7079/HA^2 -
    361.4722/HA^3``.
    """
    if heavy_atoms <= 0:
        raise ValueError(f"heavy_atoms must be positive; got {heavy_atoms}")
    ha = float(heavy_atoms)
    return 0.0715 + 7.5328 / ha + 25.7079 / (ha**2) - 361.4722 / (ha**3)


def fit_quality(affinity_kcal_mol: float, heavy_atoms: int) -> float:
    """Fit quality: LE divided by the maximum LE achievable at that size.

    ``FQ`` near 1.0 means the compound is about as efficient as anything of its
    size can be. It is the fairest single-number comparison across a set whose
    members differ greatly in size, which is exactly the GC-MS situation.
    Clamped at 0 from below; values above 1 are possible and are left as-is
    because they are informative (and usually indicate a scoring artefact).
    """
    ceiling = _max_achievable_le(heavy_atoms)
    if ceiling <= 0:
        return float("nan")
    return max(0.0, ligand_efficiency(affinity_kcal_mol, heavy_atoms) / ceiling)


def binding_efficiency_index(
    affinity_kcal_mol: float, mw: float, temperature_k: float = 298.15
) -> float:
    """Binding efficiency index: ``BEI = pKd / (MW / 1000)``.

    Potency per kilodalton of molecular weight. Abad-Zapatero & Metz,
    *Drug Discov. Today* 2005, 10, 464-469. Reported for comparability with
    ChEMBL, which curates BEI alongside its activities; as with every metric
    here it is built on the docking-derived pKd estimate (a log-affinity-scaled
    score, not a measured constant), not an experimental Kd.

    Returns NaN for non-positive MW rather than raising.
    """
    if mw <= 0:
        return float("nan")
    return float(pkd_estimate(affinity_kcal_mol, temperature_k) / mw * 1000.0)


def surface_efficiency_index(
    affinity_kcal_mol: float, tpsa: float, temperature_k: float = 298.15
) -> float:
    """Surface efficiency index: ``SEI = pKd / (TPSA / 100)``.

    Potency per 100 square-angstrom of polar surface. Abad-Zapatero & Metz,
    *Drug Discov. Today* 2005. Read together with BEI it separates size- from
    polarity-driven efficiency. Returns NaN for non-positive TPSA.
    """
    if tpsa <= 0:
        return float("nan")
    return float(pkd_estimate(affinity_kcal_mol, temperature_k) / tpsa * 100.0)


def size_independent_ligand_efficiency(
    affinity_kcal_mol: float, heavy_atoms: int, temperature_k: float = 298.15
) -> float:
    """Size-independent ligand efficiency: ``SILE = pKd / HA**0.3``.

    Nissink, *J. Chem. Inf. Model.* 2009, 49, 1617-1622. Unlike plain LE, whose
    expected value falls with size, SILE is designed to be roughly flat across a
    size range, which makes it well suited to a GC-MS set spanning C6 to C30.

    Raises:
        ValueError: if ``heavy_atoms`` is not positive.
    """
    if heavy_atoms <= 0:
        raise ValueError(f"heavy_atoms must be positive; got {heavy_atoms}")
    return float(pkd_estimate(affinity_kcal_mol, temperature_k) / (float(heavy_atoms) ** 0.3))


@dataclass(slots=True, frozen=True)
class EfficiencyMetrics:
    """All size- and lipophilicity-corrected efficiency metrics for one row."""

    affinity_kcal_mol: float
    heavy_atoms: int
    clogp: float
    mw: float
    tpsa: float
    ligand_efficiency: float
    pkd_estimate: float
    lipophilic_efficiency: float
    lelp: float
    fit_quality: float
    binding_efficiency_index: float
    surface_efficiency_index: float
    size_independent_le: float

    def as_record(self) -> dict[str, Any]:
        def _finite(value: float, ndigits: int) -> float | None:
            return round(value, ndigits) if math.isfinite(value) else None

        return {
            "best_affinity_kcal_mol": round(self.affinity_kcal_mol, 3),
            "heavy_atoms": self.heavy_atoms,
            "mw": round(self.mw, 2),
            "tpsa": round(self.tpsa, 2),
            "clogp": round(self.clogp, 3),
            "ligand_efficiency_kcal_mol_per_ha": round(self.ligand_efficiency, 4),
            "pkd_estimate_from_score": round(self.pkd_estimate, 3),
            "lipophilic_efficiency_lle": round(self.lipophilic_efficiency, 3),
            "lelp": _finite(self.lelp, 3),
            "fit_quality": _finite(self.fit_quality, 4),
            "binding_efficiency_index_bei": _finite(self.binding_efficiency_index, 3),
            "surface_efficiency_index_sei": _finite(self.surface_efficiency_index, 3),
            "size_independent_le_sile": _finite(self.size_independent_le, 4),
        }


def compute_efficiency(
    affinity_kcal_mol: float,
    heavy_atoms: int,
    clogp: float,
    mw: float,
    tpsa: float,
    temperature_k: float,
) -> EfficiencyMetrics:
    """Assemble every efficiency metric for one ligand-receptor result."""
    return EfficiencyMetrics(
        affinity_kcal_mol=affinity_kcal_mol,
        heavy_atoms=heavy_atoms,
        clogp=clogp,
        mw=mw,
        tpsa=tpsa,
        ligand_efficiency=ligand_efficiency(affinity_kcal_mol, heavy_atoms),
        pkd_estimate=pkd_estimate(affinity_kcal_mol, temperature_k),
        lipophilic_efficiency=lipophilic_efficiency(affinity_kcal_mol, clogp, temperature_k),
        lelp=lelp(affinity_kcal_mol, clogp, heavy_atoms),
        fit_quality=fit_quality(affinity_kcal_mol, heavy_atoms),
        binding_efficiency_index=binding_efficiency_index(affinity_kcal_mol, mw, temperature_k),
        surface_efficiency_index=surface_efficiency_index(affinity_kcal_mol, tpsa, temperature_k),
        size_independent_le=size_independent_ligand_efficiency(
            affinity_kcal_mol, heavy_atoms, temperature_k
        ),
    )


def _min_max_normalise(values: np.ndarray) -> np.ndarray:
    """Scale to [0, 1]. A constant vector maps to 0.5, not 0 or 1.

    Mapping a constant column to 0.5 keeps it neutral in the weighted sum; the
    alternative (all zeros) would silently zero out that criterion's weight and
    quietly re-weight the whole score.
    """
    finite = np.isfinite(values)
    if not finite.any():
        return np.full(values.shape, 0.5)
    low = float(values[finite].min())
    high = float(values[finite].max())
    out = np.full(values.shape, 0.5, dtype=np.float64)
    if high > low:
        out[finite] = (values[finite] - low) / (high - low)
    return out


@dataclass(slots=True)
class RankedRow:
    """One ranked compound-target pair."""

    ligand_id: str
    name: str
    compound_class: str
    area_percent: float
    receptor_id: str
    activity: str
    target_name: str
    efficiency: EfficiencyMetrics
    admet_pass_rate: float
    z_score: float | None
    empirical_p: float | None
    n_contacts: int
    multi_criteria_score: float = 0.0
    rank: int = 0
    above_area_threshold: bool = False
    normalised: dict[str, float] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "rank": self.rank,
            "ligand_id": self.ligand_id,
            "name": self.name,
            "compound_class": self.compound_class,
            "area_percent": self.area_percent,
            "above_area_threshold": self.above_area_threshold,
            "receptor_id": self.receptor_id,
            "activity": self.activity,
            "target_name": self.target_name,
        }
        record.update(self.efficiency.as_record())
        record.update(
            {
                "admet_pass_rate": round(self.admet_pass_rate, 4),
                "z_score_vs_null": self.z_score,
                "empirical_p_vs_null": self.empirical_p,
                "n_contacts": self.n_contacts,
                "multi_criteria_score": round(self.multi_criteria_score, 5),
            }
        )
        record.update({f"normalised_{k}": round(v, 4) for k, v in sorted(self.normalised.items())})
        record["flags"] = "; ".join(self.flags)
        return record


@dataclass(slots=True)
class RankingSummary:
    """Aggregate ranking outcome."""

    n_rows: int
    n_above_area_threshold: int
    weights: dict[str, float]
    spearman_vs_raw_affinity: dict[str, float] = field(default_factory=dict)
    sensitivity: dict[str, Any] = field(default_factory=dict)
    results_path: str = ""

    def as_metrics(self) -> dict[str, Any]:
        return {
            "n_rows": self.n_rows,
            "n_above_area_threshold": self.n_above_area_threshold,
            "weights": self.weights,
            "spearman_rank_correlation_vs_raw_affinity": self.spearman_vs_raw_affinity,
            "sensitivity_top5_stability": self.sensitivity.get("top5_stability"),
        }


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation, with average ranks for ties."""
    if a.size < 3:
        return float("nan")

    def rank(values: np.ndarray) -> np.ndarray:
        order = np.argsort(values, kind="mergesort")
        ranks = np.empty(values.size, dtype=np.float64)
        i = 0
        sorted_values = values[order]
        while i < values.size:
            j = i
            while j + 1 < values.size and sorted_values[j + 1] == sorted_values[i]:
                j += 1
            ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return ranks

    ra, rb = rank(a), rank(b)
    ra -= ra.mean()
    rb -= rb.mean()
    denominator = math.sqrt(float((ra**2).sum()) * float((rb**2).sum()))
    if denominator == 0:
        return float("nan")
    return float((ra * rb).sum() / denominator)


def rank_compounds(
    cfg: Config,
    screen_rows: list[ScreenRow],
    admet_rows: list[AdmetRow],
    out_dir: Path,
    sort_by: str = "multi_criteria_score",
) -> tuple[list[RankedRow], RankingSummary]:
    """Rank compounds per receptor on efficiency-corrected, multi-criteria score.

    Args:
        sort_by: Must not be a raw-affinity column. Attempting to sort by raw
            affinity raises, by design.

    Raises:
        ValueError: if ``sort_by`` is a forbidden raw-score column, or if no
            usable rows remain.
    """
    if sort_by in FORBIDDEN_SORT_KEYS:
        raise ValueError(
            f"Refusing to rank by {sort_by!r}. Raw docking affinity is approximately "
            "extensive in molecular size, so ranking a compound set that spans a wide "
            "size range by raw score ranks it largely by molecular weight. Rank by "
            "'ligand_efficiency_kcal_mol_per_ha', 'fit_quality' or "
            "'multi_criteria_score' instead. This restriction is deliberate and is "
            "documented in DECISIONS.md."
        )

    admet_index = {row.ligand_id: row for row in admet_rows}
    weights = dict(cfg.ranking.weights)

    rows: list[RankedRow] = []
    for screen in screen_rows:
        if not screen.ok or screen.best_affinity_kcal_mol is None:
            continue
        admet = admet_index.get(screen.ligand_id)
        if admet is None:
            log.warning(
                "No ADMET profile for a docked ligand; excluded from ranking",
                extra={"ligand": screen.ligand_id},
            )
            continue

        heavy_atoms = int(admet.descriptors["heavy_atoms"])
        efficiency = compute_efficiency(
            screen.best_affinity_kcal_mol,
            heavy_atoms,
            admet.descriptors["clogp"],
            admet.descriptors["mw"],
            admet.descriptors["tpsa"],
            cfg.ranking.reference_temperature_k,
        )

        flags: list[str] = []
        if admet.alerts.get("pains_flagged"):
            flags.append(
                f"PAINS: {admet.alerts.get('pains_filters')} "
                "(assay-interference liability, not a verdict on the compound)"
            )
        if admet.alerts.get("brenk_flagged"):
            flags.append(f"Brenk: {admet.alerts.get('brenk_filters')}")
        if int(admet.ml_predictions.get("n_ml_out_of_domain", 0) or 0) > 0:
            flags.append(
                "at least one QSPR prediction is outside its applicability domain and "
                "is excluded from the score"
            )
        if admet.descriptors.get("n_unspecified_stereocentres", 0) > 0:
            flags.append(
                f"{int(admet.descriptors['n_unspecified_stereocentres'])} unspecified "
                "stereocentre(s): the docked stereoisomer is one of several consistent "
                "with the identification"
            )
        if (
            screen.empirical_p is not None
            and screen.p_resolution is not None
            and (screen.empirical_p <= screen.p_resolution)
        ):
            flags.append(
                f"empirical p is at the resolution limit of the null set "
                f"(p <= {screen.p_resolution:g}); a larger null set is needed to "
                "distinguish it further"
            )

        rows.append(
            RankedRow(
                ligand_id=screen.ligand_id,
                name=screen.name,
                compound_class=screen.compound_class,
                area_percent=screen.area_percent,
                receptor_id=screen.receptor_id,
                activity=screen.activity,
                target_name=screen.target_name,
                efficiency=efficiency,
                admet_pass_rate=admet.admet_pass_rate,
                z_score=screen.z_score,
                empirical_p=screen.empirical_p,
                n_contacts=screen.n_contacts,
                above_area_threshold=screen.area_percent >= cfg.ranking.area_highlight_threshold,
                flags=flags,
            )
        )

    if not rows:
        raise ValueError(
            "No rows survived ranking: every docking either failed or lacked an ADMET profile."
        )

    # Normalisation is per receptor: affinities from different targets are not
    # on a common scale, so pooling them before normalising would be wrong.
    summary_correlations: dict[str, float] = {}
    for receptor_id in sorted({r.receptor_id for r in rows}):
        group = [r for r in rows if r.receptor_id == receptor_id]
        le = np.array([r.efficiency.ligand_efficiency for r in group], dtype=np.float64)
        admet_rate = np.array([r.admet_pass_rate for r in group], dtype=np.float64)
        area = np.array([r.area_percent for r in group], dtype=np.float64)

        norm_le = _min_max_normalise(le)
        norm_admet = _min_max_normalise(admet_rate)
        norm_area = _min_max_normalise(area)

        for index, row in enumerate(group):
            row.normalised = {
                "ligand_efficiency": float(norm_le[index]),
                "admet_pass_rate": float(norm_admet[index]),
                "gcms_area": float(norm_area[index]),
            }
            row.multi_criteria_score = float(
                weights["ligand_efficiency"] * norm_le[index]
                + weights["admet_pass_rate"] * norm_admet[index]
                + weights["gcms_area"] * norm_area[index]
            )

        raw = np.array([r.efficiency.affinity_kcal_mol for r in group], dtype=np.float64)
        summary_correlations[receptor_id] = round(
            _spearman(-raw, np.array([r.multi_criteria_score for r in group])), 4
        )

    # Final ordering. Compounds at or above the area threshold are sorted into
    # the upper rows, as the manuscript's tables require, and within each band
    # the ordering is by the multi-criteria score.
    rows.sort(
        key=lambda r: (
            r.receptor_id,
            not r.above_area_threshold,
            -r.multi_criteria_score,
            r.name,
        )
    )
    for receptor_id in {r.receptor_id for r in rows}:
        for position, row in enumerate([r for r in rows if r.receptor_id == receptor_id], start=1):
            row.rank = position

    results_path = out_dir / "ranked_compounds.csv"
    pd.DataFrame([r.as_record() for r in rows]).to_csv(
        results_path, index=False, lineterminator="\n"
    )

    sensitivity = sensitivity_analysis(cfg, rows)
    summary = RankingSummary(
        n_rows=len(rows),
        n_above_area_threshold=sum(1 for r in rows if r.above_area_threshold),
        weights=weights,
        spearman_vs_raw_affinity=summary_correlations,
        sensitivity=sensitivity,
        results_path=results_path.as_posix(),
    )
    log.info("Ranking complete", extra=summary.as_metrics())
    return rows, summary


def sensitivity_analysis(cfg: Config, rows: list[RankedRow]) -> dict[str, Any]:
    """Resample the weights and report how much the ranking moves.

    Weights are drawn from a Dirichlet distribution centred on the configured
    values. For each draw the top-5 set is recomputed per receptor. The reported
    stability is the mean Jaccard overlap between the resampled top-5 and the
    top-5 from the configured weights.

    A low stability does not invalidate the ranking; it means the ranking is a
    product of the weighting choice rather than of the data, and the manuscript
    should say so instead of presenting one weighting as the answer.
    """
    rng = np.random.default_rng(cfg.ranking.sensitivity_seed)
    alpha = cfg.ranking.sensitivity_dirichlet_alpha
    keys = ["ligand_efficiency", "admet_pass_rate", "gcms_area"]
    base = np.array([cfg.ranking.weights[k] for k in keys], dtype=np.float64)

    per_receptor: dict[str, dict[str, Any]] = {}
    for receptor_id in sorted({r.receptor_id for r in rows}):
        group = [r for r in rows if r.receptor_id == receptor_id]
        if len(group) < 2:
            continue
        matrix = np.array([[r.normalised[k] for k in keys] for r in group], dtype=np.float64)
        names = [r.name for r in group]

        baseline_scores = matrix @ base
        k = min(5, len(group))
        baseline_top = {names[i] for i in np.argsort(-baseline_scores)[:k]}

        overlaps: list[float] = []
        appearances: dict[str, int] = {}
        for _ in range(cfg.ranking.sensitivity_n_samples):
            drawn = rng.dirichlet(base * alpha / base.sum() * len(keys))
            scores = matrix @ drawn
            top = {names[i] for i in np.argsort(-scores)[:k]}
            overlaps.append(len(top & baseline_top) / len(top | baseline_top))
            for name in top:
                appearances[name] = appearances.get(name, 0) + 1

        per_receptor[receptor_id] = {
            "baseline_top_k": sorted(baseline_top),
            "mean_jaccard_vs_baseline": round(float(np.mean(overlaps)), 4),
            "min_jaccard_vs_baseline": round(float(np.min(overlaps)), 4),
            "top_k_appearance_frequency": {
                name: round(count / cfg.ranking.sensitivity_n_samples, 4)
                for name, count in sorted(appearances.items(), key=lambda kv: -kv[1])[:10]
            },
        }

    stabilities = [v["mean_jaccard_vs_baseline"] for v in per_receptor.values()]
    return {
        "n_samples": cfg.ranking.sensitivity_n_samples,
        "dirichlet_alpha": alpha,
        "seed": cfg.ranking.sensitivity_seed,
        "per_receptor": per_receptor,
        "top5_stability": round(float(np.mean(stabilities)), 4) if stabilities else None,
        "interpretation": (
            "Mean Jaccard overlap between the top-5 under resampled weights and the "
            "top-5 under the configured weights. Above ~0.8 the shortlist is a "
            "property of the data; below ~0.5 it is substantially a property of the "
            "chosen weighting and must be reported as such."
        ),
    }
