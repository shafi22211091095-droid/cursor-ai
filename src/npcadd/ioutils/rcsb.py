"""Structure retrieval with checksummed local caching.

Behaviour contract
------------------
* A structure already in the cache whose SHA256 matches the recorded checksum is
  used without any network access.
* A cache entry whose checksum does not match is treated as corrupt and is
  re-fetched (or, offline, raises) — it is never silently used.
* With ``allow_network=False`` nothing is fetched. A cache miss raises
  :class:`StructureUnavailableError` naming exactly what is missing and how to
  supply it.
* Metadata (resolution, R-free, co-crystal ligand, UniProt accession) is read
  from the structure file itself where the format carries it, and from the RCSB
  REST API when the network is available. Fields that cannot be determined are
  recorded as ``None`` and reported as unavailable — never guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..hashing import hash_file
from ..logging_utils import get_logger

__all__ = [
    "StructureMetadata",
    "StructureSource",
    "StructureUnavailableError",
    "fetch_structure",
    "parse_pdb_metadata",
]

log = get_logger("io.rcsb")

RCSB_FILE_URL = "https://files.rcsb.org/download/{pdb_id}.pdb"
RCSB_ENTRY_API = "https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
RCSB_POLYMER_API = "https://data.rcsb.org/rest/v1/core/polymer_entity/{pdb_id}/1"

_TIMEOUT_S = 60


class StructureUnavailableError(RuntimeError):
    """A required structure could not be obtained."""


@dataclass(slots=True)
class StructureMetadata:
    """Provenance for one receptor structure.

    Every field is optional because the honest answer is frequently "not
    determinable from what we have". The receptor provenance table prints
    ``unavailable`` rather than a plausible-looking number.
    """

    pdb_id: str
    resolution_angstrom: float | None = None
    r_free: float | None = None
    r_work: float | None = None
    experimental_method: str | None = None
    deposition_date: str | None = None
    title: str | None = None
    uniprot_accession: str | None = None
    cocrystal_ligands: list[str] = field(default_factory=list)
    source: str = "unknown"
    retrieved_utc: str | None = None
    notes: str = ""

    def as_record(self) -> dict[str, Any]:
        def show(value: Any) -> Any:
            return "unavailable" if value is None else value

        return {
            "pdb_id": self.pdb_id,
            "title": show(self.title),
            "experimental_method": show(self.experimental_method),
            "resolution_angstrom": show(self.resolution_angstrom),
            "r_work": show(self.r_work),
            "r_free": show(self.r_free),
            "deposition_date": show(self.deposition_date),
            "uniprot_accession": show(self.uniprot_accession),
            "cocrystal_ligands": ";".join(self.cocrystal_ligands) or "none",
            "metadata_source": self.source,
            "retrieved_utc": show(self.retrieved_utc),
            "notes": self.notes,
        }


@dataclass(slots=True)
class StructureSource:
    """Where a structure came from and whether it was verified."""

    path: Path
    sha256: str
    origin: str
    metadata: StructureMetadata
    from_cache: bool


def _parse_float(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value


def parse_pdb_metadata(pdb_text: str, pdb_id: str) -> StructureMetadata:
    """Extract what the PDB-format header actually states.

    Only values present in the text are returned. A file that has had its header
    stripped (as prepared receptor files often have) yields a metadata record
    that is almost entirely ``None``, which is the correct answer.
    """
    meta = StructureMetadata(pdb_id=pdb_id.upper(), source="pdb_header")

    title_parts: list[str] = []
    het_residues: set[str] = set()
    standard = _STANDARD_RESIDUES

    for line in pdb_text.splitlines():
        record = line[:6]
        if record == "TITLE ":
            title_parts.append(line[10:].strip())
        elif record == "EXPDTA":
            meta.experimental_method = line[10:].strip() or None
        elif record == "HEADER":
            date = line[50:59].strip()
            meta.deposition_date = date or None
        elif line.startswith("REMARK   2 RESOLUTION."):
            match = re.search(r"RESOLUTION\.\s+([0-9.]+)\s*ANGSTROM", line)
            if match:
                meta.resolution_angstrom = _parse_float(match.group(1))
        elif line.startswith("REMARK   3   FREE R VALUE  "):
            match = re.search(r":\s*([0-9.]+)", line)
            if match:
                meta.r_free = _parse_float(match.group(1))
        elif line.startswith("REMARK   3   R VALUE            (WORKING SET)"):
            match = re.search(r":\s*([0-9.]+)", line)
            if match:
                meta.r_work = _parse_float(match.group(1))
        elif record == "DBREF " and "UNP" in line and meta.uniprot_accession is None:
            parts = line.split()
            if "UNP" in parts:
                index = parts.index("UNP")
                if index + 1 < len(parts):
                    meta.uniprot_accession = parts[index + 1]
        elif record == "HETATM":
            residue = line[17:20].strip()
            if residue and residue not in standard and residue not in {"HOH", "DOD"}:
                het_residues.add(residue)

    if title_parts:
        meta.title = " ".join(title_parts).strip() or None
    meta.cocrystal_ligands = sorted(het_residues)
    return meta


_STANDARD_RESIDUES = frozenset(
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
        "SEC",
        "PYL",
        "MSE",
        "HID",
        "HIE",
        "HIP",
        "CYX",
        "ASH",
        "GLH",
        "LYN",
        "A",
        "C",
        "G",
        "U",
        "DA",
        "DC",
        "DG",
        "DT",
        "DI",
        "N",
    }
)


def _fetch_rcsb_api_metadata(pdb_id: str) -> StructureMetadata | None:
    """Query the RCSB REST API. Returns None if the network call does not succeed."""
    try:
        import requests
    except ImportError:  # pragma: no cover - dependency is pinned
        return None

    try:
        response = requests.get(RCSB_ENTRY_API.format(pdb_id=pdb_id), timeout=_TIMEOUT_S)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    except (requests.RequestException, json.JSONDecodeError, ValueError) as exc:
        log.warning(
            "RCSB entry metadata unavailable; structural metadata will be read from "
            "the file header only",
            extra={"pdb_id": pdb_id, "error": f"{type(exc).__name__}: {exc}"},
        )
        return None

    meta = StructureMetadata(pdb_id=pdb_id.upper(), source="rcsb_rest_api")
    refine = payload.get("refine") or []
    if isinstance(refine, list) and refine:
        first = refine[0]
        meta.r_free = first.get("ls_rfactor_rfree")
        meta.r_work = first.get("ls_rfactor_rwork")
    entry_info = payload.get("rcsb_entry_info") or {}
    resolutions = entry_info.get("resolution_combined") or []
    if resolutions:
        meta.resolution_angstrom = resolutions[0]
    methods = payload.get("exptl") or []
    if methods:
        meta.experimental_method = methods[0].get("method")
    struct = payload.get("struct") or {}
    meta.title = struct.get("title")
    accession = payload.get("rcsb_accession_info") or {}
    meta.deposition_date = accession.get("deposit_date")
    meta.cocrystal_ligands = sorted(entry_info.get("nonpolymer_bound_components") or [])
    return meta


def _merge_metadata(
    primary: StructureMetadata | None, secondary: StructureMetadata
) -> StructureMetadata:
    """Prefer ``primary`` values, filling gaps from ``secondary``."""
    if primary is None:
        return secondary
    for name in (
        "resolution_angstrom",
        "r_free",
        "r_work",
        "experimental_method",
        "deposition_date",
        "title",
        "uniprot_accession",
    ):
        if getattr(primary, name) is None:
            setattr(primary, name, getattr(secondary, name))
    if not primary.cocrystal_ligands:
        primary.cocrystal_ligands = secondary.cocrystal_ligands
    primary.source = f"{primary.source}+{secondary.source}"
    return primary


def fetch_structure(
    pdb_id: str,
    cache_dir: Path,
    *,
    allow_network: bool = True,
    local_structure: Path | None = None,
    expected_sha256: str | None = None,
) -> StructureSource:
    """Obtain a structure, preferring a verified cache entry.

    Args:
        pdb_id: Four-character PDB ID.
        cache_dir: Directory holding cached ``<ID>.pdb`` files and their
            ``<ID>.sha256`` checksums.
        allow_network: When False, never fetch; raise on a cache miss.
        local_structure: An explicit path that bypasses both cache and network.
        expected_sha256: If given, the obtained file must match this digest.

    Raises:
        StructureUnavailableError: if no verified structure could be obtained.
    """
    import time

    pdb_id = pdb_id.upper()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{pdb_id}.pdb"
    checksum_file = cache_dir / f"{pdb_id}.sha256"

    if local_structure is not None:
        if not local_structure.is_file():
            raise StructureUnavailableError(
                f"receptor.local_structure points at a file that does not exist: {local_structure}"
            )
        digest = hash_file(local_structure)
        if expected_sha256 and digest != expected_sha256:
            raise StructureUnavailableError(
                f"{local_structure} has SHA256 {digest} but {expected_sha256} was "
                "expected. Refusing to use a structure that does not match its "
                "recorded checksum."
            )
        text = local_structure.read_text(encoding="utf-8", errors="replace")
        meta = parse_pdb_metadata(text, pdb_id)
        meta.notes = "supplied via receptor.local_structure; not fetched"
        meta.retrieved_utc = None
        return StructureSource(
            path=local_structure,
            sha256=digest,
            origin=f"local:{local_structure}",
            metadata=meta,
            from_cache=False,
        )

    if cached.is_file():
        digest = hash_file(cached)
        recorded = (
            checksum_file.read_text(encoding="utf-8").strip().split()[0]
            if checksum_file.is_file()
            else None
        )
        reference = expected_sha256 or recorded
        if reference is None or digest == reference:
            text = cached.read_text(encoding="utf-8", errors="replace")
            meta = parse_pdb_metadata(text, pdb_id)
            if reference is None:
                checksum_file.write_text(f"{digest}  {cached.name}\n", encoding="utf-8")
                meta.notes = "cache entry had no checksum; one has been recorded"
            log.info("Using cached structure", extra={"pdb_id": pdb_id, "path": cached.as_posix()})
            return StructureSource(
                path=cached,
                sha256=digest,
                origin="cache",
                metadata=meta,
                from_cache=True,
            )
        log.warning(
            "Cached structure failed checksum verification and will not be used",
            extra={"pdb_id": pdb_id, "found": digest, "expected": reference},
        )
        if not allow_network:
            raise StructureUnavailableError(
                f"Cached {cached} has SHA256 {digest}, not the expected {reference}, and "
                "allow_network is false so it cannot be re-fetched. Delete the corrupt "
                "cache entry and re-run with network access."
            )

    if not allow_network:
        raise StructureUnavailableError(
            f"Structure {pdb_id} is not in the cache at {cache_dir} and allow_network is "
            f"false.\nTo proceed offline, either:\n"
            f"  1. place the structure at {cached} (its checksum will be recorded), or\n"
            f"  2. set receptors[].local_structure to a path you already have, or\n"
            f"  3. set allow_network: true and re-run with outbound access to "
            f"files.rcsb.org."
        )

    try:
        import requests
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise StructureUnavailableError("requests is not installed; cannot fetch") from exc

    url = RCSB_FILE_URL.format(pdb_id=pdb_id)
    log.info("Fetching structure", extra={"pdb_id": pdb_id, "url": url})
    try:
        response = requests.get(url, timeout=_TIMEOUT_S)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise StructureUnavailableError(
            f"Could not fetch {pdb_id} from {url}: {type(exc).__name__}: {exc}\n"
            "If this environment has no outbound access to files.rcsb.org, obtain the "
            f"structure elsewhere, place it at {cached}, and re-run. npcadd will verify "
            "and record its checksum."
        ) from exc

    text = response.text
    if "ATOM" not in text:
        raise StructureUnavailableError(
            f"Response for {pdb_id} from {url} contains no ATOM records; refusing to cache it."
        )

    cached.write_text(text, encoding="utf-8")
    digest = hash_file(cached)
    if expected_sha256 and digest != expected_sha256:
        cached.unlink(missing_ok=True)
        raise StructureUnavailableError(
            f"Fetched {pdb_id} has SHA256 {digest} but {expected_sha256} was expected; "
            "the deposited entry may have been revised. The download has been discarded."
        )
    checksum_file.write_text(f"{digest}  {cached.name}\n", encoding="utf-8")

    header_meta = parse_pdb_metadata(text, pdb_id)
    api_meta = _fetch_rcsb_api_metadata(pdb_id)
    meta = _merge_metadata(header_meta, api_meta) if api_meta else header_meta
    meta.retrieved_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    return StructureSource(
        path=cached,
        sha256=digest,
        origin=url,
        metadata=meta,
        from_cache=False,
    )
