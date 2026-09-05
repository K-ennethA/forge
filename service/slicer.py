"""Wrapping the OrcaSlicer command line: an STL or 3MF in, G-code out.

This is the last step of the PartForge pipeline.  ``/export`` and
``/export_segments`` write files a slicer can open; ``/slice`` hands one of them
to the slicer that is actually installed and brings back the G-code.

Nothing in here imports build123d.  Slicing is a subprocess call, not a geometry
job, so it runs in the HTTP process rather than the warm worker -- there is
nothing for the worker to keep warm and no OCC state to serialise behind.

Finding the slicer
------------------
Four sources, in order, and the first one that exists on disk wins:

1. ``slicer_path`` in the request,
2. the ``FORGE_SLICER`` environment variable,
3. the standard install locations in :data:`INSTALL_CANDIDATES`,
4. the ``PATH``.

When none of them hits, the failure is a structured ``400`` that lists every
path probed and says how to point the service at a real install -- never a
traceback about a missing file.  ``GET /health`` reports the same detection
result so a panel can grey out its *Slice* button before the user presses it.

The command line
----------------
Every flag below was verified against **OrcaSlicer 2.3.2** on Windows by
running the real binary; the notes say what each one actually did.  They live in
:data:`CLI` rather than inline so that a version which renames one is a
one-line fix here instead of a hunt through the module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import ParamError, ScriptError

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


#: Wall-clock budget for one slice, seconds.  Slicing a big plate at a fine
#: layer height is minutes of work, so this is far longer than any geometry job.
SLICE_TIMEOUT_S: float = _env_float("FORGE_SLICE_TIMEOUT", 600.0)

#: How much of stdout / stderr comes back in the response, in characters.
OUTPUT_TAIL_CHARS = 4000

#: Environment variable holding an explicit slicer executable.
SLICER_ENV_VAR = "FORGE_SLICER"


# --------------------------------------------------------------------------
# The command line, in one place
# --------------------------------------------------------------------------

#: OrcaSlicer's CLI, verified by running ``orca-slicer.exe`` 2.3.2 directly.
#:
#: * ``--debug <n>`` -- OrcaSlicer is a GUI-subsystem binary and prints *nothing*
#:   without it.  With ``--debug 2`` it logs warnings and errors to stdout, which
#:   is what makes ``stdout_tail`` worth reading.  (``--version`` is *not* a
#:   valid option; the version comes out in the first ``--debug`` line.)
#: * ``--load-settings "<machine.json>;<process.json>"`` -- semicolon-separated,
#:   one string.  A missing file is a clean non-zero exit with the path in
#:   stderr.
#: * ``--slice 0`` -- slice all plates.  The G-code is named by the slicer
#:   (``plate_1.gcode``), *not* by us: there is no "write it here" flag, only
#:   ``--outputdir``, which is why :func:`run_slice` slices into a scratch
#:   directory and moves the result to the requested path.
#: * ``--export-3mf <bare filename>`` -- the value is joined onto
#:   ``--outputdir``, so it must be a plain file name; an absolute path produces
#:   "Unable to open the file <dir>/<abs path>".
#:
#: **Do not combine ``--slice`` with ``--export-3mf``**: 2.3.2 writes the G-code
#: and then dies with an access violation (0xC0000005).  The output extension
#: picks one or the other, which is why they are separate entries here.
CLI: Dict[str, str] = {
    "debug": "--debug",
    "debug_level": "2",
    "load_settings": "--load-settings",
    "load_filaments": "--load-filaments",
    "settings_separator": ";",
    "slice": "--slice",
    "slice_all_plates": "0",
    "export_3mf": "--export-3mf",
    "outputdir": "--outputdir",
}

#: Output extensions we know how to ask for, mapped to the strategy used.
OUTPUT_KINDS: Dict[str, str] = {
    ".gcode": "slice",
    ".gco": "slice",
    ".3mf": "project",
}

#: Model formats the slicer will open.  Exactly what ``/export`` writes.
INPUT_SUFFIXES: Tuple[str, ...] = (".stl", ".3mf", ".step", ".stp", ".obj")


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def _expand(template: str) -> str:
    return os.path.expandvars(os.path.expanduser(template))


#: Standard install locations, probed in order.  ``flavor`` is what the profile's
#: ``slicer`` key calls it, so a profile saying ``"slicer": "elegooslicer"``
#: promotes those entries to the front of the queue.
INSTALL_CANDIDATES: Tuple[Dict[str, str], ...] = (
    # OrcaSlicer, the CLI this wrapper targets.
    {"flavor": "orcaslicer", "path": r"%ProgramFiles%\OrcaSlicer\orca-slicer.exe"},
    {"flavor": "orcaslicer", "path": r"%ProgramFiles(x86)%\OrcaSlicer\orca-slicer.exe"},
    {"flavor": "orcaslicer", "path": r"%LOCALAPPDATA%\Programs\OrcaSlicer\orca-slicer.exe"},
    {"flavor": "orcaslicer", "path": r"%LOCALAPPDATA%\OrcaSlicer\orca-slicer.exe"},
    # Elegoo Slicer is an OrcaSlicer fork and takes the same flags.  The
    # executable has been shipped under three different spellings.
    {"flavor": "elegooslicer", "path": r"%ProgramFiles%\ElegooSlicer\elegoo-slicer.exe"},
    {"flavor": "elegooslicer", "path": r"%ProgramFiles%\Elegoo Slicer\elegoo-slicer.exe"},
    {"flavor": "elegooslicer", "path": r"%ProgramFiles%\ELEGOO\ElegooSlicer\elegoo-slicer.exe"},
    {"flavor": "elegooslicer", "path": r"%LOCALAPPDATA%\Programs\ElegooSlicer\elegoo-slicer.exe"},
    {"flavor": "elegooslicer", "path": r"%LOCALAPPDATA%\ElegooSlicer\elegoo-slicer.exe"},
    # Linux / macOS, for completeness.  Windows is the primary platform.
    {"flavor": "orcaslicer", "path": "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"},
    {"flavor": "orcaslicer", "path": "/usr/bin/orca-slicer"},
    {"flavor": "orcaslicer", "path": "/usr/local/bin/orca-slicer"},
)

#: Names looked up on ``PATH`` when no install location matched.
PATH_NAMES: Tuple[Tuple[str, str], ...] = (
    ("orcaslicer", "orca-slicer"),
    ("orcaslicer", "orcaslicer"),
    ("elegooslicer", "elegoo-slicer"),
    ("elegooslicer", "elegooslicer"),
)


class SlicerNotFoundError(ScriptError):
    """No slicer executable anywhere -> 400 carrying the whole probe report.

    The payload keeps the contract's ``error`` / ``traceback`` and adds a
    ``slicer`` object, so a caller that only knows the contract still shows a
    sensible message and one that knows about this endpoint can render the list
    of paths it tried.
    """

    def __init__(self, message: str, detection: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.detection = dict(detection)

    def to_payload(self) -> Dict[str, Any]:
        payload = super().to_payload()
        payload["slicer"] = self.detection
        return payload


class SlicerFailedError(ScriptError):
    """The slicer ran and refused -> 400 carrying its own diagnosis."""

    def __init__(self, message: str, detail: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.detail = dict(detail)

    def to_payload(self) -> Dict[str, Any]:
        payload = super().to_payload()
        payload["slicer"] = self.detail
        return payload


def _probe(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path)
    except OSError:  # pragma: no cover - defensive on exotic paths
        return False


def detect_slicer(
    explicit: Optional[str] = None,
    prefer: Optional[str] = None,
) -> Dict[str, Any]:
    """Find a slicer executable and report everything that was tried.

    Never raises for a missing slicer: the result carries ``found: False`` and
    the full ``probed`` list, and the caller decides whether that is fatal.  An
    *explicit* path that does not exist is the one hard error, because the user
    asked for that file by name and a silent fall-through to some other install
    would be worse than a complaint.
    """
    probed: List[Dict[str, Any]] = []

    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise ParamError("slicer_path must be a non-empty absolute path")
        candidate = _expand(explicit.strip())
        exists = _probe(candidate)
        probed.append({"source": "request.slicer_path", "path": candidate, "exists": exists})
        if not exists:
            raise ParamError(
                f"slicer_path {candidate!r} does not exist. Point it at the "
                "slicer executable itself (for OrcaSlicer that is "
                r"C:\Program Files\OrcaSlicer\orca-slicer.exe)."
            )
        return _found(candidate, "request.slicer_path", prefer or _flavor_of(candidate), probed)

    from_env = os.environ.get(SLICER_ENV_VAR)
    if from_env:
        candidate = _expand(from_env.strip())
        exists = _probe(candidate)
        probed.append({"source": f"${SLICER_ENV_VAR}", "path": candidate, "exists": exists})
        if exists:
            return _found(candidate, f"${SLICER_ENV_VAR}", _flavor_of(candidate), probed)

    order = list(INSTALL_CANDIDATES)
    if prefer:
        wanted = str(prefer).strip().lower().replace(" ", "")
        # A profile that names its slicer gets that flavour looked at first;
        # everything else keeps its order behind it.
        order.sort(key=lambda entry: 0 if entry["flavor"] == wanted else 1)

    for entry in order:
        candidate = _expand(entry["path"])
        exists = _probe(candidate)
        probed.append({"source": "install", "path": candidate, "exists": exists})
        if exists:
            return _found(candidate, "install", entry["flavor"], probed)

    for flavor, name in PATH_NAMES:
        candidate = shutil.which(name)
        probed.append(
            {"source": "PATH", "path": candidate or name, "exists": bool(candidate)}
        )
        if candidate:
            return _found(candidate, "PATH", flavor, probed)

    return {
        "found": False,
        "path": None,
        "source": None,
        "flavor": None,
        "probed": probed,
        "configure": (
            "No slicer was found. Install OrcaSlicer, or set the "
            f"{SLICER_ENV_VAR} environment variable to the executable, or pass "
            '"slicer_path" in the request body.'
        ),
    }


def _flavor_of(path: str) -> str:
    stem = Path(path).stem.lower().replace("-", "").replace("_", "")
    if "elegoo" in stem:
        return "elegooslicer"
    if "orca" in stem:
        return "orcaslicer"
    return "unknown"


def _found(
    path: str, source: str, flavor: Optional[str], probed: List[Dict[str, Any]]
) -> Dict[str, Any]:
    return {
        "found": True,
        "path": path,
        "source": source,
        "flavor": flavor or _flavor_of(path),
        "probed": probed,
    }


def health_detection() -> Dict[str, Any]:
    """The compact detection summary ``GET /health`` carries.

    Only the paths that were *not* there are worth listing once one was found,
    so a healthy machine gets a two-line answer instead of a wall of misses.
    """
    try:
        result = detect_slicer()
    except ParamError as exc:  # pragma: no cover - only an explicit path raises
        return {"found": False, "path": None, "error": exc.message}

    summary = {
        "found": result["found"],
        "path": result["path"],
        "source": result["source"],
        "flavor": result["flavor"],
    }
    if not result["found"]:
        summary["probed"] = [entry["path"] for entry in result["probed"]]
        summary["configure"] = result["configure"]
    return summary


# --------------------------------------------------------------------------
# Request validation
# --------------------------------------------------------------------------


def resolve_input(path: Any) -> Path:
    """The model file to slice: absolute, existing, and a format Orca opens."""
    if not isinstance(path, str) or not path.strip():
        raise ParamError("input is required and must be an absolute path to a model file")
    candidate = Path(path.strip())
    if not candidate.is_absolute():
        raise ParamError(
            f"input must be absolute, got {path!r}; the service does not guess a "
            "working directory"
        )
    if not candidate.is_file():
        raise ParamError(
            f"input {str(candidate)!r} does not exist. Write it first with /export "
            "or /export_segments."
        )
    if candidate.suffix.lower() not in INPUT_SUFFIXES:
        raise ParamError(
            f"input {candidate.name!r} is not a model the slicer opens; use one of "
            f"{', '.join(INPUT_SUFFIXES)}"
        )
    return candidate


def resolve_slice_output(path: Any) -> Tuple[Path, str]:
    """The G-code (or project) to write, plus which strategy that implies."""
    if not isinstance(path, str) or not path.strip():
        raise ParamError(
            "output is required and must be an absolute .gcode or .3mf path"
        )
    candidate = Path(path.strip())
    if not candidate.is_absolute():
        raise ParamError(f"output must be absolute, got {path!r}")
    if candidate.is_dir():
        raise ParamError(f"output {str(candidate)!r} is an existing directory")

    suffix = candidate.suffix.lower()
    if not suffix:
        candidate = candidate.with_suffix(".gcode")
        suffix = ".gcode"
    kind = OUTPUT_KINDS.get(suffix)
    if kind is None:
        raise ParamError(
            f"output {candidate.name!r} has an extension the slicer does not "
            f"produce; use one of {', '.join(sorted(OUTPUT_KINDS))}"
        )
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ParamError(
            f"cannot create the output directory {str(candidate.parent)!r}: {exc}"
        ) from exc
    return candidate, kind


def resolve_profiles(profile: Any) -> List[str]:
    """Zero or more Orca ``.json`` profiles, given as a path or a list of them."""
    if profile is None:
        return []
    items = profile if isinstance(profile, (list, tuple)) else [profile]
    resolved: List[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            raise ParamError("profile entries must be paths to OrcaSlicer .json profiles")
        candidate = Path(_expand(item.strip()))
        if not candidate.is_absolute():
            raise ParamError(f"profile must be absolute, got {item!r}")
        if not candidate.is_file():
            raise ParamError(
                f"profile {str(candidate)!r} does not exist. OrcaSlicer's own "
                "profiles live under resources/profiles in its install directory."
            )
        # Orca joins these with ';', so a path containing one cannot be passed.
        if CLI["settings_separator"] in str(candidate):
            raise ParamError(
                f"profile path {str(candidate)!r} contains "
                f"{CLI['settings_separator']!r}, which the slicer uses as its own "
                "separator; move the file somewhere without it"
            )
        resolved.append(str(candidate))
    return resolved


def resolve_extra_args(extra: Any) -> List[str]:
    if extra is None:
        return []
    if not isinstance(extra, (list, tuple)):
        raise ParamError("extra_args must be a list of strings")
    args: List[str] = []
    for item in extra:
        if not isinstance(item, str):
            raise ParamError(f"extra_args entries must be strings, got {item!r}")
        args.append(item)
    return args


# --------------------------------------------------------------------------
# Building and running the command
# --------------------------------------------------------------------------


def launcher(executable: str) -> List[str]:
    """The argv prefix that runs *executable*.

    A real slicer is an ``.exe`` and runs directly.  A ``.py`` is run under this
    interpreter, which is how the tests point ``slicer_path`` at a stub -- and
    is genuinely useful for wrapping a slicer in a script of your own.
    """
    if executable.lower().endswith(".py"):
        return [sys.executable, executable]
    return [executable]


def build_argv(
    executable: str,
    input_path: Path,
    workdir: Path,
    output_name: str,
    kind: str,
    profiles: Sequence[str],
    filaments: Sequence[str] = (),
    extra_args: Sequence[str] = (),
) -> List[str]:
    """Assemble the slicer command line.  See :data:`CLI` for every flag."""
    argv = launcher(executable)
    argv += [CLI["debug"], CLI["debug_level"]]

    if profiles:
        argv += [CLI["load_settings"], CLI["settings_separator"].join(profiles)]
    if filaments:
        argv += [CLI["load_filaments"], CLI["settings_separator"].join(filaments)]

    if kind == "project":
        # Bare file name on purpose: Orca appends it to --outputdir.
        argv += [CLI["export_3mf"], output_name]
    else:
        argv += [CLI["slice"], CLI["slice_all_plates"]]

    argv += [CLI["outputdir"], str(workdir)]
    argv += list(extra_args)
    argv += [str(input_path)]
    return argv


def _tail(text: Optional[str]) -> str:
    if not text:
        return ""
    return text[-OUTPUT_TAIL_CHARS:]


def _signed(code: Optional[int]) -> Optional[int]:
    """Windows reports ``-13`` as ``4294967283``; show the number people know."""
    if code is None:
        return None
    return code - 0x100000000 if code > 0x7FFFFFFF else code


def _collect_output(workdir: Path, wanted_name: str, kind: str) -> Optional[Path]:
    """Find what the slicer actually wrote.

    ``--slice`` names its own file (``plate_1.gcode``), so the requested name is
    only a hint.  Preference order: the exact name, then the newest file with
    the right extension, then the newest file of any kind.
    """
    exact = workdir / wanted_name
    if exact.is_file():
        return exact

    suffixes = tuple(
        suffix for suffix, strategy in OUTPUT_KINDS.items() if strategy == kind
    )
    produced = [entry for entry in workdir.iterdir() if entry.is_file()]
    if not produced:
        return None

    matching = [entry for entry in produced if entry.suffix.lower() in suffixes]
    pool = matching or produced
    return max(pool, key=lambda entry: entry.stat().st_mtime)


def run_slice(
    input_path: Any,
    output_path: Any,
    profile: Any = None,
    filaments: Any = None,
    slicer_path: Optional[str] = None,
    printer: Optional[Mapping[str, Any]] = None,
    extra_args: Any = None,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """Slice one model file and return where the G-code landed.

    Raises :class:`SlicerNotFoundError` when there is no slicer to run,
    :class:`SlicerFailedError` when there is one and it refused (non-zero exit,
    a timeout, or a silent run that produced no file).  Both are 400s: the
    caller can fix all three by changing the request or installing something.
    """
    from .printer import normalize_printer  # noqa: PLC0415 - avoid an import cycle

    resolved_printer = normalize_printer(printer)
    source = resolve_input(input_path)
    target, kind = resolve_slice_output(output_path)
    profiles = resolve_profiles(profile)
    filament_profiles = resolve_profiles(filaments)
    extras = resolve_extra_args(extra_args)
    budget = float(timeout_s) if timeout_s else SLICE_TIMEOUT_S
    if budget <= 0:
        raise ParamError(f"timeout_s must be greater than zero, got {timeout_s!r}")

    detection = detect_slicer(
        explicit=slicer_path, prefer=str(resolved_printer.get("slicer") or "") or None
    )
    if not detection["found"]:
        raise SlicerNotFoundError(
            "no slicer executable was found, so there is nothing to slice with. "
            + str(detection["configure"]),
            detection,
        )

    executable = str(detection["path"])

    # Slice into a scratch directory and move the result: --outputdir is the
    # only placement flag OrcaSlicer has, and it names the G-code itself.
    workdir = Path(tempfile.mkdtemp(prefix="forge-slice-"))
    started = time.perf_counter()
    try:
        argv = build_argv(
            executable,
            source,
            workdir,
            target.name,
            kind,
            profiles,
            filament_profiles,
            extras,
        )

        creationflags = 0
        if sys.platform == "win32":
            # The ground rules: nothing this service does flashes a window.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            process = subprocess.Popen(  # noqa: S603 - argv is built above, never a shell string
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                cwd=str(workdir),
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
        except OSError as exc:
            raise SlicerFailedError(
                f"could not start the slicer at {executable!r}: {exc}",
                {"path": executable, "argv": argv, "error": str(exc)},
            ) from exc

        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=budget)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            try:
                stdout, stderr = process.communicate(timeout=15)
            except Exception:  # noqa: BLE001 - the child is already dead
                stdout, stderr = "", ""

        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        detail = {
            "path": executable,
            "flavor": detection["flavor"],
            "argv": argv,
            "returncode": _signed(process.returncode),
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
            "duration_ms": duration_ms,
        }

        if timed_out:
            raise SlicerFailedError(
                f"the slicer exceeded the {budget:.0f}s time limit and was stopped. "
                "Raise FORGE_SLICE_TIMEOUT, or slice a simpler plate.",
                {**detail, "timed_out": True},
            )

        if process.returncode != 0:
            raise SlicerFailedError(
                f"the slicer exited with code {_signed(process.returncode)}: "
                + (_tail(stderr).strip() or _tail(stdout).strip() or "no output"),
                detail,
            )

        produced = _collect_output(workdir, target.name, kind)
        if produced is None:
            raise SlicerFailedError(
                "the slicer reported success but wrote no file. Check that the "
                "profile matches the model's printer.",
                detail,
            )

        try:
            if target.exists():
                target.unlink()
            shutil.move(str(produced), str(target))
        except OSError as exc:
            raise SlicerFailedError(
                f"could not move the slicer's output to {str(target)!r}: {exc}",
                detail,
            ) from exc

        return {
            "output": str(target),
            "slicer": {
                "path": executable,
                "flavor": detection["flavor"],
                "source": detection["source"],
            },
            "produced_name": produced.name,
            "input": str(source),
            "profiles": profiles,
            "argv": argv,
            "returncode": _signed(process.returncode),
            "stdout_tail": _tail(stdout),
            "stderr_tail": _tail(stderr),
            "duration_ms": duration_ms,
            "size_bytes": target.stat().st_size,
            "printer": resolved_printer,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = [
    "CLI",
    "INSTALL_CANDIDATES",
    "OUTPUT_KINDS",
    "SLICE_TIMEOUT_S",
    "SLICER_ENV_VAR",
    "SlicerFailedError",
    "SlicerNotFoundError",
    "build_argv",
    "detect_slicer",
    "health_detection",
    "launcher",
    "resolve_extra_args",
    "resolve_input",
    "resolve_profiles",
    "resolve_slice_output",
    "run_slice",
]
