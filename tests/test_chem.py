"""Tests for standardisation, descriptors, rules, alerts and embedding."""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rdkit import Chem

from npcadd.chem.alerts import screen_alerts
from npcadd.chem.descriptors import DESCRIPTOR_FIELDS, compute_descriptors
from npcadd.chem.embed import EmbeddingError, generate_conformers
from npcadd.chem.protonation import (
    DimorphiteProtonator,
    NullProtonator,
    ProtonationError,
    get_protonator,
)
from npcadd.chem.rules import apply_all_rules, lipinski, pass_rate, pfizer_3_75, veber
from npcadd.chem.standardize import RejectionReason, Standardiser

LOLIOLIDE = "CC1(C)CC(O)CC2(C)OC(=O)C=C12"
SPATHULENOL = "CC1(C)C2CCC(=C)C3CCC(C)(O)C3C12"

# Small, valid, diverse SMILES for property-based tests.
SMILES_POOL = [
    LOLIOLIDE,
    SPATHULENOL,
    "CCCCCCCCCCCCCCCC(=O)O",
    "Oc1ccc(C=CC(=O)O)cc1",
    "COc1cc(C=O)ccc1O",
    "c1ccccc1",
    "CC(=O)Nc1ccc(O)cc1",
    "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12",
    "CC(C)=CCCC(C)=CCO",
    "C1CCNCC1",
]


class TestStandardiser:
    def test_accepts_a_clean_natural_product(self) -> None:
        result = Standardiser().standardise_smiles(LOLIOLIDE)
        assert result.ok
        assert result.mol is not None
        assert result.inchikey
        assert Chem.MolFromSmiles(result.standard_smiles or "") is not None

    def test_strips_counter_ion_and_keeps_the_organic_fragment(self) -> None:
        # Sodium benzoate: the salt must be stripped, leaving benzoic acid.
        result = Standardiser(min_heavy_atoms=5).standardise_smiles(
            "[Na+].[O-]C(=O)c1ccccc1"
        )
        assert result.ok
        assert "Na" not in (result.standard_smiles or "")
        assert result.stripped_fragments >= 1

    def test_neutralises_charge(self) -> None:
        result = Standardiser(min_heavy_atoms=3).standardise_smiles("CC(=O)[O-]")
        assert result.ok
        assert result.mol is not None
        assert Chem.GetFormalCharge(result.mol) == 0

    @pytest.mark.parametrize(
        ("smiles", "reason"),
        [
            ("", RejectionReason.EMPTY_SMILES),
            ("   ", RejectionReason.EMPTY_SMILES),
            ("C1=CC=CC=C1(", RejectionReason.UNPARSEABLE_SMILES),
            ("not_a_smiles!!", RejectionReason.UNPARSEABLE_SMILES),
            ("C", RejectionReason.TOO_FEW_HEAVY_ATOMS),
        ],
    )
    def test_rejects_with_the_specific_reason(
        self, smiles: str, reason: RejectionReason
    ) -> None:
        result = Standardiser().standardise_smiles(smiles)
        assert not result.ok
        assert result.reason == reason
        assert result.detail  # a human-readable explanation is always present

    def test_rejects_disallowed_elements(self) -> None:
        """Phenylboronic acid: one fragment, 9 heavy atoms, so only the element
        gate can reject it. An organometallic that fragments into something small
        would be caught by the size gate instead and would not test this at all.
        """
        result = Standardiser().standardise_smiles("OB(O)c1ccccc1")
        assert not result.ok
        assert result.reason == RejectionReason.DISALLOWED_ELEMENT
        assert "B" in result.detail

    def test_rejects_silicon_containing_compounds(self) -> None:
        result = Standardiser().standardise_smiles("C[Si](C)(C)c1ccccc1")
        assert not result.ok
        assert result.reason == RejectionReason.DISALLOWED_ELEMENT

    def test_reports_unspecified_stereocentres_without_inventing_them(self) -> None:
        # Alanine drawn without stereochemistry has one unspecified centre.
        result = Standardiser(min_heavy_atoms=3).standardise_smiles("CC(N)C(=O)O")
        assert result.ok
        assert result.n_unspecified_stereocentres == 1
        assert "@" not in (result.standard_smiles or "")

    def test_is_idempotent(self) -> None:
        standardiser = Standardiser()
        once = standardiser.standardise_smiles(LOLIOLIDE)
        twice = standardiser.standardise_smiles(once.standard_smiles or "")
        assert once.standard_smiles == twice.standard_smiles

    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(smiles=st.sampled_from(SMILES_POOL))
    def test_standardisation_is_deterministic(self, smiles: str) -> None:
        a = Standardiser().standardise_smiles(smiles)
        b = Standardiser().standardise_smiles(smiles)
        assert a.ok == b.ok
        assert a.standard_smiles == b.standard_smiles
        assert a.inchikey == b.inchikey


class TestDescriptors:
    def test_returns_the_full_panel(self) -> None:
        descriptors = compute_descriptors(Chem.MolFromSmiles(LOLIOLIDE))
        assert set(descriptors) == set(DESCRIPTOR_FIELDS)
        assert all(isinstance(v, float) for v in descriptors.values())

    def test_known_values_for_loliolide(self) -> None:
        d = compute_descriptors(Chem.MolFromSmiles(LOLIOLIDE))
        assert d["heavy_atoms"] == 14
        assert math.isclose(d["mw"], 196.25, abs_tol=0.05)
        assert d["hbd"] == 1
        assert 0.0 <= d["qed"] <= 1.0
        assert 0.0 <= d["fraction_csp3"] <= 1.0

    def test_rejects_none(self) -> None:
        with pytest.raises(ValueError, match="None"):
            compute_descriptors(None)

    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(smiles=st.sampled_from(SMILES_POOL))
    def test_invariants_hold_for_any_molecule(self, smiles: str) -> None:
        d = compute_descriptors(Chem.MolFromSmiles(smiles))
        assert d["mw"] > 0
        assert d["heavy_atoms"] >= 1
        assert d["tpsa"] >= 0
        assert d["hbd"] >= 0
        assert d["hba"] >= 0
        assert d["rotatable_bonds"] >= 0
        assert d["aromatic_rings"] <= d["rings"]
        assert 0.0 <= d["fraction_csp3"] <= 1.0
        assert 0.0 <= d["qed"] <= 1.0
        assert d["n_atoms_total"] >= d["heavy_atoms"]
        assert d["n_unspecified_stereocentres"] <= d["n_stereocentres"]

    @settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(smiles=st.sampled_from(SMILES_POOL))
    def test_descriptors_are_deterministic(self, smiles: str) -> None:
        mol = Chem.MolFromSmiles(smiles)
        assert compute_descriptors(mol) == compute_descriptors(mol)

    @settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(smiles=st.sampled_from(SMILES_POOL))
    def test_descriptors_are_invariant_to_smiles_writing(self, smiles: str) -> None:
        """The same molecule written two ways must give identical descriptors."""
        mol = Chem.MolFromSmiles(smiles)
        other = Chem.MolFromSmiles(Chem.MolToSmiles(mol, canonical=False, doRandom=False))
        a = compute_descriptors(mol)
        b = compute_descriptors(other)
        for field in DESCRIPTOR_FIELDS:
            assert math.isclose(a[field], b[field], rel_tol=1e-9, abs_tol=1e-9)


class TestRules:
    def test_lipinski_passes_a_small_natural_product(self) -> None:
        result = lipinski(compute_descriptors(Chem.MolFromSmiles(LOLIOLIDE)))
        assert result.passed
        assert result.n_violations == 0
        assert result.citation

    def test_lipinski_reports_which_criterion_failed(self) -> None:
        # A long-chain lipid: high cLogP, so cLogP <= 5 should be the failure.
        d = compute_descriptors(Chem.MolFromSmiles("CCCCCCCCCCCCCCCCCCCCCCCCCC(=O)O"))
        result = lipinski(d, max_violations=0)
        assert not result.passed
        assert "clogp_le_5" in result.failed_criteria

    def test_lipinski_violation_tolerance_is_configurable(self) -> None:
        d = compute_descriptors(Chem.MolFromSmiles("CCCCCCCCCCCCCCCCCCCCCCCCCC(=O)O"))
        assert not lipinski(d, max_violations=0).passed
        assert lipinski(d, max_violations=1).passed

    def test_pfizer_rule_is_a_conjunction(self) -> None:
        greasy_nonpolar = {"clogp": 4.0, "tpsa": 50.0}
        greasy_polar = {"clogp": 4.0, "tpsa": 100.0}
        assert not pfizer_3_75(greasy_nonpolar).passed
        assert pfizer_3_75(greasy_polar).passed

    def test_veber_requires_both_criteria(self) -> None:
        assert veber({"rotatable_bonds": 5.0, "tpsa": 100.0}).passed
        assert not veber({"rotatable_bonds": 15.0, "tpsa": 100.0}).passed
        assert not veber({"rotatable_bonds": 5.0, "tpsa": 200.0}).passed

    def test_unknown_rule_set_raises_rather_than_being_skipped(self) -> None:
        d = compute_descriptors(Chem.MolFromSmiles(LOLIOLIDE))
        with pytest.raises(KeyError, match="Unknown rule set"):
            apply_all_rules(d, rule_names=["lipinski", "not_a_real_rule"])

    def test_pass_rate_is_a_fraction(self) -> None:
        d = compute_descriptors(Chem.MolFromSmiles(LOLIOLIDE))
        rate = pass_rate(apply_all_rules(d))
        assert 0.0 <= rate <= 1.0

    def test_pass_rate_requires_results(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            pass_rate({})


class TestAlerts:
    def test_catechol_matches_pains_and_brenk(self) -> None:
        screen = screen_alerts(Chem.MolFromSmiles("Oc1ccc(cc1O)C=CC(=O)O"), ["pains", "brenk"])
        assert not screen.clean
        assert screen.hits_for("pains")
        assert screen.hits_for("brenk")
        assert "catechol" in screen.names_for("brenk")

    def test_clean_molecule_has_no_hits(self) -> None:
        screen = screen_alerts(Chem.MolFromSmiles(LOLIOLIDE), ["pains", "brenk"])
        assert screen.clean
        assert screen.n_hits == 0

    def test_summary_has_a_column_per_catalogue(self) -> None:
        summary = screen_alerts(Chem.MolFromSmiles(LOLIOLIDE), ["pains", "brenk"]).summary()
        assert summary["pains_flagged"] is False
        assert summary["brenk_flagged"] is False
        assert summary["n_structural_alerts"] == 0

    def test_unknown_catalogue_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown structural alert catalogue"):
            screen_alerts(Chem.MolFromSmiles(LOLIOLIDE), ["not_a_catalogue"])


class TestProtonation:
    def test_dimorphite_deprotonates_a_carboxylic_acid_at_ph_7_4(self) -> None:
        protonator = DimorphiteProtonator()
        states = protonator.protonate(Chem.MolFromSmiles("CCCCC(=O)O"), 7.4, 1)
        assert states
        assert states[0].formal_charge == -1
        assert protonator.limitation()

    def test_null_protonator_leaves_the_molecule_neutral(self) -> None:
        protonator = NullProtonator()
        states = protonator.protonate(Chem.MolFromSmiles("CCCCC(=O)O"), 7.4, 1)
        assert states[0].formal_charge == 0
        assert "No pKa model" in protonator.limitation()

    def test_unknown_method_raises_rather_than_defaulting(self) -> None:
        with pytest.raises(ProtonationError, match="does not substitute"):
            get_protonator("some_other_method")

    def test_registry_returns_the_requested_backend(self) -> None:
        assert isinstance(get_protonator("dimorphite_dl"), DimorphiteProtonator)
        assert isinstance(get_protonator("none"), NullProtonator)


class TestEmbedding:
    def test_generates_energy_ordered_conformers(self) -> None:
        mol = Chem.MolFromSmiles(SPATHULENOL)
        conformers = generate_conformers(mol, n_conformers=6, seed=42)
        assert conformers.n_conformers >= 1
        assert conformers.energies_kcal == sorted(conformers.energies_kcal)
        assert conformers.min_energy_kcal == conformers.energies_kcal[0]
        assert conformers.force_field == "MMFF94"

    def test_same_seed_gives_identical_coordinates(self) -> None:
        mol = Chem.MolFromSmiles(LOLIOLIDE)
        a = generate_conformers(mol, n_conformers=4, seed=7)
        b = generate_conformers(mol, n_conformers=4, seed=7)
        coords_a = a.mol.GetConformer(a.best_conformer_id()).GetPositions()
        coords_b = b.mol.GetConformer(b.best_conformer_id()).GetPositions()
        assert coords_a.shape == coords_b.shape
        assert abs(coords_a - coords_b).max() < 1e-9
        assert a.energies_kcal == b.energies_kcal

    def test_energy_window_is_respected(self) -> None:
        mol = Chem.MolFromSmiles("CCCCCCCCCC(=O)O")
        conformers = generate_conformers(
            mol, n_conformers=12, seed=1, energy_window_kcal=1.0, prune_rms_thresh=0.1
        )
        minimum = conformers.min_energy_kcal
        assert all(e - minimum <= 1.0 + 1e-6 for e in conformers.energies_kcal)

    def test_pruning_reduces_or_preserves_the_count(self) -> None:
        mol = Chem.MolFromSmiles(LOLIOLIDE)
        conformers = generate_conformers(mol, n_conformers=10, seed=3)
        assert conformers.n_after_rms_prune <= conformers.n_after_energy_window
        assert conformers.n_after_energy_window <= conformers.n_embedded

    def test_rejects_invalid_parameters(self) -> None:
        mol = Chem.MolFromSmiles(LOLIOLIDE)
        with pytest.raises(ValueError, match="n_conformers"):
            generate_conformers(mol, n_conformers=0)
        with pytest.raises(ValueError, match="energy_window"):
            generate_conformers(mol, energy_window_kcal=-1.0)

    def test_embedding_error_is_raised_not_swallowed(self) -> None:
        # A molecule RDKit cannot parameterise with MMFF should raise cleanly.
        mol = Chem.MolFromSmiles("[Xe]")
        with pytest.raises((EmbeddingError, ValueError)):
            generate_conformers(mol, n_conformers=1)
