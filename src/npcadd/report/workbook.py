"""Multi-sheet XLSX workbook assembly.

Sheet order follows the pipeline, and the first sheet is always ``README``:
a reader who opens the workbook without the repository should still be able to
tell what each sheet is, which parameters produced it, and what the numbers do
and do not mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Config
from ..logging_utils import get_logger

__all__ = ["SheetSpec", "write_workbook"]

log = get_logger("report.workbook")

#: Excel caps sheet names at 31 characters.
_MAX_SHEET_NAME = 31


@dataclass(slots=True)
class SheetSpec:
    """One worksheet and the one-line explanation that goes in the README sheet."""

    name: str
    frame: pd.DataFrame
    description: str

    @property
    def safe_name(self) -> str:
        return self.name[:_MAX_SHEET_NAME]


def _readme_frame(
    cfg: Config,
    sheets: list[SheetSpec],
    versions: dict[str, str],
    run_id: str,
    config_hash: str,
    caveats: list[str],
) -> pd.DataFrame:
    """Build the README sheet as a two-column key/value table."""
    rows: list[tuple[str, str]] = [
        ("WORKBOOK", f"{cfg.project}: {cfg.study}"),
        ("run_id", run_id),
        ("config_sha256", config_hash),
        ("", ""),
        ("=== HOW TO READ THIS ===", ""),
        (
            "Ranking basis",
            "Compounds are ranked by ligand efficiency and a multi-criteria score, "
            "NOT by raw docking affinity. Raw affinity scales with molecular size, so "
            "ranking a size-diverse set by it ranks largely by molecular weight.",
        ),
        (
            "Docking scores",
            "AutoDock Vina scores are empirical and are not free energies. The "
            "pkd_estimate column is a unit conversion of the score onto a log-affinity "
            "scale so that LLE is comparable with published values; it is NOT a "
            "predicted binding constant.",
        ),
        (
            "z-score / empirical p",
            "Computed against a random-ligand null distribution docked under identical "
            "parameters. The p-value cannot be smaller than 1/(n+1) for a null set of "
            "size n; that floor is reported as p_value_resolution.",
        ),
        (
            "Structural alerts",
            "A PAINS or Brenk match is a flag for assay-interference or liability, not "
            "a verdict. Several genuine natural-product bioactives (catechols, "
            "quinones, curcuminoids) match these filters.",
        ),
        (
            "ADMET predictions",
            "QSPR values carry an applicability-domain verdict. A value with "
            "in_domain=FALSE is an extrapolation and is excluded from the ranking "
            "score.",
        ),
        ("", ""),
        ("=== PARAMETERS ===", ""),
        ("docking.engine", cfg.docking.engine),
        ("docking.scoring", cfg.docking.scoring),
        ("docking.exhaustiveness", str(cfg.docking.exhaustiveness)),
        ("docking.seed", str(cfg.docking.seed)),
        ("docking.n_poses", str(cfg.docking.n_poses)),
        ("ligands.ph", str(cfg.ligands.ph)),
        ("ligands.protonation_method", cfg.ligands.protonation_method),
        ("ligands.embed_seed", str(cfg.ligands.embed_seed)),
        ("ligands.n_conformers", str(cfg.ligands.n_conformers)),
        ("validation.redock_rmsd_threshold", str(cfg.validation.redock_rmsd_threshold)),
        ("validation.halt_on_failure", str(cfg.validation.halt_on_failure)),
        (
            "ranking.weights",
            ", ".join(f"{k}={v:g}" for k, v in sorted(cfg.ranking.weights.items())),
        ),
        ("ranking.area_highlight_threshold", str(cfg.ranking.area_highlight_threshold)),
        ("", ""),
        ("=== SOFTWARE VERSIONS ===", ""),
    ]
    rows.extend((k, v) for k, v in sorted(versions.items()))
    rows.append(("", ""))
    rows.append(("=== SHEETS ===", ""))
    rows.extend((s.safe_name, s.description) for s in sheets)

    if caveats:
        rows.append(("", ""))
        rows.append(("=== CAVEATS AND WARNINGS ===", ""))
        rows.extend((f"caveat {i}", c) for i, c in enumerate(caveats, start=1))

    return pd.DataFrame(rows, columns=["field", "value"])


def write_workbook(
    cfg: Config,
    sheets: list[SheetSpec],
    out_path: Path,
    versions: dict[str, str],
    run_id: str,
    config_hash: str,
    caveats: list[str] | None = None,
) -> Path:
    """Write the multi-sheet workbook, README sheet first.

    Empty frames are still written, with a single explanatory row, so a reader
    can tell "this analysis produced nothing" apart from "this analysis was not
    run".
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    readme = _readme_frame(cfg, sheets, versions, run_id, config_hash, caveats or [])

    seen: set[str] = set()
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        readme.to_excel(writer, sheet_name="README", index=False)
        _autosize(writer, "README", readme, max_width=110)

        for spec in sheets:
            name = spec.safe_name
            suffix = 2
            while name in seen:
                name = f"{spec.safe_name[: _MAX_SHEET_NAME - 2]}_{suffix}"
                suffix += 1
            seen.add(name)

            frame = spec.frame
            if frame.empty:
                frame = pd.DataFrame(
                    {
                        "note": [
                            "This analysis produced no rows. See the README sheet and "
                            "reports/VALIDATION.md for why."
                        ]
                    }
                )
            frame.to_excel(writer, sheet_name=name, index=False)
            _autosize(writer, name, frame)

    log.info(
        "Wrote workbook",
        extra={"path": out_path.as_posix(), "n_sheets": len(sheets) + 1},
    )
    return out_path


def _autosize(writer: Any, sheet_name: str, frame: pd.DataFrame, max_width: int = 42) -> None:
    """Set column widths from content so the workbook is readable on open."""
    from openpyxl.utils import get_column_letter

    worksheet = writer.sheets[sheet_name]
    for index, column in enumerate(frame.columns, start=1):
        values = frame[column].astype(str)
        longest = int(values.str.len().max()) if not values.empty else 0
        width = min(max(len(str(column)) + 2, longest + 2), max_width)
        worksheet.column_dimensions[get_column_letter(index)].width = width
    worksheet.freeze_panes = "A2"
