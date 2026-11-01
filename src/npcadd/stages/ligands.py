"""Stage 1: ligand preparation.

Input is a SMILES CSV carrying compound name, GC-MS area %% and compound class.
Output is one PDBQT per accepted ligand (lowest-energy conformer, with the full
conformer set retained as SDF), a ligand manifest table, and — equally important
— a rejection table recording every molecule that did not make it through and
exactly why.

The rejection table is not an afterthought. "102 components were screened" is
only honest if the reader can see which ones were not, and on what grounds.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from rdkit import Chem

from ..chem.embed import EmbeddingError, generate_conformers
from ..chem.protonation import ProtonationError, get_protonator
from ..chem.standardize import RejectionReason, Standardiser
from ..config import Config
from ..hashing import hash_file
from ..logging_utils import get_logger

__all__ = ["LigandPrepSummary", "LigandRecord", "prepare_ligands", "read_ligand_csv"]

log = get_logger("stages.ligands")

REJECTED_CSV_NAME = "rejected_ligands.csv"
MANIFEST_CSV_NAME = "ligand_manifest.csv"


@dataclass(slots=True)
class LigandRecord:
    """One accepted ligand, ready for docking."""

    ligand_id: str
    name: str
    compound_class: str
    area_percent: float
    input_smiles: str
    standard_smiles: str
    docked_smiles: str
    inchikey: str
    formal_charge: int
    protonation_method: str
    protonation_version: str
    ph: float
    protonation_state_uncertain: bool
    protonation_uncertainty_detail: str
    n_conformers: int
    n_embedded: int
    min_energy_kcal: float
    embed_seed: int
    force_field: str
    n_unspecified_stereocentres: int
    stripped_fragments: int
    changed_by_standardisation: bool
    pdbqt_path: str
    """Path to the exported PDBQT, **relative to the run directory**.

    Deliberately relative. An absolute path would embed the run directory and
    the machine's filesystem layout into the manifest, so the manifest's bytes
    would differ between two otherwise identical runs -- which the golden
    determinism test correctly rejects. Resolve it against the run directory to
    get a usable path.
    """

    sdf_path: str
    """Conformer SDF path, relative to the run directory. See ``pdbqt_path``."""

    pdbqt_sha256: str

    def as_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LigandPrepSummary:
    """Aggregate outcome of the stage."""

    n_input: int
    n_accepted: int
    n_rejected: int
    rejection_counts: dict[str, int] = field(default_factory=dict)
    n_with_unspecified_stereo: int = 0
    n_charged_at_ph: int = 0
    n_uncertain_protonation: int = 0
    manifest_path: str = ""
    rejected_path: str = ""
    protonation_limitation: str = ""

    def as_metrics(self) -> dict[str, Any]:
        return {
            "n_input": self.n_input,
            "n_accepted": self.n_accepted,
            "n_rejected": self.n_rejected,
            "rejection_counts": self.rejection_counts,
            "n_with_unspecified_stereo": self.n_with_unspecified_stereo,
            "n_charged_at_ph": self.n_charged_at_ph,
            "n_uncertain_protonation": self.n_uncertain_protonation,
        }


def read_ligand_csv(cfg: Config) -> pd.DataFrame:
    """Read and validate the input SMILES CSV.

    Raises:
        FileNotFoundError: if the CSV is absent.
        ValueError: if a required column is missing or the file is empty.
    """
    path = cfg.ligands.input_csv
    if not path.is_file():
        raise FileNotFoundError(
            f"Ligand input CSV not found: {path}. "
            "Point ligands.input_csv at your GC-MS component table, or use the "
            "clearly-labelled synthetic set under data/sample/."
        )

    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if frame.empty:
        raise ValueError(f"Ligand input CSV is empty: {path}")

    lig = cfg.ligands
    required = [lig.name_column, lig.smiles_column, lig.area_column, lig.class_column]
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(
            f"Ligand CSV {path} is missing required column(s): {missing}. "
            f"Present columns: {list(frame.columns)}. Column names are configurable "
            "under the 'ligands' section of config.yaml."
        )
    return frame


def _parse_area(value: str, name: str) -> float:
    """Parse GC-MS area %%, tolerating blanks but not nonsense."""
    text = (value or "").strip().rstrip("%")
    if not text:
        return 0.0
    try:
        area = float(text)
    except ValueError as exc:
        raise ValueError(f"Compound {name!r} has a non-numeric GC-MS area value {value!r}") from exc
    if area < 0:
        raise ValueError(f"Compound {name!r} has a negative GC-MS area value {area}")
    return area


def _safe_id(name: str, index: int) -> str:
    """Build a filesystem-safe, collision-resistant ligand id."""
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in name.strip())
    cleaned = cleaned.strip("_")[:48] or "unnamed"
    return f"L{index:04d}_{cleaned}"


def _write_pdbqt(conformer_mol: Chem.Mol, conf_id: int, out_path: Path) -> tuple[bool, str]:
    """Export one conformer to PDBQT via Meeko. Returns (ok, error_detail)."""
    try:
        from meeko import MoleculePreparation, PDBQTWriterLegacy
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        return False, f"Meeko unavailable: {exc}"

    single = Chem.Mol(conformer_mol)
    conformer = Chem.Conformer(conformer_mol.GetConformer(conf_id))
    single.RemoveAllConformers()
    single.AddConformer(conformer, assignId=True)

    try:
        setups = MoleculePreparation().prepare(single)
    except (ValueError, RuntimeError, KeyError) as exc:
        return False, f"Meeko preparation failed: {type(exc).__name__}: {exc}"
    if not setups:
        return False, "Meeko produced no molecule setup"

    try:
        text, ok, error = PDBQTWriterLegacy.write_string(setups[0])
    except (ValueError, RuntimeError) as exc:
        return False, f"Meeko PDBQT writer failed: {type(exc).__name__}: {exc}"
    if not ok or not text:
        return False, f"Meeko PDBQT writer reported failure: {error}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return True, ""


def prepare_ligands(cfg: Config, out_dir: Path) -> tuple[list[LigandRecord], LigandPrepSummary]:
    """Run the full ligand preparation stage.

    Args:
        cfg: Validated run configuration.
        out_dir: Run directory. PDBQT files land in ``out_dir / "ligands"``.

    Returns:
        The accepted ligand records and a stage summary. The rejection table is
        written to both the run directory and ``paths.interim`` so that
        ``data/interim/rejected_ligands.csv`` is always the latest view.
    """
    lig = cfg.ligands
    frame = read_ligand_csv(cfg)

    ligand_dir = out_dir / "ligands"
    ligand_dir.mkdir(parents=True, exist_ok=True)

    standardiser = Standardiser(
        strip_salts=lig.strip_salts,
        neutralise=lig.neutralise,
        canonical_tautomer=lig.canonical_tautomer,
        max_tautomers=lig.max_tautomers,
        min_heavy_atoms=lig.min_heavy_atoms,
        max_heavy_atoms=lig.max_heavy_atoms,
        allowed_elements=frozenset(lig.allowed_elements),
    )
    protonator = get_protonator(lig.protonation_method)

    accepted: list[LigandRecord] = []
    rejected: list[dict[str, Any]] = []
    seen_inchikeys: dict[str, str] = {}

    for position, row in enumerate(frame.to_dict(orient="records"), start=1):
        name = str(row[lig.name_column]).strip() or f"compound_{position}"
        smiles = str(row[lig.smiles_column])
        compound_class = str(row[lig.class_column]).strip() or "unclassified"
        try:
            area = _parse_area(str(row[lig.area_column]), name)
        except ValueError as exc:
            rejected.append(
                _rejection(name, smiles, compound_class, 0.0, "invalid_area_percent", str(exc))
            )
            continue

        result = standardiser.standardise_smiles(smiles)
        if not result.ok or result.mol is None:
            rejected.append(
                _rejection(
                    name,
                    smiles,
                    compound_class,
                    area,
                    str(result.reason),
                    result.detail,
                )
            )
            continue

        # Structural duplicates: GC-MS lists frequently carry the same compound
        # under two library names. Keeping both would double-count area %% and
        # inflate the apparent size of the screening set.
        key = result.inchikey or result.standard_smiles or ""
        if key and key in seen_inchikeys:
            rejected.append(
                _rejection(
                    name,
                    smiles,
                    compound_class,
                    area,
                    str(RejectionReason.DUPLICATE_STRUCTURE),
                    f"same structure as {seen_inchikeys[key]!r} (InChIKey {key})",
                )
            )
            continue

        try:
            states = protonator.protonate(result.mol, lig.ph, lig.max_protonation_variants)
        except ProtonationError as exc:
            rejected.append(
                _rejection(
                    name,
                    smiles,
                    compound_class,
                    area,
                    str(RejectionReason.PROTONATION_FAILED),
                    str(exc),
                )
            )
            continue

        dominant = states[0]
        try:
            conformers = generate_conformers(
                dominant.mol,
                n_conformers=lig.n_conformers,
                seed=lig.embed_seed,
                mmff_max_iters=lig.mmff_max_iters,
                energy_window_kcal=lig.energy_window_kcal,
                prune_rms_thresh=lig.prune_rms_thresh,
            )
        except EmbeddingError as exc:
            rejected.append(
                _rejection(
                    name,
                    smiles,
                    compound_class,
                    area,
                    str(RejectionReason.EMBEDDING_FAILED),
                    str(exc),
                )
            )
            continue

        ligand_id = _safe_id(name, position)
        pdbqt_path = ligand_dir / f"{ligand_id}.pdbqt"
        sdf_path = ligand_dir / f"{ligand_id}_conformers.sdf"

        ok, detail = _write_pdbqt(conformers.mol, conformers.best_conformer_id(), pdbqt_path)
        if not ok:
            rejected.append(
                _rejection(
                    name,
                    smiles,
                    compound_class,
                    area,
                    str(RejectionReason.PDBQT_EXPORT_FAILED),
                    detail,
                )
            )
            continue

        _write_conformer_sdf(conformers, sdf_path, ligand_id, name)
        seen_inchikeys[key] = name

        accepted.append(
            LigandRecord(
                ligand_id=ligand_id,
                name=name,
                compound_class=compound_class,
                area_percent=area,
                input_smiles=smiles,
                standard_smiles=result.standard_smiles or "",
                docked_smiles=dominant.smiles,
                inchikey=result.inchikey or "",
                formal_charge=dominant.formal_charge,
                protonation_method=dominant.method,
                protonation_version=dominant.method_version,
                ph=dominant.ph,
                protonation_state_uncertain=dominant.state_uncertain,
                protonation_uncertainty_detail=dominant.uncertainty_detail,
                n_conformers=conformers.n_conformers,
                n_embedded=conformers.n_embedded,
                min_energy_kcal=round(conformers.min_energy_kcal, 6),
                embed_seed=conformers.seed,
                force_field=conformers.force_field,
                n_unspecified_stereocentres=result.n_unspecified_stereocentres,
                stripped_fragments=result.stripped_fragments,
                changed_by_standardisation=result.changed,
                pdbqt_path=pdbqt_path.relative_to(out_dir).as_posix(),
                sdf_path=sdf_path.relative_to(out_dir).as_posix(),
                pdbqt_sha256=hash_file(pdbqt_path),
            )
        )

    manifest_path = out_dir / MANIFEST_CSV_NAME
    _write_csv(manifest_path, [r.as_record() for r in accepted])

    rejected_run_path = out_dir / REJECTED_CSV_NAME
    _write_csv(rejected_run_path, rejected, _REJECTION_FIELDS)
    interim_path = cfg.paths.interim / REJECTED_CSV_NAME
    _write_csv(interim_path, rejected, _REJECTION_FIELDS)

    counts: dict[str, int] = {}
    for entry in rejected:
        counts[entry["reason"]] = counts.get(entry["reason"], 0) + 1

    summary = LigandPrepSummary(
        n_input=len(frame),
        n_accepted=len(accepted),
        n_rejected=len(rejected),
        rejection_counts=dict(sorted(counts.items())),
        n_with_unspecified_stereo=sum(1 for r in accepted if r.n_unspecified_stereocentres > 0),
        n_charged_at_ph=sum(1 for r in accepted if r.formal_charge != 0),
        n_uncertain_protonation=sum(1 for r in accepted if r.protonation_state_uncertain),
        manifest_path=manifest_path.as_posix(),
        rejected_path=rejected_run_path.as_posix(),
        protonation_limitation=protonator.limitation(),
    )

    log.info(
        "Ligand preparation complete: %d/%d accepted",
        summary.n_accepted,
        summary.n_input,
        extra={
            "n_accepted": summary.n_accepted,
            "n_rejected": summary.n_rejected,
            "rejection_counts": summary.rejection_counts,
        },
    )
    if summary.n_accepted == 0:
        raise RuntimeError(
            f"No ligand survived preparation ({summary.n_rejected} rejected). "
            f"See {rejected_run_path} for per-molecule reasons."
        )
    return accepted, summary


_REJECTION_FIELDS = (
    "name",
    "input_smiles",
    "compound_class",
    "area_percent",
    "reason",
    "detail",
)


def _rejection(
    name: str,
    smiles: str,
    compound_class: str,
    area: float,
    reason: str,
    detail: str,
) -> dict[str, Any]:
    log.debug("Rejected ligand", extra={"name": name, "reason": reason, "detail": detail})
    return {
        "name": name,
        "input_smiles": smiles,
        "compound_class": compound_class,
        "area_percent": area,
        "reason": reason,
        "detail": detail,
    }


def _write_csv(
    path: Path, rows: list[dict[str, Any]], fields: tuple[str, ...] | None = None
) -> None:
    """Write rows to CSV with a stable column order and LF line endings.

    An empty row list still writes a header, so a downstream reader never has to
    distinguish "no file" from "no rows".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(fields) if fields else (list(rows[0].keys()) if rows else [])
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def _write_conformer_sdf(conformers: Any, path: Path, ligand_id: str, name: str) -> None:
    """Write the retained conformer set with per-conformer MMFF energies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = Chem.SDWriter(str(path))
    try:
        for rank, (conf_id, energy) in enumerate(
            zip(conformers.conformer_ids, conformers.energies_kcal, strict=True)
        ):
            mol = Chem.Mol(conformers.mol)
            mol.SetProp("_Name", f"{ligand_id} conformer {rank}")
            mol.SetProp("ligand_id", ligand_id)
            mol.SetProp("compound_name", name)
            mol.SetProp("conformer_rank", str(rank))
            mol.SetProp("mmff94_energy_kcal_mol", f"{energy:.6f}")
            mol.SetProp("embed_seed", str(conformers.seed))
            writer.write(mol, confId=conf_id)
    finally:
        writer.close()
