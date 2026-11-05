"""Search-box geometry.

The box is derived from the co-crystal ligand rather than transcribed, so there
is no opportunity for a hand-copied centre to drift out of step with the
structure it is supposed to describe. The derivation is pure and tested.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import GridBox
from ..logging_utils import get_logger

__all__ = ["PosePlacement", "ResolvedBox", "assess_placement", "derive_box", "point_in_box"]

log = get_logger("docking.grid")


@dataclass(slots=True, frozen=True)
class ResolvedBox:
    """A concrete search box with a record of how it was obtained."""

    center: tuple[float, float, float]
    size: tuple[float, float, float]
    mode: str
    derivation: str

    @property
    def volume_a3(self) -> float:
        return float(self.size[0] * self.size[1] * self.size[2])

    def as_record(self) -> dict[str, object]:
        return {
            "box_center_x": round(self.center[0], 3),
            "box_center_y": round(self.center[1], 3),
            "box_center_z": round(self.center[2], 3),
            "box_size_x": round(self.size[0], 3),
            "box_size_y": round(self.size[1], 3),
            "box_size_z": round(self.size[2], 3),
            "box_volume_a3": round(self.volume_a3, 1),
            "box_mode": self.mode,
            "box_derivation": self.derivation,
        }


def derive_box(grid: GridBox, cocrystal_coords: np.ndarray | None = None) -> ResolvedBox:
    """Resolve a :class:`~npcadd.config.GridBox` into concrete numbers.

    Args:
        grid: The configured box specification.
        cocrystal_coords: ``(n_atoms, 3)`` coordinates of the co-crystal ligand.
            Required for ``mode='cocrystal'``.

    Returns:
        A :class:`ResolvedBox`.

    Raises:
        ValueError: if ``mode='cocrystal'`` and no coordinates were supplied, if
            the coordinate array has the wrong shape, or if the resulting box
            exceeds ``max_edge``.
    """
    if grid.mode == "explicit":
        if grid.center is None or grid.size is None:  # pragma: no cover - validated
            raise ValueError("explicit grid is missing centre or size")
        return ResolvedBox(
            center=tuple(float(v) for v in grid.center),  # type: ignore[arg-type]
            size=tuple(float(v) for v in grid.size),  # type: ignore[arg-type]
            mode="explicit",
            derivation="centre and size taken verbatim from config.yaml",
        )

    if cocrystal_coords is None:
        raise ValueError(
            "grid.mode='cocrystal' requires the co-crystal ligand coordinates, but none "
            "were available. Either provide the co-crystal ligand (receptor."
            "cocrystal_ligand / cocrystal_reference_sdf) or switch this receptor to "
            "grid.mode='explicit' and record where the coordinates came from."
        )

    coords = np.asarray(cocrystal_coords, dtype=np.float64)
    if coords.ndim != 2 or coords.shape[1] != 3 or coords.shape[0] == 0:
        raise ValueError(
            f"cocrystal_coords must have shape (n_atoms, 3) with n_atoms > 0; got {coords.shape}"
        )
    if not np.isfinite(coords).all():
        raise ValueError("cocrystal_coords contains non-finite values")

    centre = coords.mean(axis=0)
    extent = coords.max(axis=0) - coords.min(axis=0)
    size = np.maximum(extent + 2.0 * grid.padding, np.asarray(grid.min_size, dtype=np.float64))

    over = [float(s) for s in size if s > grid.max_edge]
    if over:
        raise ValueError(
            f"Derived box edge(s) {over} exceed max_edge {grid.max_edge} A for a "
            f"co-crystal ligand of extent {np.round(extent, 2).tolist()} A with padding "
            f"{grid.padding} A. Reduce padding or raise max_edge deliberately."
        )

    box = ResolvedBox(
        center=(float(centre[0]), float(centre[1]), float(centre[2])),
        size=(float(size[0]), float(size[1]), float(size[2])),
        mode="cocrystal",
        derivation=(
            f"centroid of {coords.shape[0]} co-crystal ligand atoms; size = ligand "
            f"extent {np.round(extent, 2).tolist()} + 2 x {grid.padding} A padding, "
            f"floored at min_size {list(grid.min_size)}"
        ),
    )
    log.info(
        "Derived search box from co-crystal ligand",
        extra={
            "center": [round(c, 3) for c in box.center],
            "size": [round(s, 3) for s in box.size],
        },
    )
    return box


def point_in_box(point: np.ndarray, box: ResolvedBox, tolerance: float = 0.0) -> bool:
    """Whether ``point`` lies inside ``box``, optionally with a tolerance."""
    p = np.asarray(point, dtype=np.float64)
    centre = np.asarray(box.center, dtype=np.float64)
    half = np.asarray(box.size, dtype=np.float64) / 2.0 + tolerance
    return bool(np.all(np.abs(p - centre) <= half))


@dataclass(slots=True, frozen=True)
class PosePlacement:
    """Where a docked pose sits relative to its search box.

    A redocking gate asks whether a *known* pose was reproduced; it has a
    crystallographic reference to compare against. A screened ligand has no such
    reference, so the question that can still be asked of it is geometric: did
    the engine place the pose *inside the region it was told to search*, or did
    it pin the ligand against a wall and let atoms protrude? A pose hugging the
    box boundary is the classic signature that the true energy minimum lies
    outside the defined pocket, in which case the affinity is an artefact of
    where the box was drawn rather than a property of the pocket.

    This is the failure mode documented by Agboola et al. (2026), who showed
    that Vina's ranking metrics (ROC-AUC, enrichment) can stay almost unchanged
    while correct placement collapses, so a score- or ranking-based check cannot
    detect it and an explicit placement check is needed. It is also the bundled
    ABL1 system's own exhaustiveness-8 failure (right pocket centroid, wrong
    orientation) reduced to the part that needs no reference pose. See
    ``DECISIONS.md`` D-021.
    """

    n_atoms: int
    n_inside: int
    fraction_inside: float
    centroid_offset_a: float
    centroid_offset_frac: float
    max_atom_offset_frac: float
    in_box: bool
    note: str

    def as_record(self) -> dict[str, object]:
        return {
            "n_pose_atoms": self.n_atoms,
            "n_atoms_in_box": self.n_inside,
            "frac_atoms_in_box": round(self.fraction_inside, 4),
            "pose_centroid_offset_a": round(self.centroid_offset_a, 3),
            "pose_centroid_offset_frac": round(self.centroid_offset_frac, 4),
            "pose_max_atom_offset_frac": round(self.max_atom_offset_frac, 4),
            "placement_ok": self.in_box,
            "placement_note": self.note,
        }


def assess_placement(
    coords: np.ndarray,
    box: ResolvedBox,
    *,
    tolerance: float = 0.0,
    min_fraction_inside: float = 0.90,
    max_centroid_offset_frac: float = 0.90,
) -> PosePlacement:
    """Assess whether a pose's atoms sit inside ``box``.

    Containment uses the same ``<=`` convention as :func:`point_in_box`. The
    offset fractions are expressed against the box *half-width* on each axis, so
    0 is the box centre, 1 is a face and >1 is outside; this makes the numbers
    comparable between boxes of different sizes.

    Args:
        coords: ``(n_atoms, 3)`` heavy-atom coordinates of the pose, in the
            receptor frame. Pass heavy atoms only; modelled hydrogen positions
            would add noise, exactly as for redocking RMSD.
        box: The resolved search box the pose was docked into.
        tolerance: Angstroms added to each half-width when testing containment,
            so an atom grazing the boundary is not counted as outside.
        min_fraction_inside: A pose with a smaller fraction of atoms inside is
            flagged.
        max_centroid_offset_frac: A pose whose centroid lies beyond this
            fraction of the half-width on any axis is flagged.

    Returns:
        A :class:`PosePlacement`; ``in_box`` is the AND of both criteria.

    Raises:
        ValueError: if ``coords`` is empty, the wrong shape, or non-finite.
    """
    pts = np.asarray(coords, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
        raise ValueError(f"coords must have shape (n_atoms, 3) with n_atoms > 0; got {pts.shape}")
    if not np.isfinite(pts).all():
        raise ValueError("coords contains non-finite values")

    centre = np.asarray(box.center, dtype=np.float64)
    half = np.asarray(box.size, dtype=np.float64) / 2.0
    if not bool(np.all(half > 0)):  # pragma: no cover - ResolvedBox edges are positive
        raise ValueError("box has a non-positive edge")

    dev = np.abs(pts - centre)
    inside_mask = np.all(dev <= half + tolerance, axis=1)
    n = int(pts.shape[0])
    n_inside = int(inside_mask.sum())
    fraction_inside = n_inside / n

    # Per-atom offset: the furthest axis expressed as a fraction of that axis'
    # half-width. The maximum over atoms is the worst protrusion.
    max_atom_offset_frac = float((dev / half).max())

    centroid = pts.mean(axis=0)
    centroid_dev = np.abs(centroid - centre)
    centroid_offset_a = float(np.linalg.norm(centroid - centre))
    centroid_offset_frac = float((centroid_dev / half).max())

    reasons: list[str] = []
    if fraction_inside < min_fraction_inside:
        reasons.append(
            f"{n - n_inside}/{n} heavy atoms fall outside the search box "
            f"(fraction inside {fraction_inside:.2f} < {min_fraction_inside:g})"
        )
    if centroid_offset_frac > max_centroid_offset_frac:
        reasons.append(
            f"pose centroid at {centroid_offset_frac:.2f} of the box half-width "
            f"(> {max_centroid_offset_frac:g}); the pose is hugging the box wall, so the "
            f"energy minimum may lie outside the defined pocket"
        )
    in_box = not reasons
    note = "pose well inside the search box" if in_box else "; ".join(reasons)
    return PosePlacement(
        n_atoms=n,
        n_inside=n_inside,
        fraction_inside=fraction_inside,
        centroid_offset_a=centroid_offset_a,
        centroid_offset_frac=centroid_offset_frac,
        max_atom_offset_frac=max_atom_offset_frac,
        in_box=in_box,
        note=note,
    )
