"""Stage 2: receptor preparation.

Steps, in order:

1. Obtain the structure (verified cache, explicit local file, or RCSB fetch).
2. **Normalise atom ordering** so each residue's atoms are contiguous. Deposited
   and third-party-prepared files occasionally interleave a residue's atoms,
   which downstream polymer parsers reject outright.
3. Select the requested chain; strip waters and ions, retaining any residue
   named in ``keep_hetatms`` (catalytic metals, FAD, haem).
4. Add hydrogens and assign protonation at the configured pH.
5. Export a rigid receptor PDBQT via Meeko.
6. Extract the co-crystal ligand pose and derive the search box from it.
7. Record a provenance row: resolution, R-free, method, UniProt accession,
   co-crystal ligand, checksums — with ``unavailable`` wherever the structure
   genuinely does not state it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..config import Config, ReceptorConfig
from ..docking.grid import ResolvedBox, derive_box
from ..hashing import hash_file
from ..ioutils.rcsb import StructureMetadata, StructureUnavailableError, fetch_structure
from ..logging_utils import get_logger

__all__ = [
    "PreparedReceptor",
    "ReceptorPrepError",
    "normalise_atom_order",
    "prepare_all_receptors",
    "prepare_receptor",
]

log = get_logger("stages.receptor")

#: Residues stripped unless explicitly retained via ``keep_hetatms``.
SOLVENT_RESIDUES = frozenset({"HOH", "DOD", "WAT", "TIP", "SOL"})

#: Common crystallisation additives and ions. Retained only when named in
#: ``keep_hetatms``, because some of these are structurally essential in some
#: proteins (ZN in metalloenzymes) and pure artefact in others.
ION_RESIDUES = frozenset(
    {
        "NA",
        "CL",
        "K",
        "MG",
        "CA",
        "ZN",
        "MN",
        "FE",
        "FE2",
        "CU",
        "CU1",
        "NI",
        "CO",
        "CD",
        "HG",
        "SO4",
        "PO4",
        "GOL",
        "EDO",
        "PEG",
        "DMS",
        "ACT",
        "MES",
        "TRS",
        "EPE",
        "IMD",
        "NO3",
        "BR",
        "IOD",
        "F",
        "CAC",
        "FMT",
        "ACY",
    }
)

_STANDARD_AA = frozenset(
    {
        "ALA",
        "ARG",
        "ASN",
        "ASP",
        "CYS",
        "GLN",
        "GLU",
        "GLY",
        "HIS",
        "ILE",
        "LEU",
        "LYS",
        "MET",
        "PHE",
        "PRO",
        "SER",
        "THR",
        "TRP",
        "TYR",
        "VAL",
        "MSE",
        "SEC",
        "PYL",
        "HID",
        "HIE",
        "HIP",
        "CYX",
        "ASH",
        "GLH",
        "LYN",
    }
)


class ReceptorPrepError(RuntimeError):
    """Receptor preparation failed in a way that must stop the pipeline."""


@dataclass(slots=True)
class PreparedReceptor:
    """A docking-ready receptor plus everything needed to audit it."""

    receptor_id: str
    pdb_id: str
    activity: str
    target_name: str
    chain: str
    pdbqt_path: Path
    cleaned_pdb_path: Path
    cocrystal_sdf_path: Path | None
    box: ResolvedBox
    metadata: StructureMetadata
    structure_sha256: str
    pdbqt_sha256: str
    n_atoms: int
    n_residues: int
    n_hetatms_kept: int
    phenotype_direction: str
    directionality_conflict: bool
    protonation_tool: str
    warnings: list[str] = field(default_factory=list)

    def as_record(self) -> dict[str, Any]:
        """One row of the receptor provenance table."""
        record: dict[str, Any] = {
            "receptor_id": self.receptor_id,
            "activity": self.activity,
            "target_name": self.target_name,
            "chain": self.chain,
            "n_atoms": self.n_atoms,
            "n_residues": self.n_residues,
            "n_hetatms_kept": self.n_hetatms_kept,
            "phenotype_direction": self.phenotype_direction,
            "directionality_conflict": self.directionality_conflict,
            "protonation_tool": self.protonation_tool,
            "structure_sha256": self.structure_sha256,
            "pdbqt_sha256": self.pdbqt_sha256,
            "warnings": "; ".join(self.warnings),
        }
        record.update(self.metadata.as_record())
        record.update(self.box.as_record())
        return record


def _residue_sort_key(key: tuple[str, str, str]) -> tuple[str, int, str]:
    """Sort key for a residue: chain, then numeric sequence number, then icode."""
    chain, resseq, icode = key
    try:
        number = int(resseq)
    except ValueError:
        # Non-numeric sequence numbers do occur in hand-edited files. Push them
        # to the end rather than crashing, and keep them mutually ordered.
        return (chain, 10**9, resseq + icode)
    return (chain, number, icode)


def normalise_atom_order(pdb_text: str) -> tuple[str, int, int]:
    """Group each residue's atoms contiguously, in true sequence order.

    Two things happen here, and the second one matters chemically.

    **Contiguity.** A single out-of-place atom record makes polymer parsers
    (Meeko, MDAnalysis) reject the whole file with "interrupted residues". The
    bundled sample structure has exactly this defect: one atom of SER A 438
    appears at line 2, far from the rest of its residue.

    **Sequence ordering.** Residues are emitted ordered by (chain, residue
    number, insertion code) rather than by order of first appearance. This is
    not cosmetic. If the stray SER A 438 atom is allowed to make residue 438 the
    first residue in the file, PDB2PQR identifies it as the chain's N-terminus
    and builds a charged NH3+ group on a residue that sits in the middle of the
    kinase domain -- inventing a formal charge that is not in the protein, and
    producing a molecule RDKit rejects outright for impossible hydrogen valence.
    Ordering by sequence number makes the real termini the termini.

    Atom order *within* a residue is preserved, and no coordinate is altered, so
    the transformation is a pure permutation plus serial renumbering.

    Returns:
        ``(normalised_text, n_atoms, n_residues)``.
    """
    groups: OrderedDict[tuple[str, str, str], list[str]] = OrderedDict()
    preamble: list[str] = []

    for line in pdb_text.splitlines(keepends=True):
        if line.startswith(("ATOM", "HETATM")):
            key = (line[20:22], line[22:26], line[26:27])
            groups.setdefault(key, []).append(line)
        elif line.startswith(("TER", "END", "CONECT", "MASTER", "ANISOU")):
            continue
        else:
            preamble.append(line)

    out: list[str] = list(preamble)
    serial = 0
    for key in sorted(groups, key=_residue_sort_key):
        for line in groups[key]:
            serial += 1
            # Columns 7-11 are the atom serial; everything else is untouched.
            out.append(f"{line[:6]}{serial:5d}{line[11:]}")
    out.append("TER\n")
    out.append("END\n")
    return "".join(out), serial, len(groups)


def _filter_structure(
    pdb_text: str, chain: str, keep_hetatms: frozenset[str]
) -> tuple[str, list[str], int]:
    """Keep one chain's polymer plus retained heteroatoms.

    Returns ``(filtered_text, dropped_residue_names, n_hetatms_kept)``.
    """
    kept: list[str] = []
    dropped: set[str] = set()
    n_het_kept = 0

    for line in pdb_text.splitlines(keepends=True):
        if not line.startswith(("ATOM", "HETATM")):
            kept.append(line)
            continue

        line_chain = line[21:22].strip()
        residue = line[17:20].strip()

        if chain and line_chain and line_chain != chain:
            continue

        if line.startswith("HETATM"):
            if residue in keep_hetatms:
                kept.append(line)
                n_het_kept += 1
            elif residue in _STANDARD_AA:
                # Modified residues (MSE) arrive as HETATM but are polymer.
                kept.append(line)
            else:
                dropped.add(residue)
            continue

        kept.append(line)

    return "".join(kept), sorted(dropped), n_het_kept


def _extract_cocrystal_coords(pdb_text: str, residue_name: str, chain: str) -> np.ndarray | None:
    """Pull heavy-atom coordinates of the co-crystal ligand from the structure."""
    coords: list[list[float]] = []
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if line[17:20].strip() != residue_name:
            continue
        if chain and line[21:22].strip() and line[21:22].strip() != chain:
            continue
        element = line[76:78].strip().upper()
        if element == "H" or line[12:16].strip().startswith("H"):
            continue
        coords.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
    return np.asarray(coords, dtype=np.float64) if coords else None


def _cocrystal_coords_from_sdf(path: Path) -> np.ndarray:
    """Heavy-atom coordinates of the reference pose from an SDF."""
    from rdkit import Chem

    mol = Chem.MolFromMolFile(str(path), removeHs=True)
    if mol is None:
        raise ReceptorPrepError(
            f"Could not parse the co-crystal reference pose at {path}. The redocking "
            "RMSD gate cannot run without a reference pose."
        )
    if mol.GetNumConformers() == 0:
        raise ReceptorPrepError(f"{path} contains no 3D conformer")
    return np.asarray(mol.GetConformer().GetPositions(), dtype=np.float64)


def _module_available(name: str) -> bool:
    """Whether ``name`` can be imported in the current interpreter."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - defensive
        return False


def _sibling_executable(name: str) -> str | None:
    """Look for a console script next to the running interpreter."""
    candidate = Path(sys.executable).parent / name
    return str(candidate) if candidate.is_file() else None


def _add_hydrogens_pdb2pqr(in_pdb: Path, out_pdb: Path, ph: float) -> tuple[bool, str, str]:
    """Assign pH-dependent protonation with PDB2PQR + PROPKA.

    Returns ``(ok, tool_description, detail)``. Failure is reported, never
    silently swallowed: the caller decides whether to continue with the
    structure's existing hydrogens or abort.
    """
    # Prefer invoking the installed module through the *current* interpreter.
    # PDB2PQR is a Python package in this environment's own dependency set, so
    # `sys.executable -m pdb2pqr` finds it whether or not the virtualenv's bin
    # directory happens to be on PATH -- which it is not when the pipeline is
    # driven by an absolute interpreter path, as it is in CI and in Docker.
    command_prefix: list[str]
    if _module_available("pdb2pqr"):
        command_prefix = [sys.executable, "-m", "pdb2pqr"]
    else:
        found = (
            shutil.which("pdb2pqr30")
            or shutil.which("pdb2pqr")
            or _sibling_executable("pdb2pqr30")
            or _sibling_executable("pdb2pqr")
        )
        if found is None:
            return (
                False,
                "none",
                "pdb2pqr is neither importable nor on PATH; cannot assign pH-dependent protonation",
            )
        command_prefix = [found]

    pqr_out = out_pdb.with_suffix(".pqr")
    command = [
        *command_prefix,
        "--ff=AMBER",
        "--with-ph",
        str(ph),
        "--titration-state-method=propka",
        "--keep-chain",
        f"--pdb-output={out_pdb}",
        str(in_pdb),
        str(pqr_out),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "pdb2pqr+propka", f"{type(exc).__name__}: {exc}"

    if result.returncode != 0 or not out_pdb.is_file():
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-6:]
        return False, "pdb2pqr+propka", "; ".join(tail) or "pdb2pqr returned non-zero"
    return True, f"pdb2pqr+propka (pH {ph})", ""


def _write_receptor_pdbqt(cleaned_pdb: Path, out_basename: Path) -> Path:
    """Export a rigid receptor PDBQT with Meeko.

    Uses the same ``Polymer`` + ``PDBQTWriterLegacy`` path as Meeko's own
    ``mk_prepare_receptor`` CLI, and has been verified to produce byte-identical
    output to it. Going through the library rather than shelling out keeps the
    receptor step inside the typed, testable code path.
    """
    from meeko import (
        MoleculePreparation,
        PDBQTWriterLegacy,
        Polymer,
        PolymerCreationError,
        ResidueChemTemplates,
    )

    text = cleaned_pdb.read_text(encoding="utf-8")
    try:
        templates = ResidueChemTemplates.create_from_defaults()
        polymer = Polymer.from_pdb_string(text, templates, MoleculePreparation())
    except (PolymerCreationError, ValueError, KeyError, RuntimeError) as exc:
        raise ReceptorPrepError(
            f"Meeko could not build a polymer from {cleaned_pdb}: "
            f"{type(exc).__name__}: {exc}\n"
            "Common causes: a non-standard residue with no chemical template, an "
            "incomplete residue, or unresolved alternate locations. Inspect the "
            "cleaned structure; retain a needed cofactor via "
            "receptors[].keep_hetatms, or supply an already-prepared structure via "
            "receptors[].local_structure."
        ) from exc

    pdbqt_path = out_basename.with_suffix(".pdbqt")
    try:
        written = PDBQTWriterLegacy.write_from_polymer(polymer)
    except (AttributeError, ValueError, RuntimeError) as exc:
        raise ReceptorPrepError(
            f"Meeko could not serialise the receptor PDBQT: {type(exc).__name__}: {exc}"
        ) from exc

    # write_from_polymer returns (rigid_pdbqt, flexible_pdbqt); npcadd docks
    # rigid receptors, so only the first element is used.
    rigid = written[0] if isinstance(written, tuple) else written
    if not isinstance(rigid, str) or "ATOM" not in rigid:
        raise ReceptorPrepError("Meeko produced an empty or unexpected receptor PDBQT payload")
    pdbqt_path.write_text(rigid, encoding="utf-8")
    return pdbqt_path


def prepare_receptor(cfg: Config, receptor: ReceptorConfig, out_dir: Path) -> PreparedReceptor:
    """Prepare one receptor for docking."""
    work = out_dir / "receptors" / receptor.id
    work.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    source = fetch_structure(
        receptor.pdb_id,
        cfg.paths.receptor_cache,
        allow_network=cfg.allow_network,
        local_structure=receptor.local_structure,
    )
    raw_text = source.path.read_text(encoding="utf-8", errors="replace")

    if source.metadata.resolution_angstrom is None:
        warnings.append(
            "Resolution is unavailable from this structure file, so its suitability "
            "for docking cannot be judged from the data at hand. Re-prepare from the "
            "deposited RCSB entry before publishing."
        )
    elif source.metadata.resolution_angstrom > 2.5:
        warnings.append(
            f"Resolution is {source.metadata.resolution_angstrom} A. Side-chain "
            "positions at worse than ~2.5 A are not reliable enough for pose-level "
            "interpretation; treat interaction fingerprints from this receptor as "
            "indicative only."
        )

    normalised, _, _ = normalise_atom_order(raw_text)
    filtered, dropped, n_het_kept = _filter_structure(
        normalised, receptor.chain, frozenset(receptor.keep_hetatms)
    )
    if dropped:
        solvent = sorted(set(dropped) & (SOLVENT_RESIDUES | ION_RESIDUES))
        other = sorted(set(dropped) - (SOLVENT_RESIDUES | ION_RESIDUES))
        log.info(
            "Stripped heteroatoms",
            extra={"solvent_and_ions": solvent, "other": other, "receptor": receptor.id},
        )
        if other:
            warnings.append(
                f"Stripped non-solvent heteroatom residue(s) {other}. If any of these "
                "is a cofactor required for the binding site to be physically "
                "meaningful, add it to receptors[].keep_hetatms and re-run."
            )

    stripped_path = work / f"{receptor.id}_stripped.pdb"
    normalised_filtered, n_atoms, n_residues = normalise_atom_order(filtered)
    stripped_path.write_text(normalised_filtered, encoding="utf-8")

    protonated_path = work / f"{receptor.id}_protonated.pdb"
    ok, tool, detail = _add_hydrogens_pdb2pqr(stripped_path, protonated_path, cfg.ligands.ph)
    if ok:
        cleaned_text, n_atoms, n_residues = normalise_atom_order(
            protonated_path.read_text(encoding="utf-8")
        )
        protonated_path.write_text(cleaned_text, encoding="utf-8")
        cleaned_path = protonated_path
        protonation_tool = tool
    else:
        has_hydrogens = any(
            line[76:78].strip() == "H"
            for line in normalised_filtered.splitlines()
            if line.startswith(("ATOM", "HETATM"))
        )
        if not has_hydrogens:
            raise ReceptorPrepError(
                f"Could not assign protonation states for receptor {receptor.id!r} at pH "
                f"{cfg.ligands.ph} ({detail}), and the structure carries no hydrogens. "
                "Docking an unprotonated receptor would silently change the hydrogen-"
                "bonding pattern, so npcadd refuses to continue. Install pdb2pqr, or "
                "supply an already-protonated structure via receptors[].local_structure."
            )
        cleaned_path = stripped_path
        protonation_tool = "pre-existing hydrogens in the input structure"
        warnings.append(
            f"pdb2pqr/PROPKA could not run ({detail}). The structure's pre-existing "
            f"hydrogens were used as-is, so protonation is NOT guaranteed to correspond "
            f"to pH {cfg.ligands.ph}. Histidine tautomers and the ionisation of Asp/Glu/"
            "Lys in the site were not re-evaluated."
        )

    pdbqt_path = _write_receptor_pdbqt(cleaned_path, work / receptor.id)

    cocrystal_sdf: Path | None = None
    coords: np.ndarray | None = None
    if receptor.cocrystal_reference_sdf is not None:
        coords = _cocrystal_coords_from_sdf(receptor.cocrystal_reference_sdf)
        cocrystal_sdf = receptor.cocrystal_reference_sdf
    elif receptor.cocrystal_ligand:
        coords = _extract_cocrystal_coords(raw_text, receptor.cocrystal_ligand, receptor.chain)
        if coords is None:
            warnings.append(
                f"Co-crystal ligand {receptor.cocrystal_ligand!r} was not found in "
                f"chain {receptor.chain} of the structure, so neither the search box "
                "nor the redocking RMSD gate can be derived from it."
            )

    box = derive_box(receptor.grid, coords)

    if receptor.directionality_conflict:
        warnings.append(
            "DIRECTIONALITY: the desired phenotype for this target is activation, but "
            "the structure used is an inhibitor-bound complex. A favourable docking "
            "score against this conformation is evidence of binding to the "
            "inhibitor-bound state, NOT evidence of activation. This must be stated "
            "explicitly wherever these scores are reported."
        )

    prepared = PreparedReceptor(
        receptor_id=receptor.id,
        pdb_id=receptor.pdb_id,
        activity=receptor.activity,
        target_name=receptor.target_name,
        chain=receptor.chain,
        pdbqt_path=pdbqt_path,
        cleaned_pdb_path=cleaned_path,
        cocrystal_sdf_path=cocrystal_sdf,
        box=box,
        metadata=source.metadata,
        structure_sha256=source.sha256,
        pdbqt_sha256=hash_file(pdbqt_path),
        n_atoms=n_atoms,
        n_residues=n_residues,
        n_hetatms_kept=n_het_kept,
        phenotype_direction=receptor.phenotype_direction,
        directionality_conflict=receptor.directionality_conflict,
        protonation_tool=protonation_tool,
        warnings=warnings,
    )
    if receptor.uniprot and prepared.metadata.uniprot_accession is None:
        prepared.metadata.uniprot_accession = receptor.uniprot
        prepared.metadata.notes = (
            f"{prepared.metadata.notes} UniProt accession taken from config, not from "
            "the structure file."
        ).strip()

    log.info(
        "Prepared receptor %s (%s, %s)",
        receptor.id,
        receptor.pdb_id,
        receptor.activity,
        extra={
            "n_atoms": n_atoms,
            "n_residues": n_residues,
            "box_center": [round(c, 2) for c in box.center],
            "box_size": [round(s, 2) for s in box.size],
            "n_warnings": len(warnings),
        },
    )
    for warning in warnings:
        log.warning("[%s] %s", receptor.id, warning)
    return prepared


def prepare_all_receptors(cfg: Config, out_dir: Path) -> list[PreparedReceptor]:
    """Prepare every configured receptor.

    A receptor that cannot be obtained is a hard failure: silently screening
    against a subset of the configured panel would make the reported coverage
    wrong.
    """
    prepared: list[PreparedReceptor] = []
    for receptor in cfg.receptors:
        try:
            prepared.append(prepare_receptor(cfg, receptor, out_dir))
        except StructureUnavailableError as exc:
            raise ReceptorPrepError(
                f"Receptor {receptor.id!r} ({receptor.pdb_id}) could not be prepared:\n{exc}"
            ) from exc
    if not prepared:
        raise ReceptorPrepError("No receptor could be prepared")
    return prepared
