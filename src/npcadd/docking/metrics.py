"""Virtual-screening performance metrics and null-distribution statistics.

All functions here are pure and operate on plain arrays, which is what makes
the statistical claims in the manuscript testable rather than asserted.

Sign convention
---------------
Docking affinities are negative-is-better (kcal/mol). Every ranking metric in
this module takes a **score where larger is better**, so callers pass
``-affinity``. The convention is stated in each signature because getting it
backwards silently inverts ROC-AUC around 0.5, which looks like a result
instead of a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..logging_utils import get_logger

__all__ = [
    "EnrichmentReport",
    "NullDistribution",
    "bedroc",
    "enrichment_factor",
    "roc_auc",
    "roc_curve",
]

log = get_logger("docking.metrics")


def _validate_labels_scores(
    labels: np.ndarray, scores: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    y = np.asarray(labels).astype(int).ravel()
    s = np.asarray(scores, dtype=np.float64).ravel()
    if y.size != s.size:
        raise ValueError(f"labels and scores differ in length: {y.size} vs {s.size}")
    if y.size == 0:
        raise ValueError("labels and scores are empty")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("labels must contain only 0 (decoy) and 1 (active)")
    if not np.isfinite(s).all():
        raise ValueError("scores contain non-finite values")
    n_active = int(y.sum())
    if n_active == 0:
        raise ValueError(
            "No actives in the label vector, so no enrichment metric is defined. "
            "Supply known actives via validation.known_actives_csv."
        )
    if n_active == y.size:
        raise ValueError("No decoys in the label vector, so no enrichment metric is defined.")
    return y, s


def roc_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    """ROC-AUC computed from the rank-sum (Mann-Whitney U) identity.

    Ties are handled by averaging their ranks, which is the standard correction
    and matters here because docking scores are reported to 3 decimal places and
    genuinely tie.

    Args:
        labels: 1 for active, 0 for decoy.
        scores: Larger is better. Pass ``-affinity`` for docking scores.
    """
    y, s = _validate_labels_scores(labels, scores)
    n_pos = int(y.sum())
    n_neg = y.size - n_pos

    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(s.size, dtype=np.float64)
    sorted_scores = s[order]
    i = 0
    while i < s.size:
        j = i
        while j + 1 < s.size and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        # Ranks are 1-based; assign the mean rank to every member of the tie.
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1

    rank_sum_pos = float(ranks[y == 1].sum())
    u = rank_sum_pos - n_pos * (n_pos + 1) / 2.0
    return float(u / (n_pos * n_neg))


def roc_curve(labels: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(fpr, tpr)`` arrays including the (0,0) and (1,1) endpoints."""
    y, s = _validate_labels_scores(labels, scores)
    order = np.argsort(-s, kind="mergesort")
    y_sorted = y[order]
    tp = np.cumsum(y_sorted)
    fp = np.cumsum(1 - y_sorted)
    tpr = np.concatenate(([0.0], tp / tp[-1]))
    fpr = np.concatenate(([0.0], fp / fp[-1]))
    return fpr, tpr


def enrichment_factor(
    labels: np.ndarray, scores: np.ndarray, fraction: float
) -> tuple[float, float]:
    """Enrichment factor at a screened fraction of the library.

    ``EF_x = (actives in top x) / (total actives) / x``

    An EF of 1.0 is random. The maximum attainable EF is ``1/x`` capped by the
    active fraction, so the function also returns that ceiling: an EF1% of 20
    means nothing until you know whether the ceiling was 21 or 100.

    Returns:
        ``(ef, max_possible_ef)``.
    """
    y, s = _validate_labels_scores(labels, scores)
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"fraction must be in (0, 1]; got {fraction}")

    n_total = y.size
    n_top = max(1, round(float(fraction) * int(n_total)))
    order = np.argsort(-s, kind="mergesort")
    n_active_top = int(y[order][:n_top].sum())
    n_active = int(y.sum())

    actual_fraction = n_top / n_total
    ef = (n_active_top / n_active) / actual_fraction
    max_ef = (min(n_top, n_active) / n_active) / actual_fraction
    return float(ef), float(max_ef)


def bedroc(labels: np.ndarray, scores: np.ndarray, alpha: float = 20.0) -> float:
    """Boltzmann-Enhanced Discrimination of ROC.

    Truchon & Bayly, *J. Chem. Inf. Model.* 2007, 47, 488-508.

    BEDROC applies an exponential weight that emphasises early recognition.
    ``alpha=20`` concentrates ~80%% of the weight in the top 8%% of the ranked
    list, which is the regime that matters when only the top handful of
    compounds will ever be assayed.

    Returns a value in [0, 1]; the random expectation is not 0.5 but depends on
    ``alpha`` and the active fraction, which is why this is reported alongside
    ROC-AUC rather than instead of it.
    """
    y, s = _validate_labels_scores(labels, scores)
    if alpha <= 0:
        raise ValueError(f"alpha must be positive; got {alpha}")

    n = y.size
    n_active = int(y.sum())
    ratio = n_active / n

    order = np.argsort(-s, kind="mergesort")
    # 1-based ranks of the actives.
    active_ranks = np.flatnonzero(y[order] == 1) + 1

    rie_numerator = float(np.exp(-alpha * active_ranks / n).sum())
    # Random-expectation normaliser from Truchon & Bayly eq. 12.
    random_sum = n_active * (1.0 - np.exp(-alpha)) / (n * (np.exp(alpha / n) - 1.0))
    if random_sum == 0:  # pragma: no cover - guarded by alpha > 0 and n_active > 0
        raise ValueError("Degenerate BEDROC normaliser")
    rie = rie_numerator / random_sum

    factor = (
        ratio * np.sinh(alpha / 2.0) / (np.cosh(alpha / 2.0) - np.cosh(alpha / 2.0 - alpha * ratio))
    )
    offset = 1.0 / (1.0 - np.exp(alpha * (1.0 - ratio)))
    value = rie * factor + offset
    # BEDROC is analytically bounded to [0, 1]; clamp away floating-point dust
    # (a worst-case ranking evaluates to -1e-17) rather than reporting -0.0.
    return float(min(1.0, max(0.0, value)))


@dataclass(slots=True)
class EnrichmentReport:
    """Enrichment metrics for one receptor."""

    receptor_id: str
    n_actives: int
    n_decoys: int
    roc_auc: float
    bedroc: float
    bedroc_alpha: float
    enrichment_factors: dict[str, float]
    max_enrichment_factors: dict[str, float]

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "receptor_id": self.receptor_id,
            "n_actives": self.n_actives,
            "n_decoys": self.n_decoys,
            "roc_auc": round(self.roc_auc, 4),
            f"bedroc_alpha{self.bedroc_alpha:g}": round(self.bedroc, 4),
        }
        for key, value in sorted(self.enrichment_factors.items()):
            record[f"ef_{key}"] = round(value, 3)
            record[f"ef_{key}_max_possible"] = round(self.max_enrichment_factors[key], 3)
        return record

    @property
    def verdict(self) -> str:
        """A plain-language reading of whether this receptor discriminates."""
        if self.roc_auc >= 0.80:
            return "good discrimination"
        if self.roc_auc >= 0.70:
            return "moderate discrimination"
        if self.roc_auc >= 0.60:
            return "weak discrimination"
        if self.roc_auc >= 0.45:
            return "no better than random"
        return "worse than random (check the score sign convention)"


@dataclass(slots=True)
class NullDistribution:
    """Empirical null distribution of docking scores for one receptor.

    Built by docking a set of random ligands that have no reason to bind. It
    turns "-7.4 kcal/mol" into "-7.4 kcal/mol, which is 1.8 SD better than a
    random ligand of this size, empirical p = 0.03" — the difference between a
    number and a claim.
    """

    receptor_id: str
    affinities: np.ndarray
    n_failed: int = 0

    def __post_init__(self) -> None:
        self.affinities = np.asarray(self.affinities, dtype=np.float64).ravel()
        if self.affinities.size < 5:
            raise ValueError(
                f"A null distribution needs at least 5 successful dockings; got "
                f"{self.affinities.size}. Increase validation.null_set_size."
            )
        if not np.isfinite(self.affinities).all():
            raise ValueError("null affinities contain non-finite values")

    @property
    def n(self) -> int:
        return int(self.affinities.size)

    @property
    def mean(self) -> float:
        return float(self.affinities.mean())

    @property
    def std(self) -> float:
        # Sample standard deviation: the null set is a sample, not a population.
        return float(self.affinities.std(ddof=1))

    def z_score(self, affinity: float) -> float:
        """Standard score of ``affinity``, signed so that **more negative
        affinity gives a more positive z**, i.e. larger z is better binding.
        """
        if self.std == 0:
            raise ValueError("Null distribution has zero variance; z-score undefined")
        return float((self.mean - affinity) / self.std)

    def empirical_p(self, affinity: float) -> float:
        """One-sided empirical p-value for an affinity at least this favourable.

        Uses the ``(k + 1) / (n + 1)`` estimator, which never returns exactly
        zero. Reporting ``p = 0`` from a finite null set would overstate the
        evidence; with ``n`` samples the smallest defensible claim is
        ``p < 1/(n+1)``, and this estimator says exactly that.
        """
        k = (self.affinities <= affinity).sum()
        return float((k + 1) / (self.n + 1))

    @property
    def resolution(self) -> float:
        """The smallest p-value this null set can support."""
        return float(1.0 / (self.n + 1))

    def summary(self) -> dict[str, Any]:
        quantiles = np.percentile(self.affinities, [1, 5, 25, 50, 75, 95, 99])
        return {
            "receptor_id": self.receptor_id,
            "n_null_ligands": self.n,
            "n_failed": self.n_failed,
            "mean_affinity_kcal_mol": round(self.mean, 3),
            "sd_affinity_kcal_mol": round(self.std, 3),
            "min_affinity_kcal_mol": round(float(self.affinities.min()), 3),
            "max_affinity_kcal_mol": round(float(self.affinities.max()), 3),
            "p01": round(float(quantiles[0]), 3),
            "p05": round(float(quantiles[1]), 3),
            "median": round(float(quantiles[3]), 3),
            "p95": round(float(quantiles[5]), 3),
            "smallest_supportable_p": round(self.resolution, 5),
        }
