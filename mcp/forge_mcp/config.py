"""Connection settings for both Forge backends.

Defaults match docs/architecture.md. Every value can be overridden with an
environment variable, which is how .mcp.json would point the server at a
non-default port without editing code.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_str(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_str(name, str(default)))
    except ValueError:
        return default


# --- Blender add-on socket (docs/architecture.md: TCP 127.0.0.1:9876) --------
BLENDER_HOST: str = _env_str("FORGE_BLENDER_HOST", "127.0.0.1")
BLENDER_PORT: int = _env_int("FORGE_BLENDER_PORT", 9876)

# Short, so "is Blender up?" fails fast instead of hanging a tool call.
BLENDER_CONNECT_TIMEOUT: float = _env_float("FORGE_BLENDER_CONNECT_TIMEOUT", 2.0)
# Generous, because remesh/quadriflow on a dense mesh blocks Blender's main thread.
BLENDER_READ_TIMEOUT: float = _env_float("FORGE_BLENDER_READ_TIMEOUT", 180.0)

# --- Geometry service HTTP (docs/architecture.md: 127.0.0.1:8765) -----------
SERVICE_HOST: str = _env_str("FORGE_SERVICE_HOST", "127.0.0.1")
SERVICE_PORT: int = _env_int("FORGE_SERVICE_PORT", 8765)
SERVICE_URL: str = _env_str(
    "FORGE_SERVICE_URL", f"http://{SERVICE_HOST}:{SERVICE_PORT}"
).rstrip("/")

SERVICE_CONNECT_TIMEOUT: float = _env_float("FORGE_SERVICE_CONNECT_TIMEOUT", 2.0)
SERVICE_READ_TIMEOUT: float = _env_float("FORGE_SERVICE_READ_TIMEOUT", 180.0)

# Phase 2 endpoints get their own budgets: the service allows 120 s for /check and
# 300 s for /segment /export_segments (FORGE_CHECK_TIMEOUT / FORGE_SEGMENT_TIMEOUT
# on its side), and a client that gives up first turns a slow-but-working job into
# a mystery.  These sit just above the service's own limits.
SERVICE_CHECK_TIMEOUT: float = _env_float("FORGE_SERVICE_CHECK_TIMEOUT", 150.0)
SERVICE_SEGMENT_TIMEOUT: float = _env_float("FORGE_SERVICE_SEGMENT_TIMEOUT", 330.0)

# --- meshgen HTTP (docs/architecture.md Phase 7: 127.0.0.1:8902) ------------
# Image-to-3D. Optional on any given machine: the models are an 18.5 GB
# download, so "not running" is a normal answer here, never an error to fix.
MESHGEN_HOST: str = _env_str("FORGE_MESHGEN_HOST", "127.0.0.1")
MESHGEN_PORT: int = _env_int("FORGE_MESHGEN_PORT", 8902)
MESHGEN_URL: str = _env_str(
    "FORGE_MESHGEN_URL", f"http://{MESHGEN_HOST}:{MESHGEN_PORT}"
).rstrip("/")

MESHGEN_CONNECT_TIMEOUT: float = _env_float("FORGE_MESHGEN_CONNECT_TIMEOUT", 2.0)
# /generate3d answers 202 straight away and /job is a dict read; only the job
# itself is slow, and that is waited out by polling, not by one long read.
MESHGEN_READ_TIMEOUT: float = _env_float("FORGE_MESHGEN_READ_TIMEOUT", 30.0)

# How long generate_3d(wait=True) follows a job before handing back the job id
# instead. Measured on the reference machine: 304 s (trellis2) / 249 s
# (pixal3d) plus ComfyUI's cold start, so 900 s is generous on purpose.
MESHGEN_JOB_TIMEOUT: float = _env_float("FORGE_MESHGEN_JOB_TIMEOUT", 900.0)
MESHGEN_POLL_INTERVAL: float = _env_float("FORGE_MESHGEN_POLL_INTERVAL", 3.0)

# Refuse to buffer a runaway response rather than eating all of RAM.
MAX_RESPONSE_BYTES: int = _env_int("FORGE_MAX_RESPONSE_BYTES", 256 * 1024 * 1024)

# --- printer profile --------------------------------------------------------
# The repo's templates/printer.json, resolved from this file so it works no
# matter what directory Claude Code launched the server from. /check and
# /segment fall back to the service's built-in Centauri Carbon profile when this
# file is missing, so a checkout without templates/ still works.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRINTER_PATH: str = _env_str(
    "FORGE_PRINTER_PATH", str(_REPO_ROOT / "templates" / "printer.json")
)

# --- maker mode (Phase 10) --------------------------------------------------
# The one backend this server does NOT reach over a wire. service/components.py,
# service/wiring.py and the arithmetic half of service/maker_lib.py are imported
# into this process (forge_mcp/maker.py says why at length): there is no HTTP
# endpoint for them, they are dependency-free, and a second copy of the resistor
# maths would drift from the one the part scripts actually run.
#
# This is the folder that CONTAINS service/, i.e. the repo root — it goes on
# sys.path so `import service.wiring` resolves. The import is lazy and guarded,
# so a checkout without service/ still runs every other tool.
SERVICE_PACKAGE_ROOT: str = _env_str("FORGE_SERVICE_PACKAGE_ROOT", str(_REPO_ROOT))

# --- part projects ----------------------------------------------------------
# Where partforge_new_part is allowed to write, and the ONLY place it writes:
# one folder per part under projects/<slug>/ holding part.py and spec.json
# (docs/architecture.md, "spec.json / printer.json / character.json").
PROJECTS_DIR: str = _env_str("FORGE_PROJECTS_DIR", str(_REPO_ROOT / "projects"))

# The printer profile a generated spec.json points at. Relative on purpose: a
# spec is a repo document, and templates/printer.json is where it lives.
SPEC_PRINTER_REF: str = "templates/printer.json"

# --- flows ------------------------------------------------------------------
# Saved, parameterised sequences of Forge operations (docs/architecture.md,
# "Phase 6b"). The second and last place this server writes, and it writes
# exactly one file shape: flows/<slug>.json. The Blender add-on has its own
# `forge_flows_dir` preference pointing at the same folder.
FLOWS_DIR: str = _env_str("FORGE_FLOWS_DIR", str(_REPO_ROOT / "flows"))

# A flow can contain a /segment (300 s on the service) plus mesh loading, and it
# runs through one Blender socket call, so it needs its own budget rather than
# the per-command default.
FLOW_RUN_TIMEOUT: float = _env_float("FORGE_FLOW_RUN_TIMEOUT", 900.0)

# --- previews ---------------------------------------------------------------
# Where render_preview drops the PNGs the model then Reads. Scratch by design:
# they are how the model SEES what it made, not artefacts the artist keeps, so
# they go to the system temp folder and never into the repo or a project.
# Every render gets its own filename rather than overwriting the last one — a
# second preview is a comparison with the first, and comparing needs both.
PREVIEWS_DIR: str = _env_str(
    "FORGE_PREVIEWS_DIR", str(Path(tempfile.gettempdir()) / "forge-previews")
)

# A Workbench render of an ordinary part is well under a second; the budget is
# for a dense import at 2048 px, and it sits inside BLENDER_READ_TIMEOUT.
PREVIEW_TIMEOUT: float = _env_float("FORGE_PREVIEW_TIMEOUT", 180.0)


def blender_address() -> str:
    return f"{BLENDER_HOST}:{BLENDER_PORT}"


def service_address() -> str:
    return SERVICE_URL


def meshgen_address() -> str:
    return MESHGEN_URL
