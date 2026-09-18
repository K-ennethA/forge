#!/usr/bin/env python
"""Run the joint detector from *outside* its virtualenv, and cache the answer.

``detect_joints.py`` is the detector: it imports torch, imports UniRig, and can
only run under ``C:\\forge-models\\unirig\\.venv``.  This file is the thing that
*calls* it, and it is the opposite kind of code -- **stdlib only**, no torch, no
``bpy``, no import of anything under the UniRig checkout.  It spawns a child
process and reads a JSON file.

That process boundary is not an implementation detail, it is the licence
boundary.  UniRig ships under a licence Forge does not want to inherit, so
nothing that links it may ever share an address space with the add-on.  A
subprocess plus a JSON file cannot accidentally become a library import, which
is why the handoff is shaped this way (``README.md``, "Why a CLI and not a
service").  This module is therefore safe for Blender's own interpreter to
import, and the add-on does exactly that.

What it adds over calling the CLI by hand
-----------------------------------------
* **A diagnosis instead of a crash.**  :func:`diagnose` answers "can this
  machine detect joints?" in one call and *names* what is missing -- the
  checkout, the virtualenv, the checkpoint, the GPU -- with the command that
  would fix it.  :func:`detect` never raises for a missing install; it returns
  ``ok: False`` and a reason code the caller can branch on.  The one thing that
  must never happen is a rigging command dying inside a subprocess call.
* **One job at a time.**  Skeleton inference peaks near 8.5 GB on a 12 GB card.
  Two at once do not both fail cleanly, they thrash and then fail dirtily, so a
  lock file serialises them and the second caller is told to wait rather than
  finding out in CUDA.
* **A cache keyed on the mesh.**  Detection costs ~5 s and ~8.5 GB; re-tagging
  the same mesh should cost neither.  The key is the SHA-256 of the mesh file
  plus every argument that changes the answer, so a cache hit is a *provable*
  hit rather than a filename coincidence.
* **No console window.**  ``CREATE_NO_WINDOW`` on Windows: a rigging command
  must never flash a black box over the artist's viewport.

Usage as a CLI (any Python 3.8+, *not* the UniRig venv)::

    python rigbridge\\detector_runner.py --input mesh.glb --output joints.json
    python rigbridge\\detector_runner.py --diagnose

Environment overrides: ``FORGE_UNIRIG_ROOT`` (checkout), ``FORGE_UNIRIG_PYTHON``
(interpreter), ``FORGE_UNIRIG_WEIGHTS`` (checkpoint), ``FORGE_RIGBRIDGE_CACHE``
(cache directory), ``FORGE_RIGBRIDGE_DISABLE`` (set to 1 to make every install
look absent -- what the test suite uses to pin the degrade path).
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

__all__ = [
    "SCHEMA",
    "REASONS",
    "DEFAULT_TIMEOUT",
    "cache_dir",
    "mesh_key",
    "diagnose",
    "detect",
]

SCHEMA = "forge.joints/1"

HERE = os.path.dirname(os.path.abspath(__file__))
DETECT_SCRIPT = os.path.join(HERE, "detect_joints.py")

DEFAULT_UNIRIG_ROOT = r"C:\forge-models\unirig"
DEFAULT_WEIGHTS = os.path.join(
    "weights", "skeleton", "articulation-xl_quantization_256", "model.ckpt")

#: Detection on the reference card is ~5 s; the first call of a session also
#: pays torch's import and the checkpoint load.  Ten minutes is "something is
#: wrong", not "this mesh is big".
DEFAULT_TIMEOUT = 600.0

#: A stale lock is one whose owner is gone.  We cannot portably ask "is pid N
#: alive" for a pid in another session, so the lock also carries a deadline:
#: a lock older than one detector timeout plus slack is abandoned.
LOCK_STALE_SECONDS = DEFAULT_TIMEOUT + 120.0

SUPPORTED_SUFFIXES = ("obj", "fbx", "dae", "glb", "gltf", "vrm")

#: Every way this can fail, as a stable code the caller may branch on.  The
#: human sentence lives next to it in the result; the code is what tests pin.
REASONS = (
    "ok",
    "disabled",          # FORGE_RIGBRIDGE_DISABLE=1
    "runner_missing",    # detect_joints.py is not next to this file
    "unirig_missing",    # no checkout
    "python_missing",    # no virtualenv interpreter
    "weights_missing",   # no checkpoint
    "gpu_unavailable",   # nvidia-smi says no
    "mesh_missing",      # the input does not exist
    "mesh_unsupported",  # wrong suffix
    "busy",              # another detection holds the lock
    "timeout",
    "detector_failed",   # non-zero exit, or no output file
    "bad_output",        # the JSON is not forge.joints/1
)


# ---------------------------------------------------------------------------
# where things are
# ---------------------------------------------------------------------------

def _env(name, default=""):
    value = os.environ.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else default


def unirig_root():
    return os.path.abspath(_env("FORGE_UNIRIG_ROOT", DEFAULT_UNIRIG_ROOT))


def venv_python(root=None):
    """The interpreter ``detect_joints.py`` must run under."""
    override = _env("FORGE_UNIRIG_PYTHON")
    if override:
        return os.path.abspath(override)
    root = root or unirig_root()
    if os.name == "nt":
        return os.path.join(root, ".venv", "Scripts", "python.exe")
    return os.path.join(root, ".venv", "bin", "python")


def weights_path(root=None):
    override = _env("FORGE_UNIRIG_WEIGHTS")
    if override:
        return os.path.abspath(override)
    return os.path.join(root or unirig_root(), DEFAULT_WEIGHTS)


def cache_dir():
    """Where detections are remembered.  Never inside the repo.

    Out of tree on purpose: a cache is machine state, not source, and a
    multi-megabyte JSON per mesh appearing in ``git status`` is how a cache
    becomes a commit.
    """
    override = _env("FORGE_RIGBRIDGE_CACHE")
    if override:
        return os.path.abspath(override)
    base = _env("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "Forge", "rigbridge", "cache")


def _gpu_present():
    """``(ok, detail)`` from nvidia-smi.  Telemetry, never fatal by itself."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
            creationflags=_no_window_flags())
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "nvidia-smi could not be run (%s)" % exc
    if proc.returncode != 0:
        return False, "nvidia-smi exited %d: %s" % (
            proc.returncode, (proc.stderr or "").strip()[:200])
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return False, "nvidia-smi listed no GPUs"
    return True, line[0].strip()


def _no_window_flags():
    """``CREATE_NO_WINDOW`` on Windows, 0 elsewhere.

    Without it every detection flashes a console over whatever the artist is
    looking at, which is the kind of thing that makes a pipeline feel broken
    even when it works.
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


# ---------------------------------------------------------------------------
# the diagnosis
# ---------------------------------------------------------------------------

def diagnose(check_gpu=True):
    """Can this machine detect joints?  A dict, never an exception.

    ``installed`` is the question the add-on actually asks; ``reason`` names the
    first missing piece and ``says`` is the sentence to put in a warning.
    """
    root = unirig_root()
    python = venv_python(root)
    weights = weights_path(root)
    out = {
        "installed": False,
        "reason": "ok",
        "unirig_root": root,
        "python": python,
        "weights": weights,
        "runner": DETECT_SCRIPT,
        "cache": cache_dir(),
        "gpu": None,
        "gpu_detail": None,
        "says": "",
    }

    if _env("FORGE_RIGBRIDGE_DISABLE") in ("1", "true", "yes", "on"):
        out["reason"] = "disabled"
        out["says"] = ("Joint detection is switched off by FORGE_RIGBRIDGE_DISABLE. "
                       "Unset it to use the detector.")
        return out
    if not os.path.isfile(DETECT_SCRIPT):
        out["reason"] = "runner_missing"
        out["says"] = ("The detector script %s is missing, so this checkout cannot run "
                       "joint detection at all." % DETECT_SCRIPT)
        return out
    if not os.path.isdir(os.path.join(root, "src")):
        out["reason"] = "unirig_missing"
        out["says"] = ("No UniRig checkout at %s (no src/). Install it there, or set "
                       "FORGE_UNIRIG_ROOT." % root)
        return out
    if not os.path.isfile(python):
        out["reason"] = "python_missing"
        out["says"] = ("UniRig's virtualenv interpreter is not at %s. The detector must "
                       "run under its own venv (torch, and a bpy wheel that must never "
                       "share an interpreter with Blender's); create it, or set "
                       "FORGE_UNIRIG_PYTHON." % python)
        return out
    if not os.path.isfile(weights):
        out["reason"] = "weights_missing"
        out["says"] = ("The skeleton checkpoint is not at %s. Fetch it from the "
                       "VAST-AI/UniRig repo, or set FORGE_UNIRIG_WEIGHTS." % weights)
        return out

    if check_gpu:
        ok, detail = _gpu_present()
        out["gpu"] = ok
        out["gpu_detail"] = detail
        if not ok:
            out["reason"] = "gpu_unavailable"
            out["says"] = ("UniRig is installed but no CUDA GPU answered: %s. Skeleton "
                           "inference peaks near 8.5 GB and there is no CPU path worth "
                           "offering." % detail)
            return out

    out["installed"] = True
    out["says"] = "Joint detection is available (%s)." % (out["gpu_detail"] or "GPU unchecked")
    return out


# ---------------------------------------------------------------------------
# the cache
# ---------------------------------------------------------------------------

def _file_digest(path, chunk=1 << 20):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def mesh_key(mesh, seed, faces_target_count, unit_scale, weights):
    """The cache key: the mesh's own bytes plus everything that changes the answer.

    Hashing the *contents* rather than the path is the whole point.  A mesh
    re-exported after an edit lands on a different key even though the filename
    is identical, and two different paths holding the same bytes share one
    detection.  The checkpoint's identity is in the key too, because a new
    checkpoint is a new answer to the same question.
    """
    digest = hashlib.sha256()
    digest.update(_file_digest(mesh).encode("ascii"))
    weights_id = ""
    try:
        stat = os.stat(weights)
        weights_id = "%s:%d" % (os.path.basename(weights), stat.st_size)
    except OSError:
        weights_id = os.path.basename(weights or "")
    for part in (SCHEMA, weights_id, "seed=%d" % int(seed),
                 "faces=%d" % int(faces_target_count),
                 "unit=%.6f" % float(unit_scale)):
        digest.update(b"\x00")
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _cache_read(key):
    path = os.path.join(cache_dir(), key + ".json")
    if not os.path.isfile(path):
        return None, path
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None, path
    if not isinstance(data, dict) or not isinstance(data.get("joints"), list):
        return None, path
    return data, path


def _cache_write(key, document):
    directory = cache_dir()
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, key + ".json")
        # Write-then-rename: a half-written cache entry read by the next call
        # would be a corrupt detection that looks like a good one.
        handle, temp = tempfile.mkstemp(dir=directory, suffix=".part")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
        os.replace(temp, path)
        return path
    except OSError:
        return None


# ---------------------------------------------------------------------------
# the lock
# ---------------------------------------------------------------------------

class _Lock(object):
    """One detection at a time, machine-wide.  Advisory, and it expires.

    ``O_CREAT | O_EXCL`` is the only cross-process atomic primitive available
    without a dependency.  The file carries a pid and a deadline so a run killed
    mid-flight costs one wait, not a permanently poisoned machine.
    """

    def __init__(self, path=None):
        self.path = path or os.path.join(cache_dir(), "detector.lock")
        self.held = False
        self.owner = None

    def acquire(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except OSError:
            pass
        for attempt in (0, 1):
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    # An unwritable lock directory must not block detection:
                    # serialisation is a courtesy, correctness is not at stake.
                    self.held = True
                    return True
                if attempt == 0 and self._reclaim_if_stale():
                    continue
                return False
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "started": time.time()}, stream)
            self.held = True
            return True
        return False

    def _reclaim_if_stale(self):
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                data = json.load(stream)
            self.owner = data
            age = time.time() - float(data.get("started") or 0.0)
        except (OSError, ValueError, TypeError):
            age = LOCK_STALE_SECONDS + 1.0  # unreadable is as dead as expired
        if age <= LOCK_STALE_SECONDS:
            return False
        try:
            os.remove(self.path)
        except OSError:
            return False
        return True

    def release(self):
        if not self.held:
            return
        self.held = False
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def _fail(reason, says, **extra):
    out = {"ok": False, "reason": reason, "says": says, "joints": None,
           "cached": False, "seconds": 0.0}
    out.update(extra)
    return out


def detect(mesh, output=None, seed=12345, faces_target_count=50000,
           unit_scale=1000.0, timeout=DEFAULT_TIMEOUT, use_cache=True,
           check_gpu=True, refresh=False):
    """Detect joints in ``mesh``.  Returns a result dict; **never raises**.

    On success: ``{"ok": True, "joints": <forge.joints/1 document>, "cached":
    bool, "seconds": float, "path": <written file or None>}``.  On any failure:
    ``ok`` is False, ``reason`` is one of :data:`REASONS`, and ``says`` is a
    sentence naming what to do about it.

    The no-raise contract is the point.  This is called from inside a rigging
    command; a missing checkout, a busy GPU or a detector that segfaults must
    each cost that command a *warning and a fallback*, never a traceback.
    """
    started = time.perf_counter()
    mesh = os.path.abspath(mesh)
    if not os.path.isfile(mesh):
        return _fail("mesh_missing", "No mesh to detect joints in at %s." % mesh)
    suffix = mesh.rsplit(".", 1)[-1].lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return _fail("mesh_unsupported",
                     "The detector reads %s; %s is a .%s."
                     % (", ".join("." + s for s in SUPPORTED_SUFFIXES), mesh, suffix))

    install = diagnose(check_gpu=check_gpu)
    if not install["installed"]:
        return _fail(install["reason"], install["says"], install=install)

    key = None
    if use_cache:
        try:
            key = mesh_key(mesh, seed, faces_target_count, unit_scale,
                           install["weights"])
        except OSError:
            key = None
    if key and not refresh:
        cached, cache_path = _cache_read(key)
        if cached is not None:
            written = _deliver(cached, output)
            return {"ok": True, "reason": "ok", "joints": cached, "cached": True,
                    "cache_key": key, "cache_file": cache_path,
                    "seconds": round(time.perf_counter() - started, 3),
                    "path": written, "install": install,
                    "says": "Joints came from the cache (%s); the detector did not run."
                            % key[:12]}

    lock = _Lock()
    if not lock.acquire():
        return _fail("busy",
                     "Another joint detection is already running on this machine "
                     "(lock %s). Skeleton inference peaks near 8.5 GB, so they are "
                     "run one at a time; try again when it finishes." % lock.path,
                     install=install)

    temp_dir = tempfile.mkdtemp(prefix="forge_rigbridge_")
    joints_path = os.path.join(temp_dir, "joints.json")
    try:
        command = [
            install["python"], DETECT_SCRIPT,
            "--input", mesh,
            "--output", joints_path,
            "--seed", str(int(seed)),
            "--faces-target-count", str(int(faces_target_count)),
            "--unit-scale", repr(float(unit_scale)),
        ]
        environment = dict(os.environ)
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            proc = subprocess.run(
                command, cwd=HERE, capture_output=True, text=True,
                timeout=timeout, creationflags=_no_window_flags(),
                env=environment)
        except subprocess.TimeoutExpired:
            return _fail("timeout",
                         "The joint detector did not finish within %.0f s. It normally "
                         "takes about 5 s plus the checkpoint load; something is stuck."
                         % timeout, install=install, command=command)
        except OSError as exc:
            return _fail("detector_failed",
                         "Could not start the joint detector (%s: %s)."
                         % (type(exc).__name__, exc), install=install, command=command)

        if not os.path.isfile(joints_path):
            return _fail("detector_failed",
                         "The joint detector exited %d without writing joints. "
                         "Its last words: %s"
                         % (proc.returncode, _tail(proc.stderr or proc.stdout)),
                         install=install, command=command, returncode=proc.returncode,
                         stderr=_tail(proc.stderr, 4000))
        try:
            with open(joints_path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, ValueError) as exc:
            return _fail("bad_output",
                         "The joint detector wrote a file this runner could not read "
                         "(%s)." % exc, install=install)
        if not isinstance(document, dict) or not isinstance(document.get("joints"), list) \
                or not document["joints"]:
            return _fail("bad_output",
                         "The joint detector wrote no joints array; the file is not a "
                         "%s document." % SCHEMA, install=install)

        cache_path = _cache_write(key, document) if key else None
        written = _deliver(document, output)
        return {
            "ok": True, "reason": "ok", "joints": document, "cached": False,
            "cache_key": key, "cache_file": cache_path,
            "seconds": round(time.perf_counter() - started, 3),
            "path": written, "install": install, "returncode": proc.returncode,
            "says": "The detector found %d joints in %.1f s%s."
                    % (len(document["joints"]),
                       float((document.get("detector") or {}).get("seconds") or 0.0),
                       " (cached for next time)" if cache_path else ""),
        }
    finally:
        lock.release()
        shutil.rmtree(temp_dir, ignore_errors=True)


def _deliver(document, output):
    if not output:
        return None
    path = os.path.abspath(output)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    return path


def _tail(text, limit=600):
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else (text or "(nothing on stderr)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="detector_runner.py",
        description="Run the UniRig joint detector in its own venv, with a cache.")
    parser.add_argument("--input", help="mesh file to detect joints in")
    parser.add_argument("--output", help="joints JSON to write")
    parser.add_argument("--diagnose", action="store_true",
                        help="report whether detection is available, and exit")
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--faces-target-count", type=int, default=50000)
    parser.add_argument("--unit-scale", type=float, default=1000.0)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore any cached answer and detect again")
    parser.add_argument("--json", action="store_true",
                        help="print the result dict instead of a summary")
    args = parser.parse_args(argv)

    if args.diagnose:
        report = diagnose()
        print(json.dumps(report, indent=2) if args.json else report["says"])
        return 0 if report["installed"] else 2

    if not args.input:
        parser.error("--input is required (or use --diagnose)")

    result = detect(args.input, output=args.output, seed=args.seed,
                    faces_target_count=args.faces_target_count,
                    unit_scale=args.unit_scale, timeout=args.timeout,
                    use_cache=not args.no_cache, refresh=args.refresh)
    if args.json:
        printable = dict(result)
        printable.pop("joints", None)
        print(json.dumps(printable, indent=2))
    else:
        print(result["says"])
        if result["ok"]:
            print("  joints  %d" % len(result["joints"]["joints"]))
            print("  cached  %s" % result["cached"])
            if result.get("path"):
                print("  wrote   %s" % result["path"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
