"""Tests for the added analysis features: NP-likeness, scaffolds, report artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from rdkit import Chem

from npcadd.chem.np_likeness import np_likeness, np_model_available
from npcadd.chem.scaffolds import analyse_scaffolds, butina_clusters, murcko_scaffold
from npcadd.config import load_config
from npcadd.report.dossier import write_dossiers
from npcadd.report.html import render_html_summary

LOLIOLIDE = "CC1(C)CC(O)CC2(C)OC(=O)C=C12"
REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_CONFIG = REPO_ROOT / "config.smoke.yaml"


def _write_minimal_run(out_dir: Path) -> None:
    """Write just enough of a finished run for the report engines to read."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        [
            {
                "rank": 1,
                "ligand_id": "L1",
                "name": "Loliolide",
                "compound_class": "terpenoid",
                "area_percent": 2.8,
                "receptor_id": "abl1",
                "activity": "cytotoxic",
                "target_name": "ABL1",
                "best_affinity_kcal_mol": -7.58,
                "ligand_efficiency_kcal_mol_per_ha": 0.54,
                "lipophilic_efficiency_lle": 4.1,
                "lelp": 2.6,
                "fit_quality": 0.89,
                "binding_efficiency_index_bei": 28.3,
                "surface_efficiency_index_sei": 11.9,
                "size_independent_le_sile": 2.5,
                "multi_criteria_score": 0.73,
                "admet_pass_rate": 0.83,
                "z_score_vs_null": 0.72,
                "empirical_p_vs_null": 0.27,
                "flags": "PAINS: x",
            }
        ]
    ).to_csv(out_dir / "ranked_compounds.csv", index=False, lineterminator="\n")
    pd.DataFrame(
        [
            {
                "ligand_id": "L1",
                "name": "Loliolide",
                "smiles": LOLIOLIDE,
                "mw": 196.2,
                "clogp": 1.5,
                "tpsa": 46.5,
                "hbd": 1,
                "hba": 3,
                "qed": 0.6,
                "np_likeness_score": 2.98,
                "np_likeness_class": "natural-product-like",
            }
        ]
    ).to_csv(out_dir / "admet_results.csv", index=False, lineterminator="\n")
    pd.DataFrame(
        [
            {
                "ligand_id": "L1",
                "receptor_id": "abl1",
                "placement_ok": True,
                "consensus_ok": True,
                "top2_pose_rmsd_a": 2.5,
                "mean_top_affinity_kcal_mol": -7.1,
                "affinity_spread_kcal_mol": 1.3,
                "n_poses_returned": 5,
                "contact_residues": "THR315.A;MET318.A",
            }
        ]
    ).to_csv(out_dir / "docking_results.csv", index=False, lineterminator="\n")
    (out_dir / "provenance.json").write_text(
        json.dumps(
            {
                "git": {"commit": "abc123", "dirty": False},
                "config": {"hash": "deadbeef"},
                "warnings": ["a stamped caveat"],
                "npcadd_version": "0.1.0",
            }
        ),
        encoding="utf-8",
    )


class TestNPLikeness:
    def test_model_is_available_in_this_env(self) -> None:
        assert np_model_available()

    def test_terpenoid_scores_natural_product_like(self) -> None:
        result = np_likeness(Chem.MolFromSmiles(LOLIOLIDE))
        assert result is not None
        assert result.score > 0.0
        assert result.verdict in {"natural-product-like", "intermediate"}
        record = result.as_record()
        assert "np_likeness_score" in record
        assert "np_likeness_class" in record

    def test_terpenoid_outscores_an_obviously_synthetic_molecule(self) -> None:
        terpenoid = np_likeness(Chem.MolFromSmiles(LOLIOLIDE))
        synthetic = np_likeness(Chem.MolFromSmiles("O=S(=O)(Cl)c1ccccc1"))  # sulfonyl chloride
        assert terpenoid is not None
        assert synthetic is not None
        assert terpenoid.score > synthetic.score

    def test_none_input_returns_none(self) -> None:
        assert np_likeness(None) is None


class TestScaffolds:
    def test_murcko_scaffold_of_toluene_is_benzene(self) -> None:
        assert murcko_scaffold("Cc1ccccc1") == "c1ccccc1"

    def test_acyclic_molecule_has_no_scaffold(self) -> None:
        assert murcko_scaffold("CCCCO") is None

    def test_unparseable_smiles_returns_none(self) -> None:
        assert murcko_scaffold("not_a_smiles(((") is None

    def test_analyse_reports_diversity(self) -> None:
        # Three benzene-framework molecules, one distinct bicyclic, one acyclic.
        smiles = ["c1ccccc1C", "c1ccccc1CC", "c1ccccc1CCO", "C1CCC2CCCCC2C1", "CCCCCCO"]
        analysis = analyse_scaffolds(smiles, cluster_cutoff=0.35)
        assert analysis.n_compounds == 5
        assert analysis.n_parsed == 5
        # Benzene scaffold (x3) + decalin + acyclic bucket -> at least two distinct.
        assert analysis.n_unique_scaffolds >= 2
        assert 0.0 <= analysis.singleton_fraction <= 1.0
        assert analysis.as_record()["n_compounds"] == 5

    def test_butina_cluster_indices_cover_every_parsed_molecule_once(self) -> None:
        smiles = ["c1ccccc1C", "c1ccccc1CC", "C1CCCCC1CCCCCC"]
        clusters = butina_clusters(smiles, cutoff=0.4)
        flat = sorted(i for cluster in clusters for i in cluster)
        assert flat == [0, 1, 2]

    def test_empty_input_is_handled(self) -> None:
        analysis = analyse_scaffolds([])
        assert analysis.n_compounds == 0
        assert analysis.n_clusters == 0
        assert analysis.singleton_fraction == 0.0


class TestReportArtifacts:
    def test_html_summary_is_self_contained_and_grounded(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        cfg.paths.reports = tmp_path / "reports"
        out_dir = tmp_path / "run"
        _write_minimal_run(out_dir)
        path = render_html_summary(cfg, "testrun", out_dir, cfg.paths.reports, top_n=5)
        assert path.is_file()
        html = path.read_text(encoding="utf-8")
        assert "Loliolide" in html
        assert "a stamped caveat" in html  # provenance warnings surfaced
        assert "abc123" in html  # git commit
        assert "http://" not in html and "https://" not in html  # no external resources

    def test_dossier_cards_carry_the_key_sections(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        cfg.paths.reports = tmp_path / "reports"
        out_dir = tmp_path / "run"
        _write_minimal_run(out_dir)
        paths = write_dossiers(cfg, out_dir, cfg.paths.reports, top_n=2)
        assert len(paths) == 1
        card = paths[0].read_text(encoding="utf-8")
        assert "# Loliolide" in card
        assert "## Efficiency metrics" in card
        assert "NP-likeness: 2.98" in card
        assert "not a claim of activity" in card

    def test_html_requires_a_ranked_run(self, tmp_path: Path) -> None:
        import pytest

        cfg = load_config(SMOKE_CONFIG)
        with pytest.raises(FileNotFoundError, match="ranked_compounds"):
            render_html_summary(cfg, "missing", tmp_path / "empty", tmp_path / "reports")
