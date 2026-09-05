"""Geometry execution: build -> tessellate -> stats, plus the worker process.

This module has two halves that never run in the same process:

**Child side** (imported by :mod:`worker`): :func:`normalize_build_result`,
:func:`tessellate_shape`, :func:`weld_vertices`, :func:`compute_stats`.  These
touch build123d / OCP.

**Parent side** (imported by :mod:`main`): :class:`WorkerPool` and the
``run_*`` helpers, which hand jobs to a long-lived child process and enforce a
wall-clock timeout.  Nothing here imports build123d, so the HTTP process stays
light and starts instantly.

Why a subprocess rather than a thread
-------------------------------------
A PartForge script is arbitrary Python.  A thread that hangs in a ``while True``
or deep inside OCC cannot be killed from Python -- ``Thread`` has no terminate,
and OCC calls do not release the GIL predictably -- so a runaway script would
wedge the service permanently.  A child process can always be killed, which is
the only containment that actually holds.

Why the child is kept *warm* rather than spawned per request
------------------------------------------------------------
Importing build123d costs several seconds.  Spawning per request would make
every slider drag a multi-second stall, against the plan's "a second or two"
target.  So one child process is started on first use, imports build123d once,
and then serves jobs over a newline-delimited stdin/stdout protocol.  On a
timeout or a crash the child is killed and the next request transparently
starts a fresh one.  Access is serialised with a lock: OCC is not thread-safe
and regeneration is inherently a one-at-a-time operation.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import ForgeError, ScriptError, ServiceError, TimeoutError_

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


#: Wall-clock budget for one script run (exec + build + tessellate), seconds.
DEFAULT_TIMEOUT_S: float = _env_float("FORGE_SCRIPT_TIMEOUT", 30.0)

#: Budget for the first job on a cold worker, which pays the build123d import.
COLD_START_EXTRA_S: float = _env_float("FORGE_COLD_START_EXTRA", 60.0)

#: Wall-clock budget for a segmenting job.  Cutting a part into wedges and
#: fusing a joint onto every face is dozens of OCC booleans, an order of
#: magnitude more work than one ``build()``, so it gets its own budget rather
#: than forcing the interactive one up.
SEGMENT_TIMEOUT_S: float = _env_float("FORGE_SEGMENT_TIMEOUT", 300.0)

#: Wall-clock budget for a check run: one build plus mesh analysis.
CHECK_TIMEOUT_S: float = _env_float("FORGE_CHECK_TIMEOUT", 120.0)

#: Wall-clock budget for a mold job.  Same shape of work as segmenting -- a
#: build, a draft, and a dozen booleans against a box -- so it gets the same
#: order of budget rather than the interactive one.
MOLD_TIMEOUT_S: float = _env_float("FORGE_MOLD_TIMEOUT", 300.0)

#: Linear deflection for tessellation, in millimetres.  0.05 mm is well below
#: a 0.4 mm nozzle's resolution while keeping vertex counts sane.
DEFAULT_TOLERANCE_MM: float = _env_float("FORGE_TESSELLATION_TOLERANCE", 0.05)

#: Angular deflection for tessellation, in radians.
DEFAULT_ANGULAR_TOLERANCE: float = _env_float("FORGE_ANGULAR_TOLERANCE", 0.2)

#: Vertices are welded on a grid this many decimals fine (1e-6 mm = 1 nm).
#: Far below any print or CAD tolerance, but coarse enough to merge the
#: floating-point noise between two faces' triangulations of a shared edge.
WELD_DECIMALS: int = 6

#: Coordinates sent to Blender are rounded here.  1e-6 mm keeps the JSON small
#: without touching anything a printer could resolve.
OUTPUT_DECIMALS: int = 6


# --------------------------------------------------------------------------
# Child side: turning a build() result into triangles
# --------------------------------------------------------------------------


def normalize_build_result(result: Any) -> Any:
    """Coerce whatever ``build(p)`` returned into a single build123d ``Shape``.

    Accepts a ``Part``/``Solid``/``Compound``/``Shell``/``Face``, a
    ``ShapeList`` or plain list/tuple of shapes (combined into a ``Compound``),
    or a builder object (``BuildPart``) whose ``.part`` is taken.
    """
    if result is None:
        raise ScriptError(
            "build(p) returned None; it must return a Build123d Part, Solid, "
            "Compound or ShapeList"
        )

    # A 2D builder is a common mistake worth naming explicitly.
    if not hasattr(result, "wrapped") and hasattr(result, "sketch"):
        raise ScriptError(
            "build(p) returned a BuildSketch; PartForge needs a 3D solid. "
            "Extrude or revolve the sketch inside a BuildPart and return "
            "builder.part"
        )
    if not hasattr(result, "wrapped") and hasattr(result, "line"):
        raise ScriptError(
            "build(p) returned a BuildLine; PartForge needs a 3D solid."
        )

    # BuildPart (and anything else exposing .part) hands over its result.
    if not hasattr(result, "wrapped") and hasattr(result, "part"):
        candidate = result.part
        if candidate is None:
            raise ScriptError(
                "build(p) returned a BuildPart with no geometry (builder.part is "
                "None); nothing was added inside the BuildPart context"
            )
        result = candidate

    if isinstance(result, (list, tuple)) or (
        hasattr(result, "__iter__") and not hasattr(result, "wrapped")
    ):
        shapes = [item for item in result if item is not None]
        if not shapes:
            raise ScriptError("build(p) returned an empty list of shapes")
        for item in shapes:
            if not hasattr(item, "wrapped"):
                raise ScriptError(
                    "build(p) returned a list containing a non-shape: "
                    f"{type(item).__name__}"
                )
        if len(shapes) == 1:
            result = shapes[0]
        else:
            result = _make_compound(shapes)

    if not hasattr(result, "wrapped"):
        raise ScriptError(
            "build(p) must return a Build123d Part/Solid/Compound/ShapeList, got "
            f"{type(result).__name__}"
        )
    if result.wrapped is None:
        raise ScriptError("build(p) returned an empty shape (wrapped is None)")

    return result


def _make_compound(shapes: Sequence[Any]) -> Any:
    """Combine several shapes into one ``Compound``, across build123d versions."""
    from build123d import Compound  # noqa: PLC0415 - keep build123d out of the parent

    # Compound(children=[...]) is the current constructor (confirmed against
    # build123d 0.11.1: it populates .wrapped with a real TopoDS_Compound, so
    # the result tessellates and exports).  make_compound() was the older
    # classmethod and is gone in 0.11; it is still tried so 0.5-era releases
    # keep working.
    try:
        return Compound(children=list(shapes))
    except Exception:  # noqa: BLE001 - fall through to the legacy API
        pass
    maker = getattr(Compound, "make_compound", None)
    if maker is not None:
        return maker(list(shapes))
    raise ServiceError(
        "cannot combine multiple shapes: this build123d version exposes neither "
        "Compound(children=...) nor Compound.make_compound()"
    )


def tessellate_shape(
    shape: Any,
    tolerance: float = DEFAULT_TOLERANCE_MM,
    angular_tolerance: float = DEFAULT_ANGULAR_TOLERANCE,
) -> Tuple[List[Tuple[float, float, float]], List[Tuple[int, int, int]]]:
    """Triangulate *shape* and return ``(vertices_mm, triangles)``.

    Vertices are millimetres in the shape's own coordinate system (build123d
    works in mm, so no unit conversion happens here).  Triangles are
    zero-based index triples wound counter-clockwise as seen from outside.
    """
    # Preferred path: build123d's own tessellate, which handles face orientation
    # and location transforms for us.  Confirmed against build123d 0.11.1:
    # Shape.tessellate(tolerance, angular_tolerance=0.1)
    #   -> (list[Vector], list[tuple[int, int, int]])
    primary_error: Optional[BaseException] = None
    tessellate = getattr(shape, "tessellate", None)
    if tessellate is not None:
        try:
            try:
                raw_vertices, raw_faces = tessellate(tolerance, angular_tolerance)
            except TypeError:
                # Defensive: a release that drops the second positional.
                raw_vertices, raw_faces = tessellate(tolerance)
            vertices = [_vector_xyz(v) for v in raw_vertices]
            faces = [tuple(int(i) for i in tri) for tri in raw_faces]
            if vertices and faces:
                return vertices, faces  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001 - the OCP path is the safety net
            primary_error = exc

    try:
        return _tessellate_via_ocp(shape, tolerance, angular_tolerance)
    except ForgeError:
        raise
    except Exception as exc:  # noqa: BLE001
        detail = f"tessellation failed: {type(exc).__name__}: {exc}"
        if primary_error is not None:
            detail += (
                f" (Shape.tessellate failed first with "
                f"{type(primary_error).__name__}: {primary_error})"
            )
        raise ServiceError(detail, traceback.format_exc()) from exc


def _vector_xyz(vector: Any) -> Tuple[float, float, float]:
    """Read (x, y, z) off a build123d ``Vector``, an OCC point, or a sequence."""
    for names in (("X", "Y", "Z"), ("x", "y", "z")):
        if all(hasattr(vector, n) for n in names):
            parts = []
            for n in names:
                attribute = getattr(vector, n)
                parts.append(float(attribute() if callable(attribute) else attribute))
            return (parts[0], parts[1], parts[2])
    try:
        x, y, z = vector  # sequence-like
        return (float(x), float(y), float(z))
    except Exception as exc:  # noqa: BLE001
        raise ServiceError(
            f"cannot read coordinates from tessellation vertex {vector!r}"
        ) from exc


def _tessellate_via_ocp(
    shape: Any, tolerance: float, angular_tolerance: float
) -> Tuple[List[Tuple[float, float, float]], List[Tuple[int, int, int]]]:
    """Direct OCP triangulation -- the safety net if ``Shape.tessellate`` moves.

    This is the precision-critical path, so it is spelled out rather than
    delegated: mesh the shape, walk every face, apply the face's location
    transform to its nodes, and flip the winding of REVERSED faces so all
    triangle normals point out of the solid.
    """
    # OCP module paths confirmed against cadquery-ocp 7.9.3 (the OCP the
    # installed build123d 0.11.1 pulls in).
    from OCP.BRep import BRep_Tool  # noqa: PLC0415
    from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: PLC0415
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED  # noqa: PLC0415
    from OCP.TopExp import TopExp_Explorer  # noqa: PLC0415
    from OCP.TopLoc import TopLoc_Location  # noqa: PLC0415
    from OCP.TopoDS import TopoDS  # noqa: PLC0415

    solid = shape.wrapped

    # (shape, linear deflection, relative, angular deflection, parallel)
    BRepMesh_IncrementalMesh(solid, tolerance, False, angular_tolerance, True)

    vertices: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, int, int]] = []

    explorer = TopExp_Explorer(solid, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is not None:
            transform = location.Transformation()
            offset = len(vertices)
            node_count = triangulation.NbNodes()
            for index in range(1, node_count + 1):
                # Poly_Triangulation.Node(i) is OCC 7.6+ (confirmed on 7.9.3);
                # older releases used Nodes().Value(i).
                point = triangulation.Node(index).Transformed(transform)
                vertices.append((point.X(), point.Y(), point.Z()))
            flipped = face.Orientation() == TopAbs_REVERSED
            for index in range(1, triangulation.NbTriangles() + 1):
                a, b, c = triangulation.Triangle(index).Get()
                if flipped:
                    a, c = c, a
                faces.append((a - 1 + offset, b - 1 + offset, c - 1 + offset))
        explorer.Next()

    if not faces:
        raise ScriptError(
            "the shape produced no triangles; build(p) returned geometry with no "
            "faces (an empty part, a wire, or a point)"
        )
    return vertices, faces


def weld_vertices(
    vertices: Sequence[Tuple[float, float, float]],
    faces: Sequence[Sequence[int]],
    decimals: int = WELD_DECIMALS,
) -> Tuple[List[List[float]], List[List[int]], int]:
    """Merge coincident vertices and drop degenerate triangles.

    OCC triangulates each face independently, so a solid's shared edges arrive
    duplicated once per adjoining face.  Welding on a fixed grid stitches them
    back together, which is what makes the mesh manifold in Blender and what
    lets :func:`compute_stats` test watertightness honestly.

    Returns ``(vertices, faces, degenerate_dropped)``.

    (If watertight ever reports false negatives on geometry that is genuinely
    closed, the upgrade here is a spatial hash that also probes neighbouring
    cells -- grid welding can in principle split two points that straddle a
    cell boundary.  At 1e-6 mm that is far below OCC's own node agreement.)
    """
    index_of: Dict[Tuple[float, float, float], int] = {}
    welded: List[List[float]] = []
    remap: List[int] = [0] * len(vertices)

    for old_index, (x, y, z) in enumerate(vertices):
        # +0.0 normalises -0.0 so it hashes with 0.0.
        key = (
            round(x, decimals) + 0.0,
            round(y, decimals) + 0.0,
            round(z, decimals) + 0.0,
        )
        new_index = index_of.get(key)
        if new_index is None:
            new_index = len(welded)
            index_of[key] = new_index
            welded.append([key[0], key[1], key[2]])
        remap[old_index] = new_index

    out_faces: List[List[int]] = []
    degenerate = 0
    for tri in faces:
        mapped = [remap[i] for i in tri]
        if len(set(mapped)) < 3:
            # Zero-area after welding; slicers choke on these.
            degenerate += 1
            continue
        out_faces.append(mapped)

    return welded, out_faces, degenerate


def mesh_edge_report(faces: Sequence[Sequence[int]]) -> Dict[str, Any]:
    """Manifold analysis of a triangle soup.

    ``closed``   -- every undirected edge is shared by exactly two triangles.
    ``oriented`` -- no directed edge appears twice, i.e. the winding is
    consistent, which is what tells a slicer which side is solid.
    """
    undirected: Counter = Counter()
    directed: Counter = Counter()

    for tri in faces:
        count = len(tri)
        for i in range(count):
            a = tri[i]
            b = tri[(i + 1) % count]
            directed[(a, b)] += 1
            undirected[(a, b) if a < b else (b, a)] += 1

    boundary_edges = sum(1 for c in undirected.values() if c == 1)
    nonmanifold_edges = sum(1 for c in undirected.values() if c > 2)
    duplicate_directed = sum(1 for c in directed.values() if c > 1)

    return {
        "closed": bool(faces) and boundary_edges == 0 and nonmanifold_edges == 0,
        "oriented": duplicate_directed == 0,
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
    }


def compute_stats(
    shape: Any,
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    degenerate_dropped: int = 0,
) -> Dict[str, Any]:
    """Build the ``stats`` block of a ``/generate`` response.

    ``watertight`` means both of these hold:

    * the B-Rep solid passes OCC's own validity check
      (``Shape.is_valid()`` -> ``BRepCheck_Analyzer``), and
    * the tessellated mesh we are about to hand Blender is edge-manifold and
      closed: every edge is shared by exactly two triangles, with consistent
      winding.

    The second half is the one that matters for printing -- a slicer sees the
    triangles, not the B-Rep -- and it is checked on exactly the mesh that
    leaves the service, after welding.
    """
    edges = mesh_edge_report(faces)

    solid_is_valid: Optional[bool] = None
    try:
        # build123d 0.11 exposes this as a *property* (backed by
        # BRepCheck_Analyzer); 0.5-era releases had it as a method.  Reading it
        # with getattr already evaluates the property, so accept both shapes --
        # treating the property's bool as "not callable, therefore unknown" is
        # how this silently reported null.
        is_valid = getattr(shape, "is_valid", None)
        if callable(is_valid):
            solid_is_valid = bool(is_valid())
        elif is_valid is not None:
            solid_is_valid = bool(is_valid)
    except Exception:  # noqa: BLE001 - a validity check must never fail the build
        solid_is_valid = None

    size, bbox_min, bbox_max, source = _bounding_box(shape, vertices)

    watertight = bool(edges["closed"] and edges["oriented"])
    if solid_is_valid is False:
        watertight = False

    return {
        # Contract fields.
        "vertex_count": len(vertices),
        "face_count": len(faces),
        "bounding_box_mm": [round(v, OUTPUT_DECIMALS) for v in size],
        "watertight": watertight,
        # Additive diagnostics -- the panel can show these, nothing depends on them.
        "bounding_box_min_mm": [round(v, OUTPUT_DECIMALS) for v in bbox_min],
        "bounding_box_max_mm": [round(v, OUTPUT_DECIMALS) for v in bbox_max],
        "bounding_box_source": source,
        "solid_is_valid": solid_is_valid,
        "mesh_is_closed": edges["closed"],
        "mesh_is_oriented": edges["oriented"],
        "boundary_edges": edges["boundary_edges"],
        "nonmanifold_edges": edges["nonmanifold_edges"],
        "degenerate_faces_dropped": degenerate_dropped,
    }


def _bounding_box(
    shape: Any, vertices: Sequence[Sequence[float]]
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], Tuple[float, float, float], str]:
    """Exact B-Rep bounding box when available, mesh extents otherwise."""
    try:
        # Shape.bounding_box(tolerance=None, optimal=True) -> BoundBox with
        # .min / .max / .size as Vectors (confirmed against build123d 0.11.1).
        box = shape.bounding_box()
        low = _vector_xyz(box.min)
        high = _vector_xyz(box.max)
        size = (high[0] - low[0], high[1] - low[1], high[2] - low[2])
        return size, low, high, "brep"
    except Exception:  # noqa: BLE001 - fall back to what we actually meshed
        pass

    if not vertices:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "empty"

    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    zs = [v[2] for v in vertices]
    low = (min(xs), min(ys), min(zs))
    high = (max(xs), max(ys), max(zs))
    size = (high[0] - low[0], high[1] - low[1], high[2] - low[2])
    return size, low, high, "mesh"


# --------------------------------------------------------------------------
# Parent side: the warm worker process
# --------------------------------------------------------------------------

_PACKAGE = __package__ or Path(__file__).resolve().parent.name
_PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)


class WorkerPool:
    """One long-lived child process, serialised behind a lock.

    Not a pool in the concurrent sense -- OCC is single-threaded and part
    regeneration is a one-at-a-time operation -- but it owns the lifecycle of
    the child the way a pool would: lazy start, health, kill-and-replace.
    """

    def __init__(self, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._stdout_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stderr_tail: Deque[str] = deque(maxlen=200)
        self._warm = False
        self._tempdir: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def _ensure_tempdir(self) -> str:
        if self._tempdir is None or not os.path.isdir(self._tempdir):
            self._tempdir = tempfile.mkdtemp(prefix="forge-jobs-")
        return self._tempdir

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _spawn(self) -> None:
        self._kill()
        # Fresh channels per generation.  The pump threads are handed *these*
        # objects rather than reading them off self, so a dying child's threads
        # can never poison the next child's queue with a stale sentinel.
        out_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        stderr_tail: Deque[str] = deque(maxlen=200)
        self._stdout_queue = out_queue
        self._stderr_tail = stderr_tail

        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            _PACKAGE_PARENT + (os.pathsep + existing if existing else "")
        )
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        creationflags = 0
        if sys.platform == "win32":
            # Never flash a console window: the ground rules say nothing opens
            # windows on the user's desktop.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        command = [sys.executable, "-X", "utf8", "-m", f"{_PACKAGE}.worker", "--serve"]

        try:
            self._process = subprocess.Popen(  # noqa: S603 - our own module
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=_PACKAGE_PARENT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise ServiceError(f"could not start the geometry worker: {exc}") from exc

        threading.Thread(
            target=self._pump_stdout,
            args=(self._process, out_queue),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pump_stderr,
            args=(self._process, stderr_tail),
            daemon=True,
        ).start()
        self._warm = False

    @staticmethod
    def _pump_stdout(
        process: subprocess.Popen, out_queue: "queue.Queue[Optional[str]]"
    ) -> None:
        stream = process.stdout
        if stream is None:  # pragma: no cover - defensive
            out_queue.put(None)
            return
        try:
            for line in stream:
                out_queue.put(line)
        except Exception:  # noqa: BLE001 - the pipe died with the process
            pass
        finally:
            out_queue.put(None)  # sentinel: this child's stdout is finished

    @staticmethod
    def _pump_stderr(process: subprocess.Popen, tail: Deque[str]) -> None:
        stream = process.stderr
        if stream is None:  # pragma: no cover - defensive
            return
        try:
            for line in stream:
                tail.append(line.rstrip("\r\n"))
        except Exception:  # noqa: BLE001
            pass

    def _kill(self) -> None:
        process, self._process = self._process, None
        self._warm = False
        if process is None:
            return
        try:
            if process.stdin:
                try:
                    process.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        except Exception:  # noqa: BLE001 - best effort teardown
            pass

    def shutdown(self) -> None:
        """Stop the child.  Safe to call repeatedly and at interpreter exit."""
        with self._lock:
            self._kill()
            if self._tempdir and os.path.isdir(self._tempdir):
                try:
                    import shutil  # noqa: PLC0415 - only needed on teardown

                    shutil.rmtree(self._tempdir, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass
                self._tempdir = None

    # -- job submission ----------------------------------------------------

    def submit(self, job: Mapping[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Run one job in the child and return its ``result`` payload.

        Raises :class:`ScriptError` (400) when the script is at fault,
        :class:`TimeoutError_` (400) when it overruns, :class:`ServiceError`
        (500) when the worker itself misbehaves.
        """
        budget = float(timeout if timeout is not None else self.timeout)

        with self._lock:
            if not self._alive():
                self._spawn()
            if not self._warm:
                # First job on this child also pays for importing build123d.
                budget += COLD_START_EXTRA_S

            try:
                return self._submit_locked(job, budget)
            except BrokenPipeError:
                # The child died between the liveness check and the write (or
                # its stdin was already closed).  One clean retry on a fresh
                # child; the job has not started, so this cannot double-execute.
                self._spawn()
                return self._submit_locked(job, budget + COLD_START_EXTRA_S)

    def _submit_locked(self, job: Mapping[str, Any], budget: float) -> Dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None:  # pragma: no cover - defensive
            raise ServiceError("geometry worker is not running")

        tempdir = self._ensure_tempdir()
        stamp = f"{int(time.time() * 1000)}-{os.getpid()}-{id(job) & 0xFFFF:04x}"
        job_path = os.path.join(tempdir, f"job-{stamp}.json")
        out_path = os.path.join(tempdir, f"out-{stamp}.json")

        try:
            try:
                with open(job_path, "w", encoding="utf-8") as handle:
                    json.dump(dict(job), handle)
            except OSError as exc:
                raise ServiceError(
                    f"could not stage the job file in {tempdir!r}: {exc}"
                ) from exc

            # Drain anything stale before we listen for this job's reply.
            while True:
                try:
                    self._stdout_queue.get_nowait()
                except queue.Empty:
                    break

            envelope = json.dumps({"job": job_path, "out": out_path})
            try:
                process.stdin.write(envelope + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                # Normalised so submit() can tell "never started" (retryable)
                # from "started and then went wrong" (not retryable).
                raise BrokenPipeError(str(exc)) from exc

            line = self._await_line(budget)
            if line is None:
                stderr = "\n".join(self._stderr_tail)
                self._kill()
                raise TimeoutError_(
                    f"script exceeded the {budget:.0f}s time limit and was stopped",
                    stderr or None,
                )

            try:
                ack = json.loads(line)
            except json.JSONDecodeError as exc:
                stderr = "\n".join(self._stderr_tail)
                self._kill()
                raise ServiceError(
                    "geometry worker sent malformed output; it has been restarted",
                    f"{exc}\nline: {line!r}\nstderr:\n{stderr}",
                ) from exc

            self._warm = True

            if not os.path.exists(out_path):
                stderr = "\n".join(self._stderr_tail)
                raise ServiceError(
                    "geometry worker produced no result file",
                    f"ack: {ack!r}\nstderr:\n{stderr}",
                )

            try:
                with open(out_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError) as exc:
                raise ServiceError(
                    f"geometry worker wrote an unreadable result file: {exc}"
                ) from exc

            return self._unwrap(payload)
        finally:
            for path in (job_path, out_path):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError:
                    pass

    def _await_line(self, budget: float) -> Optional[str]:
        """Wait up to *budget* seconds for one reply line, else ``None``."""
        deadline = time.monotonic() + budget
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                line = self._stdout_queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if self._process is None or self._process.poll() is not None:
                    stderr = "\n".join(self._stderr_tail)
                    raise ServiceError(
                        "geometry worker exited unexpectedly", stderr or None
                    )
                continue
            if line is None:  # stdout closed -> process gone
                stderr = "\n".join(self._stderr_tail)
                raise ServiceError(
                    "geometry worker closed its output stream", stderr or None
                )
            line = line.strip()
            if line:
                return line

    @staticmethod
    def _unwrap(payload: Mapping[str, Any]) -> Dict[str, Any]:
        if payload.get("ok"):
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ServiceError("geometry worker returned a malformed result")
            captured = payload.get("stdout")
            if captured:
                result.setdefault("script_stdout", captured)
            return result

        message = str(payload.get("error") or "the script failed")
        tb = payload.get("traceback")
        captured = payload.get("stdout")
        if captured:
            tb = (tb or "") + f"\n--- script stdout ---\n{captured}"
        if payload.get("kind") == "service":
            raise ServiceError(message, tb)
        raise ScriptError(message, tb)


# A single module-level worker: the service is one part at a time by design.
_POOL: Optional[WorkerPool] = None
_POOL_LOCK = threading.Lock()


def get_pool() -> WorkerPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = WorkerPool()
            atexit.register(_POOL.shutdown)
        return _POOL


def shutdown_pool() -> None:
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown()
            _POOL = None


# --------------------------------------------------------------------------
# Parent-side job helpers -- one per HTTP endpoint
# --------------------------------------------------------------------------


def run_health(timeout: Optional[float] = None) -> Dict[str, Any]:
    """Ask the worker which build123d it has.  Doubles as a warm-up."""
    return get_pool().submit({"kind": "health"}, timeout=timeout)


def run_parse_params(
    script: str,
    overrides: Optional[Mapping[str, Any]] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    return get_pool().submit(
        {
            "kind": "parse_params",
            "script": script,
            "overrides": dict(overrides or {}),
        },
        timeout=timeout,
    )


def run_generate(
    script: str,
    overrides: Optional[Mapping[str, Any]] = None,
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    return get_pool().submit(
        {
            "kind": "generate",
            "script": script,
            "overrides": dict(overrides or {}),
            "tolerance": tolerance,
            "angular_tolerance": angular_tolerance,
        },
        timeout=timeout,
    )


def run_export(
    script: str,
    fmt: str,
    path: str,
    overrides: Optional[Mapping[str, Any]] = None,
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    return get_pool().submit(
        {
            "kind": "export",
            "script": script,
            "overrides": dict(overrides or {}),
            "format": fmt,
            "path": path,
            "tolerance": tolerance,
            "angular_tolerance": angular_tolerance,
        },
        timeout=timeout,
    )


def run_check(
    script: str,
    overrides: Optional[Mapping[str, Any]] = None,
    printer: Optional[Mapping[str, Any]] = None,
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
    plate_margin_mm: Optional[float] = None,
    min_wall_probe_mm: Optional[float] = None,
    max_wall_samples: Optional[int] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    return get_pool().submit(
        {
            "kind": "check",
            "script": script,
            "overrides": dict(overrides or {}),
            "printer": dict(printer) if printer is not None else None,
            "tolerance": tolerance,
            "angular_tolerance": angular_tolerance,
            "plate_margin_mm": plate_margin_mm,
            "min_wall_probe_mm": min_wall_probe_mm,
            "max_wall_samples": max_wall_samples,
        },
        timeout=timeout if timeout is not None else CHECK_TIMEOUT_S,
    )


def _segment_job(
    kind: str,
    script: str,
    overrides: Optional[Mapping[str, Any]],
    printer: Optional[Mapping[str, Any]],
    joint: Optional[Mapping[str, Any]],
    mode: Any,
    tolerance: Optional[float],
    angular_tolerance: Optional[float],
    plate_margin_mm: Optional[float],
    plate_spacing_mm: Optional[float],
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "script": script,
        "overrides": dict(overrides or {}),
        "printer": dict(printer) if printer is not None else None,
        "joint": dict(joint) if joint is not None else None,
        "mode": mode,
        "tolerance": tolerance,
        "angular_tolerance": angular_tolerance,
        "plate_margin_mm": plate_margin_mm,
        "plate_spacing_mm": plate_spacing_mm,
    }


def run_segment(
    script: str,
    overrides: Optional[Mapping[str, Any]] = None,
    printer: Optional[Mapping[str, Any]] = None,
    joint: Optional[Mapping[str, Any]] = None,
    mode: Any = "auto",
    include_mesh: bool = True,
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
    plate_margin_mm: Optional[float] = None,
    plate_spacing_mm: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    job = _segment_job(
        "segment",
        script,
        overrides,
        printer,
        joint,
        mode,
        tolerance,
        angular_tolerance,
        plate_margin_mm,
        plate_spacing_mm,
    )
    job["include_mesh"] = bool(include_mesh)
    return get_pool().submit(
        job, timeout=timeout if timeout is not None else SEGMENT_TIMEOUT_S
    )


def run_export_segments(
    script: str,
    directory: str,
    overrides: Optional[Mapping[str, Any]] = None,
    printer: Optional[Mapping[str, Any]] = None,
    joint: Optional[Mapping[str, Any]] = None,
    mode: Any = "auto",
    basename: Optional[str] = None,
    fmt: str = "stl",
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
    plate_margin_mm: Optional[float] = None,
    plate_spacing_mm: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    job = _segment_job(
        "export_segments",
        script,
        overrides,
        printer,
        joint,
        mode,
        tolerance,
        angular_tolerance,
        plate_margin_mm,
        plate_spacing_mm,
    )
    job["directory"] = directory
    job["basename"] = basename
    job["format"] = fmt
    return get_pool().submit(
        job, timeout=timeout if timeout is not None else SEGMENT_TIMEOUT_S
    )


def _mold_job(
    kind: str,
    script: str,
    overrides: Optional[Mapping[str, Any]],
    printer: Optional[Mapping[str, Any]],
    options: Optional[Mapping[str, Any]],
    tolerance: Optional[float],
    angular_tolerance: Optional[float],
    plate_margin_mm: Optional[float],
) -> Dict[str, Any]:
    """One mold job envelope.  ``options`` carries the mold-shaped fields."""
    job: Dict[str, Any] = {
        "kind": kind,
        "script": script,
        "overrides": dict(overrides or {}),
        "printer": dict(printer) if printer is not None else None,
        "tolerance": tolerance,
        "angular_tolerance": angular_tolerance,
        "plate_margin_mm": plate_margin_mm,
    }
    # Passed through verbatim; mold.normalize_options is the validator, and it
    # runs in the worker so the error text comes back through the same path as
    # every other 400.
    job.update(dict(options or {}))
    return job


def run_mold(
    script: str,
    overrides: Optional[Mapping[str, Any]] = None,
    printer: Optional[Mapping[str, Any]] = None,
    options: Optional[Mapping[str, Any]] = None,
    include_mesh: bool = True,
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
    plate_margin_mm: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    job = _mold_job(
        "mold",
        script,
        overrides,
        printer,
        options,
        tolerance,
        angular_tolerance,
        plate_margin_mm,
    )
    job["include_mesh"] = bool(include_mesh)
    return get_pool().submit(
        job, timeout=timeout if timeout is not None else MOLD_TIMEOUT_S
    )


def run_export_mold(
    script: str,
    directory: str,
    overrides: Optional[Mapping[str, Any]] = None,
    printer: Optional[Mapping[str, Any]] = None,
    options: Optional[Mapping[str, Any]] = None,
    basename: Optional[str] = None,
    fmt: str = "stl",
    tolerance: Optional[float] = None,
    angular_tolerance: Optional[float] = None,
    plate_margin_mm: Optional[float] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    job = _mold_job(
        "export_mold",
        script,
        overrides,
        printer,
        options,
        tolerance,
        angular_tolerance,
        plate_margin_mm,
    )
    job["directory"] = directory
    job["basename"] = basename
    job["format"] = fmt
    return get_pool().submit(
        job, timeout=timeout if timeout is not None else MOLD_TIMEOUT_S
    )


__all__ = [
    "CHECK_TIMEOUT_S",
    "MOLD_TIMEOUT_S",
    "DEFAULT_ANGULAR_TOLERANCE",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_TOLERANCE_MM",
    "OUTPUT_DECIMALS",
    "SEGMENT_TIMEOUT_S",
    "WELD_DECIMALS",
    "WorkerPool",
    "compute_stats",
    "get_pool",
    "mesh_edge_report",
    "normalize_build_result",
    "run_check",
    "run_export",
    "run_export_mold",
    "run_export_segments",
    "run_generate",
    "run_mold",
    "run_health",
    "run_parse_params",
    "run_segment",
    "shutdown_pool",
    "tessellate_shape",
    "weld_vertices",
]
