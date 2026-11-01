"""Stage-level tests that do not require docking.

Docking-dependent behaviour is covered by the end-to-end smoke run in CI
(``make smoke``) rather than here, because a Vina run is minutes long and
pytest should stay fast enough to run on every save.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from npcadd.chem.alerts import screen_alerts
from npcadd.config import load_config
from npcadd.stages.admet import (
    EXTERNAL_TOOL_SCHEMAS,
    read_external_tool_csv,
    run_admet,
    write_external_tool_batches,
)
from npcadd.stages.ligands import prepare_ligands, read_ligand_csv
from npcadd.stages.rank import FORBIDDEN_SORT_KEYS, rank_compounds
from npcadd.stages.receptor import (
    SOLVENT_RESIDUES,
    _filter_structure,
    normalise_atom_order,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_CONFIG = REPO_ROOT / "config.smoke.yaml"
REJECTS_CONFIG = REPO_ROOT / "config.sample.yaml"


class TestLigandStage:
    def test_reads_the_sample_csv(self) -> None:
        cfg = load_config(SMOKE_CONFIG)
        frame = read_ligand_csv(cfg)
        assert not frame.empty
        for column in ("name", "smiles", "area_percent", "compound_class"):
            assert column in frame.columns

    def test_missing_column_raises_with_the_column_named(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        bad = tmp_path / "bad.csv"
        bad.write_text("name,smiles\nx,CCO\n", encoding="utf-8")
        cfg.ligands.input_csv = bad
        with pytest.raises(ValueError, match="area_percent"):
            read_ligand_csv(cfg)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        cfg.ligands.input_csv = tmp_path / "nope.csv"
        with pytest.raises(FileNotFoundError, match="not found"):
            read_ligand_csv(cfg)

    def test_prepares_ligands_and_writes_both_tables(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        ligands, summary = prepare_ligands(cfg, tmp_path)

        assert summary.n_accepted == len(ligands) >= 1
        assert Path(summary.manifest_path).is_file()
        assert Path(summary.rejected_path).is_file()
        assert summary.protonation_limitation  # the caveat travels with the data

        for ligand in ligands:
            # Paths are run-relative, so the manifest is machine-independent.
            assert not Path(ligand.pdbqt_path).is_absolute()
            assert (tmp_path / ligand.pdbqt_path).is_file()
            assert (tmp_path / ligand.sdf_path).is_file()
            assert ligand.pdbqt_sha256

    def test_every_rejection_path_fires_with_a_reason(self, tmp_path: Path) -> None:
        """The dirty sample exercises salt, unparseable, element and duplicate."""
        cfg = load_config(REJECTS_CONFIG)
        _, summary = prepare_ligands(cfg, tmp_path)

        assert summary.n_rejected >= 4
        reasons = set(summary.rejection_counts)
        assert "too_few_heavy_atoms" in reasons
        assert "unparseable_smiles" in reasons
        assert "duplicate_structure" in reasons
        assert "disallowed_element" in reasons

        rejected = pd.read_csv(summary.rejected_path)
        assert not rejected.empty
        # Every rejection carries a human-readable detail, not just a code.
        assert rejected["detail"].astype(str).str.len().min() > 0

    def test_duplicate_structures_are_collapsed(self, tmp_path: Path) -> None:
        cfg = load_config(REJECTS_CONFIG)
        ligands, summary = prepare_ligands(cfg, tmp_path)
        keys = [lig.inchikey for lig in ligands if lig.inchikey]
        assert len(keys) == len(set(keys))
        assert summary.rejection_counts.get("duplicate_structure", 0) >= 1

    def test_raises_when_nothing_survives(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        bad = tmp_path / "all_bad.csv"
        bad.write_text(
            "name,smiles,compound_class,area_percent\na,XX((,other,1.0\n", encoding="utf-8"
        )
        cfg.ligands.input_csv = bad
        with pytest.raises(RuntimeError, match="No ligand survived"):
            prepare_ligands(cfg, tmp_path / "out")


class TestReceptorHelpers:
    def test_normalise_orders_residues_by_sequence_number(self) -> None:
        """A residue whose atoms appear first must not become the N-terminus."""
        pdb = (
            "ATOM      1  CB  SER A 438      13.6  67.3  33.3  1.00  0.00           C\n"
            "ATOM      2  N   MET A 225      19.3  41.5  -3.8  1.00  0.00           N\n"
            "ATOM      3  CA  MET A 225      20.5  40.9  -4.5  1.00  0.00           C\n"
            "ATOM      4  N   SER A 438      10.4  67.6  32.1  1.00  0.00           N\n"
        )
        out, n_atoms, n_residues = normalise_atom_order(pdb)
        assert n_atoms == 4
        assert n_residues == 2

        atom_lines = [ln for ln in out.splitlines() if ln.startswith("ATOM")]
        assert atom_lines[0][22:26].strip() == "225", "MET 225 must come first"
        # SER 438's atoms must now be contiguous.
        residues = [ln[22:26].strip() for ln in atom_lines]
        assert residues == ["225", "225", "438", "438"]

    def test_normalise_preserves_coordinates_exactly(self) -> None:
        pdb = (
            "ATOM      1  N   ALA A   1       1.234   5.678   9.012  1.00 20.00           N\n"
            "ATOM      2  CA  ALA A   1       2.000   3.000   4.000  1.00 20.00           C\n"
        )
        out, _, _ = normalise_atom_order(pdb)
        assert "1.234   5.678   9.012" in out
        assert "2.000   3.000   4.000" in out

    def test_normalise_renumbers_serials_contiguously(self) -> None:
        pdb = "".join(
            f"ATOM  {i * 7:5d}  CA  ALA A {i:3d}       0.0   0.0   0.0  1.00  0.00           C\n"
            for i in range(1, 6)
        )
        out, n_atoms, _ = normalise_atom_order(pdb)
        serials = [int(ln[6:11]) for ln in out.splitlines() if ln.startswith("ATOM")]
        assert serials == list(range(1, n_atoms + 1))

    def test_filter_strips_water_but_keeps_requested_cofactors(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.0   0.0   0.0  1.00  0.00           C\n"
            "HETATM    2  O   HOH A 500       1.0   1.0   1.0  1.00  0.00           O\n"
            "HETATM    3 ZN    ZN A 600       2.0   2.0   2.0  1.00  0.00          ZN\n"
            "HETATM    4  C1  STI A 700       3.0   3.0   3.0  1.00  0.00           C\n"
        )
        kept, dropped, n_het = _filter_structure(pdb, "A", frozenset({"ZN"}))
        assert "HOH" not in kept
        assert "ZN" in kept
        assert n_het == 1
        assert "HOH" in dropped
        assert "STI" in dropped

    def test_filter_selects_the_requested_chain(self) -> None:
        pdb = (
            "ATOM      1  CA  ALA A   1       0.0   0.0   0.0  1.00  0.00           C\n"
            "ATOM      2  CA  ALA B   1       1.0   1.0   1.0  1.00  0.00           C\n"
        )
        kept, _, _ = _filter_structure(pdb, "A", frozenset())
        lines = [ln for ln in kept.splitlines() if ln.startswith("ATOM")]
        assert len(lines) == 1
        assert lines[0][21] == "A"

    def test_modified_residues_arriving_as_hetatm_are_kept(self) -> None:
        """MSE (selenomethionine) is polymer, not a ligand, despite HETATM."""
        pdb = "HETATM    1  CA  MSE A   5       0.0   0.0   0.0  1.00  0.00           C\n"
        kept, dropped, _ = _filter_structure(pdb, "A", frozenset())
        assert "MSE" in kept
        assert "MSE" not in dropped

    def test_solvent_set_covers_the_usual_names(self) -> None:
        assert {"HOH", "WAT"} <= SOLVENT_RESIDUES


class TestAdmetStage:
    def test_profiles_every_ligand(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        ligands, _ = prepare_ligands(cfg, tmp_path)
        rows, summary = run_admet(cfg, ligands, tmp_path)

        assert summary.n_ligands == len(rows) == len(ligands)
        assert Path(summary.results_path).is_file()
        for row in rows:
            assert row.descriptors["mw"] > 0
            assert set(row.rule_results) == set(cfg.admet.rule_sets)
            assert 0.0 <= row.admet_pass_rate <= 1.0
            assert "n_structural_alerts" in row.alerts
            assert "logs_esol_pred" in row.ml_predictions
            assert "logs_esol_in_domain" in row.ml_predictions

    def test_results_csv_has_one_row_per_ligand(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        ligands, _ = prepare_ligands(cfg, tmp_path)
        _, summary = run_admet(cfg, ligands, tmp_path)
        frame = pd.read_csv(summary.results_path)
        assert len(frame) == len(ligands)
        assert "lipinski_pass" in frame.columns
        assert "lipinski_failed_criteria" in frame.columns


class TestExternalToolHandoff:
    def test_writes_batches_schema_and_readme(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        ligands, _ = prepare_ligands(cfg, tmp_path)
        rows, _ = run_admet(cfg, ligands, tmp_path)
        written = write_external_tool_batches(cfg, rows, tmp_path)

        assert written
        tool_dir = tmp_path / "external_tools" / "swissadme"
        assert (tool_dir / "input_batch_01.smi").is_file()
        assert (tool_dir / "paste_back_template.csv").is_file()
        readme = (tool_dir / "README.md").read_text(encoding="utf-8")
        assert "does not scrape" in readme
        assert "retrieved_utc" in readme

        batch = (tool_dir / "input_batch_01.smi").read_text(encoding="utf-8")
        assert "\t" in batch  # SMILES<TAB>ligand_id

    def test_paste_back_round_trips(self, tmp_path: Path) -> None:
        schema = EXTERNAL_TOOL_SCHEMAS["swissadme"]
        path = tmp_path / "results.csv"
        header = ",".join(schema)
        row = ["L0001_x", "High", "Yes", "No", "No", "No", "No", "No", "Yes", "-6.1", "0.55"]
        path.write_text(f"{header}\n{','.join(row)}\n", encoding="utf-8")

        frame = read_external_tool_csv("swissadme", path)
        assert frame.loc[0, "ligand_id"] == "L0001_x"
        assert bool(frame.loc[0, "swissadme_bbb_permeant"]) is True
        assert bool(frame.loc[0, "swissadme_pgp_substrate"]) is False
        assert frame.loc[0, "swissadme_log_kp_cm_s"] == pytest.approx(-6.1)

    def test_unparseable_cell_raises_rather_than_defaulting(self, tmp_path: Path) -> None:
        schema = EXTERNAL_TOOL_SCHEMAS["swissadme"]
        path = tmp_path / "results.csv"
        header = ",".join(schema)
        row = ["L1", "High", "Yes", "No", "No", "No", "No", "No", "Yes", "not_a_number", "0.5"]
        path.write_text(f"{header}\n{','.join(row)}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="expected a number"):
            read_external_tool_csv("swissadme", path)

    def test_invalid_category_raises(self, tmp_path: Path) -> None:
        schema = EXTERNAL_TOOL_SCHEMAS["swissadme"]
        path = tmp_path / "results.csv"
        header = ",".join(schema)
        row = ["L1", "Medium", "Yes", "No", "No", "No", "No", "No", "Yes", "-6.0", "0.5"]
        path.write_text(f"{header}\n{','.join(row)}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="expected one of"):
            read_external_tool_csv("swissadme", path)

    def test_missing_column_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "results.csv"
        path.write_text("ligand_id\nL1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing required column"):
            read_external_tool_csv("swissadme", path)

    def test_blank_cell_is_missing_not_zero(self, tmp_path: Path) -> None:
        schema = EXTERNAL_TOOL_SCHEMAS["swissadme"]
        path = tmp_path / "results.csv"
        header = ",".join(schema)
        row = ["L1", "High", "Yes", "No", "No", "No", "No", "No", "Yes", "", "0.5"]
        path.write_text(f"{header}\n{','.join(row)}\n", encoding="utf-8")
        frame = read_external_tool_csv("swissadme", path)
        assert frame.loc[0, "swissadme_log_kp_cm_s"] is None

    def test_unknown_tool_raises(self, tmp_path: Path) -> None:
        with pytest.raises(KeyError, match="No paste-back schema"):
            read_external_tool_csv("not_a_tool", tmp_path / "x.csv")

    def test_every_configured_tool_has_a_schema(self) -> None:
        cfg = load_config(REJECTS_CONFIG)
        for tool in cfg.admet.external_tools:
            assert tool in EXTERNAL_TOOL_SCHEMAS, f"no paste-back schema for {tool}"


class TestRankingStage:
    def test_refuses_to_rank_by_raw_affinity(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        for key in sorted(FORBIDDEN_SORT_KEYS):
            with pytest.raises(ValueError, match="Refusing to rank"):
                rank_compounds(cfg, [], [], tmp_path, sort_by=key)

    def test_empty_input_raises_clearly(self, tmp_path: Path) -> None:
        cfg = load_config(SMOKE_CONFIG)
        with pytest.raises(ValueError, match="No rows survived ranking"):
            rank_compounds(cfg, [], [], tmp_path)


class TestAlertInterpretation:
    def test_a_known_pains_matching_natural_product_is_flagged_not_dropped(self) -> None:
        """Quercetin matches PAINS filters and is a genuine bioactive.

        The pipeline must report the matched filter names so a reader can judge
        relevance, rather than collapsing it to a verdict.
        """
        from rdkit import Chem

        quercetin = Chem.MolFromSmiles("O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12")
        screen = screen_alerts(quercetin, ["pains", "brenk"])
        assert not screen.clean
        summary = screen.summary()
        # Named filters, not just a boolean.
        assert summary["pains_filters"] or summary["brenk_filters"]
