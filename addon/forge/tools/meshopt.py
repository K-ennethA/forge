"""meshoptimizer bindings: UV-preserving LOD simplification.

Blender's Decimate **Collapse** is a position-only quadric.  It has no UV term
at all, so it is free to walk a vertex across a UV seam and smear the atlas, and
its only lever — the vertex group — is a *hard lock*, not a soft cost (measured
on Blender 5.0.1; see ``rigforge._protect_seams``).  Locking the seams of an
unwrapped character floors the reduction far above any LOD budget, so the atlas
loses either way.

meshoptimizer's ``meshopt_simplifyWithAttributes`` is the fix: a quadric that
carries attribute gradients, so UV error is *priced* into every collapse instead
of being invisible.  Two properties matter downstream:

* the result is an **index buffer into the original vertex buffer** — a surviving
  vertex keeps its original position and its original UV, bit for bit.  Every
  LOD therefore shares LOD0's atlas exactly and one baked map serves the chain;
* seams survive as *attribute discontinuities* that the metric can see, so
  ``protect_seams`` — the hard lock that cost most of the budget — is no longer
  needed under meshopt.  It stays on the Decimate fallback path only.

This module is deliberately small and free-standing:

* **no numpy.**  Blender ships numpy, but ``foreach_get`` hands back flat lists
  and the buffers here are built from :mod:`array`, which ctypes can point at
  without a copy.  Nothing here imports ``bpy`` either — it takes flat sequences
  of floats and ints, so it is testable outside Blender and reusable outside
  RigForge;
* **optional.**  The DLL is built by ``native/meshopt/build.ps1``.  When it is
  missing every entry point still imports and :func:`unavailable_reason` says
  why in one sentence; callers fall back rather than fail.

The three traps this encodes, all of them silent failures rather than errors:

1. **Unwelded input no-ops.**  The simplifier rebuilds topology by hashing the
   *raw float bits* of each position (``PositionHasher`` in ``simplifier.cpp``
   compares the three words exactly, only folding -0.0 to 0.0).  Vertices split
   for a UV seam are fine — they hash together and become "wedges" of one
   vertex.  A triangle *soup*, where every corner carries its own position, has
   no shared edges at all: every edge is a border, no collapse is legal, and the
   call returns the input index count with a tiny ``result_error`` and no error
   of any kind.  :func:`weld_report` catches that before the call.
2. **``result_error`` is not a success signal.**  It is the error of what the
   simplifier *did*, not evidence it reached the target; a run that collapsed
   nothing reports a beautiful 0.0.  The only honest check is the achieved index
   count, which :func:`simplify_lod` always measures and ``require_target`` turns
   into a raise.
3. **gltfpack's UV weight is 0.0.**  Read ``gltf/mesh.cpp``'s
   ``simplifyAttributes``: normals get 0.5, vertex colours 1.0, and texture
   coordinates get ``update ? 1.f : 0.f`` — i.e. **zero** on the ordinary
   simplification path.  Copying that call site verbatim is the documented way
   to get a UV-blind "UV-aware" simplifier.  :func:`uv_weight` derives a non-zero
   weight from the mesh's own UV density instead.
"""

import ctypes
import json
import os
from array import array

#: Environment override for the DLL location; wins over every search path.
DLL_ENV = "FORGE_MESHOPT_DLL"

#: File names looked for, in the add-on's ``lib`` directory and in the
#: checkout's ``native/meshopt``.
DLL_NAME = "meshoptimizer.dll"
MANIFEST_NAME = "meshopt_build.json"

# --- meshopt_SimplifyX options (meshoptimizer.h, pinned commit) --------------
SIMPLIFY_LOCK_BORDER = 1 << 0
SIMPLIFY_SPARSE = 1 << 1
SIMPLIFY_ERROR_ABSOLUTE = 1 << 2
SIMPLIFY_PRUNE = 1 << 3
SIMPLIFY_REGULARIZE = 1 << 4
SIMPLIFY_PERMISSIVE = 1 << 5

# --- meshopt_SimplifyVertex_X locks -----------------------------------------
VERTEX_LOCK = 1 << 0
VERTEX_PROTECT = 1 << 1
VERTEX_PRIORITY = 1 << 2

#: Per-scalar weight for each normal component.  gltfpack uses 0.5; 1.0 is the
#: value the LOD research settled on, because a character's silhouette is read
#: off its shading long before its outline.
NORMAL_WEIGHT = 1.0

#: UV weight = ``UV_PRIORITY`` x the mesh's reciprocal UV density (world units
#: per UV unit), clamped into the band the research gives for per-scalar UV
#: weights.  The density term is what makes one number work for a 2 m character
#: and a 20 cm prop: both are unwrapped into the same 0-1 atlas, so the *same*
#: UV drift means ten times more texel smear on the small one.
UV_PRIORITY = 8.0
UV_WEIGHT_MIN = 10.0
UV_WEIGHT_MAX = 100.0

#: Relative error ceiling handed to the simplifier when the caller names none.
#: 1.0 is "whatever it takes to hit the triangle target"; a budget-driven LOD
#: chain wants the count, and measures the error it got afterwards.
DEFAULT_TARGET_ERROR = 1.0


class MeshoptError(Exception):
    """Any meshoptimizer failure.  Callers that can fall back catch this."""


class MeshoptUnavailable(MeshoptError):
    """The DLL is not present or will not load.  Carries the reason."""


class MeshoptUnwelded(MeshoptError):
    """Input positions share nothing, so simplification would silently no-op."""


class MeshoptTargetMissed(MeshoptError):
    """``require_target`` was set and the achieved index count missed it."""


# --- locating the library ----------------------------------------------------

_LOADED = None          # cached ctypes.CDLL
_LOAD_FAILURE = None    # cached reason string


def _candidate_dirs():
    """Directories that may hold the DLL, in priority order."""
    here = os.path.dirname(os.path.abspath(__file__))          # .../forge/tools
    package = os.path.dirname(here)                            # .../forge
    addon = os.path.dirname(package)                           # .../addon
    repo = os.path.dirname(addon)                              # checkout root
    return [
        # Deployed beside the installed add-on: the installer copies the DLL in,
        # because an installed extension cannot see the checkout at all.
        os.path.join(package, "lib"),
        # In-checkout: where native/meshopt/build.ps1 leaves it.
        os.path.join(repo, "native", "meshopt"),
        here,
    ]


def dll_path():
    """Absolute path of the DLL, or ``None``."""
    override = os.environ.get(DLL_ENV)
    if override:
        return override if os.path.isfile(override) else None
    for directory in _candidate_dirs():
        candidate = os.path.join(directory, DLL_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def build_manifest():
    """``native/meshopt/meshopt_build.json`` as a dict, or ``{}``.

    The build script writes it on success *and* on failure, so it is also where
    the "why is there no DLL" sentence comes from.
    """
    for directory in _candidate_dirs():
        candidate = os.path.join(directory, MANIFEST_NAME)
        if os.path.isfile(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return data
    return {}


def commit():
    """The pinned upstream commit, short, or ``"unknown"``."""
    value = build_manifest().get("commit")
    return value[:8] if isinstance(value, str) and value else "unknown"


def load():
    """The loaded library.  Raises :class:`MeshoptUnavailable` with a reason."""
    global _LOADED, _LOAD_FAILURE
    if _LOADED is not None:
        return _LOADED
    if _LOAD_FAILURE is not None:
        raise MeshoptUnavailable(_LOAD_FAILURE)

    path = dll_path()
    if not path:
        manifest = build_manifest()
        reason = manifest.get("reason") or ""
        if manifest.get("status") == "blocked" and reason:
            _LOAD_FAILURE = "%s not built: %s" % (DLL_NAME, reason)
        else:
            _LOAD_FAILURE = (
                "%s not found; build it with native/meshopt/build.ps1 (or point "
                "%s at a copy)" % (DLL_NAME, DLL_ENV))
        raise MeshoptUnavailable(_LOAD_FAILURE)

    try:
        lib = ctypes.CDLL(path)
    except OSError as exc:
        _LOAD_FAILURE = "%s failed to load (%s)" % (path, exc)
        raise MeshoptUnavailable(_LOAD_FAILURE)

    try:
        _bind(lib)
    except AttributeError as exc:
        _LOAD_FAILURE = "%s does not export the C interface (%s)" % (path, exc)
        raise MeshoptUnavailable(_LOAD_FAILURE)

    _LOADED = lib
    return lib


def available():
    """True when the DLL is present and loadable."""
    try:
        load()
    except MeshoptUnavailable:
        return False
    return True


def unavailable_reason():
    """One sentence saying why meshopt is not in play, or ``""``."""
    try:
        load()
    except MeshoptUnavailable as exc:
        return str(exc)
    return ""


def simplifier_name():
    """The string the LOD reports carry in ``"simplifier"``.

    ``"meshopt <commit>"`` when the library is in play, and the Decimate name
    *plus the reason meshopt is not* when it is not — a report that says only
    "blender-decimate" leaves the reader guessing whether the build is missing
    or the lane was never wired up.
    """
    if available():
        return "meshopt %s" % commit()
    return "blender-decimate (meshopt unavailable: %s)" % unavailable_reason()


class _Stream(ctypes.Structure):
    _fields_ = [("data", ctypes.c_void_p),
                ("size", ctypes.c_size_t),
                ("stride", ctypes.c_size_t)]


_UINT_P = ctypes.POINTER(ctypes.c_uint)
_FLOAT_P = ctypes.POINTER(ctypes.c_float)
_UBYTE_P = ctypes.POINTER(ctypes.c_ubyte)

#: ``{name: (restype, [argtypes])}`` for every entry point bound here.
#:
#: Declaring these is not optional on 64-bit Windows: an unannotated ``size_t``
#: return defaults to ``c_int`` in ctypes and truncates, and unannotated pointer
#: arguments pass whatever Python happened to hand over.  It is a table rather
#: than a pile of assignments so the test suite can check each arity against
#: ``native/meshopt/meshoptimizer/meshoptimizer.h`` itself - the one binding bug
#: that a machine without the DLL can still catch, and the one that would
#: otherwise surface as a silent stack mismatch on the machine that has it.
_SIGNATURES = {
    "meshopt_simplify": (ctypes.c_size_t, [
        _UINT_P, _UINT_P, ctypes.c_size_t, _FLOAT_P, ctypes.c_size_t,
        ctypes.c_size_t, ctypes.c_size_t, ctypes.c_float, ctypes.c_uint,
        _FLOAT_P]),
    "meshopt_simplifyWithAttributes": (ctypes.c_size_t, [
        _UINT_P, _UINT_P, ctypes.c_size_t, _FLOAT_P, ctypes.c_size_t,
        ctypes.c_size_t, _FLOAT_P, ctypes.c_size_t, _FLOAT_P, ctypes.c_size_t,
        _UBYTE_P, ctypes.c_size_t, ctypes.c_float, ctypes.c_uint, _FLOAT_P]),
    "meshopt_generateVertexRemap": (ctypes.c_size_t, [
        _UINT_P, _UINT_P, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_size_t]),
    "meshopt_generateVertexRemapMulti": (ctypes.c_size_t, [
        _UINT_P, _UINT_P, ctypes.c_size_t, ctypes.c_size_t,
        ctypes.POINTER(_Stream), ctypes.c_size_t]),
    "meshopt_remapIndexBuffer": (None, [
        _UINT_P, _UINT_P, ctypes.c_size_t, _UINT_P]),
    "meshopt_remapVertexBuffer": (None, [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t,
        _UINT_P]),
}


def _bind(lib):
    """Apply :data:`_SIGNATURES` to ``lib``; raises ``AttributeError`` if a
    symbol is missing, which :func:`load` turns into a named unavailability."""
    for name, (restype, argtypes) in _SIGNATURES.items():
        function = getattr(lib, name)
        function.restype = restype
        function.argtypes = argtypes


# --- buffers -----------------------------------------------------------------

def _floats(values, name):
    if isinstance(values, array) and values.typecode == "f":
        return values
    try:
        return array("f", values)
    except (TypeError, ValueError) as exc:
        raise MeshoptError("%s must be a flat sequence of floats (%s)" % (name, exc))


def _uints(values, name):
    if isinstance(values, array) and values.typecode == "I":
        return values
    try:
        return array("I", values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MeshoptError("%s must be a flat sequence of non-negative ints (%s)"
                           % (name, exc))


def _ptr(buffer, ctype):
    """A ctypes pointer into ``buffer`` with no copy.

    ``from_buffer`` keeps the array alive for as long as the returned object
    lives and refuses a buffer that is too small, which is exactly the check
    that would otherwise be a heap corruption three calls later.
    """
    count = len(buffer)
    if not count:
        return None
    return ctypes.cast((ctype * count).from_buffer(buffer), ctypes.POINTER(ctype))


# --- welding -----------------------------------------------------------------

def weld_report(vertices, indices):
    """How much position sharing the input has.  Pure Python, no DLL needed.

    Mirrors the simplifier's own rule: positions are compared *exactly*, with
    -0.0 folded to 0.0 (Python's ``-0.0 == 0.0`` does that for free), so this
    counts the same buckets the C code will.

    ``soup`` is the fatal case: every corner is its own vertex *and* no two of
    them share a position, so the mesh has no interior edges, every edge is a
    topological border, and the simplifier legally collapses nothing while
    reporting success.
    """
    positions = _floats(vertices, "vertices")
    vertex_count = len(positions) // 3
    unique = set()
    for index in range(vertex_count):
        base = index * 3
        unique.add((positions[base] + 0.0, positions[base + 1] + 0.0,
                    positions[base + 2] + 0.0))
    index_count = len(indices)
    return {
        "vertex_count": vertex_count,
        "unique_positions": len(unique),
        "index_count": index_count,
        # Split-for-UV wedges are welded input, not unwelded: they share
        # positions exactly and the simplifier stitches them back together.
        "wedges": vertex_count - len(unique),
        "soup": bool(vertex_count) and index_count == vertex_count
                and len(unique) == vertex_count,
    }


def weld_positions(vertices, indices):
    """Position-only weld through ``meshopt_generateVertexRemap``.

    Returns ``(remap, unique_count)``: ``remap[old] = new``.  Position-only is
    the point — a remap over the full vertex (position + UV + normal) would keep
    every seam split apart, which is the opposite of what welding is for.  The
    LOD path does not need this (the simplifier welds internally and the split
    vertices are what carry the exact UVs), so it exists for callers that want a
    compacted buffer, and for asserting what the simplifier will see.
    """
    lib = load()
    positions = _floats(vertices, "vertices")
    index_buffer = _uints(indices, "indices")
    vertex_count = len(positions) // 3
    remap = array("I", bytes(4 * vertex_count))
    unique = lib.meshopt_generateVertexRemap(
        _ptr(remap, ctypes.c_uint),
        _ptr(index_buffer, ctypes.c_uint) if len(index_buffer) else None,
        len(index_buffer),
        ctypes.cast(_ptr(positions, ctypes.c_float), ctypes.c_void_p),
        vertex_count, 12)
    return remap, int(unique)


# --- attribute weights -------------------------------------------------------

def uv_weight(vertices, indices, uvs, priority=UV_PRIORITY,
              minimum=UV_WEIGHT_MIN, maximum=UV_WEIGHT_MAX):
    """Per-scalar UV weight from the mesh's reciprocal UV density.

    Position error is measured in world units and UV error in 0-1 atlas units,
    so a single hard-coded number is meaningless across assets.  The ratio of
    summed triangle-edge length in world space to the same edges in UV space is
    *world units per UV unit* — multiply by it and a UV weight of 1.0 would mean
    "a UV slip costs exactly the world distance it corresponds to", i.e. neutral.
    ``priority`` is how much more than neutral the atlas is worth; the clamp
    keeps the result inside the 10-100 band that LOD research gives for
    per-scalar UV weights, whatever a degenerate unwrap does to the ratio.
    """
    positions = _floats(vertices, "vertices")
    texcoords = _floats(uvs, "uvs")
    world = 0.0
    atlas = 0.0
    for triangle in range(len(indices) // 3):
        corner = triangle * 3
        for edge in range(3):
            a = int(indices[corner + edge])
            b = int(indices[corner + (edge + 1) % 3])
            dx = positions[a * 3] - positions[b * 3]
            dy = positions[a * 3 + 1] - positions[b * 3 + 1]
            dz = positions[a * 3 + 2] - positions[b * 3 + 2]
            world += (dx * dx + dy * dy + dz * dz) ** 0.5
            du = texcoords[a * 2] - texcoords[b * 2]
            dv = texcoords[a * 2 + 1] - texcoords[b * 2 + 1]
            atlas += (du * du + dv * dv) ** 0.5
    if atlas <= 1e-12 or world <= 1e-12:
        return minimum
    return max(minimum, min(maximum, priority * (world / atlas)))


def attribute_weights(vertices, indices, uvs=None, normals=None,
                      normal_weight=NORMAL_WEIGHT, priority=UV_PRIORITY):
    """The per-scalar weight vector matching :func:`interleave_attributes`.

    Order is normals first, then UVs — the same order the attribute buffer is
    built in, because ``attribute_weights`` is positional against it and a
    mismatch weights the wrong channel without any complaint.
    """
    weights = []
    if normals is not None:
        weights.extend([float(normal_weight)] * 3)
    if uvs is not None:
        weights.extend([uv_weight(vertices, indices, uvs, priority=priority)] * 2)
    return weights


def interleave_attributes(vertex_count, uvs=None, normals=None):
    """``[nx, ny, nz, u, v]`` per vertex as one flat float array."""
    stride = (3 if normals is not None else 0) + (2 if uvs is not None else 0)
    if not stride:
        return array("f"), 0
    normal_data = _floats(normals, "normals") if normals is not None else None
    uv_data = _floats(uvs, "uvs") if uvs is not None else None
    if normal_data is not None and len(normal_data) < vertex_count * 3:
        raise MeshoptError("normals holds %d floats, expected %d (3 per vertex)"
                           % (len(normal_data), vertex_count * 3))
    if uv_data is not None and len(uv_data) < vertex_count * 2:
        raise MeshoptError("uvs holds %d floats, expected %d (2 per vertex)"
                           % (len(uv_data), vertex_count * 2))
    out = array("f", bytes(4 * stride * vertex_count))
    for index in range(vertex_count):
        base = index * stride
        cursor = 0
        if normal_data is not None:
            out[base] = normal_data[index * 3]
            out[base + 1] = normal_data[index * 3 + 1]
            out[base + 2] = normal_data[index * 3 + 2]
            cursor = 3
        if uv_data is not None:
            out[base + cursor] = uv_data[index * 2]
            out[base + cursor + 1] = uv_data[index * 2 + 1]
    return out, stride


# --- the one call RigForge makes --------------------------------------------

def simplify_lod(vertices, indices, uvs=None, normals=None, target_index_count=0,
                 target_error=DEFAULT_TARGET_ERROR, weights=None,
                 lock_border=False, prune=True, permissive=True,
                 vertex_lock=None, require_target=False, report=None):
    """Simplify to ``target_index_count`` indices, pricing UVs into the metric.

    ``vertices`` is a flat ``[x, y, z, ...]`` sequence and ``indices`` a flat
    triangle list into it; ``uvs`` and ``normals`` are flat per-vertex sequences
    of the same vertex count.  Vertices split at a UV seam are expected and
    wanted: they must share the *exact* position floats, which they do when they
    come from one Blender vertex, and the simplifier stitches them into wedges of
    one topological vertex while still seeing the UV discontinuity between them.

    Returns ``(indices, result_error)`` — a new ``array("I")`` indexing the
    **original** vertex buffer, so every surviving vertex keeps its original UV
    byte for byte, and the relative error the simplifier reports.  Pass a dict as
    ``report`` to also receive the achieved counts, the weights used and the weld
    census; ``require_target`` turns a missed target into
    :class:`MeshoptTargetMissed` instead of a quiet short result.

    Flags follow gltfpack's policy — ``Permissive`` (collapse across attribute
    discontinuities unless a vertex is tagged ``VERTEX_PROTECT``) plus ``Prune``
    (drop disconnected specks rather than stall on them) — with the one
    deliberate departure named in this module's docstring: the UV weight is not
    zero.
    """
    # Validation first, the library second: an argument mistake and an unwelded
    # mesh are wrong whether or not the DLL was ever built, and saying so only on
    # machines that happen to have it is how a bad caller ships.
    positions = _floats(vertices, "vertices")
    index_buffer = _uints(indices, "indices")
    vertex_count = len(positions) // 3
    index_count = len(index_buffer)
    if index_count % 3:
        raise MeshoptError("indices holds %d entries, which is not a whole number "
                           "of triangles" % index_count)
    if not index_count or not vertex_count:
        raise MeshoptError("nothing to simplify: %d vertices, %d indices"
                           % (vertex_count, index_count))
    highest = max(index_buffer)
    if highest >= vertex_count:
        raise MeshoptError("indices reference vertex %d but only %d vertices were "
                           "given" % (highest, vertex_count))

    census = weld_report(positions, index_buffer)
    if census["soup"]:
        raise MeshoptUnwelded(
            "input is an unwelded triangle soup (%d vertices, %d indices, %d "
            "distinct positions): no two triangles share a position, so every "
            "edge is a topological border and simplification would collapse "
            "nothing while reporting success. Weld positions first."
            % (census["vertex_count"], census["index_count"],
               census["unique_positions"]))

    target = int(target_index_count)
    if target <= 0:
        target = index_count
    target -= target % 3

    attributes, stride = interleave_attributes(vertex_count, uvs=uvs, normals=normals)
    if weights is None:
        weight_values = attribute_weights(positions, index_buffer, uvs=uvs,
                                          normals=normals)
    elif isinstance(weights, dict):
        weight_values = []
        if normals is not None:
            weight_values.extend([float(weights.get("normal", NORMAL_WEIGHT))] * 3)
        if uvs is not None:
            weight_values.extend([float(weights.get(
                "uv", uv_weight(positions, index_buffer, uvs)))] * 2)
    else:
        weight_values = [float(value) for value in weights]
    if len(weight_values) != stride:
        raise MeshoptError("weights has %d entries but the attribute buffer has "
                           "%d floats per vertex" % (len(weight_values), stride))
    if stride > 32:
        raise MeshoptError("attribute_count must be <= 32, got %d" % stride)

    # Everything above is shape checking that holds whether or not the library
    # was ever built; only now is the DLL needed.
    lib = load()

    options = 0
    if lock_border:
        options |= SIMPLIFY_LOCK_BORDER
    else:
        if prune:
            options |= SIMPLIFY_PRUNE
        if permissive:
            options |= SIMPLIFY_PERMISSIVE

    # "worst case is index_count elements (*not* target_index_count)" - the
    # header says so twice, and a destination sized to the target is the classic
    # way to corrupt the heap on a mesh the simplifier could not reduce.
    destination = array("I", bytes(4 * index_count))
    result_error = ctypes.c_float(0.0)
    locks = None
    if vertex_lock is not None:
        locks = array("B", bytes(vertex_count))
        for index, value in enumerate(vertex_lock):
            if index < vertex_count:
                locks[index] = int(value) & 0xFF

    weight_buffer = array("f", weight_values) if weight_values else array("f")

    if stride:
        achieved = lib.meshopt_simplifyWithAttributes(
            _ptr(destination, ctypes.c_uint),
            _ptr(index_buffer, ctypes.c_uint), index_count,
            _ptr(positions, ctypes.c_float), vertex_count, 12,
            _ptr(attributes, ctypes.c_float), 4 * stride,
            _ptr(weight_buffer, ctypes.c_float), stride,
            _ptr(locks, ctypes.c_ubyte) if locks is not None else None,
            target, float(target_error), options,
            ctypes.byref(result_error))
    else:
        achieved = lib.meshopt_simplify(
            _ptr(destination, ctypes.c_uint),
            _ptr(index_buffer, ctypes.c_uint), index_count,
            _ptr(positions, ctypes.c_float), vertex_count, 12,
            target, float(target_error), options,
            ctypes.byref(result_error))

    achieved = int(achieved)
    if achieved > index_count or achieved % 3:
        raise MeshoptError("meshopt returned %d indices for a %d-index input; the "
                           "binding is wrong, not the mesh" % (achieved, index_count))
    out = destination[:achieved]

    if isinstance(report, dict):
        report.update({
            "target_index_count": target,
            "achieved_index_count": achieved,
            "input_index_count": index_count,
            "hit_target": achieved <= target,
            "result_error": round(float(result_error.value), 8),
            "attribute_count": stride,
            "attribute_weights": [round(value, 6) for value in weight_values],
            "options": options,
            "weld": census,
        })

    # result_error is the error of what it *did*, never evidence it reached the
    # target: a run that collapsed nothing reports 0.0.  The count is the check.
    if require_target and achieved > target:
        raise MeshoptTargetMissed(
            "meshopt stopped at %d indices against a target of %d (%d in, "
            "result_error %.6f): topology or attribute discontinuities blocked "
            "the remaining collapses."
            % (achieved, target, index_count, result_error.value))

    return out, float(result_error.value)
