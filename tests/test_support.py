"""Tests for support modules: RCSB parsing, poses, decoys, workbook, logging, ADMET ML."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rdkit import Chem

from npcadd.chem.admet_ml import (
    DescriptorBoxDomain,
    ESOLSolubilityPredictor,
    TanimotoKNNDomain,
    get_ml_predictor,
)
from npcadd.chem.descriptors import compute_descriptors
from npcadd.config import load_config
from npcadd.docking.decoys import (
    DecoyGenerationError,
    build_pool,
    generate_decoys,
    generate_null_set,
    pool_property_summary,
)
from npcadd.docking.poses import PoseLoadError, centroid_distance, in_place_rmsd, load_poses
from npcadd.docking.vina_runner import effective_cpu_budget, job_fingerprint
from npcadd.ioutils.rcsb import (
    StructureUnavailableError,
    fetch_structure,
    parse_pdb_metadata,
)
from npcadd.logging_utils import configure_logging, get_logger, new_run_id
from npcadd.report.workbook import SheetSpec, write_workbook
from npcadd.resources.building_blocks import describe_library, enumerate_triples

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDB = REPO_ROOT / "data/sample/receptors/1IEP_abl1_receptor_protonated.pdb"
SAMPLE_SDF = REPO_ROOT / "data/sample/receptors/1IEP_cocrystal_STI_imatinib.sdf"

HEADER = """HEADER    TRANSFERASE                             22-MAR-13   4JSV
TITLE     MTOR KINASE STRUCTURE
EXPDTA    X-RAY DIFFRACTION
REMARK   2 RESOLUTION.    2.10 ANGSTROMS.
REMARK   3   FREE R VALUE                     : 0.231
REMARK   3   R VALUE            (WORKING SET) : 0.187
DBREF  4JSV A    1   100  UNP    P42345   MTOR_HUMAN      1    100
HETATM    1  C1  ADP A 500       0.0   0.0   0.0  1.00  0.00           C
HETATM    2  O   HOH A 501       1.0   1.0   1.0  1.00  0.00           O
ATOM      3  CA  ALA A   1       2.0   2.0   2.0  1.00  0.00           C
"""


class TestRcsbMetadata:
    def test_parses_header_fields(self) -> None:
        meta = parse_pdb_metadata(HEADER, "4jsv")
        assert meta.pdb_id == "4JSV"
        assert meta.resolution_angstrom == pytest.approx(2.10)
        assert meta.r_free == pytest.approx(0.231)
        assert meta.r_work == pytest.approx(0.187)
        assert meta.experimental_method == "X-RAY DIFFRACTION"
        assert meta.uniprot_accession == "P42345"
        assert meta.cocrystal_ligands == ["ADP"]  # water excluded

    def test_stripped_header_reports_unavailable_not_a_guess(self) -> None:
        meta = parse_pdb_metadata("ATOM      1  CA  ALA A   1       0.0 0.0 0.0\n", "1abc")
        record = meta.as_record()
        for key in ("resolution_angstrom", "r_free", "uniprot_accession", "title"):
            assert record[key] == "unavailable"
        assert record["cocrystal_ligands"] == "none"


class TestFetchStructure:
    def test_local_structure_is_hashed(self) -> None:
        source = fetch_structure("1IEP", Path("/nonexistent"), local_structure=SAMPLE_PDB)
        assert source.sha256
        assert not source.from_cache

    def test_local_checksum_mismatch_refused(self) -> None:
        with pytest.raises(StructureUnavailableError, match="does not match|expected"):
            fetch_structure(
                "1IEP", Path("/x"), local_structure=SAMPLE_PDB, expected_sha256="0" * 64
            )

    def test_missing_local_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(StructureUnavailableError, match="does not exist"):
            fetch_structure("1IEP", tmp_path, local_structure=tmp_path / "nope.pdb")

    def test_offline_cache_miss_explains_remedies(self, tmp_path: Path) -> None:
        with pytest.raises(StructureUnavailableError, match="allow_network is"):
            fetch_structure("1IEP", tmp_path, allow_network=False)

    def test_cache_hit_records_checksum(self, tmp_path: Path) -> None:
        (tmp_path / "1IEP.pdb").write_text(HEADER, encoding="utf-8")
        source = fetch_structure("1IEP", tmp_path, allow_network=False)
        assert source.from_cache
        assert (tmp_path / "1IEP.sha256").is_file()

    def test_corrupt_cache_is_never_used_offline(self, tmp_path: Path) -> None:
        (tmp_path / "1IEP.pdb").write_text(HEADER, encoding="utf-8")
        (tmp_path / "1IEP.sha256").write_text("f" * 64 + "  1IEP.pdb\n", encoding="utf-8")
        with pytest.raises(StructureUnavailableError, match="not the expected"):
            fetch_structure("1IEP", tmp_path, allow_network=False)


class TestPoses:
    @staticmethod
    def _mol_with_shift(shift: float) -> Chem.Mol:
        mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
        from rdkit.Chem import AllChem

        AllChem.EmbedMolecule(mol, randomSeed=1)
        conf = mol.GetConformer()
        for i in range(mol.GetNumAtoms()):
            p = conf.GetAtomPosition(i)
            conf.SetAtomPosition(i, (p.x + shift, p.y, p.z))
        return mol

    def test_identical_poses_have_zero_rmsd(self) -> None:
        assert in_place_rmsd(self._mol_with_shift(0), self._mol_with_shift(0)) == pytest.approx(
            0.0, abs=1e-6
        )

    def test_translation_is_not_aligned_away(self) -> None:
        """The reason for in-place RMSD: a shifted pose must NOT score zero."""
        rmsd = in_place_rmsd(self._mol_with_shift(5.0), self._mol_with_shift(0.0))
        assert rmsd == pytest.approx(5.0, abs=1e-3)

    def test_centroid_distance(self) -> None:
        d = centroid_distance(self._mol_with_shift(3.0), self._mol_with_shift(0.0))
        assert d == pytest.approx(3.0, abs=1e-3)

    def test_different_molecules_raise(self) -> None:
        other = Chem.AddHs(Chem.MolFromSmiles("CCCCO"))
        from rdkit.Chem import AllChem

        AllChem.EmbedMolecule(other, randomSeed=1)
        with pytest.raises(ValueError, match="different molecules"):
            in_place_rmsd(self._mol_with_shift(0), other)

    def test_missing_conformer_and_none_raise(self) -> None:
        with pytest.raises(ValueError, match="None"):
            in_place_rmsd(None, self._mol_with_shift(0))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="conformer"):
            in_place_rmsd(self._mol_with_shift(0), self._mol_with_shift(0), probe_conf_id=3)

    def test_load_poses_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(PoseLoadError, match="does not exist"):
            load_poses(tmp_path / "nope.pdbqt")


class TestDecoys:
    @pytest.fixture(scope="class")
    def pool(self) -> list:
        return build_pool(max_size=300)

    def test_pool_is_flexibility_capped(self, pool: list) -> None:
        assert max(m.descriptors["rotatable_bonds"] for m in pool) <= 10

    def test_building_blocks_enumeration_is_deterministic(self) -> None:
        assert enumerate_triples()[:50] == enumerate_triples()[:50]
        info = describe_library()
        assert info["theoretical_pool_size"] > 1000

    def test_decoys_require_actives(self, pool: list) -> None:
        with pytest.raises(DecoyGenerationError, match="No known actives"):
            generate_decoys([], pool)

    def test_decoys_are_dissimilar_and_matched(self, pool: list) -> None:
        active = Chem.MolFromSmiles("CC1(C)CC(O)CC2(C)OC(=O)C=C12")
        sets = generate_decoys(
            [("lol", active)],
            pool,
            n_per_active=5,
            tolerance={"mw": 40.0, "clogp": 1.5, "tpsa": 25.0},
            seed=1,
        )
        assert sets[0].decoys
        assert sets[0].as_record()["n_decoys_selected"] == len(sets[0].decoys)
        assert sets[0].shortfall == 5 - len(sets[0].decoys)

    def test_null_set_is_seed_stable_and_validated(self, pool: list) -> None:
        a = generate_null_set(pool, n=10, seed=3)
        b = generate_null_set(pool, n=10, seed=3)
        assert [m.pool_id for m in a] == [m.pool_id for m in b]
        with pytest.raises(ValueError, match="too small"):
            generate_null_set(pool, n=2)
        with pytest.raises(DecoyGenerationError):
            generate_null_set([], n=10)

    def test_pool_summary(self, pool: list) -> None:
        summary = pool_property_summary(pool)
        assert summary["n_pool"] == len(pool)
        assert summary["mw_min"] < summary["mw_max"]


class TestVinaHelpers:
    def test_cpu_budget_widens_only_when_jobs_are_few(self) -> None:
        cfg = load_config(REPO_ROOT / "config.smoke.yaml").docking
        assert effective_cpu_budget(cfg, 1) == cfg.workers * cfg.cpu_per_worker
        assert effective_cpu_budget(cfg, 100) == cfg.cpu_per_worker
        with pytest.raises(ValueError):
            effective_cpu_budget(cfg, 0)

    def test_fingerprint_changes_with_exhaustiveness(self, tmp_path: Path) -> None:
        from npcadd.docking.grid import ResolvedBox
        from npcadd.docking.vina_runner import DockingJob

        lig = tmp_path / "l.pdbqt"
        rec = tmp_path / "r.pdbqt"
        lig.write_text("L")
        rec.write_text("R")
        job = DockingJob(
            "l",
            "r",
            lig,
            rec,
            ResolvedBox((0, 0, 0), (20, 20, 20), "explicit", "x"),
            tmp_path / "o",
        )
        cfg = load_config(REPO_ROOT / "config.smoke.yaml").docking
        before = job_fingerprint(job, cfg)
        cfg.exhaustiveness += 1
        assert job_fingerprint(job, cfg) != before


class TestAdmetMl:
    def test_esol_known_ballpark(self) -> None:
        mol = Chem.MolFromSmiles("c1ccccc1")  # benzene, measured logS ~ -1.64
        preds = ESOLSolubilityPredictor().predict(mol, compute_descriptors(mol))
        assert -3.0 < preds.predictions[0].value < -0.5
        assert preds.predictions[0].reportable

    def test_out_of_domain_is_flagged(self) -> None:
        mol = Chem.MolFromSmiles("C" * 80)
        preds = ESOLSolubilityPredictor().predict(mol, compute_descriptors(mol))
        assert not preds.predictions[0].reportable
        assert preds.n_out_of_domain == 1
        assert "outside training range" in preds.as_record()["logs_esol_domain_note"]

    def test_box_domain_validates_ranges(self) -> None:
        with pytest.raises(ValueError, match="inverted"):
            DescriptorBoxDomain({"mw": (10.0, 1.0)})
        with pytest.raises(ValueError, match="at least one"):
            DescriptorBoxDomain({})

    def test_knn_domain(self) -> None:
        domain = TanimotoKNNDomain(["CCO", "CCCO", "CCCCO"], threshold=0.3, k=2)
        near = domain.assess(Chem.MolFromSmiles("CCCCCO"), {})
        far = domain.assess(Chem.MolFromSmiles("c1ccc2ccccc2c1"), {})
        assert near.score > far.score
        with pytest.raises(ValueError, match="training set SMILES"):
            TanimotoKNNDomain([])

    def test_unknown_predictor_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown ML ADMET predictor"):
            get_ml_predictor("nope")


class TestWorkbook:
    def test_readme_sheet_first_and_empty_frames_explained(self, tmp_path: Path) -> None:
        cfg = load_config(REPO_ROOT / "config.smoke.yaml")
        out = write_workbook(
            cfg,
            [
                SheetSpec("data", pd.DataFrame({"a": [1, 2]}), "has data"),
                SheetSpec("empty", pd.DataFrame(), "no rows"),
            ],
            tmp_path / "w.xlsx",
            {"rdkit": "x"},
            "run1",
            "hash",
            ["a caveat"],
        )
        sheets = pd.read_excel(out, sheet_name=None)
        assert list(sheets)[0] == "README"
        assert "note" in sheets["empty"].columns
        assert "a caveat" in sheets["README"]["value"].astype(str).tolist()

    def test_long_sheet_names_truncated(self) -> None:
        assert len(SheetSpec("x" * 50, pd.DataFrame(), "d").safe_name) == 31


class TestLogging:
    def test_run_id_embeds_config_hash(self) -> None:
        assert new_run_id("abcdef1234567890").endswith("abcdef12")

    def test_jsonl_sink_carries_run_id_and_extras(self, tmp_path: Path) -> None:
        log_file = tmp_path / "run.jsonl"
        configure_logging("rid-1", log_file=log_file)
        get_logger("t").info("hello", extra={"n": 3})
        for handler in logging.getLogger("npcadd").handlers:
            handler.flush()
        record = json.loads(log_file.read_text().splitlines()[-1])
        assert record["run_id"] == "rid-1"
        assert record["n"] == 3
        assert record["message"] == "hello"


def test_grid_from_real_cocrystal_pose() -> None:
    from npcadd.config import GridBox
    from npcadd.docking.grid import derive_box

    mol = Chem.MolFromMolFile(str(SAMPLE_SDF), removeHs=True)
    box = derive_box(GridBox(mode="cocrystal"), np.asarray(mol.GetConformer().GetPositions()))
    assert box.center == pytest.approx((15.614, 53.38, 15.455), abs=0.01)


def test_interaction_profiles_use_each_conformer_not_just_the_first() -> None:
    """Regression: ProLIF's from_rdkit reads conformer 0 only, so a multi-pose
    ligand must be split per pose or every pose gets the first pose's contacts
    (originally it crashed outright on the unsupported conf_id argument)."""
    from rdkit import Chem

    from npcadd.docking.interactions import load_protein, profile_interactions

    protein = load_protein(REPO_ROOT / "data/sample/receptors/1IEP_abl1_receptor_protonated.pdb")
    ligand = Chem.MolFromMolFile(str(SAMPLE_SDF), removeHs=False)
    far = Chem.Mol(ligand)
    conf = far.GetConformer()
    for i in range(far.GetNumAtoms()):
        p = conf.GetAtomPosition(i)
        conf.SetAtomPosition(i, (p.x + 60.0, p.y, p.z))  # well outside the protein
    ligand.AddConformer(Chem.Conformer(far.GetConformer()), assignId=True)

    near, away = profile_interactions(protein, ligand, "sti", "abl1", ["Hydrophobic", "HBDonor"])
    assert near.n_contacts > 0
    assert away.n_contacts == 0
