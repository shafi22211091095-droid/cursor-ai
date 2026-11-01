# Provenance of the bundled sample receptor

## What these files are

| File | What it is |
|---|---|
| `1IEP_abl1_receptor_protonated.pdb` | **A prepared derivative**, not the deposited PDB entry. Human ABL1 kinase domain (chain A, residues 225–498), hydrogens added, waters and the co-crystal ligand already removed. 4412 atoms. |
| `1IEP_cocrystal_STI_imatinib.sdf` | The crystallographic pose of the co-crystal ligand STI-571 (imatinib), 69 atoms, `C29H32N7O+` (piperazine N protonated, as in the deposit). Used as the **reference pose for redocking RMSD**. |
| `SHA256SUMS` | Checksums, verified by `npcadd prepare-receptor` on every run. |

## Where they came from

Both files were obtained from the `example/basic_docking/data/` directory of the
AutoDock Vina source distribution (`ccsb-scripps/AutoDock-Vina`), which
distributes them as the reference system for its own basic-docking tutorial.
They derive from **PDB entry 1IEP** — the ABL1 kinase domain in complex with
STI-571 — deposited by Nagar et al.

Protein Data Bank coordinate data carries no copyright restriction, so
redistribution here is unencumbered. The upstream tutorial files are
redistributed under AutoDock Vina's Apache-2.0 licence.

Why a mirror rather than RCSB: the environment in which this sample was
assembled had no outbound access to `files.rcsb.org` or `data.rcsb.org`. See
`LIMITATIONS.md`, "Structure retrieval in restricted environments".

## What is therefore *unavailable* — and is reported as such

The prepared file has had its PDB header stripped. Consequently
`npcadd prepare-receptor` reports the following as `unavailable` in the receptor
provenance table, and **does not guess them**:

- resolution
- R-work / R-free
- experimental method
- deposition date
- UniProt accession
- co-crystal ligand residue identifiers parsed from `HETATM` records

On a machine with outbound access to RCSB, running `npcadd prepare-receptor`
with `allow_network: true` and no `local_structure` set will fetch the deposited
entry and populate every one of these fields from the structure header and the
RCSB REST API. **Do that before reporting anything from this receptor in a
manuscript**, because resolution and R-free are exactly the fields a reviewer
will want in order to judge whether the structure supports docking at all.

## Scientific standing of this receptor

Unlike the ligand abundances in `data/sample/*.csv`, **this receptor is real**.
It is a genuine, high-interest, experimentally determined kinase–inhibitor
complex, and it is the system AutoDock Vina itself uses to demonstrate that
redocking reproduces a crystallographic pose. That makes it suitable for a
*real* redocking validation: the RMSD gate in `npcadd validate-docking` is
measured against a true crystallographic pose, not a fixture.

It is bundled because it makes the validation stage demonstrable offline. It is
mapped to the **cytotoxic** activity in `config.sample.yaml`, which is
defensible — ABL1 is the target of imatinib in chronic myeloid leukaemia — but
note that ABL1 is a *specific oncogenic kinase*, not a general cytotoxicity
target. Whether it is the right target for your assay is a scientific question
your manuscript has to answer; see `DECISIONS.md`.
