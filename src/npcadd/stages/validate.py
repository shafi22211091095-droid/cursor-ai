"""Stage 3: docking validation. **Mandatory, and runs before any screening.**

Three independent controls, each answering a different question.

1. **Redocking.** Can the engine reproduce a known answer? The co-crystal
   ligand is docked back into its own receptor and the in-place RMSD to the
   crystallographic pose is measured. Above the threshold the pipeline
   *halts*: a protocol that cannot reproduce a pose it was given has no
   standing to predict poses it was not.

2. **Enrichment against property-matched decoys.** Can the protocol tell
   actives from similar-looking non-actives? Reported as ROC-AUC, BEDROC and
   enrichment factors. Requires known actives; if none are supplied this
   control is reported as *not performed* rather than skipped silently.

3. **Null distribution.** Is a given affinity actually unusual? A set of random
   ligands is docked to build an empirical null, which converts every reported
   affinity into a z-score and a one-sided empirical p-value.

Validation uses **exactly** the docking parameters that screening will use.
This is enforced, not merely documented: validating at exhaustiveness 32 and
then screening at 8 would make the validation describe a protocol that was
never run. Measured on the bundled system, that distinction is the difference
between reproducing the crystal pose at 0.24 A and missing it at 12.3 A.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rdkit import Chem

from ..chem.embed import generate_conformers
from ..chem.standardize import Standardiser
from ..config import Config
from ..docking.decoys import (
    DecoySet,
    PoolMolecule,
    build_pool,
    generate_decoys,
    generate_null_set,
    pool_property_summary,
)
from ..docking.metrics import EnrichmentReport, NullDistribution, bedroc, enrichment_factor, roc_auc
from ..docking.poses import PoseLoadError, centroid_distance, in_place_rmsd, load_poses
from ..docking.vina_runner import DockingJob, dock_many
from ..logging_utils import get_logger
from .receptor import PreparedReceptor

__all__ = [
    "RedockResult",
    "ValidationFailure",
    "ValidationReport",
    "run_validation",
]

log = get_logger("stages.validate")


class ValidationFailure(RuntimeError):  # noqa: N818
    """A mandatory validation gate failed. The pipeline must not continue.

    Named ``ValidationFailure`` rather than ``ValidationFailureError`` because
    this type exists to be read in a traceback by a scientist deciding whether
    their protocol is sound, and "validation failure" is what that event is
    called in the literature.
    """


@dataclass(slots=True)
class RedockResult:
    """Outcome of redocking one co-crystal ligand into its own receptor."""

    receptor_id: str
    pdb_id: str
    cocrystal_ligand: str
    performed: bool
    rmsd_angstrom: float | None = None
    centroid_distance_angstrom: float | None = None
    affinity_kcal_mol: float | None = None
    best_of_n_rmsd: float | None = None
    best_rmsd_pose_rank: int | None = None
    threshold: float = 2.0
    reason_not_performed: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.performed
            and self.rmsd_angstrom is not None
            and self.rmsd_angstrom <= self.threshold
        )

    @property
    def diagnosis(self) -> str:
        """Plain-language reading of a failure, distinguishing its two modes."""
        if not self.performed:
            return f"not performed: {self.reason_not_performed}"
        if self.rmsd_angstrom is None:  # pragma: no cover - defensive
            return "not evaluated"
        if self.passed:
            return "top-ranked pose reproduces the crystallographic pose"
        centroid = self.centroid_distance_angstrom or 0.0
        if centroid > 4.0:
            return (
                f"top-ranked pose is in a different location (centroid offset "
                f"{centroid:.1f} A): the search box or the site assignment is wrong"
            )
        detail = ""
        if self.best_of_n_rmsd is not None and self.best_of_n_rmsd <= self.threshold:
            detail = (
                f" A pose within the threshold does exist at rank "
                f"{self.best_rmsd_pose_rank} (RMSD {self.best_of_n_rmsd:.2f} A), so the "
                "sampling finds the right pose but the scoring function does not rank "
                "it first."
            )
        return (
            f"top-ranked pose is in approximately the right place (centroid offset "
            f"{centroid:.1f} A) but misoriented (RMSD {self.rmsd_angstrom:.2f} A)."
            f"{detail}"
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "receptor_id": self.receptor_id,
            "pdb_id": self.pdb_id,
            "cocrystal_ligand": self.cocrystal_ligand,
            "performed": self.performed,
            "redock_rmsd_angstrom": self.rmsd_angstrom,
            "centroid_offset_angstrom": self.centroid_distance_angstrom,
            "redock_affinity_kcal_mol": self.affinity_kcal_mol,
            "best_of_n_rmsd_angstrom": self.best_of_n_rmsd,
            "best_rmsd_pose_rank": self.best_rmsd_pose_rank,
            "threshold_angstrom": self.threshold,
            "passed": self.passed,
            "diagnosis": self.diagnosis,
        }


@dataclass(slots=True)
class ValidationReport:
    """Everything the validation stage determined."""

    redock: list[RedockResult] = field(default_factory=list)
    enrichment: list[EnrichmentReport] = field(default_factory=list)
    nulls: dict[str, NullDistribution] = field(default_factory=dict)
    decoy_sets: list[DecoySet] = field(default_factory=list)
    pool_summary: dict[str, Any] = field(default_factory=dict)
    enrichment_performed: bool = False
    enrichment_skip_reason: str = ""
    halted: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def all_redocks_passed(self) -> bool:
        evaluated = [r for r in self.redock if r.performed]
        return bool(evaluated) and all(r.passed for r in evaluated)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "n_redock_performed": sum(1 for r in self.redock if r.performed),
            "n_redock_passed": sum(1 for r in self.redock if r.passed),
            "redock_rmsds": {r.receptor_id: r.rmsd_angstrom for r in self.redock if r.performed},
            "enrichment_performed": self.enrichment_performed,
            "roc_auc": {e.receptor_id: round(e.roc_auc, 4) for e in self.enrichment},
            "null_sizes": {k: v.n for k, v in self.nulls.items()},
            "n_warnings": len(self.warnings),
        }


def _prepare_ligand_pdbqt(mol: Chem.Mol, cfg: Config, out_path: Path, name: str) -> bool:
    """Embed and export one molecule to PDBQT. Returns success."""
    from meeko import MoleculePreparation, PDBQTWriterLegacy

    try:
        conformers = generate_conformers(
            mol,
            n_conformers=1,
            seed=cfg.ligands.embed_seed,
            mmff_max_iters=cfg.ligands.mmff_max_iters,
            energy_window_kcal=cfg.ligands.energy_window_kcal,
            prune_rms_thresh=cfg.ligands.prune_rms_thresh,
        )
        single = Chem.Mol(conformers.mol)
        conformer = Chem.Conformer(conformers.mol.GetConformer(conformers.best_conformer_id()))
        single.RemoveAllConformers()
        single.AddConformer(conformer, assignId=True)
        setups = MoleculePreparation().prepare(single)
        text, ok, _error = PDBQTWriterLegacy.write_string(setups[0])
    except Exception as exc:  # noqa: BLE001 - third-party failure modes vary
        log.warning(
            "Could not prepare validation ligand",
            extra={"name": name, "error": f"{type(exc).__name__}: {exc}"},
        )
        return False
    if not ok or not text:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return True


def _require_pdbqt(ok: bool, text: str, error: str) -> None:
    """Raise when Meeko reports a failed PDBQT export, keeping the caller flat."""
    if not ok or not text:
        raise ValueError(error or "Meeko produced an empty PDBQT")


def _redock_one(cfg: Config, receptor: PreparedReceptor, out_dir: Path) -> RedockResult:
    """Redock the co-crystal ligand of one receptor and measure in-place RMSD."""
    threshold = cfg.validation.redock_rmsd_threshold
    result = RedockResult(
        receptor_id=receptor.receptor_id,
        pdb_id=receptor.pdb_id,
        cocrystal_ligand=receptor.cocrystal_sdf_path.name if receptor.cocrystal_sdf_path else "",
        performed=False,
        threshold=threshold,
    )

    if receptor.cocrystal_sdf_path is None:
        result.reason_not_performed = (
            "no co-crystal reference pose is available for this receptor, so there is "
            "no known answer to reproduce. This receptor is UNVALIDATED and must be "
            "declared as such in LIMITATIONS.md."
        )
        return result

    reference = Chem.MolFromMolFile(str(receptor.cocrystal_sdf_path), removeHs=False)
    if reference is None:
        result.reason_not_performed = (
            f"the reference pose at {receptor.cocrystal_sdf_path} could not be parsed"
        )
        return result

    work = out_dir / "validation" / receptor.receptor_id
    work.mkdir(parents=True, exist_ok=True)
    ligand_pdbqt = work / "redock_input.pdbqt"

    # The co-crystal ligand is docked from its own crystallographic geometry, so
    # the conformer is prepared directly rather than re-embedded from SMILES:
    # re-embedding would conflate conformer generation error with docking error.
    from meeko import MoleculePreparation, PDBQTWriterLegacy

    try:
        setups = MoleculePreparation().prepare(Chem.AddHs(reference, addCoords=True))
        text, ok, error = PDBQTWriterLegacy.write_string(setups[0])
        _require_pdbqt(ok, text, error)
        ligand_pdbqt.write_text(text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - third-party failure modes vary
        result.reason_not_performed = (
            f"the co-crystal ligand could not be prepared for docking: {type(exc).__name__}: {exc}"
        )
        return result

    job = DockingJob(
        ligand_id=f"redock_{receptor.receptor_id}",
        receptor_id=receptor.receptor_id,
        ligand_pdbqt=ligand_pdbqt,
        receptor_pdbqt=receptor.pdbqt_path,
        box=receptor.box,
        out_pdbqt=work / "redock_poses.pdbqt",
        role="redock",
    )
    docked = dock_many([job], cfg.docking)[0]
    if not docked.ok:
        result.reason_not_performed = f"the redocking run failed: {docked.error}"
        return result

    try:
        poses = load_poses(job.out_pdbqt)
    except PoseLoadError as exc:
        result.reason_not_performed = f"docked poses could not be read back: {exc}"
        return result

    try:
        rmsds = [
            in_place_rmsd(poses, reference, probe_conf_id=i)
            for i in range(poses.GetNumConformers())
        ]
    except ValueError as exc:
        result.reason_not_performed = f"RMSD could not be computed: {exc}"
        return result

    best_index = int(np.argmin(rmsds))
    result.performed = True
    result.rmsd_angstrom = round(float(rmsds[0]), 3)
    result.centroid_distance_angstrom = round(
        float(centroid_distance(poses, reference, probe_conf_id=0)), 3
    )
    result.affinity_kcal_mol = docked.best_affinity
    result.best_of_n_rmsd = round(float(rmsds[best_index]), 3)
    result.best_rmsd_pose_rank = best_index + 1

    log.info(
        "Redocking %s: RMSD %.3f A (threshold %.2f) -> %s",
        receptor.receptor_id,
        result.rmsd_angstrom,
        threshold,
        "PASS" if result.passed else "FAIL",
        extra={
            "receptor": receptor.receptor_id,
            "rmsd": result.rmsd_angstrom,
            "centroid_offset": result.centroid_distance_angstrom,
            "affinity": result.affinity_kcal_mol,
        },
    )
    return result


def _load_known_actives(cfg: Config) -> list[tuple[str, Chem.Mol]]:
    """Read and standardise the known-actives CSV, if one was configured."""
    path = cfg.validation.known_actives_csv
    if path is None:
        return []
    if not path.is_file():
        raise ValidationFailure(f"validation.known_actives_csv points at a missing file: {path}")

    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    for column in ("name", "smiles"):
        if column not in frame.columns:
            raise ValidationFailure(
                f"{path} must contain a {column!r} column; found {list(frame.columns)}"
            )

    standardiser = Standardiser(
        min_heavy_atoms=cfg.ligands.min_heavy_atoms,
        max_heavy_atoms=cfg.ligands.max_heavy_atoms,
        allowed_elements=frozenset(cfg.ligands.allowed_elements),
    )
    actives: list[tuple[str, Chem.Mol]] = []
    for row in frame.to_dict(orient="records"):
        result = standardiser.standardise_smiles(str(row["smiles"]))
        if result.ok and result.mol is not None:
            actives.append((str(row["name"]), result.mol))
        else:
            log.warning(
                "Known active rejected by standardisation",
                extra={"name": row.get("name"), "reason": str(result.reason)},
            )
    if not actives:
        raise ValidationFailure(
            f"No usable known actives were read from {path}; enrichment cannot be computed."
        )
    return actives


def _dock_pool_molecules(
    cfg: Config,
    receptor: PreparedReceptor,
    molecules: list[PoolMolecule],
    out_dir: Path,
    role: str,
) -> dict[str, float]:
    """Prepare and dock a set of pool molecules. Returns pool_id -> best affinity."""
    work = out_dir / "validation" / receptor.receptor_id / role
    work.mkdir(parents=True, exist_ok=True)

    jobs: list[DockingJob] = []
    for molecule in molecules:
        pdbqt = work / f"{molecule.pool_id}.pdbqt"
        if not pdbqt.is_file() and not _prepare_ligand_pdbqt(
            molecule.mol, cfg, pdbqt, molecule.pool_id
        ):
            continue
        jobs.append(
            DockingJob(
                ligand_id=molecule.pool_id,
                receptor_id=receptor.receptor_id,
                ligand_pdbqt=pdbqt,
                receptor_pdbqt=receptor.pdbqt_path,
                box=receptor.box,
                out_pdbqt=work / f"{molecule.pool_id}_poses.pdbqt",
                role=role,
            )
        )

    affinities: dict[str, float] = {}
    for result in dock_many(jobs, cfg.docking):
        if result.ok and result.best_affinity is not None:
            affinities[result.ligand_id] = result.best_affinity
    return affinities


def run_validation(
    cfg: Config, receptors: list[PreparedReceptor], out_dir: Path
) -> ValidationReport:
    """Run every validation control and enforce the redocking gate.

    Raises:
        ValidationFailure: if a redocking control fails and
            ``validation.halt_on_failure`` is true.
    """
    report = ValidationReport()

    if not cfg.validation.enabled:
        report.warnings.append(
            "VALIDATION WAS DISABLED (validation.enabled: false). No redocking "
            "control, no enrichment analysis and no null distribution were produced. "
            "Any affinity reported from this run is uncalibrated and unverified."
        )
        log.warning("%s", report.warnings[-1])
        return report

    # ---- Control 1: redocking (gate) -----------------------------------
    for receptor in receptors:
        report.redock.append(_redock_one(cfg, receptor, out_dir))

    failures = [r for r in report.redock if r.performed and not r.passed]
    unvalidated = [r for r in report.redock if not r.performed]

    for item in unvalidated:
        report.warnings.append(
            f"Receptor {item.receptor_id!r} ({item.pdb_id}) was NOT validated by "
            f"redocking: {item.reason_not_performed}"
        )

    if failures:
        lines = [
            f"  - {f.receptor_id} ({f.pdb_id}): RMSD {f.rmsd_angstrom} A exceeds the "
            f"{f.threshold} A threshold. {f.diagnosis}"
            for f in failures
        ]
        message = (
            "REDOCKING VALIDATION FAILED for "
            f"{len(failures)} of {sum(1 for r in report.redock if r.performed)} "
            "evaluated receptor(s):\n" + "\n".join(lines) + "\n\n"
            "The docking protocol cannot reproduce a pose that is already known, so it "
            "has no standing to predict poses that are not. Screening has NOT been "
            "run.\n\n"
            "Things that actually fix this, in order of how often they are the cause:\n"
            "  1. Raise docking.exhaustiveness. On the bundled ABL1 system, "
            "exhaustiveness 8 fails at 12.3 A while 32 succeeds at 0.24 A.\n"
            "  2. Check the search box. A centroid offset above ~4 A means the box or "
            "the site assignment is wrong, not the sampling.\n"
            "  3. Check the receptor protonation, especially histidine tautomers and "
            "any catalytic metal or cofactor stripped by keep_hetatms.\n"
            "  4. Check that the co-crystal ligand's protonation state matches the "
            "deposited one.\n"
            "Overriding the gate with validation.halt_on_failure: false is recorded in "
            "provenance and stamped on every figure caption."
        )
        if cfg.validation.halt_on_failure:
            report.halted = True
            raise ValidationFailure(message)
        report.warnings.append(
            "REDOCKING GATE OVERRIDDEN (validation.halt_on_failure: false). " + message
        )
        log.warning("Redocking gate overridden")

    # ---- Controls 2 and 3 need a molecule pool -------------------------
    # The pool is sized to what this run actually needs rather than to a fixed
    # ceiling. Because enumeration uses a bit-reversal ordering, any prefix is a
    # near-uniform sample of the full library, so a smaller pool still spans the
    # property space -- it just offers fewer candidates per property bin.
    n_actives_expected = 1
    if cfg.validation.known_actives_csv is not None:
        n_actives_expected = max(1, sum(1 for _ in cfg.validation.known_actives_csv.open()) - 1)
    needed = cfg.validation.decoys_per_active * n_actives_expected + cfg.validation.null_set_size
    pool = build_pool(max_size=max(1200, min(20000, 40 * needed)))
    report.pool_summary = pool_property_summary(pool)

    actives = _load_known_actives(cfg)
    if not actives:
        report.enrichment_performed = False
        report.enrichment_skip_reason = (
            "No known actives were supplied (validation.known_actives_csv is null), so "
            "ROC-AUC, BEDROC and enrichment factors are NOT DEFINED and have not been "
            "computed. npcadd does not fabricate an active set. To obtain these "
            "metrics, supply a CSV of experimentally confirmed actives for each target "
            "-- for example from ChEMBL or BindingDB -- and re-run."
        )
        report.warnings.append(report.enrichment_skip_reason)
        log.warning("%s", report.enrichment_skip_reason)
    else:
        report.decoy_sets = generate_decoys(
            actives,
            pool,
            n_per_active=cfg.validation.decoys_per_active,
            tolerance=cfg.validation.decoy_property_tolerance,
            seed=cfg.validation.decoy_seed,
        )
        report.enrichment_performed = True

    # ---- Per receptor: enrichment and null -----------------------------
    for receptor in receptors:
        if report.enrichment_performed:
            enrichment = _enrichment_for_receptor(cfg, receptor, actives, report, out_dir)
            if enrichment is not None:
                report.enrichment.append(enrichment)

        null_molecules = generate_null_set(
            pool, n=cfg.validation.null_set_size, seed=cfg.validation.null_seed
        )
        affinities = _dock_pool_molecules(cfg, receptor, null_molecules, out_dir, "null")
        n_failed = len(null_molecules) - len(affinities)
        try:
            report.nulls[receptor.receptor_id] = NullDistribution(
                receptor_id=receptor.receptor_id,
                affinities=np.asarray(sorted(affinities.values()), dtype=np.float64),
                n_failed=n_failed,
            )
        except ValueError as exc:
            report.warnings.append(
                f"No usable null distribution for receptor {receptor.receptor_id!r} "
                f"({exc}). Affinities against this receptor cannot be given a z-score "
                "or an empirical p-value."
            )
            log.warning("%s", report.warnings[-1])

    return report


def _enrichment_for_receptor(
    cfg: Config,
    receptor: PreparedReceptor,
    actives: list[tuple[str, Chem.Mol]],
    report: ValidationReport,
    out_dir: Path,
) -> EnrichmentReport | None:
    """Dock actives and their decoys, then compute enrichment metrics."""
    work = out_dir / "validation" / receptor.receptor_id / "actives"
    work.mkdir(parents=True, exist_ok=True)

    jobs: list[DockingJob] = []
    for name, mol in actives:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]
        pdbqt = work / f"{safe}.pdbqt"
        if not pdbqt.is_file() and not _prepare_ligand_pdbqt(mol, cfg, pdbqt, name):
            continue
        jobs.append(
            DockingJob(
                ligand_id=f"active::{safe}",
                receptor_id=receptor.receptor_id,
                ligand_pdbqt=pdbqt,
                receptor_pdbqt=receptor.pdbqt_path,
                box=receptor.box,
                out_pdbqt=work / f"{safe}_poses.pdbqt",
                role="active",
            )
        )

    active_affinities = {
        r.ligand_id: r.best_affinity
        for r in dock_many(jobs, cfg.docking)
        if r.ok and r.best_affinity is not None
    }

    all_decoys = [d for decoy_set in report.decoy_sets for d in decoy_set.decoys]
    unique_decoys = list({d.pool_id: d for d in all_decoys}.values())
    decoy_affinities = _dock_pool_molecules(cfg, receptor, unique_decoys, out_dir, "decoys")

    if not active_affinities or not decoy_affinities:
        report.warnings.append(
            f"Enrichment for receptor {receptor.receptor_id!r} could not be computed: "
            f"{len(active_affinities)} active(s) and {len(decoy_affinities)} decoy(s) "
            "docked successfully; both must be non-empty."
        )
        return None

    labels = np.concatenate(
        [np.ones(len(active_affinities), dtype=int), np.zeros(len(decoy_affinities), dtype=int)]
    )
    # Scores are negated affinities: larger must mean better for every metric.
    scores = np.concatenate(
        [
            -np.asarray(list(active_affinities.values()), dtype=np.float64),
            -np.asarray(list(decoy_affinities.values()), dtype=np.float64),
        ]
    )

    # Persist labels and scores so the report stage can redraw the ROC curve
    # without re-docking anything.
    np.savez(
        work.parent / "labels_scores.npz",
        labels=labels,
        scores=scores,
    )

    factors: dict[str, float] = {}
    maxima: dict[str, float] = {}
    for fraction in cfg.validation.ef_fractions:
        key = f"{fraction * 100:g}pct"
        value, ceiling = enrichment_factor(labels, scores, fraction)
        factors[key] = value
        maxima[key] = ceiling

    enrichment = EnrichmentReport(
        receptor_id=receptor.receptor_id,
        n_actives=len(active_affinities),
        n_decoys=len(decoy_affinities),
        roc_auc=roc_auc(labels, scores),
        bedroc=bedroc(labels, scores, cfg.validation.bedroc_alpha),
        bedroc_alpha=cfg.validation.bedroc_alpha,
        enrichment_factors=factors,
        max_enrichment_factors=maxima,
    )
    log.info(
        "Enrichment for %s: ROC-AUC %.3f (%s)",
        receptor.receptor_id,
        enrichment.roc_auc,
        enrichment.verdict,
        extra=enrichment.as_record(),
    )
    if enrichment.roc_auc < 0.6:
        report.warnings.append(
            f"Receptor {receptor.receptor_id!r} shows ROC-AUC "
            f"{enrichment.roc_auc:.3f} against property-matched decoys "
            f"({enrichment.verdict}). This protocol cannot reliably distinguish "
            "actives from decoys for this target, so a favourable score against it is "
            "weak evidence and must not be presented as a prediction of activity."
        )
    return enrichment
