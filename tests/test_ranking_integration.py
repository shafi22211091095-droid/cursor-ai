"""rank_compounds on hand-built rows: ordering, the area band, sensitivity."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from npcadd.config import load_config
from npcadd.docking.interactions import InteractionProfile, build_interaction_matrix
from npcadd.stages.admet import AdmetRow
from npcadd.stages.dock import ScreenRow
from npcadd.stages.rank import rank_compounds, sensitivity_analysis

CFG = Path(__file__).resolve().parents[1] / "config.smoke.yaml"


def _rows(specs: list[tuple[str, float, float, int, float, float]]):
    """spec = (name, area%, affinity, heavy_atoms, clogp, admet_rate)."""
    screen, admet = [], []
    for i, (name, area, aff, ha, clogp, rate) in enumerate(specs):
        lid = f"L{i}"
        screen.append(
            ScreenRow(
                lid,
                name,
                "terpenoid",
                area,
                "r1",
                "cytotoxic",
                "T",
                True,
                aff,
                z_score=1.0,
                empirical_p=0.05,
                p_resolution=0.05,
            )
        )
        admet.append(
            AdmetRow(
                lid,
                name,
                "terpenoid",
                area,
                "C",
                descriptors={
                    "heavy_atoms": float(ha),
                    "clogp": clogp,
                    "n_unspecified_stereocentres": 1.0,
                },
                admet_pass_rate=rate,
                alerts={"pains_flagged": True, "pains_filters": "x"},
            )
        )
    return screen, admet


SPECS = [
    ("big_strong", 0.5, -13.0, 40, 3.0, 0.8),
    ("small_efficient", 0.5, -7.0, 14, 1.0, 1.0),
    ("abundant_weak", 9.0, -4.0, 12, 2.0, 0.5),
    ("mid", 1.0, -8.0, 22, 2.5, 0.9),
    ("tiny_a", 0.2, -5.0, 10, 1.5, 0.7),
    ("tiny_b", 0.1, -5.5, 11, 1.5, 0.7),
]


def test_efficiency_beats_raw_affinity_and_area_band_leads(tmp_path: Path) -> None:
    cfg = load_config(CFG)
    screen, admet = _rows(SPECS)
    ranked, summary = rank_compounds(cfg, screen, admet, tmp_path)

    # Compounds at/above the area threshold are in the upper rows.
    assert ranked[0].name == "abundant_weak"
    assert ranked[0].above_area_threshold
    # Below the band, the small efficient compound outranks the big raw-score winner.
    order = [r.name for r in ranked]
    assert order.index("small_efficient") < order.index("big_strong")
    assert [r.rank for r in ranked] == list(range(1, len(ranked) + 1))
    assert summary.n_above_area_threshold == 1


def test_flags_and_table_columns(tmp_path: Path) -> None:
    cfg = load_config(CFG)
    ranked, summary = rank_compounds(cfg, *_rows(SPECS), tmp_path)
    flags = " ".join(ranked[0].flags)
    assert "PAINS" in flags and "stereocentre" in flags
    frame = pd.read_csv(summary.results_path)
    for col in (
        "compound_class",
        "area_percent",
        "ligand_efficiency_kcal_mol_per_ha",
        "fit_quality",
        "multi_criteria_score",
    ):
        assert col in frame.columns


def test_failed_dockings_and_missing_admet_are_excluded(tmp_path: Path) -> None:
    cfg = load_config(CFG)
    screen, admet = _rows(SPECS)
    screen[0].ok = False
    ranked, _ = rank_compounds(cfg, screen, admet[1:], tmp_path)
    assert "big_strong" not in [r.name for r in ranked]


def test_sensitivity_is_seeded_and_bounded(tmp_path: Path) -> None:
    cfg = load_config(CFG)
    ranked, _ = rank_compounds(cfg, *_rows(SPECS), tmp_path)
    a = sensitivity_analysis(cfg, ranked)
    b = sensitivity_analysis(cfg, ranked)
    assert a == b
    assert 0.0 <= a["top5_stability"] <= 1.0


def test_interaction_matrix_orders_conserved_contacts_first() -> None:
    profiles = [
        InteractionProfile("a", "r", 1, [("THR315.A", "HBDonor"), ("LEU248.A", "Hydrophobic")]),
        InteractionProfile("b", "r", 1, [("THR315.A", "HBDonor")]),
        InteractionProfile("b", "r", 2, [("X1.A", "Anionic")]),
    ]
    m = build_interaction_matrix(profiles)
    assert m.columns[0] == "THR315.A|HBDonor"
    assert m.loc["a", "LEU248.A|Hydrophobic"] == 1
    assert "X1.A|Anionic" not in m.columns  # non-best pose excluded
    assert build_interaction_matrix([]).empty
    assert profiles[0].counts_by_type()["HBDonor"] == 1
    assert profiles[0].as_record()["n_contacts"] == 2


def test_raw_sort_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Refusing to rank"):
        rank_compounds(load_config(CFG), [], [], tmp_path, sort_by="vina_score")
