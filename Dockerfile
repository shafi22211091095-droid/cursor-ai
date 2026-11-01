# npcadd analysis image.
#
# ============================= DIGEST PINNING =============================
# This base image MUST be pinned by digest before the pipeline is used for a
# manuscript. A tag is mutable: `python:3.11-slim-bookworm` resolves to a
# different image after every upstream rebuild, so a tag-based build is not
# reproducible over time even though it looks like it is.
#
# It is NOT pinned here because the environment this repository was assembled
# in had no outbound access to a container registry, so no digest could be
# resolved -- and npcadd does not write down a checksum it has not verified.
# See LIMITATIONS.md, "Container digest is unpinned".
#
# Pin it with one command, which rewrites the FROM line below in place:
#
#     make docker-pin
#
# or manually:
#
#     docker pull python:3.11-slim-bookworm
#     docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim-bookworm
#     # then replace the FROM line with: FROM python@sha256:<digest> AS base
#
# Record the pinned digest and the date in DECISIONS.md when you do.
# ==========================================================================
ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE} AS base

# Interpreter-level determinism: a fixed hash seed and no .pyc writes, so the
# container's behaviour does not depend on PYTHONHASHSEED randomisation.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1 \
    # Single-threaded BLAS: multi-threaded reductions are not bit-reproducible,
    # and npcadd parallelises at the docking-job level instead.
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    MPLBACKEND=Agg \
    TZ=UTC

# Build-time only: compilers for any wheel without a manylinux build, plus the
# runtime shared libraries RDKit and matplotlib need.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        libxrender1 \
        libxext6 \
        libsm6 \
        libfreetype6 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# uv, pinned by version.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /work

# Dependencies first, from the lockfile, so the layer caches independently of
# source changes. --frozen fails if uv.lock and pyproject.toml disagree, which
# is what we want: the lockfile is the contract.
COPY pyproject.toml uv.lock README.md ./
RUN uv venv -p 3.11 /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv sync --frozen --no-install-project --extra dev
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv

# Source and configuration.
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config.sample.yaml config.smoke.yaml ./
COPY tests/ ./tests/
RUN uv pip install --no-deps -e .

# Directories the pipeline writes to. data/raw is mounted read-only at runtime.
RUN mkdir -p data/raw data/sample data/interim data/processed reports/figures runs

# Fail the build if the image cannot import the whole scientific stack: better
# to discover a broken image at build time than mid-reproduction.
RUN python -c "import npcadd, rdkit, meeko, vina, prolif, gemmi, dimorphite_dl, pdb2pqr; \
print('npcadd', npcadd.__version__, '| rdkit', rdkit.__version__, '| prolif', prolif.__version__)"

ENTRYPOINT ["npcadd"]
CMD ["--help"]
