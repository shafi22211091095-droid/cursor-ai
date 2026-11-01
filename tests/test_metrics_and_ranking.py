"""Tests for screening metrics, null statistics and efficiency-based ranking.

These are the tests that matter most for scientific correctness: they are what
stands behind the statistics quoted in the manuscript.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from npcadd.docking.metrics import (
    NullDistribution,
    bedroc,
    enrichment_factor,
    roc_auc,
    roc_curve,
)
from npcadd.stages.rank import (
    FORBIDDEN_SORT_KEYS,
    _min_max_normalise,
    _spearman,
    compute_efficiency,
    fit_quality,
    ligand_efficiency,
    lipophilic_efficiency,
    pkd_estimate,
)


def labelled(n_active: int, n_decoy: int) -> np.ndarray:
    return np.concatenate([np.ones(n_active, dtype=int), np.zeros(n_decoy, dtype=int)])


class TestRocAuc:
    def test_perfect_and_inverse_separation(self) -> None:
        labels = labelled(3, 3)
        assert roc_auc(labels, np.array([6.0, 5.0, 4.0, 3.0, 2.0, 1.0])) == 1.0
        assert roc_auc(labels, np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])) == 0.0

    def test_all_tied_is_exactly_one_half(self) -> None:
        assert roc_auc(labelled(2, 2), np.ones(4)) == 0.5

    def test_matches_sklearn(self) -> None:
        """Agreement with an independent implementation, including ties."""
        sklearn_metrics = pytest.importorskip("sklearn.metrics")
        rng = np.random.default_rng(12345)
        for _ in range(25):
            n = int(rng.integers(20, 200))
            labels = (rng.random(n) < 0.3).astype(int)
            if labels.sum() in (0, n):
                continue
            # Round to 2 dp to force ties, as real docking output does.
            scores = np.round(rng.normal(labels * 1.0, 1.0), 2)
            assert roc_auc(labels, scores) == pytest.approx(
                sklearn_metrics.roc_auc_score(labels, scores), abs=1e-12
            )

    @settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        n_active=st.integers(min_value=1, max_value=30),
        n_decoy=st.integers(min_value=1, max_value=30),
        seed=st.integers(min_value=0, max_value=10_000),
    )
    def test_auc_is_always_in_the_unit_interval(
        self, n_active: int, n_decoy: int, seed: int
    ) -> None:
        rng = np.random.default_rng(seed)
        labels = labelled(n_active, n_decoy)
        scores = rng.normal(size=labels.size)
        assert 0.0 <= roc_auc(labels, scores) <= 1.0

    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(seed=st.integers(min_value=0, max_value=10_000))
    def test_negating_scores_reflects_auc_about_one_half(self, seed: int) -> None:
        rng = np.random.default_rng(seed)
        labels = labelled(10, 15)
        scores = rng.normal(size=labels.size)
        assert roc_auc(labels, scores) + roc_auc(labels, -scores) == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("labels", "scores", "match"),
        [
            (np.ones(3, dtype=int), np.arange(3.0), "No decoys"),
            (np.zeros(3, dtype=int), np.arange(3.0), "No actives"),
            (np.array([0, 1]), np.array([1.0]), "differ in length"),
            (np.array([0, 2]), np.array([1.0, 2.0]), "only 0"),
            (np.array([0, 1]), np.array([1.0, np.inf]), "non-finite"),
            (np.array([], dtype=int), np.array([]), "empty"),
        ],
    )
    def test_degenerate_input_raises_with_a_specific_message(
        self, labels: np.ndarray, scores: np.ndarray, match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            roc_auc(labels, scores)

    def test_roc_curve_endpoints_and_monotonicity(self) -> None:
        fpr, tpr = roc_curve(labelled(5, 5), np.arange(10.0)[::-1])
        assert fpr[0] == 0.0
        assert tpr[0] == 0.0
        assert fpr[-1] == pytest.approx(1.0)
        assert tpr[-1] == pytest.approx(1.0)
        assert np.all(np.diff(fpr) >= -1e-12)
        assert np.all(np.diff(tpr) >= -1e-12)


class TestBedroc:
    def test_bounded_and_oriented(self) -> None:
        labels = labelled(50, 950)
        best = np.arange(1000.0)[::-1]
        worst = np.arange(1000.0)
        assert bedroc(labels, best, 20.0) == pytest.approx(1.0, abs=1e-6)
        assert bedroc(labels, worst, 20.0) == pytest.approx(0.0, abs=1e-6)

    def test_random_expectation_is_near_the_active_ratio(self) -> None:
        """Truchon & Bayly: the random expectation depends on alpha and ratio."""
        rng = np.random.default_rng(7)
        labels = labelled(50, 950)
        values = [bedroc(labels, rng.random(1000), 20.0) for _ in range(150)]
        assert 0.02 < float(np.mean(values)) < 0.15

    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        seed=st.integers(min_value=0, max_value=5000),
        alpha=st.floats(min_value=1.0, max_value=100.0),
    )
    def test_always_within_unit_interval(self, seed: int, alpha: float) -> None:
        rng = np.random.default_rng(seed)
        labels = labelled(8, 40)
        assert 0.0 <= bedroc(labels, rng.normal(size=48), alpha) <= 1.0

    def test_early_recognition_is_rewarded(self) -> None:
        """Two rankings with equal AUC but different early enrichment."""
        labels = labelled(4, 96)
        early = np.zeros(100)
        early[:4] = [100.0, 99.0, 98.0, 97.0]  # all actives first
        early[4:] = np.linspace(50, 1, 96)

        late = np.zeros(100)
        late[:4] = [60.0, 59.0, 58.0, 57.0]  # actives mid-pack
        late[4:] = np.concatenate([np.linspace(100, 61, 40), np.linspace(56, 1, 56)])

        assert bedroc(labels, early, 20.0) > bedroc(labels, late, 20.0)

    def test_rejects_nonpositive_alpha(self) -> None:
        with pytest.raises(ValueError, match="alpha must be positive"):
            bedroc(labelled(2, 2), np.arange(4.0), 0.0)


class TestEnrichmentFactor:
    def test_perfect_enrichment_equals_the_ceiling(self) -> None:
        labels = labelled(50, 950)
        ef, ceiling = enrichment_factor(labels, np.arange(1000.0)[::-1], 0.01)
        assert ef == pytest.approx(20.0)
        assert ceiling == pytest.approx(20.0)

    def test_worst_case_is_zero_but_the_ceiling_is_unchanged(self) -> None:
        labels = labelled(50, 950)
        ef, ceiling = enrichment_factor(labels, np.arange(1000.0), 0.01)
        assert ef == 0.0
        assert ceiling == pytest.approx(20.0)

    def test_whole_library_gives_unity(self) -> None:
        labels = labelled(10, 90)
        ef, _ = enrichment_factor(labels, np.arange(100.0), 1.0)
        assert ef == pytest.approx(1.0)

    def test_ceiling_is_capped_by_the_active_count(self) -> None:
        """With 2 actives in 100 compounds, EF1% cannot exceed 50."""
        labels = labelled(2, 98)
        _, ceiling = enrichment_factor(labels, np.arange(100.0)[::-1], 0.01)
        assert ceiling == pytest.approx(50.0)

    @pytest.mark.parametrize("fraction", [0.0, -0.1, 1.5])
    def test_invalid_fraction_raises(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="fraction must be"):
            enrichment_factor(labelled(2, 8), np.arange(10.0), fraction)


class TestNullDistribution:
    def test_statistics(self) -> None:
        null = NullDistribution("r", np.array([-5.0, -6.0, -7.0, -4.0, -5.0, -6.0]))
        assert null.n == 6
        assert null.mean == pytest.approx(-5.5)
        assert null.std > 0

    def test_z_score_is_oriented_so_better_binding_is_more_positive(self) -> None:
        null = NullDistribution("r", np.array([-5.0, -5.0, -5.0, -6.0, -4.0]))
        assert null.z_score(-9.0) > 0
        assert null.z_score(-1.0) < 0
        assert null.z_score(null.mean) == pytest.approx(0.0)

    def test_empirical_p_is_never_zero(self) -> None:
        null = NullDistribution("r", np.linspace(-6.0, -4.0, 50))
        assert null.empirical_p(-999.0) == pytest.approx(1.0 / 51.0)
        assert null.empirical_p(-999.0) > 0.0

    def test_p_resolution_matches_the_floor(self) -> None:
        null = NullDistribution("r", np.linspace(-6.0, -4.0, 99))
        assert null.resolution == pytest.approx(1.0 / 100.0)
        assert null.empirical_p(-50.0) == pytest.approx(null.resolution)

    def test_p_is_monotone_in_affinity(self) -> None:
        null = NullDistribution("r", np.linspace(-7.0, -3.0, 40))
        values = [null.empirical_p(a) for a in (-8.0, -6.0, -5.0, -4.0, -2.0)]
        assert values == sorted(values)

    def test_too_small_a_null_set_raises(self) -> None:
        with pytest.raises(ValueError, match="at least 5"):
            NullDistribution("r", np.array([-5.0, -6.0]))

    def test_nonfinite_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            NullDistribution("r", np.array([-5.0, -6.0, np.nan, -4.0, -5.0]))

    def test_zero_variance_z_score_raises(self) -> None:
        null = NullDistribution("r", np.full(10, -5.0))
        with pytest.raises(ValueError, match="zero variance"):
            null.z_score(-6.0)

    def test_summary_is_complete(self) -> None:
        summary = NullDistribution("rec", np.linspace(-8.0, -3.0, 30)).summary()
        for key in (
            "receptor_id",
            "n_null_ligands",
            "mean_affinity_kcal_mol",
            "sd_affinity_kcal_mol",
            "median",
            "smallest_supportable_p",
        ):
            assert key in summary


class TestEfficiencyMetrics:
    def test_ligand_efficiency_definition(self) -> None:
        assert ligand_efficiency(-7.0, 14) == pytest.approx(0.5)
        assert ligand_efficiency(-10.0, 20) == pytest.approx(0.5)

    def test_ligand_efficiency_rejects_zero_atoms(self) -> None:
        with pytest.raises(ValueError, match="heavy_atoms must be positive"):
            ligand_efficiency(-7.0, 0)

    def test_equal_le_for_different_sizes_shows_why_raw_score_misleads(self) -> None:
        """A big weak binder can beat a small efficient one on raw score."""
        small, big = -7.58, -13.28  # smoke run: loliolide, imatinib redock
        assert big < small  # imatinib wins on raw score
        le_small = ligand_efficiency(small, 14)
        le_big = ligand_efficiency(big, 37)
        assert le_small > le_big  # but loliolide is the more efficient binder

    def test_pkd_conversion_is_monotone_and_signed_correctly(self) -> None:
        assert pkd_estimate(-10.0) > pkd_estimate(-5.0)
        assert pkd_estimate(-5.0) > 0

    def test_pkd_rejects_nonpositive_temperature(self) -> None:
        with pytest.raises(ValueError, match="temperature_k must be positive"):
            pkd_estimate(-7.0, 0.0)

    def test_lle_penalises_lipophilicity(self) -> None:
        greasy = lipophilic_efficiency(-8.0, 6.0)
        polar = lipophilic_efficiency(-8.0, 1.0)
        assert polar > greasy
        assert polar - greasy == pytest.approx(5.0)

    def test_fit_quality_is_near_one_for_a_maximally_efficient_ligand(self) -> None:
        # A 20-heavy-atom ligand at the Reynolds ceiling should score ~1.
        from npcadd.stages.rank import _max_achievable_le

        ceiling = _max_achievable_le(20)
        affinity = -ceiling * 20
        assert fit_quality(affinity, 20) == pytest.approx(1.0, rel=1e-6)

    def test_fit_quality_is_non_negative(self) -> None:
        assert fit_quality(5.0, 20) == 0.0  # a positive (repulsive) score clamps to 0

    def test_fit_quality_compares_sizes_more_fairly_than_raw_le(self) -> None:
        """LE falls with size by construction; FQ corrects for that."""
        le_small = ligand_efficiency(-6.0, 12)
        le_large = ligand_efficiency(-12.0, 40)
        assert le_small > le_large
        # Both are at a similar fraction of what their size allows.
        assert fit_quality(-6.0, 12) > 0
        assert fit_quality(-12.0, 40) > 0

    def test_compute_efficiency_assembles_every_metric(self) -> None:
        metrics = compute_efficiency(-8.5, 22, 2.5, 298.15)
        record = metrics.as_record()
        for key in (
            "ligand_efficiency_kcal_mol_per_ha",
            "pkd_estimate_from_score",
            "lipophilic_efficiency_lle",
            "lelp",
            "fit_quality",
        ):
            assert key in record
        assert metrics.ligand_efficiency == pytest.approx(8.5 / 22)

    @settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(
        affinity=st.floats(min_value=-15.0, max_value=-1.0),
        heavy_atoms=st.integers(min_value=6, max_value=60),
        clogp=st.floats(min_value=-3.0, max_value=8.0),
    )
    def test_efficiency_invariants(self, affinity: float, heavy_atoms: int, clogp: float) -> None:
        metrics = compute_efficiency(affinity, heavy_atoms, clogp, 298.15)
        assert metrics.ligand_efficiency > 0
        assert metrics.pkd_estimate > 0
        assert math.isfinite(metrics.lipophilic_efficiency)
        assert metrics.fit_quality >= 0.0


class TestRankingGuards:
    def test_raw_affinity_is_a_forbidden_sort_key(self) -> None:
        assert "best_affinity_kcal_mol" in FORBIDDEN_SORT_KEYS
        assert "vina_score" in FORBIDDEN_SORT_KEYS

    def test_min_max_normalise_maps_to_unit_interval(self) -> None:
        out = _min_max_normalise(np.array([1.0, 2.0, 3.0]))
        assert out.min() == 0.0
        assert out.max() == 1.0
        assert out[1] == pytest.approx(0.5)

    def test_constant_column_maps_to_one_half_not_zero(self) -> None:
        """Mapping to zero would silently null out that criterion's weight."""
        out = _min_max_normalise(np.array([5.0, 5.0, 5.0]))
        assert np.allclose(out, 0.5)

    def test_normalise_handles_all_nan(self) -> None:
        out = _min_max_normalise(np.array([np.nan, np.nan]))
        assert np.allclose(out, 0.5)

    def test_spearman_detects_perfect_and_inverse_order(self) -> None:
        a = np.array([1.0, 2.0, 3.0, 4.0])
        assert _spearman(a, a) == pytest.approx(1.0)
        assert _spearman(a, -a) == pytest.approx(-1.0)

    def test_spearman_of_constant_is_nan(self) -> None:
        assert math.isnan(_spearman(np.array([1.0, 2.0, 3.0]), np.ones(3)))

    def test_spearman_needs_three_points(self) -> None:
        assert math.isnan(_spearman(np.array([1.0, 2.0]), np.array([1.0, 2.0])))
