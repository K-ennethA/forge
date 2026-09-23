"""Grab-and-track: move things by numbers, measure them continuously.

Why this exists
---------------
Every spatial adjustment an agent made before this module was a loop of
render, look, nudge, render again.  That loop is slow (a render per step) and
imprecise (an eye reading pixels is a millimetre-class instrument at best, and
the eevee-bowl-v2 seating rounds ended each time on a render "looked at, and
disagreed with" — ``docs/recipes/seating.md``).  This module replaces the look
with a readout and the nudge with a solve:

* ``track_points`` pins named **trackers** onto an object: a world point (held
  in the object's own frame, so it rides the object), a **vertex** (read off the
  *evaluated* mesh, so it follows armature deformation, shape keys and every
  other modifier that keeps the vertex count), or a **bone** end (the posed head,
  or tail on request).  The set is a JSON ledger on the object,
  ``obj["forge_trackers"]`` — the ``bosses.py`` pattern, and for its reason: a
  string survives a .blend round trip intact, nested ID-property lists do not.
* ``probe`` reads tracker world positions and pairwise distances.  A pure read:
  it writes nothing, is in ``READ_ONLY_COMMANDS``, and costs one view-layer
  update plus one evaluated-mesh read per object that carries vertex trackers.
* ``grab_to`` translates an object (or one pose bone) so a **pivot** — the
  origin, the bone head, or any tracker — lands on a target, or moves by a
  delta, with chosen **world axes locked**.  It solves first, refuses with the
  numbers if the locks make the target unreachable, and only then applies.
* ``list_trackers`` shows the ledgers and whether each tracker still resolves.

Renders become appearance-only: "is it in the right place" is a probe.

The solve
---------
Everything ``grab_to`` moves is a set of three translation channels ``c``
(``Object.location`` or ``PoseBone.location``) whose effect is affine: the
grabbed thing's world position ("the handle") moves by ``H·Δc`` and the pivot by
``J·Δc``.  The command works in handle-world moves ``d = H·Δc``, so a lock on
world axis *k* is simply ``d_k = 0``, and the pivot's response to ``d`` is
``K = J·H⁻¹``.  The move is the least-squares solution of ``K_F·d_F = target −
pivot`` over the free axes *F*; a nonzero predicted residual means the locks
forbid the target, and that is a refusal, not a partial move.

Two paths:

* **exact** — an unparented, unconstrained object with a rigidly attached pivot
  (the origin, a location tracker, a bone tracker on that armature object).
  ``H`` and ``K`` are the identity by construction, nothing is probed, and the
  locked ``location`` channels are **never written**, so a locked axis is held
  bit-for-bit, not to a tolerance.
* **probed** — everything else (a pose bone, a parented object, a vertex pivot
  that deforms).  ``H`` and ``J`` are measured by three finite steps of
  :data:`PROBE_STEP` channel units, each restored exactly from the saved float32
  channel values, then refined by up to :data:`MAX_NEWTON` Newton steps on the
  measured residual.  Locked axes are held to float precision here, and the
  report quotes the measured drift rather than claiming zero.

Precision floor
---------------
Blender stores locations and matrices as float32.  A channel written from a
double lands on the nearest float32, so a pivot at 50 mm lands within half an
ulp of 0.05 m — 1.9 nm, i.e. ``1.9e-6 mm`` per axis — of the solved target.
:data:`CONVERGED_MM` (1e-6 mm) is that floor rounded down; Newton stops there,
or as soon as a step fails to halve the residual (the floor grows with the
coordinate, so past ~50 mm the stagnation stop is the one that fires), and keeps
the best iterate, because below the floor the next step is chasing rounding.

Units
-----
Millimetres on the wire, metres in Blender, converted at the ``bpy`` boundary —
the house rule.  Parameters therefore carry ``_mm`` (``location_mm``,
``target_mm``, ``delta_mm``); the bare names ``location`` / ``target`` /
``delta`` are accepted as aliases and are **also millimetres**.  All readout
arithmetic runs in Python doubles over the float32 values Blender stores, so
the readout adds no rounding of its own.
"""

import json
import math

import bpy

from . import common
from .registry import ForgeError, command

#: The ledger: a JSON list on the object that owns the trackers.
RECORD_KEY = "forge_trackers"
RECORD_VERSION = 1

#: ``"<object>::<tracker>"`` names a tracker unambiguously across the scene.  Two
#: colons, not a dot or a slash: Blender object names carry ``.001`` suffixes
#: routinely and slashes occasionally, and neither may be mistaken for this.
QUALIFIER = "::"

KINDS = ("location", "vertex", "bone")
AXIS_NAMES = ("X", "Y", "Z")

#: A target whose predicted residual (after the best move the free axes allow)
#: exceeds this is refused.  100x the float32 landing floor at 50 mm: anything
#: larger is a real geometric shortfall, not rounding.
REACH_TOLERANCE_MM = 1.0e-4
#: Newton stops below this residual (the float32 landing floor, see docstring).
CONVERGED_MM = 1.0e-6
#: An applied grab whose measured residual still exceeds this is undone and
#: refused: the pivot did not respond affinely (a constraint, a driver, an
#: animated channel overriding the write).  1 micron — ten times the reach
#: tolerance, so a merely slow Newton converge is never mistaken for this.
ACCEPT_MM = 1.0e-3
MAX_NEWTON = 6
#: Finite step for the probed path, in channel units (metres for an object;
#: bone-local units for a pose bone).  10 mm: the map is affine, so the step
#: size only sets how many float32 ulps the measured slope carries (~4e-7
#: relative at this size), which Newton then removes.
PROBE_STEP = 0.01
#: A free axis whose column of ``K`` is shorter than this moves the pivot by
#: less than a micron per metre of handle move: the pivot does not follow that
#: axis, and the axis is dropped from the solve (and said so).
DEAD_RESPONSE = 1.0e-6

MM = common.MM_TO_M
M_TO_MM = common.M_TO_MM


# ---------------------------------------------------------------------------
# double-precision affine helpers (Blender's mathutils is single precision)
# ---------------------------------------------------------------------------

def _rows(matrix):
    return [[float(matrix[i][j]) for j in range(4)] for i in range(4)]


def _apply(rows, v):
    return [rows[i][0] * v[0] + rows[i][1] * v[1] + rows[i][2] * v[2] + rows[i][3]
            for i in range(3)]


def _inv3(a):
    """Inverse of a 3x3 (list of rows) in doubles, or None when singular."""
    c00 = a[1][1] * a[2][2] - a[1][2] * a[2][1]
    c01 = a[1][2] * a[2][0] - a[1][0] * a[2][2]
    c02 = a[1][0] * a[2][1] - a[1][1] * a[2][0]
    det = a[0][0] * c00 + a[0][1] * c01 + a[0][2] * c02
    scale = max(abs(value) for row in a for value in row) or 1.0
    if abs(det) <= 1e-15 * scale ** 3:
        return None
    inv_det = 1.0 / det
    return [
        [c00 * inv_det,
         (a[0][2] * a[2][1] - a[0][1] * a[2][2]) * inv_det,
         (a[0][1] * a[1][2] - a[0][2] * a[1][1]) * inv_det],
        [c01 * inv_det,
         (a[0][0] * a[2][2] - a[0][2] * a[2][0]) * inv_det,
         (a[0][2] * a[1][0] - a[0][0] * a[1][2]) * inv_det],
        [c02 * inv_det,
         (a[0][1] * a[2][0] - a[0][0] * a[2][1]) * inv_det,
         (a[0][0] * a[1][1] - a[0][1] * a[1][0]) * inv_det],
    ]


def _matmul3(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _world_to_local_mm(obj, world_mm):
    """A world point (mm) in ``obj``'s own frame (mm), in doubles."""
    rows = _rows(obj.matrix_world)
    linear = [row[:3] for row in rows[:3]]
    inverse = _inv3(linear)
    if inverse is None:
        raise ForgeError("%r has a singular world matrix (a zero scale?), so a point "
                         "cannot be held in its frame." % obj.name)
    offset = [world_mm[i] * MM - rows[i][3] for i in range(3)]
    return [sum(inverse[i][j] * offset[j] for j in range(3)) * M_TO_MM for i in range(3)]


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _norm(v):
    return math.sqrt(sum(value * value for value in v))


def _r(values, places=9):
    return [round(float(value), places) for value in values]


def _lstsq(columns, rhs):
    """Least-squares ``x`` for ``sum_j x_j * columns[j] ~= rhs`` (normal equations)."""
    n = len(columns)
    if n == 0:
        return []
    normal = [[sum(columns[a][i] * columns[b][i] for i in range(3)) for b in range(n)]
              for a in range(n)]
    vector = [sum(columns[a][i] * rhs[i] for i in range(3)) for a in range(n)]
    # Gaussian elimination with partial pivoting; n <= 3.
    aug = [normal[i] + [vector[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda row: abs(aug[row][col]))
        if abs(aug[pivot][col]) < 1e-18:
            raise ForgeError("The pivot's response to the free axes is degenerate "
                             "(two free axes move it the same way).")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        for row in range(n):
            if row != col:
                factor = aug[row][col] / aug[col][col]
                for k in range(col, n + 1):
                    aug[row][k] -= factor * aug[col][k]
    return [aug[i][n] / aug[i][i] for i in range(n)]


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------

def _vec3(params, keys, label):
    """Three finite numbers under the first present key of ``keys``, or None."""
    for key in keys:
        value = params.get(key)
        if value is None:
            continue
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise ForgeError("%s %r must be three numbers [x, y, z] in mm, got %r."
                             % (label, key, value))
        out = []
        for item in value:
            if isinstance(item, bool):
                raise ForgeError("%s %r must be numbers, got %r." % (label, key, value))
            try:
                item = float(item)
            except (TypeError, ValueError):
                raise ForgeError("%s %r must be numbers, got %r." % (label, key, value))
            if not math.isfinite(item):
                raise ForgeError("%s %r must be finite, got %r." % (label, key, value))
            out.append(item)
        return out, key
    return None, None


def _resolve(params):
    name = params.get("object")
    if name is None:
        name = params.get("object_name")
    return common.resolve_object({"object": name})


def _axis_locks(params):
    raw = params.get("axis_locks")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [ch for ch in raw if not ch.isspace() and ch not in ",;"]
    if not isinstance(raw, (list, tuple)):
        raise ForgeError("axis_locks must be a list of 'x', 'y', 'z' (or 0, 1, 2), "
                         "got %r." % (raw,))
    locks = set()
    for item in raw:
        if isinstance(item, bool):
            raise ForgeError("axis_locks entries must be x/y/z or 0/1/2, got %r." % (item,))
        if isinstance(item, int) and 0 <= item <= 2:
            locks.add(item)
        elif isinstance(item, str) and item.strip().upper() in AXIS_NAMES:
            locks.add(AXIS_NAMES.index(item.strip().upper()))
        else:
            raise ForgeError("axis_locks entries must be x/y/z or 0/1/2, got %r." % (item,))
    return sorted(locks)


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------

def records(obj):
    """The tracker ledger on ``obj`` as a list of dicts (empty when none)."""
    raw = obj.get(RECORD_KEY)
    if raw is None:
        return []
    if not isinstance(raw, str):
        raise ForgeError("Object %r carries a %r property that is not the JSON ledger "
                         "this module writes (got %s)." % (obj.name, RECORD_KEY,
                                                           type(raw).__name__))
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ForgeError("The tracker ledger on %r is not valid JSON (%s)." % (obj.name, exc))
    if not isinstance(parsed, list):
        raise ForgeError("The tracker ledger on %r is a %s, expected a list."
                         % (obj.name, type(parsed).__name__))
    return [entry for entry in parsed if isinstance(entry, dict) and entry.get("name")]


def write_records(obj, entries):
    if entries:
        obj[RECORD_KEY] = json.dumps(entries)
    elif RECORD_KEY in obj:
        del obj[RECORD_KEY]


def _armature_for(obj, name=None):
    """The armature a bone tracker on ``obj`` reads: named, itself, its modifier, its parent."""
    if name:
        arm = common.find_object(name)
        if arm.type != "ARMATURE":
            raise ForgeError("%r is a %s, not an ARMATURE." % (arm.name, arm.type))
        return arm
    if obj.type == "ARMATURE":
        return obj
    for modifier in getattr(obj, "modifiers", ()):
        if modifier.type == "ARMATURE" and modifier.object is not None:
            return modifier.object
    if obj.parent is not None and obj.parent.type == "ARMATURE":
        return obj.parent
    raise ForgeError("%r is a %s with no Armature modifier and no armature parent, so "
                     "a bone tracker on it has no armature to read; pass 'armature'."
                     % (obj.name, obj.type))


def _deformed_by(arm):
    """Objects whose shape depends on ``arm`` (modifier or armature parent)."""
    out = []
    for obj in bpy.data.objects:
        if obj is arm:
            continue
        if any(m.type == "ARMATURE" and m.object is arm for m in getattr(obj, "modifiers", ())):
            out.append(obj)
        elif obj.parent is arm:
            out.append(obj)
    return out


def _qualified(obj, entry):
    return "%s%s%s" % (obj.name, QUALIFIER, entry["name"])


def _index(scope=None):
    """Every tracker in scope: ``{qualified: (obj, entry)}`` and ``{bare: [qualified]}``."""
    objects = scope if scope is not None else list(bpy.data.objects)
    by_qualified = {}
    by_bare = {}
    skipped = []
    for obj in objects:
        if RECORD_KEY not in obj.keys():
            continue
        try:
            entries = records(obj)
        except ForgeError as exc:
            skipped.append(str(exc))
            continue
        for entry in entries:
            qualified = _qualified(obj, entry)
            by_qualified[qualified] = (obj, entry)
            by_bare.setdefault(entry["name"], []).append(qualified)
    return by_qualified, by_bare, skipped


def _lookup(name, by_qualified, by_bare, prefer=None):
    """Resolve a bare or qualified tracker name to ``(qualified, obj, entry)``."""
    if not isinstance(name, str) or not name.strip():
        raise ForgeError("Tracker names must be non-empty strings, got %r." % (name,))
    name = name.strip()
    if name in by_qualified:
        obj, entry = by_qualified[name]
        return name, obj, entry
    hits = by_bare.get(name, [])
    if prefer:
        for obj in prefer:
            qualified = "%s%s%s" % (obj.name, QUALIFIER, name)
            if qualified in by_qualified:
                return (qualified,) + by_qualified[qualified]
    if len(hits) == 1:
        return (hits[0],) + by_qualified[hits[0]]
    if len(hits) > 1:
        raise ForgeError("Tracker %r is ambiguous: %s. Qualify it as '<object>%s<tracker>'."
                         % (name, ", ".join(sorted(hits)), QUALIFIER))
    known = sorted(by_qualified)
    raise ForgeError("No tracker %r. Known: %s." % (
        name, ", ".join(known[:40]) + (" ..." if len(known) > 40 else "") if known
        else "none (register some with track_points)"))


# ---------------------------------------------------------------------------
# reading positions
# ---------------------------------------------------------------------------

def _bone_point_mm(arm, bone_name, end="head"):
    pose_bone = arm.pose.bones.get(bone_name) if arm.pose else None
    if pose_bone is None:
        raise ForgeError("Armature %r has no bone %r." % (arm.name, bone_name))
    point = pose_bone.tail if end == "tail" else pose_bone.head
    return [value * M_TO_MM for value in _apply(_rows(arm.matrix_world), point)]


def positions(pairs, refresh=True):
    """World positions (mm, doubles) for ``[(obj, entry), ...]``.

    Returns ``[(position_or_None, problem_or_None), ...]`` in the same order.
    One evaluated-mesh read per object, however many vertex trackers it has.
    """
    if refresh:
        common.refresh_view_layer()
    out = [None] * len(pairs)
    vertex_jobs = {}
    for index, (obj, entry) in enumerate(pairs):
        kind = entry.get("kind")
        try:
            if kind == "location":
                local = [value * MM for value in entry["local_mm"]]
                out[index] = ([value * M_TO_MM for value in
                               _apply(_rows(obj.matrix_world), local)], None)
            elif kind == "bone":
                arm = bpy.data.objects.get(entry.get("armature") or "")
                if arm is None or arm.type != "ARMATURE":
                    arm = _armature_for(obj)
                out[index] = (_bone_point_mm(arm, entry["bone"], entry.get("end", "head")),
                              None)
            elif kind == "vertex":
                vertex_jobs.setdefault(obj.name, (obj, []))[1].append(index)
            else:
                out[index] = (None, "unknown tracker kind %r" % kind)
        except (ForgeError, KeyError, ReferenceError) as exc:
            out[index] = (None, str(exc))

    if vertex_jobs:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for obj, indices in vertex_jobs.values():
            if obj.type != "MESH":
                for index in indices:
                    out[index] = (None, "%r is no longer a mesh" % obj.name)
                continue
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            try:
                rows = _rows(obj.matrix_world)
                count = len(mesh.vertices)
                base_count = len(obj.data.vertices)
                for index in indices:
                    vertex = pairs[index][1]["vertex_index"]
                    if count != base_count:
                        out[index] = (None, "the evaluated mesh of %r has %d vertices "
                                      "against %d in its data (a modifier changes the "
                                      "topology), so vertex %d has no stable identity"
                                      % (obj.name, count, base_count, vertex))
                    elif not 0 <= vertex < count:
                        out[index] = (None, "vertex %d is out of range (%r has %d)"
                                      % (vertex, obj.name, count))
                    else:
                        out[index] = ([value * M_TO_MM for value in
                                       _apply(rows, mesh.vertices[vertex].co)], None)
            finally:
                evaluated.to_mesh_clear()
    return out


def _describe(entry):
    kind = entry.get("kind")
    if kind == "location":
        return "point held at %s mm in the object's frame" % _r(entry["local_mm"], 6)
    if kind == "vertex":
        return "vertex %d of the evaluated (deformed) mesh" % entry["vertex_index"]
    return "posed %s of bone %r on %r" % (entry.get("end", "head"), entry.get("bone"),
                                          entry.get("armature"))


def _readout(pairs, labels=None):
    """``(rows, positions_by_label)`` for a probe-style report."""
    found = positions(pairs)
    rows = []
    by_label = {}
    for index, ((obj, entry), (position, problem)) in enumerate(zip(pairs, found)):
        label = labels[index] if labels else _qualified(obj, entry)
        row = {
            "name": entry["name"],
            "object": obj.name,
            "qualified": _qualified(obj, entry),
            "kind": entry.get("kind"),
            "world_mm": _r(position) if position is not None else None,
        }
        if problem:
            row["problem"] = problem
        rows.append(row)
        by_label[label] = row["world_mm"]
    return rows, by_label


# ---------------------------------------------------------------------------
# track_points
# ---------------------------------------------------------------------------

def _build_entry(obj, point, index):
    if not isinstance(point, dict):
        raise ForgeError("points[%d] must be an object {name, location_mm | vertex_index "
                         "| bone}, got %s." % (index, type(point).__name__))
    name = point.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ForgeError("points[%d] needs a non-empty 'name'." % index)
    name = name.strip()
    if QUALIFIER in name:
        raise ForgeError("Tracker name %r may not contain %r (it qualifies names as "
                         "'<object>%s<tracker>')." % (name, QUALIFIER, QUALIFIER))
    location, location_key = _vec3(point, ("location_mm", "location"), "points[%d]" % index)
    has_vertex = point.get("vertex_index") is not None
    has_bone = point.get("bone") is not None
    given = [label for label, present in (("location_mm", location is not None),
                                          ("vertex_index", has_vertex),
                                          ("bone", has_bone)) if present]
    if len(given) != 1:
        raise ForgeError("Tracker %r needs exactly one of location_mm (a world point), "
                         "vertex_index or bone; got %s." % (name, given or "none"))

    entry = {"name": name, "version": RECORD_VERSION}
    if location is not None:
        entry["kind"] = "location"
        entry["local_mm"] = _world_to_local_mm(obj, location)
    elif has_vertex:
        if obj.type != "MESH":
            raise ForgeError("Tracker %r: vertex trackers need a MESH, %r is a %s."
                             % (name, obj.name, obj.type))
        vertex = common.get_int(point, "vertex_index", minimum=0)
        if vertex >= len(obj.data.vertices):
            raise ForgeError("Tracker %r: vertex_index %d is out of range (%r has %d "
                             "vertices)." % (name, vertex, obj.name, len(obj.data.vertices)))
        entry["kind"] = "vertex"
        entry["vertex_index"] = vertex
    else:
        bone = common.get_str(point, "bone").strip()
        arm = _armature_for(obj, point.get("armature"))
        if arm.pose is None or arm.pose.bones.get(bone) is None:
            names = [b.name for b in arm.pose.bones] if arm.pose else []
            raise ForgeError("Tracker %r: armature %r has no bone %r. Bones: %s."
                             % (name, arm.name, bone, ", ".join(names[:40]) or "none"))
        end = str(point.get("end", "head")).strip().lower()
        if end not in ("head", "tail"):
            raise ForgeError("Tracker %r: 'end' must be head or tail, got %r." % (name, end))
        entry["kind"] = "bone"
        entry["bone"] = bone
        entry["armature"] = arm.name
        entry["end"] = end
    return entry


@command("track_points")
def cmd_track_points(params):
    """Register named trackers on an object (merged into its ledger by name)."""
    obj = _resolve(params)
    replace = common.get_bool(params, "replace", False)
    points = params.get("points")
    remove = params.get("remove")
    if points is None and remove is None and not replace:
        raise ForgeError("track_points needs 'points' (a list of {name, location_mm | "
                         "vertex_index | bone}), 'remove' (names) or replace=true.")
    if points is not None and not isinstance(points, (list, tuple)):
        raise ForgeError("'points' must be a list, got %s." % type(points).__name__)
    if remove is not None:
        if isinstance(remove, str):
            remove = [remove]
        if not isinstance(remove, (list, tuple)):
            raise ForgeError("'remove' must be a list of tracker names.")

    existing = records(obj)
    entries = [] if replace else list(existing)
    removed = []
    for name in remove or []:
        match = [entry for entry in entries if entry["name"] == name]
        if not match:
            raise ForgeError("No tracker %r on %r to remove. Known: %s."
                             % (name, obj.name, ", ".join(e["name"] for e in entries) or "none"))
        entries = [entry for entry in entries if entry["name"] != name]
        removed.append(name)

    # Build every entry before writing any: one bad point refuses the whole call.
    built = [_build_entry(obj, point, index) for index, point in enumerate(points or [])]
    names = [entry["name"] for entry in built]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ForgeError("Tracker names repeat within this call: %s." % ", ".join(duplicates))

    replaced = []
    for entry in built:
        if any(old["name"] == entry["name"] for old in entries):
            replaced.append(entry["name"])
        entries = [old for old in entries if old["name"] != entry["name"]]
        entries.append(entry)

    write_records(obj, entries)
    rows, by_name = _readout([(obj, entry) for entry in entries],
                             [entry["name"] for entry in entries])
    for row, entry in zip(rows, entries):
        row["definition"] = _describe(entry)
    return {
        "object": obj.name,
        "registered": names,
        "replaced": replaced,
        "removed": removed,
        "cleared": bool(replace),
        "count": len(entries),
        "trackers": rows,
        "positions_mm": by_name,
        "ledger_key": RECORD_KEY,
        "next": "probe(names=[...], pairs=[[a, b]]) after every change; grab_to to move "
                "by numbers. Qualify a name as '%s%s<tracker>' when two objects share it."
                % (obj.name, QUALIFIER),
    }


# ---------------------------------------------------------------------------
# probe / list_trackers — pure reads
# ---------------------------------------------------------------------------

def _names_param(params, key):
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        raise ForgeError("%r must be a list of tracker names." % key)
    return list(value)


@command("probe")
def cmd_probe(params):
    """Current world positions of trackers, and distances between pairs.  Writes nothing."""
    scope = None
    scope_name = params.get("object") or params.get("object_name")
    if scope_name:
        scope = [common.find_object(scope_name)]
    by_qualified, by_bare, skipped = _index(scope)

    names = _names_param(params, "names")
    raw_pairs = params.get("pairs")
    if raw_pairs is not None and (not isinstance(raw_pairs, (list, tuple)) or any(
            not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in raw_pairs)):
        raise ForgeError("'pairs' must be a list of [a, b] tracker-name pairs.")

    labels = []
    resolved = []
    seen = {}

    def want(label):
        qualified, obj, entry = _lookup(label, by_qualified, by_bare)
        if label not in seen:
            seen[label] = len(resolved)
            labels.append(label)
            resolved.append((obj, entry))
        return seen[label]

    if names is None:
        for qualified in sorted(by_qualified):
            obj, entry = by_qualified[qualified]
            # Bare label where it is unique, so the common case reads naturally.
            label = entry["name"] if len(by_bare[entry["name"]]) == 1 else qualified
            seen[label] = len(resolved)
            seen.setdefault(qualified, len(resolved))
            labels.append(label)
            resolved.append((obj, entry))
    else:
        for name in names:
            want(name)
    pair_slots = [(want(a), want(b), a, b) for a, b in (raw_pairs or [])]

    rows, by_label = _readout(resolved, labels)
    distances = []
    for slot_a, slot_b, a, b in pair_slots:
        pa, pb = rows[slot_a]["world_mm"], rows[slot_b]["world_mm"]
        if pa is None or pb is None:
            distances.append({"a": a, "b": b, "distance_mm": None,
                              "problem": rows[slot_a].get("problem")
                              or rows[slot_b].get("problem")})
            continue
        delta = _sub(pb, pa)
        distances.append({"a": a, "b": b, "distance_mm": round(_norm(delta), 9),
                          "delta_mm": _r(delta)})
    report = {
        "count": len(rows),
        "positions_mm": by_label,
        "trackers": rows,
        "distances": distances,
    }
    if skipped:
        report["skipped_ledgers"] = skipped
    return report


@command("list_trackers")
def cmd_list_trackers(params):
    """Every tracker ledger (or one object's), with definitions and current positions."""
    scope_name = params.get("object") or params.get("object_name")
    objects = [common.find_object(scope_name)] if scope_name else [
        obj for obj in bpy.data.objects if RECORD_KEY in obj.keys()]
    report = []
    for obj in objects:
        entries = records(obj)
        rows, _ = _readout([(obj, entry) for entry in entries])
        for row, entry in zip(rows, entries):
            row["definition"] = _describe(entry)
            row["resolves"] = row["world_mm"] is not None
        report.append({"object": obj.name, "count": len(entries), "trackers": rows})
    return {"objects": report, "count": sum(item["count"] for item in report)}


# ---------------------------------------------------------------------------
# grab_to
# ---------------------------------------------------------------------------

class _Mover:
    """The three translation channels a grab writes, and the handle they move."""

    def __init__(self, obj, pose_bone=None):
        self.obj = obj
        self.pose_bone = pose_bone
        self.owner = pose_bone if pose_bone is not None else obj
        self.saved = [float(value) for value in self.owner.location]
        self.exact = (pose_bone is None and obj.parent is None
                      and len(obj.constraints) == 0)

    def write(self, values, indices=(0, 1, 2)):
        location = self.owner.location
        for index in indices:
            location[index] = values[index]
        common.refresh_view_layer()

    def restore(self):
        self.write(self.saved)

    def handle_mm(self):
        if self.pose_bone is not None:
            return [value * M_TO_MM for value in
                    _apply(_rows(self.obj.matrix_world), self.pose_bone.head)]
        return [float(value) * M_TO_MM for value in self.obj.matrix_world.translation]

    def label(self):
        if self.pose_bone is not None:
            return "%s bone %r" % (self.obj.name, self.pose_bone.name)
        return repr(self.obj.name)


def _pivot(mover, params):
    """``(label, kind, read_fn, rigid)`` for the grab's pivot."""
    pivot = params.get("pivot", "origin")
    if pivot is None or (isinstance(pivot, str) and pivot.strip().lower() in ("", "origin")):
        return "origin", "origin", mover.handle_mm, True
    if not isinstance(pivot, str):
        raise ForgeError("'pivot' must be \"origin\" or a tracker name, got %r." % (pivot,))
    prefer = [mover.obj] + (_deformed_by(mover.obj) if mover.obj.type == "ARMATURE" else [])
    by_qualified, by_bare, _ = _index()
    qualified, owner, entry = _lookup(pivot, by_qualified, by_bare, prefer=prefer)

    def read():
        position, problem = positions([(owner, entry)])[0]
        if position is None:
            raise ForgeError("Pivot tracker %r does not resolve: %s." % (qualified, problem))
        return position

    kind = entry.get("kind")
    rigid = False
    if mover.pose_bone is None and owner is mover.obj:
        rigid = kind == "location" or (kind == "bone" and entry.get("armature") == owner.name)
    elif mover.pose_bone is not None and kind == "bone":
        rigid = (entry.get("armature") == mover.obj.name
                 and entry.get("bone") == mover.pose_bone.name
                 and entry.get("end", "head") == "head")
    return qualified, kind, read, rigid


def _grab_readout(obj):
    pairs = []
    for other in [obj] + (_deformed_by(obj) if obj.type == "ARMATURE" else []):
        if RECORD_KEY in other.keys():
            try:
                pairs.extend((other, entry) for entry in records(other))
            except ForgeError:
                continue
    return _readout(pairs) if pairs else ([], {})


def _axes(indices):
    return [AXIS_NAMES[index] for index in indices]


@command("grab_to")
def cmd_grab_to(params):
    """Translate an object or pose bone so its pivot lands on a target, locks held."""
    obj = _resolve(params)
    bone_name = params.get("bone")
    pose_bone = None
    if bone_name is not None:
        bone_name = common.get_str(params, "bone").strip()
        if obj.type != "ARMATURE":
            obj = _armature_for(obj)
        pose_bone = obj.pose.bones.get(bone_name) if obj.pose else None
        if pose_bone is None:
            raise ForgeError("Armature %r has no bone %r." % (obj.name, bone_name))
        if pose_bone.bone.use_connect:
            raise ForgeError(
                "Refusing to grab bone %r on %r: it is CONNECTED to its parent, so its head "
                "is pinned to %r's tail at %s mm and no translation of it exists. Grab the "
                "parent, or rotate it. NOTHING was moved."
                % (bone_name, obj.name, pose_bone.parent.name if pose_bone.parent else "?",
                   _r(_bone_point_mm(obj, bone_name), 6)))

    target, target_key = _vec3(params, ("target_mm", "target"), "Parameter")
    delta, delta_key = _vec3(params, ("delta_mm", "delta"), "Parameter")
    if (target is None) == (delta is None):
        raise ForgeError("grab_to needs exactly one of target_mm [x, y, z] (where the pivot "
                         "lands) or delta_mm [dx, dy, dz] (how far it moves), in mm.")
    locks = _axis_locks(params)

    common.refresh_view_layer()
    mover = _Mover(obj, pose_bone)
    pivot_label, pivot_kind, read_pivot, rigid = _pivot(mover, params)
    pivot_before = read_pivot()
    handle_before = mover.handle_mm()
    if target is None:
        target = [pivot_before[i] + delta[i] for i in range(3)]
    required = _sub(target, pivot_before)

    # --- the response: H (handle per channel) and K (pivot per handle move) ---
    notes = []
    if mover.exact:
        # Unparented and unconstrained: world translation IS the location channel.
        h_inverse = [[MM if i == j else 0.0 for j in range(3)] for i in range(3)]
    path = "exact" if (mover.exact and rigid) else "probed"
    k_matrix = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    if not (mover.exact and rigid):
        h_columns, j_columns = [], []
        try:
            for axis in range(3):
                stepped = list(mover.saved)
                stepped[axis] += PROBE_STEP
                mover.write(stepped)
                h_columns.append([(value - base) / PROBE_STEP for value, base in
                                  zip(mover.handle_mm(), handle_before)])
                j_columns.append([(value - base) / PROBE_STEP for value, base in
                                  zip(read_pivot(), pivot_before)])
        finally:
            mover.restore()
        h_matrix = [[h_columns[j][i] for j in range(3)] for i in range(3)]
        j_matrix = [[j_columns[j][i] for j in range(3)] for i in range(3)]
        if not mover.exact:
            h_inverse = _inv3(h_matrix)
            if h_inverse is None:
                raise ForgeError(
                    "Refusing to grab %s: its location channels do not move it in world "
                    "space (measured response %s mm per unit — a zero scale, or a "
                    "constraint that pins it). NOTHING was moved."
                    % (mover.label(), [_r(row, 6) for row in h_matrix]))
        if not rigid:
            k_matrix = _matmul3(j_matrix, h_inverse)

    free = [axis for axis in range(3) if axis not in locks]
    dead = [axis for axis in free
            if _norm([k_matrix[i][axis] for i in range(3)]) < DEAD_RESPONSE]
    if dead:
        notes.append("The pivot %r does not follow the grabbed thing along %s, so those "
                     "axes cannot help reach the target." % (pivot_label, _axes(dead)))
    solve_axes = [axis for axis in free if axis not in dead]
    columns = [[k_matrix[i][axis] for i in range(3)] for axis in solve_axes]

    solution = _lstsq(columns, required)
    reachable = [sum(columns[n][i] * solution[n] for n in range(len(columns)))
                 for i in range(3)]
    shortfall = _sub(required, reachable)
    if _norm(shortfall) > REACH_TOLERANCE_MM:
        closest = [pivot_before[i] + reachable[i] for i in range(3)]
        lines = [
            "Refusing to grab %s: with %s locked the pivot %r cannot reach the target."
            % (mover.label(), _axes(locks) or "no axes", pivot_label),
            "  pivot now      (%.6f, %.6f, %.6f) mm" % tuple(pivot_before),
            "  target         (%.6f, %.6f, %.6f) mm" % tuple(target),
            "  required move  (%.6f, %.6f, %.6f) mm" % tuple(required),
            "  closest reach  (%.6f, %.6f, %.6f) mm with the free axes %s"
            % (tuple(closest) + (_axes(solve_axes),)),
            "  shortfall      (%.6f, %.6f, %.6f) mm, |%.6f| mm > tolerance %.1e mm"
            % (tuple(shortfall) + (_norm(shortfall), REACH_TOLERANCE_MM)),
            "  Unlock the axis the shortfall is on, or give a target that keeps the "
            "locked coordinate(s).",
            "  NOTHING was moved.",
        ]
        raise ForgeError("\n".join(lines))

    # --- apply, then refine on the measured residual -----------------------
    # An exact mover's H is diagonal, so a locked (or dead) channel's value would be
    # its saved value plus 0.0 — still, it is not written at all, so "held exactly"
    # is a fact about the code path rather than about float arithmetic.
    touched = solve_axes if mover.exact else [0, 1, 2]

    def channels_for(d_free):
        move = [0.0, 0.0, 0.0]
        for n, axis in enumerate(solve_axes):
            move[axis] = d_free[n]
        return [mover.saved[i] + sum(h_inverse[i][k] * move[k] for k in range(3))
                for i in range(3)]

    best = None
    iterations = 0
    d_free = list(solution)
    try:
        for iterations in range(1, MAX_NEWTON + 1):
            mover.write(channels_for(d_free), touched)
            achieved = read_pivot()
            residual = _sub(target, achieved)
            size = _norm(residual)
            # Stagnation stop: once a step no longer halves the residual, the
            # residual is float32 rounding of the written channel (the floor
            # scales with the coordinate, so a fixed threshold cannot say this).
            stalled = best is not None and size > 0.5 * best[0]
            if best is None or size < best[0]:
                best = (size, list(d_free))
            if size < CONVERGED_MM or not solve_axes or stalled:
                break
            step = _lstsq(columns, residual)
            d_free = [d_free[n] + step[n] for n in range(len(step))]
        if best[1] != d_free:
            mover.write(channels_for(best[1]), touched)
        achieved = read_pivot()
        residual = _sub(target, achieved)
        if _norm(residual) > ACCEPT_MM:
            raise ForgeError(
                "Refusing to grab %s: after %d solve steps the pivot %r sits (%.6f, %.6f, "
                "%.6f) mm from the target (|%.6f| mm > %.1e mm). The pivot does not "
                "follow the channels affinely: a constraint, a driver or an animated "
                "location is overriding the write. NOTHING was moved."
                % ((mover.label(), iterations, pivot_label) + tuple(residual)
                   + (_norm(residual), ACCEPT_MM)))
    except Exception:
        mover.restore()
        raise

    handle_after = mover.handle_mm()
    moved = _sub(handle_after, handle_before)
    drift = {AXIS_NAMES[axis]: moved[axis] for axis in locks}
    trackers, by_name = _grab_readout(obj)
    return {
        "object": obj.name,
        "bone": pose_bone.name if pose_bone is not None else None,
        "applied": True,
        "mode": "target" if target_key else "delta",
        "given": {(target_key or delta_key): (params.get(target_key or delta_key))},
        "axis_locks": _axes(locks),
        "pivot": {
            "name": pivot_label,
            "kind": pivot_kind,
            "before_mm": _r(pivot_before),
            "after_mm": _r(achieved),
        },
        "target_mm": _r(target),
        "residual_mm": _r(residual, 12),
        "residual_norm_mm": _norm(residual),
        "moved_by_mm": _r(moved),
        "locked_axis_drift_mm": drift,
        "solve": {
            "path": path,
            "iterations": iterations,
            "pivot_rigid": rigid,
            "pivot_response": [_r(row, 9) for row in k_matrix],
            "channels_written": _axes(touched),
        },
        "trackers": trackers,
        "positions_mm": by_name,
        "notes": notes,
        "honesty": (
            "Positions are read back after the write, not predicted. On the exact path "
            "the locked location channels were never written, so the locked axes are "
            "unchanged bit for bit; on the probed path they are held to the drift quoted "
            "in locked_axis_drift_mm. Blender stores the channel as float32, so the "
            "residual floor is about |coordinate| x 6e-8."),
        "next": "probe to verify anything else; render only to judge appearance",
    }
