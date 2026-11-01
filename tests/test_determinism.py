"""Determinism tests, including the golden-file test the brief requires.

The contract these tests defend: running the pipeline twice on the same inputs
with the same config must produce byte-identical outputs. That is the whole
basis of the reproducibility claim, so it is asserted on file hashes rather
than on eyeballed values.

Scope note. The stages covered here are the fully deterministic ones: ligand
preparation (standardisation, pKa assignment, ETKDGv3 embedding at a fixed
seed, MMFF94, PDBQT export), descriptors, rule sets, alerts and ranking.
Docking determinism is tested separately and more weakly — see
``test_smoke.py`` — because AutoDock Vina's parallel search can interleave
work differently between runs even at a fixed seed. That limitation is recorded
in LIMITATIONS.md rather than papered over here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from npcadd.config import load_config
from npcadd.hashing import hash_file
from npcadd.stages.admet import run_admet
from npcadd.stages.ligands import prepare_ligands

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_CONFIG = REPO_ROOT / "config.smoke.yaml"

#: Files whose content must be byte-identical between two runs.
GOLDEN_OUTPUTS = ("ligand_manifest.csv", "rejected_ligands.csv", "admet_results.csv")


def _run_deterministic_stages(out_dir: Path) -> dict[str, str]:
    """Run the deterministic stages and hash their outputs."""
    cfg = load_config(SMOKE_CONFIG)
    ligands, ligand_summary = prepare_ligands(cfg, out_dir)
    run_admet(cfg, ligands, out_dir)

    hashes: dict[str, str] = {}
    for name in GOLDEN_OUTPUTS:
        path = out_dir / name
        assert path.is_file(), f"expected output missing: {path}"
        hashes[name] = hash_file(path)

    # Every exported ligand PDBQT must also be byte-stable: these are the
    # coordinates that go into docking.
    for pdbqt in sorted((out_dir / "ligands").glob("*.pdbqt")):
        hashes[f"ligands/{pdbqt.name}"] = hash_file(pdbqt)
    assert ligand_summary.n_accepted >= 1
    return hashes


class TestGoldenFileDeterminism:
    def test_pipeline_run_twice_produces_identical_output_hashes(self, tmp_path: Path) -> None:
        """The golden-file determinism test: two full runs, identical hashes."""
        first = _run_deterministic_stages(tmp_path / "run_a")
        second = _run_deterministic_stages(tmp_path / "run_b")

        assert set(first) == set(second), "the two runs produced different file sets"
        differing = {k for k in first if first[k] != second[k]}
        assert not differing, (
            "these outputs are not reproducible between runs: "
            f"{sorted(differing)}. Reproducibility is the pipeline's core claim, so "
            "this is a defect, not a tolerance to widen."
        )
        assert len(first) >= 4, "the golden set should cover the manifest plus PDBQTs"

    def test_ligand_pdbqt_coordinates_are_stable(self, tmp_path: Path) -> None:
        """A PDBQT's coordinate block must be identical, not merely similar."""
        out_a, out_b = tmp_path / "a", tmp_path / "b"
        _run_deterministic_stages(out_a)
        _run_deterministic_stages(out_b)

        files_a = sorted((out_a / "ligands").glob("*.pdbqt"))
        files_b = sorted((out_b / "ligands").glob("*.pdbqt"))
        assert files_a and len(files_a) == len(files_b)

        for path_a, path_b in zip(files_a, files_b, strict=True):
            lines_a = [line for line in path_a.read_text().splitlines() if line.startswith("ATOM")]
            lines_b = [line for line in path_b.read_text().splitlines() if line.startswith("ATOM")]
            assert lines_a == lines_b, f"coordinates drifted in {path_a.name}"

    def test_a_changed_seed_changes_the_output(self, tmp_path: Path) -> None:
        """The determinism test must be able to fail: a different seed must differ.

        Without this, a test that hashed constant output would pass vacuously.
        """
        cfg = load_config(SMOKE_CONFIG)
        baseline = tmp_path / "baseline"
        prepare_ligands(cfg, baseline)
        baseline_hashes = {
            p.name: hash_file(p) for p in sorted((baseline / "ligands").glob("*.pdbqt"))
        }

        cfg_changed = load_config(SMOKE_CONFIG)
        cfg_changed.ligands.embed_seed = cfg.ligands.embed_seed + 1
        changed = tmp_path / "changed"
        prepare_ligands(cfg_changed, changed)
        changed_hashes = {
            p.name: hash_file(p) for p in sorted((changed / "ligands").glob("*.pdbqt"))
        }

        assert set(baseline_hashes) == set(changed_hashes)
        assert any(baseline_hashes[k] != changed_hashes[k] for k in baseline_hashes), (
            "changing the embedding seed must change the coordinates"
        )

    def test_config_hash_is_recorded_and_changes_with_the_seed(self) -> None:
        cfg = load_config(SMOKE_CONFIG)
        before = cfg.config_hash()
        cfg.ligands.embed_seed += 1
        assert cfg.config_hash() != before


class TestStageIdempotence:
    def test_rerunning_ligand_prep_into_the_same_directory_is_stable(self, tmp_path: Path) -> None:
        """Re-running a stage must overwrite cleanly, not append or drift."""
        cfg = load_config(SMOKE_CONFIG)
        out_dir = tmp_path / "run"
        prepare_ligands(cfg, out_dir)
        first = hash_file(out_dir / "ligand_manifest.csv")
        prepare_ligands(cfg, out_dir)
        assert hash_file(out_dir / "ligand_manifest.csv") == first

    def test_outputs_use_lf_line_endings(self, tmp_path: Path) -> None:
        """CSV line endings are pinned, or hashes would differ across platforms."""
        cfg = load_config(SMOKE_CONFIG)
        out_dir = tmp_path / "run"
        prepare_ligands(cfg, out_dir)
        raw = (out_dir / "ligand_manifest.csv").read_bytes()
        assert b"\r\n" not in raw


class TestPoolDeterminism:
    @pytest.mark.slow
    def test_decoy_pool_is_identical_between_builds(self) -> None:
        from npcadd.docking.decoys import build_pool

        a = build_pool(max_size=400)
        b = build_pool(max_size=400)
        assert [m.smiles for m in a] == [m.smiles for m in b]
        assert [m.pool_id for m in a] == [m.pool_id for m in b]

    @pytest.mark.slow
    def test_null_set_selection_is_seed_stable(self) -> None:
        from npcadd.docking.decoys import build_pool, generate_null_set

        pool = build_pool(max_size=400)
        a = generate_null_set(pool, n=20, seed=99)
        b = generate_null_set(pool, n=20, seed=99)
        c = generate_null_set(pool, n=20, seed=100)
        assert [m.pool_id for m in a] == [m.pool_id for m in b]
        assert [m.pool_id for m in a] != [m.pool_id for m in c]

    @pytest.mark.slow
    def test_pool_spans_a_wide_mass_range(self) -> None:
        """Guards the bit-reversal ordering fix: a truncated pool must not be
        confined to small fragments, or it cannot property-match real actives.
        """
        from npcadd.docking.decoys import build_pool

        pool = build_pool(max_size=1500)
        masses = [m.descriptors["mw"] for m in pool]
        assert min(masses) < 150
        assert max(masses) > 330, (
            "the truncated pool does not reach drug-sized molecules; the "
            "enumeration ordering has regressed"
        )


def test_temporary_run_dirs_are_cleaned(tmp_path: Path) -> None:
    """Sanity check that the fixtures above are not leaking into the repo."""
    assert tmp_path.exists()
    shutil.rmtree(tmp_path, ignore_errors=True)
    assert not (REPO_ROOT / "run_a").exists()
    assert not (REPO_ROOT / "run_b").exists()
