"""Self-contained HTML run summary.

Reads a finished run's CSVs and provenance and renders one portable HTML file
with no external resources, so it opens anywhere and can be shared as-is. It is a
read-only view of outputs the pipeline already produced; it computes no science
and changes nothing.
"""

from __future__ import annotations

import html as _html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Config
from ..logging_utils import get_logger

__all__ = ["render_html_summary"]

log = get_logger("report.html")

_CSS = (
    "body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
    "max-width:1040px;margin:2rem auto;padding:0 1rem;color:#18181b;line-height:1.5}"
    "h1{font-size:1.5rem;margin-bottom:.2rem}"
    "h2{font-size:1.05rem;margin-top:2rem;border-bottom:1px solid #e4e4e7;padding-bottom:.3rem}"
    "table{border-collapse:collapse;width:100%;font-size:.85rem;margin:.6rem 0}"
    "th,td{border:1px solid #e4e4e7;padding:.35rem .5rem;text-align:left;vertical-align:top}"
    "th{background:#f4f4f5}tr:nth-child(even){background:#fafafa}"
    ".caveats{background:#fffbeb;border:1px solid #fcd34d;border-radius:6px;padding:.4rem 1rem}"
    ".meta{color:#52525b;font-size:.85rem}"
    "code{background:#f4f4f5;padding:.1rem .3rem;border-radius:3px}"
    ".flag{color:#b45309}.ok{color:#15803d;font-weight:600}.bad{color:#b91c1c;font-weight:600}"
)

_TABLE_COLUMNS: list[tuple[str, str]] = [
    ("rank", "#"),
    ("name", "Compound"),
    ("compound_class", "Class"),
    ("target_name", "Target"),
    ("best_affinity_kcal_mol", "Vina (kcal/mol)"),
    ("ligand_efficiency_kcal_mol_per_ha", "LE"),
    ("binding_efficiency_index_bei", "BEI"),
    ("size_independent_le_sile", "SILE"),
    ("multi_criteria_score", "Score"),
    ("placement_ok", "Placed"),
    ("flags", "Flags"),
]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data


def _esc(value: Any) -> str:
    return _html.escape("" if value is None else str(value))


def _cell(key: str, value: Any) -> str:
    if key == "placement_ok":
        if str(value) == "True":
            return "<span class='ok'>yes</span>"
        if str(value) == "False":
            return "<span class='bad'>NO</span>"
        return "-"
    if key == "flags":
        text = str(value).strip()
        return f"<span class='flag'>{_esc(text)}</span>" if text else ""
    return _esc(value)


def render_html_summary(
    cfg: Config, run_id: str, out_dir: Path, reports_dir: Path, top_n: int = 10
) -> Path:
    """Render ``<reports>/<run_id>_summary.html`` from a finished run's outputs.

    Raises:
        FileNotFoundError: if the run has not been ranked yet.
    """
    ranked_path = out_dir / "ranked_compounds.csv"
    if not ranked_path.is_file():
        raise FileNotFoundError(
            f"No ranked_compounds.csv in {out_dir}; run 'npcadd rank' (or run-all) first."
        )
    ranked = pd.read_csv(ranked_path, keep_default_na=False)

    docking_path = out_dir / "docking_results.csv"
    if docking_path.is_file():
        dock = pd.read_csv(docking_path, keep_default_na=False)
        extra = [
            c for c in ("placement_ok", "consensus_ok", "top2_pose_rmsd_a") if c in dock.columns
        ]
        if extra and {"ligand_id", "receptor_id"} <= set(dock.columns):
            ranked = ranked.merge(
                dock[["ligand_id", "receptor_id", *extra]],
                on=["ligand_id", "receptor_id"],
                how="left",
            )

    prov = _read_json(out_dir / "provenance.json")
    git = prov.get("git") or {}
    config_block = prov.get("config") or {}
    warnings = prov.get("warnings") or []

    top = ranked[ranked["rank"] <= top_n] if "rank" in ranked.columns else ranked.head(top_n)
    present = [(k, label) for k, label in _TABLE_COLUMNS if k in top.columns]
    header = "".join(f"<th>{_esc(label)}</th>" for _, label in present)
    body = "".join(
        "<tr>" + "".join(f"<td>{_cell(k, row.get(k, ''))}</td>" for k, _ in present) + "</tr>"
        for _, row in top.iterrows()
    )
    table = f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"

    caveats_html = ""
    if warnings:
        items = "".join(f"<li>{_esc(w)}</li>" for w in warnings)
        caveats_html = (
            "<div class='caveats'><strong>Caveats stamped on this run</strong>"
            f"<ul>{items}</ul></div>"
        )

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
    dirty = " (dirty)" if git.get("dirty") else ""
    html_doc = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>npcadd run {_esc(run_id)}</title><style>{_CSS}</style></head>\n"
        "<body>\n"
        "<h1>npcadd run summary</h1>\n"
        f'<p class="meta">Run <code>{_esc(run_id)}</code> &middot; study '
        f"<code>{_esc(cfg.study)}</code> &middot; npcadd "
        f"{_esc(prov.get('npcadd_version', '?'))} &middot; git "
        f"<code>{_esc(str(git.get('commit', 'unavailable'))[:12])}</code>{dirty} &middot; "
        f"config <code>{_esc(str(config_block.get('hash', '?'))[:12])}</code> &middot; "
        f"generated {generated}</p>\n"
        f"{caveats_html}\n"
        f"<h2>Top {len(top)} ranked compound-target pairs</h2>\n"
        '<p class="meta">Ranked by the efficiency-corrected multi-criteria score, never by '
        "raw affinity. A Vina score is a ranking signal, not a binding affinity.</p>\n"
        f"{table}\n"
        '<p class="meta">Read-only view of <code>ranked_compounds.csv</code> and '
        "<code>docking_results.csv</code>. See <code>VALIDATION.md</code> and "
        "<code>SUPPLEMENTARY_INFORMATION.md</code> for the validation gate and full "
        "methods.</p>\n"
        "</body></html>\n"
    )

    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{run_id}_summary.html"
    out.write_text(html_doc, encoding="utf-8")
    log.info("Wrote HTML summary", extra={"path": out.as_posix(), "n_rows": len(top)})
    return out
