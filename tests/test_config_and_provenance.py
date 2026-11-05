"""Tests for configuration validation, hashing, provenance and grid derivation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from npcadd.config import Config, GridBox, load_config
from npcadd.docking.grid import assess_placement, derive_box, point_in_box
from npcadd.hashing import canonical_json, hash_bytes, hash_file, hash_obj, short
from npcadd.provenance import Provenance, git_state, package_versions, platform_state

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CONFIG = REPO_ROOT / "config.sample.yaml"


@pytest.fixture
def raw_config() -> dict[str, Any]:
    with SAMPLE_CONFIG.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


def write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    """Write a config next to the repo so relative paths still resolve."""
    path = (
        REPO_ROOT / f"_test_config_{abs(hash(json.dumps(data, sort_keys=True, default=str)))}.yaml"
    )
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


class TestHashing:
    def test_file_hash_is_over_raw_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "a.txt"
        path.write_bytes(b"hello")
        assert hash_file(path) == hash_bytes(b"hello")

    def test_file_hash_distinguishes_line_endings(self, tmp_path: Path) -> None:
        """Hashing text rather than bytes would make these collide."""
        unix = tmp_path / "unix.txt"
        dos = tmp_path / "dos.txt"
        unix.write_bytes(b"a\nb\n")
        dos.write_bytes(b"a\r\nb\r\n")
        assert hash_file(unix) != hash_file(dos)

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            hash_file(tmp_path)

    def test_canonical_json_is_key_order_independent(self) -> None:
        assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})
        assert hash_obj({"a": 1, "b": 2}) == hash_obj({"b": 2, "a": 1})

    def test_canonical_json_handles_paths_and_sets(self) -> None:
        text = canonical_json({"p": Path("/tmp/x"), "s": {"b", "a"}})
        assert "/tmp/x" in text
        assert text.index('"a"') < text.index('"b"')

    def test_canonical_json_rejects_nan(self) -> None:
        with pytest.raises(ValueError, match="Out of range|NaN|not JSON"):
            canonical_json({"x": float("nan")})

    def test_distinct_objects_hash_differently(self) -> None:
        assert hash_obj({"x": 1}) != hash_obj({"x": 2})
        # 1 and "1" must not collide: a config value's type matters.
        assert hash_obj({"x": 1}) != hash_obj({"x": "1"})

    def test_short_requires_positive_length(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            short("abcdef", 0)


class TestConfigValidation:
    def test_sample_config_loads(self) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        assert cfg.receptors
        assert cfg.activities == ["cytotoxic"]

    def test_config_hash_is_stable_across_loads(self) -> None:
        assert load_config(SAMPLE_CONFIG).config_hash() == load_config(SAMPLE_CONFIG).config_hash()

    def test_config_hash_changes_with_any_parameter(
        self, tmp_path: Path, raw_config: dict[str, Any]
    ) -> None:
        base = load_config(SAMPLE_CONFIG).config_hash()
        mutated = copy.deepcopy(raw_config)
        mutated["docking"]["exhaustiveness"] = 64
        path = write_config(tmp_path, mutated)
        try:
            assert load_config(path).config_hash() != base
        finally:
            path.unlink(missing_ok=True)

    def test_relative_paths_resolve_against_the_config_file(self) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        assert cfg.ligands.input_csv.is_absolute()
        assert cfg.ligands.input_csv.is_file()

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config(REPO_ROOT / "does_not_exist.yaml")

    def test_unknown_key_is_rejected(self, tmp_path: Path, raw_config: dict[str, Any]) -> None:
        mutated = copy.deepcopy(raw_config)
        mutated["typo_key"] = 1
        path = write_config(tmp_path, mutated)
        try:
            with pytest.raises(Exception, match="(?i)extra|forbid|validation"):
                load_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_weights_must_sum_to_one(self, tmp_path: Path, raw_config: dict[str, Any]) -> None:
        mutated = copy.deepcopy(raw_config)
        mutated["ranking"]["weights"]["gcms_area"] = 0.9
        path = write_config(tmp_path, mutated)
        try:
            with pytest.raises(Exception, match="(?i)sum to 1|validation"):
                load_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_report_formats_require_a_vector_format(
        self, tmp_path: Path, raw_config: dict[str, Any]
    ) -> None:
        mutated = copy.deepcopy(raw_config)
        mutated["report"]["formats"] = ["png"]
        path = write_config(tmp_path, mutated)
        try:
            with pytest.raises(Exception, match="(?i)vector|validation"):
                load_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_duplicate_receptor_ids_rejected(
        self, tmp_path: Path, raw_config: dict[str, Any]
    ) -> None:
        mutated = copy.deepcopy(raw_config)
        mutated["receptors"].append(copy.deepcopy(mutated["receptors"][0]))
        path = write_config(tmp_path, mutated)
        try:
            with pytest.raises(Exception, match="(?i)duplicate|validation"):
                load_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_receptor_lookup(self) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        assert cfg.receptor("abl1").pdb_id == "1IEP"
        with pytest.raises(KeyError, match="No receptor with id"):
            cfg.receptor("nope")

    def test_directionality_conflict_is_detected(self) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        receptor = cfg.receptor("abl1")
        assert not receptor.directionality_conflict
        receptor.phenotype_direction = "activation"
        assert receptor.directionality_conflict

    def test_resolved_config_roundtrips_through_yaml(self) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        reparsed = Config.model_validate(yaml.safe_load(cfg.to_yaml()))
        assert reparsed.config_hash() == cfg.config_hash()


class TestGridBox:
    def test_cocrystal_mode_rejects_explicit_coordinates(self) -> None:
        with pytest.raises(ValueError, match="derives"):
            GridBox(mode="cocrystal", center=(1.0, 2.0, 3.0))

    def test_explicit_mode_requires_both_values(self) -> None:
        with pytest.raises(ValueError, match="requires both"):
            GridBox(mode="explicit", center=(1.0, 2.0, 3.0))

    def test_oversized_box_rejected(self) -> None:
        with pytest.raises(ValueError, match="exceeds max_edge"):
            GridBox(mode="explicit", center=(0.0, 0.0, 0.0), size=(60.0, 20.0, 20.0))

    def test_undersized_box_rejected(self) -> None:
        with pytest.raises(ValueError, match="below 10"):
            GridBox(mode="explicit", center=(0.0, 0.0, 0.0), size=(5.0, 20.0, 20.0))

    def test_derived_box_centres_on_the_ligand(self) -> None:
        coords = np.array([[0.0, 0.0, 0.0], [2.0, 4.0, 6.0]])
        box = derive_box(GridBox(mode="cocrystal", padding=5.0), coords)
        assert box.center == pytest.approx((1.0, 2.0, 3.0))
        # Extent (2,4,6) + 2*5 padding = (12,14,16), floored at min_size 20.
        assert box.size == pytest.approx((20.0, 20.0, 20.0))
        assert "centroid of 2 co-crystal ligand atoms" in box.derivation

    def test_derived_box_grows_beyond_min_size_for_a_large_ligand(self) -> None:
        coords = np.array([[0.0, 0.0, 0.0], [10.0, 20.0, 30.0]])
        box = derive_box(GridBox(mode="cocrystal", padding=4.0, max_edge=60.0), coords)
        assert box.size == pytest.approx((20.0, 28.0, 38.0))

    def test_cocrystal_mode_without_coordinates_raises(self) -> None:
        with pytest.raises(ValueError, match="requires the co-crystal ligand"):
            derive_box(GridBox(mode="cocrystal"), None)

    def test_rejects_malformed_coordinates(self) -> None:
        with pytest.raises(ValueError, match="shape"):
            derive_box(GridBox(mode="cocrystal"), np.array([1.0, 2.0, 3.0]))
        with pytest.raises(ValueError, match="non-finite"):
            derive_box(GridBox(mode="cocrystal"), np.array([[0.0, 0.0, 0.0], [np.nan, 0.0, 0.0]]))

    def test_derived_box_exceeding_max_edge_raises(self) -> None:
        coords = np.array([[0.0, 0.0, 0.0], [50.0, 0.0, 0.0]])
        with pytest.raises(ValueError, match="exceed max_edge"):
            derive_box(GridBox(mode="cocrystal", padding=5.0), coords)

    def test_explicit_mode_passes_values_through(self) -> None:
        box = derive_box(GridBox(mode="explicit", center=(1.0, 2.0, 3.0), size=(20.0, 20.0, 20.0)))
        assert box.center == (1.0, 2.0, 3.0)
        assert box.mode == "explicit"
        assert "verbatim" in box.derivation

    def test_point_in_box(self) -> None:
        box = derive_box(GridBox(mode="explicit", center=(0.0, 0.0, 0.0), size=(20.0, 20.0, 20.0)))
        assert point_in_box(np.array([0.0, 0.0, 0.0]), box)
        assert point_in_box(np.array([10.0, 0.0, 0.0]), box)
        assert not point_in_box(np.array([11.0, 0.0, 0.0]), box)
        assert point_in_box(np.array([11.0, 0.0, 0.0]), box, tolerance=2.0)

    def test_box_volume_and_record(self) -> None:
        box = derive_box(GridBox(mode="explicit", center=(0.0, 0.0, 0.0), size=(10.0, 20.0, 30.0)))
        assert box.volume_a3 == pytest.approx(6000.0)
        record = box.as_record()
        assert record["box_size_y"] == 20.0
        assert record["box_mode"] == "explicit"


class TestPosePlacement:
    """The screened-pose box-containment check (DECISIONS.md D-021)."""

    @staticmethod
    def _box() -> Any:
        # 20 A cube centred at the origin: half-width 10 A on every axis.
        return derive_box(GridBox(mode="explicit", center=(0.0, 0.0, 0.0), size=(20.0, 20.0, 20.0)))

    def test_a_centred_compact_pose_is_in_box(self) -> None:
        coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, -1, -1]], float)
        placement = assess_placement(coords, self._box())
        assert placement.in_box
        assert placement.fraction_inside == 1.0
        assert placement.n_inside == placement.n_atoms == 5
        assert placement.centroid_offset_frac < 0.1
        assert "well inside" in placement.note

    def test_atoms_outside_the_box_are_flagged(self) -> None:
        # Two of five heavy atoms sit beyond the +x face (|x| > 10).
        coords = np.array([[0, 0, 0], [1, 0, 0], [15, 0, 0], [15, 1, 0], [0, 1, 0]], float)
        placement = assess_placement(coords, self._box())
        assert not placement.in_box
        assert placement.fraction_inside == pytest.approx(0.6)
        assert placement.max_atom_offset_frac > 1.0
        assert "outside the search box" in placement.note

    def test_a_wall_hugging_centroid_is_flagged_even_when_all_atoms_fit(self) -> None:
        # Every atom is inside (x <= 10) but the centroid sits at 0.95 of the half-width.
        coords = np.array([[9.5, 0, 0], [9.4, 0, 0], [9.6, 0, 0], [9.5, 1, 0], [9.5, -1, 0]], float)
        placement = assess_placement(coords, self._box())
        assert placement.fraction_inside == 1.0
        assert not placement.in_box
        assert placement.centroid_offset_frac == pytest.approx(0.95)
        assert "hugging" in placement.note

    def test_tolerance_relaxes_containment(self) -> None:
        # One atom grazes just past the +x face at x = 10.5.
        coords = np.array([[0, 0, 0], [10.5, 0, 0], [0, 0, 0], [0, 1, 0], [0, -1, 0]], float)
        box = self._box()
        assert not assess_placement(coords, box).in_box
        assert assess_placement(coords, box, tolerance=1.0).in_box

    def test_thresholds_are_honoured(self) -> None:
        coords = np.array([[0, 0, 0], [1, 0, 0], [15, 0, 0], [15, 1, 0], [0, 1, 0]], float)
        # Loosening the fraction threshold to accept 60%% inside clears the flag.
        assert assess_placement(coords, self._box(), min_fraction_inside=0.5).in_box

    def test_as_record_exposes_the_report_columns(self) -> None:
        coords = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
        record = assess_placement(coords, self._box()).as_record()
        for key in (
            "frac_atoms_in_box",
            "n_atoms_in_box",
            "pose_centroid_offset_a",
            "pose_centroid_offset_frac",
            "placement_ok",
            "placement_note",
        ):
            assert key in record
        assert record["placement_ok"] is True

    def test_bad_input_raises(self) -> None:
        box = self._box()
        with pytest.raises(ValueError, match="shape"):
            assess_placement(np.zeros((0, 3)), box)
        with pytest.raises(ValueError, match="shape"):
            assess_placement(np.array([[0.0, 0.0]]), box)
        with pytest.raises(ValueError, match="non-finite"):
            assess_placement(np.array([[0.0, 0.0, np.nan]]), box)


class TestProvenance:
    def test_captures_the_expected_sections(self, tmp_path: Path) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        prov = Provenance.start("test-run", cfg)
        prov.add_input(cfg.ligands.input_csv)
        prov.add_stage("stage-one", {"n": 3})
        prov.add_warning("a caveat")
        path = prov.write(tmp_path / "provenance.json")

        record = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "git",
            "platform",
            "packages",
            "inputs",
            "outputs",
            "seeds",
            "stages",
            "warnings",
            "content_hash",
        ):
            assert key in record
        assert record["config"]["hash"] == cfg.config_hash()
        assert record["warnings"] == ["a caveat"]
        assert record["stages"][0]["stage"] == "stage-one"
        assert record["seeds"]["docking_seed"] == cfg.docking.seed

    def test_content_hash_ignores_timing_but_not_content(self, tmp_path: Path) -> None:
        cfg = load_config(SAMPLE_CONFIG)

        def build() -> dict[str, Any]:
            prov = Provenance.start("r", cfg)
            prov.add_input(cfg.ligands.input_csv)
            prov.add_stage("s", {"n": 1})
            return prov.to_dict()

        first, second = build(), build()
        assert first["content_hash"] == second["content_hash"]

        prov = Provenance.start("r", cfg)
        prov.add_input(cfg.ligands.input_csv)
        prov.add_stage("s", {"n": 2})  # different metric
        assert prov.to_dict()["content_hash"] != first["content_hash"]

    def test_missing_input_is_recorded_not_raised(self, tmp_path: Path) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        prov = Provenance.start("r", cfg)
        prov.add_input(tmp_path / "absent.csv")
        assert "<missing>" in prov.inputs.values()

    def test_warnings_are_deduplicated(self) -> None:
        cfg = load_config(SAMPLE_CONFIG)
        prov = Provenance.start("r", cfg)
        prov.add_warning("same")
        prov.add_warning("same")
        assert prov.warnings == ["same"]

    def test_package_versions_report_absence_explicitly(self) -> None:
        versions = package_versions(["rdkit", "definitely_not_installed_xyz"])
        assert versions["rdkit"] != "<not installed>"
        assert versions["definitely_not_installed_xyz"] == "<not installed>"

    def test_git_and_platform_state_are_populated(self) -> None:
        assert "commit" in git_state()
        state = platform_state()
        assert state["python_version"].startswith("3.")
        assert state["cpu_count_logical"] is None or state["cpu_count_logical"] >= 1
