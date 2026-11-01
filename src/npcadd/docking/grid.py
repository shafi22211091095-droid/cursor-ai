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

__all__ = ["ResolvedBox", "derive_box", "point_in_box"]

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
