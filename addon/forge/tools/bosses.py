"""Reversible boss attachment — union a joint feature onto a sculpt, keep the cutter.

The problem this exists for, paid for twice on eevee-bowl-v2 (recorded in
``projects/eevee-bowl-v2/design/organic-rework-log.md``): a peg was unioned onto
a sculpted ear at the wrong angle, and **there was no way to take it back** —
nothing in the scene remembered what had been added, so the boolean could not be
run backwards.  The first attempt unioned a corrected peg *alongside* the wrong
one (two pegs, worse walls); the second gave up and regenerated both ears from
scratch.  Sculpted parts receive parametric joint features by boolean, and
iterating on the angle is the normal case, not the exception.  So the attach has
to be an operation with an inverse.

What makes it reversible
------------------------
The naive inverse — "subtract the peg again" — is wrong, and wrong by
millimetres, not by rounding.  A peg is *seated*: most of it is buried inside
the part.  ``target ∪ peg`` adds only the material outside the target, but
``target − peg`` removes the buried volume too, which was the target's own
material.  Measured on this module's own fixture: a 6 × 11 mm peg seated 70° into
a 25 mm sculpt, subtracted whole, leaves a **7.0 mm** bore where the part used to
be solid.

So what is retained is not only the cutter but **the material the union actually
added**:

    added = boss − target      (computed BEFORE the union, while target is still
                                the pre-attach surface)
    target' = target ∪ boss    (and by construction target' = target ⊎ added)
    target  = target' − added  (exactly, in exact arithmetic)

Two objects are therefore kept per attachment, both hidden in the
``forge_bosses`` collection:

* ``BOSS:<target>:<id>`` — the cutter as it was unioned.  This is what a
  ``move_boss`` re-unions at the new angle, so iteration never rebuilds a part;
* ``BOSS:<target>:<id>#added`` — the added solid.  This is what ``detach_boss``
  subtracts, and it is the reason the surface comes back instead of a bore.

The seam epsilon
----------------
Subtracting the added solid *exactly* means asking the boolean solver to cut
along surfaces it produced itself — coincident, coplanar faces — and it answers
with slivers: at zero inflation the detached surface departs from the pre-attach
surface by **4.94 mm** (measured, fixture below).  The added solid is therefore
inflated along its vertex normals before the cut, by a fraction of the
**target's own median edge length**, because the seam lives on the target's
surface at the target's resolution — an absolute epsilon would be a hole in a
5 mm print and a rounding error in a 500 mm one.

:data:`SEAM_EPSILON_FRACTION` is 0.02.  Measured on the fixture (a 48×24 UV
sphere of radius 25 mm, vertices jittered ±0.6 mm along their normals, median
edge 3.247 mm, so epsilon = 0.065 mm), sweeping the fraction:

===========  ===========  ==================================  ================
fraction     epsilon      surface departure from pre-attach   volume error
===========  ===========  ==================================  ================
0.0          0.0 mm       4.937 mm  (sliver — unusable)        +0.00026 %
0.005        0.016 mm     0.096 mm                             −0.00020 %
0.01         0.032 mm     0.106 mm                             −0.00077 %
**0.02**     0.065 mm     **0.124 mm**                         −0.00196 %
0.05         0.162 mm     0.181 mm                             −0.00587 %
0.10         0.325 mm     0.325 mm                             −0.01302 %
===========  ===========  ==================================  ================

Two things that table says out loud.  First, *any* inflation fixes the sliver —
the cliff is between 0.0 and 0.005, not between 0.02 and 0.05.  Second, below
about 0.02 the departure stops improving: it bottoms out near 0.1 mm, which is
not the epsilon at all but the fixture's own ±0.6 mm facet noise being re-cut at
the seam ring (a new edge chords across a noisy triangle fan).  0.02 is chosen
as the largest fraction that is still under that floor — big enough that the
solver never sees coincident faces, small enough that the epsilon is not what
you measure afterwards.  It is a parameter (``seam_epsilon_mm``) on both
``detach_boss`` and ``move_boss`` for the part that disagrees.

What "returns to its pre-attach surface" means, precisely
---------------------------------------------------------
Not "returns to the pre-attach mesh".  Vertex counts do not come back (the cut
re-triangulates the seam ring: 1106 → 1208 on the fixture) and no hash of the
mesh will ever match again.  What comes back is the **surface**, and the honest
statement of it is two measured numbers, both reported:

* volume, back to within 0.002 % of pre-attach (−1.27 mm³ of 64 788 mm³);
* the surface itself, within 0.124 mm Hausdorff of the pre-attach surface, of
  which 0.065 mm is the epsilon and the rest is the re-cut facet noise.

Every report here therefore quotes volumes rather than claiming an identity it
cannot have.

What this is NOT
----------------
* not a mirror-duplicate (attaching the mirrored boss on the other side) and not
  an auto-seat (finding where on the surface the boss belongs).  Both are real
  and both are other lanes;
* not a socket generator.  The mating negative is the geometry service's
  ``forge_lib.socket_for``, which owns the peg contract — the ``spec`` primitives
  here are deliberately plain (cylinder, cone, cylinder+rib, optional skirt) and
  deliberately do **not** re-implement the service's keyed, chamfered production
  peg.  For that peg, generate it as a part and attach it with ``boss=<object>``;
* not undo.  ``detach_boss`` is a forward operation that happens to invert an
  earlier one; Ctrl+Z still works and is still cheaper for "I just did that".

Units
-----
Millimetres on the wire, metres in Blender, converted once at the ``bpy``
boundary — the house rule.  Every number in a record and in a report is a
millimetre (or mm³); the stored ``matrix_mm`` is a 4×4 with its translation
column in millimetres.
"""

import json
import math

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

from . import common
from .registry import ForgeError, command

#: Where retained cutters live.  Hidden from both the viewport and the render:
#: an artist who opens this file should see their part, not a drawer of stumps.
BOSS_COLLECTION = "forge_bosses"

#: The ledger, a JSON string in a custom property on the **target** object.  A
#: string rather than an ID-property dict because nested lists of dicts do not
#: survive a .blend round trip intact, and this ledger has exactly one job:
#: still being there tomorrow.
RECORD_KEY = "forge_bosses"

#: Written on every retained object, so the ledger can be repaired from the
#: scene (and a renamed object still found) rather than only from the names.
BOSS_ID_KEY = "forge_boss_id"
BOSS_TARGET_KEY = "forge_boss_target"
BOSS_ROLE_KEY = "forge_boss_role"  # "cutter" or "added"

#: Bumped when the geometry or the record shape changes, so an old record says so.
RECORD_VERSION = 1

#: Fraction of the target's median edge length used to inflate the added solid
#: before the detach cut.  Derived in the module docstring's table.
SEAM_EPSILON_FRACTION = 0.02
#: Floor and ceiling on the derived epsilon.  The floor keeps a freakishly dense
#: mesh from deriving an epsilon smaller than the solver's own merge threshold
#: (1e-6 m); the ceiling keeps a coarse blockout from gouging a visible trench.
SEAM_EPSILON_MIN_MM = 0.002
SEAM_EPSILON_MAX_MM = 0.5

#: Cylinder/cone segment count for ``spec`` primitives.  Fixed, not derived: a
#: boss that changes its own tessellation between runs would make every digest
#: in every suite downstream nondeterministic.
PRIMITIVE_SEGMENTS = 32

MM = common.MM_TO_M
M3_TO_MM3 = 1.0e9


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------

def _world_bmesh(obj, triangulate=False):
    """The object's own mesh, in world space.  Caller frees it.

    Deliberately the object's mesh datablock rather than the evaluated one: both
    booleans here are applied, so by the time anything is measured the modifier
    stack is empty, and measuring the evaluated mesh would quietly fold an
    artist's unrelated subsurf into a volume this module reports as geometry.
    """
    if obj.type != "MESH" or obj.data is None:
        raise ForgeError("Object %r is a %s, this command needs a MESH." % (obj.name, obj.type))
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    if triangulate and bm.faces:
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
    bm.normal_update()
    return bm


def volume_mm3(obj):
    """Enclosed volume in mm³.  Unsigned: an inverted normal is not a negative part."""
    bm = _world_bmesh(obj, triangulate=True)
    try:
        if not bm.faces:
            return 0.0
        return abs(bm.calc_volume(signed=True)) * M3_TO_MM3
    finally:
        bm.free()


def median_edge_mm(obj):
    """Median edge length in mm — the mesh's own idea of how fine it is."""
    bm = _world_bmesh(obj)
    try:
        lengths = sorted(edge.calc_length() for edge in bm.edges)
    finally:
        bm.free()
    if not lengths:
        return 0.0
    count = len(lengths)
    middle = (lengths[count // 2] if count % 2
              else 0.5 * (lengths[count // 2 - 1] + lengths[count // 2]))
    return middle * common.M_TO_MM


def seam_epsilon_mm(target, override=None):
    """The inflation for the detach cut, and the sentence explaining it.

    Returns ``(epsilon_mm, basis)``.  ``override`` (a millimetre value) is taken
    as given — a part whose seam disagrees with the fraction is a measurement,
    not a bug, and the number it wants belongs in the report either way.
    """
    if override is not None:
        return float(override), "given explicitly"
    median = median_edge_mm(target)
    raw = SEAM_EPSILON_FRACTION * median
    epsilon = min(max(raw, SEAM_EPSILON_MIN_MM), SEAM_EPSILON_MAX_MM)
    basis = ("%.4g x median edge %.4g mm = %.4g mm"
             % (SEAM_EPSILON_FRACTION, median, raw))
    if abs(epsilon - raw) > 1e-12:
        basis += (", clamped to %.4g mm" % epsilon)
    return epsilon, basis


# ---------------------------------------------------------------------------
# the retained-object drawer
# ---------------------------------------------------------------------------

def boss_collection(create=True):
    """The hidden collection retained cutters live in, created on demand."""
    collection = bpy.data.collections.get(BOSS_COLLECTION)
    if collection is None:
        if not create:
            return None
        collection = bpy.data.collections.new(BOSS_COLLECTION)
    scene = common.get_scene()
    linked = {child.name for child in common._iter_collections(scene.collection)}
    if collection.name not in linked:
        try:
            scene.collection.children.link(collection)
        except RuntimeError:
            pass
    # Hidden in both senses, every call: an artist who unhid the drawer to look
    # at a stump should not have the next attach leave it showing.
    collection.hide_render = True
    collection.hide_viewport = True
    return collection


def _stow(obj, boss_id, target_name, role):
    """Move ``obj`` into the hidden drawer and stamp it with its identity."""
    collection = boss_collection()
    for other in list(obj.users_collection):
        try:
            other.objects.unlink(obj)
        except RuntimeError:
            pass
    collection.objects.link(obj)
    obj[BOSS_ID_KEY] = boss_id
    obj[BOSS_TARGET_KEY] = target_name
    obj[BOSS_ROLE_KEY] = role
    common.refresh_view_layer()
    # The object's own flags as well as the collection's: a collection can be
    # unhidden by one click, and the stump should still not be in the way.
    try:
        obj.hide_render = True
        obj.hide_viewport = True
    except (AttributeError, ReferenceError):
        pass
    return obj


def _retained_name(target_name, boss_id, role):
    base = "BOSS:%s:%s" % (target_name[:24], boss_id[:20])
    return base if role == "cutter" else base + "#added"


def _find_retained(record, role):
    """The retained object for ``role``, by stamp first and by name second.

    The stamp is checked first because an artist renaming an object is normal
    and a ledger that only knows names would strand a boss for it.
    """
    boss_id = record.get("id")
    target = record.get("target")
    collection = boss_collection(create=False)
    if collection is not None:
        for obj in collection.objects:
            if (obj.get(BOSS_ID_KEY) == boss_id
                    and obj.get(BOSS_TARGET_KEY) == target
                    and obj.get(BOSS_ROLE_KEY) == role):
                return obj
    name = record.get("cutter" if role == "cutter" else "added")
    if isinstance(name, str) and name:
        obj = bpy.data.objects.get(name)
        if obj is not None and obj.type == "MESH":
            return obj
    return None


def _require_retained(record, role):
    obj = _find_retained(record, role)
    if obj is None:
        raise ForgeError(
            "Boss %r on %r has a record but its retained %s object is gone "
            "(expected %r in the %r collection). Nothing can be detached without "
            "it; delete the record with detach_boss force=true if the part is to "
            "keep the union."
            % (record.get("id"), record.get("target"), role,
               record.get("cutter" if role == "cutter" else "added"), BOSS_COLLECTION))
    return obj


def _discard(obj):
    """Remove a retained object and its mesh, quietly."""
    if obj is None:
        return None
    name = obj.name
    data = obj.data
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except (ReferenceError, RuntimeError):
        return None
    if data is not None and data.users == 0:
        try:
            bpy.data.meshes.remove(data)
        except (ReferenceError, RuntimeError):
            pass
    return name


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------

def records(obj):
    """The boss ledger on ``obj`` as a list of dicts (empty when there is none)."""
    raw = obj.get(RECORD_KEY)
    if raw is None:
        return []
    if not isinstance(raw, str):
        raise ForgeError(
            "Object %r carries a %r property that is not the JSON ledger this "
            "module writes (got %s). Refusing to guess at it."
            % (obj.name, RECORD_KEY, type(raw).__name__))
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ForgeError(
            "The boss ledger on %r is not valid JSON (%s). It was written by "
            "something other than attach_boss, or edited by hand."
            % (obj.name, exc))
    if not isinstance(parsed, list):
        raise ForgeError(
            "The boss ledger on %r is a %s, expected a list of records."
            % (obj.name, type(parsed).__name__))
    return [entry for entry in parsed if isinstance(entry, dict)]


def write_records(obj, entries):
    if entries:
        obj[RECORD_KEY] = json.dumps(entries)
    elif RECORD_KEY in obj:
        del obj[RECORD_KEY]


def find_record(obj, boss_id, entries=None):
    entries = records(obj) if entries is None else entries
    if not entries:
        raise ForgeError(
            "Object %r has no boss records — nothing has been attached to it "
            "(or the attachment predates this tool and cannot be undone)."
            % obj.name)
    for entry in entries:
        if entry.get("id") == boss_id:
            return entry
    raise ForgeError(
        "No boss %r on %r. Known: %s."
        % (boss_id, obj.name, ", ".join(repr(e.get("id")) for e in entries)))


def _next_boss_id(entries):
    used = {entry.get("id") for entry in entries}
    index = 1
    while True:
        candidate = "boss-%02d" % index
        if candidate not in used:
            return candidate
        index += 1


def _ledger_summary(entries):
    """The bookkeeping block every report carries."""
    return {
        "count": len(entries),
        "ids": [entry.get("id") for entry in entries],
        "collection": BOSS_COLLECTION,
        "retained_objects": [
            name for entry in entries
            for name in (entry.get("cutter"), entry.get("added")) if name
        ],
    }


# ---------------------------------------------------------------------------
# transforms
# ---------------------------------------------------------------------------

def matrix_to_mm(matrix):
    """A 4×4 as 16 row-major floats with the translation column in millimetres."""
    rows = []
    for row_index in range(4):
        row = list(matrix[row_index])
        if row_index < 3:
            row[3] *= common.M_TO_MM
        rows.extend(float(value) for value in row)
    return rows


def matrix_from_mm(values):
    if len(values) != 16:
        raise ForgeError("A matrix needs 16 numbers (row-major), got %d." % len(values))
    rows = []
    for row_index in range(4):
        row = [float(v) for v in values[row_index * 4:row_index * 4 + 4]]
        if row_index < 3:
            row[3] *= MM
        rows.append(row)
    return Matrix(rows)


def _float_triple(params, key, default=None):
    value = params.get(key)
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value),) * 3
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ForgeError("Parameter %r must be three numbers, got %r." % (key, value))
    out = []
    for item in value:
        try:
            item = float(item)
        except (TypeError, ValueError):
            raise ForgeError("Parameter %r must be three numbers, got %r." % (key, value))
        if not math.isfinite(item):
            raise ForgeError("Parameter %r must be finite, got %r." % (key, value))
        out.append(item)
    return tuple(out)


def transform_from_params(params, default=None):
    """The world matrix the caller asked for, in Blender units.

    ``matrix_mm`` wins outright.  Otherwise ``location_mm`` / ``rotation_deg`` /
    ``scale`` each fall back to ``default``'s decomposition, which is what makes
    "same place, new angle" — the actual iteration — one parameter long.
    """
    raw = params.get("matrix_mm")
    if raw is not None:
        if isinstance(raw, (list, tuple)) and len(raw) == 4 and all(
                isinstance(row, (list, tuple)) for row in raw):
            raw = [value for row in raw for value in row]
        if not isinstance(raw, (list, tuple)):
            raise ForgeError("Parameter 'matrix_mm' must be a list of numbers.")
        return matrix_from_mm(raw), "matrix_mm"

    base = Matrix.Identity(4) if default is None else default.copy()
    location, rotation, scale = base.decompose()
    euler = rotation.to_euler("XYZ")

    given_location = _float_triple(params, "location_mm")
    given_rotation = _float_triple(params, "rotation_deg")
    given_scale = _float_triple(params, "scale")

    if given_location is not None:
        location = Vector([value * MM for value in given_location])
    if given_rotation is not None:
        euler = Euler([math.radians(value) for value in given_rotation], "XYZ")
    if given_scale is not None:
        scale = Vector(given_scale)
        if min(abs(value) for value in scale) < 1e-9:
            raise ForgeError("Parameter 'scale' must not be zero on any axis.")

    named = [name for name, value in (("location_mm", given_location),
                                      ("rotation_deg", given_rotation),
                                      ("scale", given_scale)) if value is not None]
    return Matrix.LocRotScale(location, euler, scale), ("+".join(named) or "unchanged")


def describe_transform(matrix):
    location, rotation, scale = matrix.decompose()
    euler = rotation.to_euler("XYZ")
    return {
        "location_mm": [round(value * common.M_TO_MM, 4) for value in location],
        "rotation_deg": [round(math.degrees(value), 4) for value in euler],
        "scale": [round(value, 6) for value in scale],
    }


# ---------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------

def _piece_object(bm, name):
    """One primitive piece as its own object, so it can be welded by boolean.

    Pieces are built separately and unioned rather than dropped into one bmesh
    together, and this is not fastidiousness: a cylinder, a rib and a skirt in
    one mesh are three overlapping shells, and handing that to the exact solver
    as an operand **deletes the target** (measured on 5.0.1 — the sculpt came
    back with zero vertices). A boss is a solid or it is nothing.
    """
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    mesh.update()
    mesh.validate(verbose=False)
    obj = bpy.data.objects.new(name, mesh)
    common.get_scene().collection.objects.link(obj)
    common.refresh_view_layer()
    return obj


def _cone_mesh(bm, bottom_d_mm, top_d_mm, length_mm, z_offset_mm=0.0):
    result = bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=PRIMITIVE_SEGMENTS,
        radius1=bottom_d_mm * 0.5 * MM,
        radius2=top_d_mm * 0.5 * MM,
        depth=length_mm * MM,
    )
    # create_cone builds about the origin; every boss in this module has its
    # base on local Z=0 and grows along +Z, which is the convention
    # ``forge_lib.peg`` uses and therefore the one a part script's Pos/Rot
    # already assumes.
    bmesh.ops.translate(
        bm,
        verts=result["verts"],
        vec=Vector((0.0, 0.0, (length_mm * 0.5 + z_offset_mm) * MM)),
    )
    return result["verts"]


def _spec_float(spec, key, default=None, minimum=1e-6):
    value = spec.get(key, default)
    if value is None:
        raise ForgeError("Boss spec is missing %r." % key)
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ForgeError("Boss spec %r must be a number, got %r." % (key, value))
    if not math.isfinite(value) or value < minimum:
        raise ForgeError("Boss spec %r must be a finite number >= %g (got %r)."
                         % (key, minimum, value))
    return value


def build_primitive(spec, name):
    """A boss object from a plain spec dict.  Base on local Z=0, growing +Z.

    Kinds:

    * ``cylinder`` — ``diameter_mm``, ``length_mm``;
    * ``cone`` — ``bottom_diameter_mm``, ``top_diameter_mm``, ``length_mm``;
    * ``peg`` — a cylinder plus the anti-rotation ``rib`` (a box overlapping the
      wall at +X, as ``forge_lib.peg`` builds it) and an optional ``skirt``
      (the fillet cone from the ``eevee-bowl-v2-peg-skirt`` part: wide at the
      buried end, tapering to the peg radius, sitting *below* Z=0 so the same
      Pos/Rot serves both).
    """
    if not isinstance(spec, dict):
        raise ForgeError("Parameter 'spec' must be an object, got %s." % type(spec).__name__)
    kind = spec.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ForgeError("Boss spec needs a 'kind' of cylinder, cone or peg.")
    kind = kind.strip().lower()

    def piece(build, piece_name):
        bm = bmesh.new()
        try:
            build(bm)
            return _piece_object(bm, piece_name)
        finally:
            bm.free()

    pieces = []
    try:
        if kind == "cylinder":
            diameter = _spec_float(spec, "diameter_mm")
            length = _spec_float(spec, "length_mm")
            resolved = {"kind": kind, "diameter_mm": diameter, "length_mm": length}
            pieces.append(piece(lambda bm: _cone_mesh(bm, diameter, diameter, length),
                                name))
        elif kind == "cone":
            bottom = _spec_float(spec, "bottom_diameter_mm")
            top = _spec_float(spec, "top_diameter_mm", minimum=0.0)
            length = _spec_float(spec, "length_mm")
            resolved = {"kind": kind, "bottom_diameter_mm": bottom,
                        "top_diameter_mm": top, "length_mm": length}
            pieces.append(piece(lambda bm: _cone_mesh(bm, bottom, top, length), name))
        elif kind == "peg":
            diameter = _spec_float(spec, "diameter_mm")
            length = _spec_float(spec, "length_mm")
            resolved = {"kind": kind, "diameter_mm": diameter, "length_mm": length}
            pieces.append(piece(lambda bm: _cone_mesh(bm, diameter, diameter, length),
                                name))

            rib = spec.get("rib")
            if isinstance(rib, dict):
                width = _spec_float(rib, "width_mm")
                height = _spec_float(rib, "height_mm")
                rib_length = _spec_float(rib, "length_mm", default=length)
                resolved["rib"] = {"width_mm": width, "height_mm": height,
                                   "length_mm": rib_length}

                def build_rib(bm):
                    cube = bmesh.ops.create_cube(bm, size=1.0)["verts"]
                    bmesh.ops.scale(
                        bm, verts=cube,
                        vec=Vector((2.0 * height * MM, width * MM, rib_length * MM)))
                    bmesh.ops.translate(
                        bm, verts=cube,
                        vec=Vector((diameter * 0.5 * MM, 0.0, rib_length * 0.5 * MM)))

                pieces.append(piece(build_rib, name + "#rib"))
            elif rib is not None:
                raise ForgeError("Boss spec 'rib' must be an object with width_mm "
                                 "and height_mm, got %s." % type(rib).__name__)

            skirt = spec.get("skirt")
            if isinstance(skirt, dict):
                flare = _spec_float(skirt, "flare_mm")
                height = _spec_float(skirt, "height_mm")
                resolved["skirt"] = {"flare_mm": flare, "height_mm": height}
                pieces.append(piece(
                    lambda bm: _cone_mesh(bm, diameter + 2.0 * flare, diameter,
                                          height, z_offset_mm=-height),
                    name + "#skirt"))
            elif skirt is not None:
                raise ForgeError("Boss spec 'skirt' must be an object with flare_mm "
                                 "and height_mm, got %s." % type(skirt).__name__)
        else:
            raise ForgeError("Unknown boss kind %r; known kinds are cylinder, cone, peg."
                             % kind)

        base = pieces[0]
        for extra in pieces[1:]:
            _boolean(base, extra, "UNION")
        for extra in pieces[1:]:
            _discard(extra)
        pieces = [base]
    except Exception:
        for leftover in pieces:
            _discard(leftover)
        raise

    base.name = name
    return base, resolved


# ---------------------------------------------------------------------------
# booleans
# ---------------------------------------------------------------------------

def _boolean(target, operand, operation):
    """Apply one boolean of ``operand`` onto ``target``.

    The operand is left hidden: Blender evaluates a boolean's operand because a
    modifier references it, not because it is visible (verified on 5.0.1 — a
    hidden operand and a shown one produce byte-identical results), and unhiding
    the drawer for every cut would be a visible flicker for nothing.
    """
    with common.object_mode():
        modifier = target.modifiers.new(name="Forge Boss", type="BOOLEAN")
        modifier.object = operand
        modifier.operation = operation
        available = common.enum_items(modifier, "solver")
        if "EXACT" in available:
            # Never FAST: it is a float-tolerance solver, and the whole contract
            # here is that the added solid is the exact complement of the union.
            modifier.solver = "EXACT"
        if hasattr(modifier, "use_self"):
            # Bosses arrive as artist geometry as often as not, and an operand
            # made of overlapping shells silently ANNIHILATES the target without
            # it (measured: the sculpt came back with zero vertices). The cost is
            # solver time; the alternative is losing the part.
            modifier.use_self = True
        common.apply_modifier(target, modifier)


def _inflated_copy(source, epsilon_mm, name):
    """A world-space copy of ``source`` pushed out ``epsilon_mm`` along its normals.

    World space, with the matrix baked in and the object left at the identity,
    so the offset is a true millimetre distance even when the boss carries a
    non-uniform scale — offsetting in local space would make the epsilon an
    ellipsoid and the report a lie.
    """
    bm = _world_bmesh(source)
    try:
        offset = epsilon_mm * MM
        if offset:
            for vert in bm.verts:
                vert.co += vert.normal * offset
        mesh = bpy.data.meshes.new(name)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    boss_collection().objects.link(obj)
    obj.hide_render = True
    common.refresh_view_layer()
    return obj


def _added_solid(boss, target, boss_id, name):
    """``boss − target``: the material a union of ``boss`` would add.

    Computed while ``target`` is still the pre-attach surface — that ordering is
    the whole mechanism, and there is no way to recover this solid afterwards.
    """
    copy = boss.copy()
    copy.data = boss.data.copy()
    copy.name = name
    common.get_scene().collection.objects.link(copy)
    common.refresh_view_layer()
    _boolean(copy, target, "DIFFERENCE")
    copy.name = name
    return copy


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------

def _resolve_target(params):
    target = common.resolve_object(params, mesh_only=True)
    common.require_in_view_layer(target)
    return target


def _attach(target, boss, boss_id, label, kind, source_name, spec, entries):
    """Union ``boss`` into ``target``, retaining both halves.  Returns a record."""
    volume_before = volume_mm3(target)
    boss_volume = volume_mm3(boss)
    median = median_edge_mm(target)

    added = _added_solid(boss, target, boss_id,
                         _retained_name(target.name, boss_id, "added"))
    try:
        added_volume = volume_mm3(added)

        _boolean(target, boss, "UNION")
        volume_after = volume_mm3(target)

        _stow(boss, boss_id, target.name, "cutter")
        boss.name = _retained_name(target.name, boss_id, "cutter")
        _stow(added, boss_id, target.name, "added")
        added.name = _retained_name(target.name, boss_id, "added")
    except Exception:
        # Half an attach leaves no orphan in the drawer: the added solid is this
        # function's own and nothing else has seen it yet.
        _discard(added)
        raise

    record = {
        "id": boss_id,
        "label": label,
        "target": target.name,
        "kind": kind,
        "source": source_name,
        "spec": spec,
        "cutter": boss.name,
        "added": added.name,
        "matrix_mm": matrix_to_mm(boss.matrix_world),
        "boss_volume_mm3": round(boss_volume, 6),
        "added_volume_mm3": round(added_volume, 6),
        "volume_before_mm3": round(volume_before, 6),
        "volume_after_mm3": round(volume_after, 6),
        "median_edge_mm": round(median, 6),
        "version": RECORD_VERSION,
    }
    entries.append(record)
    write_records(target, entries)
    return record


def _attach_report(target, record, entries, extra=None):
    grew = record["volume_after_mm3"] - record["volume_before_mm3"]
    buried = 0.0
    if record["boss_volume_mm3"] > 0.0:
        buried = 100.0 * (1.0 - record["added_volume_mm3"] / record["boss_volume_mm3"])
    epsilon, epsilon_basis = seam_epsilon_mm(target)
    notes = []
    if record["added_volume_mm3"] <= 0.0:
        notes.append(
            "The boss added NOTHING: it is entirely inside %r, so the union "
            "changed no surface and a detach will only drop the record. Move it "
            "outwards (move_boss) or check its transform." % target.name)
    elif buried < 1.0:
        notes.append(
            "The boss is barely seated (%.2f%% of it is buried): it meets the "
            "part on almost no surface, which is a weak join and a boolean the "
            "solver may cut at a tangent." % buried)
    report = {
        "target": target.name,
        "boss_id": record["id"],
        "label": record["label"],
        "kind": record["kind"],
        "source": record["source"],
        "spec": record["spec"],
        "transform": describe_transform(matrix_from_mm(record["matrix_mm"])),
        "volume_before_mm3": round(record["volume_before_mm3"], 4),
        "volume_after_mm3": round(record["volume_after_mm3"], 4),
        "volume_added_mm3": round(grew, 4),
        "boss_volume_mm3": round(record["boss_volume_mm3"], 4),
        "retained_added_volume_mm3": round(record["added_volume_mm3"], 4),
        "buried_pct": round(buried, 3),
        "median_edge_mm": round(record["median_edge_mm"], 4),
        "seam_epsilon_mm": round(epsilon, 5),
        "seam_epsilon_basis": epsilon_basis,
        "retained": {
            "collection": BOSS_COLLECTION,
            "cutter": record["cutter"],
            "added": record["added"],
            "hidden": True,
        },
        "bosses": _ledger_summary(entries),
        "notes": notes,
        "honesty": (
            "The retained pair is what makes this reversible: the cutter is "
            "re-unioned by move_boss, the added solid (boss minus the pre-attach "
            "target) is what detach_boss subtracts. Delete either by hand and "
            "this attachment stops being undoable."),
        "next": "detach_boss or move_boss with boss_id=%r" % record["id"],
    }
    if extra:
        report.update(extra)
    return report


@command("attach_boss")
def cmd_attach_boss(params):
    """Union a joint feature onto a part and keep the cutter."""
    target = _resolve_target(params)
    entries = records(target)

    boss_name = params.get("boss")
    spec = params.get("spec")
    if (boss_name is None) == (spec is None):
        raise ForgeError(
            "attach_boss needs exactly one of 'boss' (an existing object to "
            "union) or 'spec' (a primitive to build).")

    boss_id = params.get("boss_id")
    if boss_id is None:
        boss_id = _next_boss_id(entries)
    else:
        boss_id = common.get_str(params, "boss_id").strip()
        if any(entry.get("id") == boss_id for entry in entries):
            raise ForgeError(
                "%r already carries a boss called %r. Pick another id, or move "
                "the existing one with move_boss." % (target.name, boss_id))
    label = common.get_str(params, "label", "", allow_empty=True)

    if spec is not None:
        boss, resolved_spec = build_primitive(spec, _retained_name(target.name, boss_id, "cutter"))
        kind = "spec:" + resolved_spec["kind"]
        source_name = ""
        default_matrix = Matrix.Identity(4)
        consume = False
    else:
        source = common.find_object(common.get_str(params, "boss"), mesh_only=True)
        if source is target:
            raise ForgeError("The boss must be a different object from the target.")
        if source.get(BOSS_ID_KEY) is not None:
            raise ForgeError(
                "%r is itself a retained boss cutter; attaching it again would "
                "make the drawer its own source. Duplicate it first." % source.name)
        resolved_spec = None
        kind = "object"
        source_name = source.name
        default_matrix = source.matrix_world.copy()
        consume = common.get_bool(params, "consume_source", True)
        boss = source.copy()
        boss.data = source.data.copy()
        common.get_scene().collection.objects.link(boss)
        common.refresh_view_layer()

    matrix, how = transform_from_params(params, default_matrix)
    boss.matrix_world = matrix
    common.refresh_view_layer()

    try:
        record = _attach(target, boss, boss_id, label, kind, source_name,
                         resolved_spec, entries)
    except Exception:
        # A half-done attach leaves no drawer junk behind: the cutter copy and
        # the added solid are this command's own, and nothing else owns them.
        for leftover in (boss,):
            if leftover is not None and leftover.name in bpy.data.objects:
                _discard(leftover)
        raise

    consumed = ""
    if spec is None and consume:
        consumed = _discard(bpy.data.objects.get(source_name)) or ""

    return _attach_report(target, record, entries, {
        "transform_from": how,
        "source_consumed": consumed,
    })


# ---------------------------------------------------------------------------
# detach
# ---------------------------------------------------------------------------

def _detach(target, record, epsilon, keep_retained):
    """Subtract the retained added solid.  Returns the measured block."""
    added = _require_retained(record, "added")
    volume_before = volume_mm3(target)
    added_volume = volume_mm3(added)

    if added_volume > 0.0:
        cutter = _inflated_copy(added, epsilon, "BOSS:detach:tmp")
        try:
            _boolean(target, cutter, "DIFFERENCE")
        finally:
            _discard(cutter)
    volume_after = volume_mm3(target)

    removed = volume_before - volume_after
    expected = record.get("added_volume_mm3") or added_volume
    residual = removed - expected
    measured = {
        "volume_before_mm3": round(volume_before, 4),
        "volume_after_mm3": round(volume_after, 4),
        "volume_removed_mm3": round(removed, 4),
        "expected_removed_mm3": round(expected, 4),
        "residual_mm3": round(residual, 4),
        "residual_pct_of_part": round(
            100.0 * residual / volume_before if volume_before else 0.0, 6),
        "attach_volume_before_mm3": round(record.get("volume_before_mm3") or 0.0, 4),
        "returned_to_attach_start_mm3": round(
            volume_after - (record.get("volume_before_mm3") or volume_after), 4),
    }
    discarded = []
    if not keep_retained:
        for role in ("cutter", "added"):
            obj = _find_retained(record, role)
            name = _discard(obj)
            if name:
                discarded.append(name)
    measured["retained_removed"] = discarded
    return measured


@command("detach_boss")
def cmd_detach_boss(params):
    """Take one attached boss back off, surface and all."""
    target = _resolve_target(params)
    entries = records(target)
    boss_id = common.get_str(params, "boss_id")
    record = find_record(target, boss_id, entries)
    keep_retained = common.get_bool(params, "keep_retained", False)
    force = common.get_bool(params, "force", False)

    override = params.get("seam_epsilon_mm")
    if override is not None:
        override = common.get_float(params, "seam_epsilon_mm", minimum=0.0)
    epsilon, basis = seam_epsilon_mm(target, override)

    if force and _find_retained(record, "added") is None:
        measured = {"forced": True, "retained_removed": []}
        notes = ["force=true: the retained solid was gone, so the record was "
                 "dropped WITHOUT cutting. The union is still in the mesh."]
    else:
        measured = _detach(target, record, epsilon, keep_retained)
        notes = []
        if abs(measured["residual_mm3"]) > max(1.0, 0.05 * measured["expected_removed_mm3"]):
            notes.append(
                "The cut removed %.3f mm3 against the %.3f mm3 the attach added: "
                "the part was edited between attach and detach, or the retained "
                "solid no longer matches the union."
                % (measured["volume_removed_mm3"], measured["expected_removed_mm3"]))

    remaining = [entry for entry in entries if entry.get("id") != boss_id]
    write_records(target, remaining)

    report = {
        "target": target.name,
        "boss_id": boss_id,
        "label": record.get("label", ""),
        "kind": record.get("kind", ""),
        "seam_epsilon_mm": round(epsilon, 5),
        "seam_epsilon_basis": basis,
        "record_removed": True,
        "retained_kept": bool(keep_retained),
        "bosses": _ledger_summary(remaining),
        "notes": notes,
        "honesty": (
            "The surface comes back, the mesh does not: the cut re-triangulates "
            "the seam ring, so vertex counts and any mesh hash differ from "
            "before the attach. What is claimed is the volume above and a "
            "surface within the seam epsilon plus the part's own facet size."),
        "next": "attach_boss again, or move_boss next time to keep the cutter",
    }
    report.update(measured)
    return report


# ---------------------------------------------------------------------------
# move — the actual iteration
# ---------------------------------------------------------------------------

@command("move_boss")
def cmd_move_boss(params):
    """Detach and re-attach the same boss at a new transform, in one command.

    This is the operation eevee-bowl-v2 needed and did not have: the angle was
    wrong, and the only moves available were "union a second peg next to it" or
    "regenerate the part".  Here the cutter is still in the drawer, so a new
    angle costs one command and leaves exactly one boss behind.
    """
    target = _resolve_target(params)
    entries = records(target)
    boss_id = common.get_str(params, "boss_id")
    record = find_record(target, boss_id, entries)

    cutter = _require_retained(record, "cutter")
    _require_retained(record, "added")

    old_matrix = matrix_from_mm(record["matrix_mm"])
    new_matrix, how = transform_from_params(params, old_matrix)
    if how == "unchanged":
        raise ForgeError(
            "move_boss needs a new transform: matrix_mm, or any of location_mm, "
            "rotation_deg, scale (the ones you leave out keep their current value).")

    override = params.get("seam_epsilon_mm")
    if override is not None:
        override = common.get_float(params, "seam_epsilon_mm", minimum=0.0)
    epsilon, basis = seam_epsilon_mm(target, override)

    # Keep the cutter through the detach: it is the boss, and it is about to be
    # unioned again a few millimetres over.
    detached = _detach(target, record, epsilon, keep_retained=True)
    remaining = [entry for entry in entries if entry.get("id") != boss_id]
    write_records(target, remaining)
    _discard(_find_retained(record, "added"))

    cutter.matrix_world = new_matrix
    # Out of the drawer for the re-attach: _attach stows it again afterwards.
    cutter.hide_viewport = False
    common.refresh_view_layer()

    new_record = _attach(target, cutter, boss_id, record.get("label", ""),
                         record.get("kind", "object"), record.get("source", ""),
                         record.get("spec"), remaining)

    report = _attach_report(target, new_record, remaining, {
        "moved": True,
        "transform_from": how,
        "transform_before": describe_transform(old_matrix),
        "transform_after": describe_transform(new_matrix),
        "seam_epsilon_mm": round(epsilon, 5),
        "seam_epsilon_basis": basis,
        "detach": detached,
        "next": "move_boss again, or detach_boss with boss_id=%r" % boss_id,
    })
    report["honesty"] = (
        "One boss, moved: the detach cut ran first and the same retained cutter "
        "was re-unioned at the new transform, so the part carries exactly one "
        "record for %r and no ghost of the old angle. The surface under the old "
        "position carries the detach's seam epsilon (%.4g mm), as a detach "
        "always does." % (boss_id, epsilon))
    return report


# ---------------------------------------------------------------------------
# bookkeeping
# ---------------------------------------------------------------------------

@command("list_bosses")
def cmd_list_bosses(params):
    """What is attached to a part, and whether each one can still be taken off."""
    target = _resolve_target(params)
    entries = records(target)
    rows = []
    for entry in entries:
        cutter = _find_retained(entry, "cutter")
        added = _find_retained(entry, "added")
        rows.append({
            "id": entry.get("id"),
            "label": entry.get("label", ""),
            "kind": entry.get("kind", ""),
            "source": entry.get("source", ""),
            "transform": describe_transform(matrix_from_mm(entry["matrix_mm"]))
            if entry.get("matrix_mm") else None,
            "added_volume_mm3": entry.get("added_volume_mm3"),
            "cutter": entry.get("cutter"),
            "added": entry.get("added"),
            "cutter_present": cutter is not None,
            "added_present": added is not None,
            "reversible": added is not None,
        })
    epsilon, basis = seam_epsilon_mm(target)
    return {
        "target": target.name,
        "volume_mm3": round(volume_mm3(target), 4),
        "seam_epsilon_mm": round(epsilon, 5),
        "seam_epsilon_basis": basis,
        "bosses": rows,
        "ledger": _ledger_summary(entries),
        "honesty": (
            "A row with reversible=false has lost its retained solid: the union "
            "is still in the mesh and no cut can take it back. That is the state "
            "this tool exists to prevent."),
    }
