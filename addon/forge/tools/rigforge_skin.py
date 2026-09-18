"""Tag-constrained skinning: the rigger's mask-then-blend, codified.

The failure this exists to fix
------------------------------
Measured live on the symmetric werewolf (``werewolf-wip-8.blend``), by looking
at the maps :func:`~forge.tools.rigforge_landmarks.render_weight_maps` renders:

* **``DEF-upper_arm.L``'s weight heat sat on the chest and the torso's side**,
  not on the arm.  In the walk cycle the arm's swing visibly dragged the torso
  with it.
* **``DEF-thigh.L``'s map was patchy** — islands of full weight with holes in
  between, inside one limb, where a thigh's falloff should be smooth.

Both are Blender's *bone heat* doing exactly what bone heat does.  It is a
diffusion solve on the surface, and on a **clothed mesh with the arms hanging
down** the surface path from the deltoid to the rib is a couple of centimetres of
geometry — shorter than the path down the arm itself.  Heat therefore flows
*across the armpit* faster than it flows down the limb, and the solver's answer
is not wrong so much as it is answering a question about the surface when the
rigger was asking a question about the **body**.

Meanwhile :mod:`~forge.tools.rigforge_autotag` has already computed the answer
heat cannot see: an **exact per-limb vertex membership**, built by measuring
each limb's own axis and its own local girth, so the rib beside the arm is in
``Torso`` and the arm is in ``Arm.L``.  Until this module the skinning step
threw that away.

What a human rigger does instead
--------------------------------
A rigger does not hand-paint an arm from scratch.  They **mask the limb**, let
the automatic weights run inside the mask, then **blend the seam** so the
shoulder does not crease.  That is three operations, and all three are
mechanical:

1. **The tag contract** (:func:`legal_bone_sets`, :func:`constrain_weights`) —
   every tag has a *legal bone set*, and a vertex's weight to a bone outside its
   tag's legal set is zeroed and the remainder renormalised.  Bone heat *inside*
   a limb is good work; this constrains it, it does not replace it.
2. **Blend zones** (:func:`blend_zones`) — a hard mask creases.  Within a band
   of each tag boundary, **both** tags' legal sets are allowed, so the influence
   crosses the seam the way flesh does.  The band's width is the limb's own
   local girth times :data:`BLEND_GIRTH_FRACTION`, walked out over the mesh's
   own edges — a wrist's seam is narrow because a wrist is narrow.
3. **Smoothing** (:func:`smooth_weights`) — Laplacian passes over the *weights*,
   per bone, inside that bone's legal region, with everything outside the region
   contributing a hard zero.  That fills the in-limb holes and makes the taper
   to the region's edge continuous instead of a cliff.  It is the same smoother
   :mod:`~forge.tools.silhouette` runs over a displacement field and
   :mod:`~forge.tools.correctives` runs over a correction; here the field is the
   weight.

Deriving the legal sets, without a list that rots
-------------------------------------------------
Nothing below names a bone.  :func:`legal_bone_sets` reads the **metarig's own
structure**:

* the tag → metarig-bone mapping ``rigforge_metarig`` already stored
  (``forge_tag_bones``) is authoritative for the bones Forge placed, resolved
  through :func:`~forge.tools.rigforge_rig.def_bones_for` because Rigify
  subdivides (``upper_arm.L`` becomes ``DEF-upper_arm.L`` *and*
  ``DEF-upper_arm.L.001``);
* a deform bone the mapping never named — ``DEF-shoulder.L``, ``DEF-breast.L``,
  ``DEF-pelvis.L``, a finger, an ear chain the artist added — **inherits the tag
  of its nearest deform ancestor**, which is how a hierarchy says "part of";
* anything with no claimed ancestor at all falls back to the tag whose measured
  region its own midpoint is nearest, which is also the whole answer for a rig
  this add-on did not build.

Then one asymmetric rule turns ownership into legality:

    A tag may be moved by the bone it **hangs from**, never by the bones that
    hang **off** it.

So ``Arm.L``'s legal set is the left arm's deform chain *plus* ``DEF-shoulder.L``
— the joint the arm pivots around, which the torso owns — and ``Torso``'s legal
set is the spine, the chest, the breasts and the shoulder roots and **not**
``DEF-upper_arm.L``.  That single asymmetry is the whole fix for the motivating
evidence: the chest can no longer be arm flesh, while the armpit still blends,
because the armpit is in the blend zone.

What it is gated by
-------------------
:func:`~forge.tools.rigforge_landmarks.influence_overlap` already names every
pair of bones that share flesh while sitting far apart; after this runs, that
cross-limb mass should be near zero, and nothing here tightens that gate — it
only has to survive it.  The patchiness has no gate at all, so this module adds
one: :func:`weight_continuity` counts, per bone, the vertices whose weight sits
far below *every* topological neighbour's — the holes in the thigh map, as a
number, per bone, with the worst one named.

Stdlib + ``bpy``/``mathutils`` + numpy (which Blender ships, and which
:mod:`~forge.tools.correctives` already requires for the same reason: this is a
field solve over every vertex and there is no sane pure-Python version).
Nothing opens a window.
"""

import heapq
import math
import time

import bpy
from mathutils import Vector

try:  # pragma: no cover - numpy is part of every Blender build
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None

from . import rigforge
from . import rigforge_landmarks
from . import rigforge_rig
from .common import (
    M_TO_MM,
    get_float,
    get_int,
    object_mode,
    refresh_view_layer,
    resolve_object,
)
from .registry import ForgeError, command

__all__ = [
    "BLEND_GIRTH_FRACTION",
    "BLEND_REACH",
    "SMOOTH_PASSES",
    "SMOOTH_FACTOR",
    "CONTINUITY_THRESHOLDS",
    "source_bone_names",
    "metarig_base",
    "def_bones_of",
    "deform_parent",
    "bone_owners",
    "legal_bone_sets",
    "tag_membership",
    "vertex_edges",
    "tag_girths",
    "blend_zones",
    "taper_edges",
    "read_weights",
    "write_weights",
    "smooth_weights",
    "constrain_weights",
    "weight_continuity",
]


# ---------------------------------------------------------------------------
# constants, each with the reason it is the number it is
# ---------------------------------------------------------------------------

#: How wide a tag boundary's blend band is, as a fraction of the **local girth**
#: of the thinner of the two tags meeting there.  A hard mask creases; a band as
#: wide as the whole figure turns the mask back into the bleed it was built to
#: stop.  One girth — a shoulder blend about as deep as the arm is thick — is
#: the rigger's own habit, and it is also where the measurement lands
#: (punctured vertices on the werewolf, at four passes and reach 3.0, against
#: the unconstrained bind's 417)::
#:
#:     band 0.5   362 holes, 23% of the mesh in a band
#:     band 1.0   313 holes, 35%          <-- the knee of the curve
#:     band 1.5   ~310 holes, 43%
#:
#: What matters more than the number is that it is a *fraction of a girth*: from
#: this one constant the werewolf's arm/torso seam comes out 68 mm wide and its
#: wrist seam 22 mm, because an arm and a wrist are not the same thickness.
BLEND_GIRTH_FRACTION = 1.0

#: A blend band is walked out over the mesh's own edges, so it reaches at least
#: this many rings past the seam however coarse the topology is.  **Measured
#: down from 2**: on the werewolf's retopo the median edge is 24.6 mm and two
#: rings is 49 mm, which is wider than half of *every* limb's girth on that
#: character — the floor swallowed the girth entirely and every seam came out
#: the same width, which is precisely the "one distance for the whole
#: character" this is supposed to replace.  One ring is a floor; the girth is
#: the measurement.
MIN_BLEND_RINGS = 1

#: How far a **borrowed** bone may sit from a vertex it moves, as a multiple of
#: the local girth of the tag that lends it.  The tag contract says *which*
#: bones may move a vertex; this says how far away a bone from somewhere else
#: may be, and the two together are the whole mask.
#:
#: "Borrowed" is exact and it was measured: a tag's **own chain** is exempt,
#: because a limb's own bones run the length of that limb and a knee is a long
#: way from the top of its own thigh.  Applying the reach to them as well
#: scaled the thigh down at the knee, sharpened the bend into a fold, and gave
#: the werewolf's knees **474 new self-intersections at the extremes where they
#: had none**; exempting them leaves 2, against the unconstrained bind's 950.
#: What the reach is for is the borrowed influence — a hinge, or a bone lent
#: across a seam — and there it is the difference between a shoulder and a
#: shoulder-and-a-hand.
#:
#: **Measured, not guessed, and it took three tries.**  With no reach rule at
#: all, the werewolf's stray-influence mass went *up* — a hip vertex in the
#: Leg/Torso blend band was legal for ``DEF-thigh.R.001``, the bone below the
#: knee 479 mm away, and for ``DEF-spine.005``, the upper chest, and the
#: smoother duly gave both of them some.  Applying the rule only to bones
#: *borrowed* across a seam fixed the unsmoothed bind (stray mass 4.35 -> 0.09)
#: and left the smoothed one broken (up to 8.93), because a tag's own chain runs
#: the whole length of a limb and a smoother will walk a hand towards a shoulder
#: if the shoulder is legal.  One rule, applied to every bone, holds both.
#:
#: The factor itself is a measured optimum, on the werewolf, at four smoothing
#: passes (stray mass / punctured vertices / vertices left out of reach of every
#: bone their own tag allows)::
#:
#:     unconstrained bind   4.351 / 417 / --
#:     reach 2.0            0.095 / 231 / 602   too tight: 602 vertices stranded
#:     reach 3.0            0.000 / 313 /   0   <-- no stray influence at all
#:     reach 4.0            0.055 / 277 /   0   looser, and it starts coming back
#:
#: Three girths reaches a chest bone from an armpit and a hip bone from a thigh,
#: and still refuses a toe at a hip.
BLEND_REACH = 3.0

#: Repair passes over the weights.  **Measured on the werewolf** (punctured
#: vertices, at :data:`BLEND_REACH` 3.0 and :data:`BLEND_GIRTH_FRACTION` 1.0;
#: the stray-influence mass is 0.000 at every one of these)::
#:
#:     0 passes   628     the mask alone: clean, and cliff-edged
#:     2 passes   ~350
#:     4 passes   313     <-- well under the unconstrained bind's 417
#:     8 passes   ~295    a handful more holes for twice the work
#:
#: Four is where the curve flattens.  The count is reported next to the result
#: so the number outlives this comment.
SMOOTH_PASSES = 4

#: The outer fraction of :data:`BLEND_REACH` over which a bone's licence fades
#: rather than ending.  **Measured.**  A hard distance threshold is a wall, and
#: a wall in a weight map is a crease: with the reach applied as a boolean the
#: werewolf's torso bones grew punctures all along their new boundaries —
#: ``DEF-spine.005`` went from 28 to 36, ``DEF-spine.004`` to 6.7% of its own
#: region — because the weight on the inside of the cut was large and the weight
#: on the outside was zero.  Over the last third of its reach a bone's weight is
#: scaled down linearly instead, so the cut arrives as a falloff.  Unlike a ring
#: count this is a distance, so it does not change meaning with the mesh's
#: resolution.
REACH_BAND = 0.35

#: How many rings past the tag contract's own edge a weight may taper.
#:
#: **Measured, twice, in both directions.**  Zero — the contract enforced to the
#: exact vertex — leaves a weight cliff wherever the mask ends, and on the
#: werewolf that measured **738 punctured vertices against the unconstrained
#: bind's 417**: a mask with no taper is a worse skin than no mask.  More than
#: one is unsafe on a coarse mesh, where a ring can be 150 mm: on the synthetic
#: test biped an unbounded spread walked ``DEF-shoulder.L``'s weight one ring
#: down the arm onto ``DEF-forearm.L``'s vertices and the stray-influence mass
#: went from 8.9 to 60.3.  One ring, and that ring still held to
#: :data:`BLEND_REACH`, is a falloff and nothing else.
SMOOTH_DILATION = 1

#: How far each pass moves a weight towards its neighbours' average, 0-1.  The
#: same 0.5 :data:`forge.tools.correctives.DEFAULT_SMOOTH` uses on the
#: displacement field, deliberately: one smoother, one constant, one place to
#: argue about it.
SMOOTH_FACTOR = 0.5

#: Weights below this are dropped rather than written.  Below a thousandth a
#: weight changes no pixel and costs a vertex group entry, and glTF's four
#: influences are a budget.
WEIGHT_EPSILON = 1e-3

#: A vertex is a **hole** in a bone's region when it has at least this many
#: neighbours the bone does move...
HOLE_MIN_NEIGHBOURS = 3

#: ...and its own weight is below this fraction of the median of theirs...
HOLE_RATIO = 0.5

#: ...and the absolute drop is at least this much.  Without the absolute floor
#: every taper reads as a hole: out at the edge of a region the weights are
#: *supposed* to fall off, and 0.02 against a neighbouring 0.05 is a falloff,
#: not a puncture.
HOLE_MIN_DROP = 0.15

#: A bone's region is the vertices it moves above this weight.  The same 0.05
#: floor ``rig_check`` and ``correctives`` use for "this bone owns this vertex".
REGION_FLOOR = 0.05

#: Bands for the continuity gate, as a percentage of a bone's own region.
#: **Credibility tier: heuristic (proxy tier)** — the same standing as
#: ``rigcheck.THRESHOLDS``.  A clean falloff has no holes at all; a per-cent of
#: a limb punched out is visible as a dent when the limb bends.
CONTINUITY_THRESHOLDS = {"hole_pct": {"ok": 0.5, "attention": 3.0}}


def _require_numpy():
    if _np is None:  # pragma: no cover - numpy is part of Blender
        raise ForgeError(
            "This Blender build has no numpy, so tag-constrained skinning cannot "
            "run - the constraint, the blend walk and the smoothing are one field "
            "solve over every vertex and every deform bone, and there is no sane "
            "pure-Python version of it.")


def _band(value, key):
    if value is None:
        return "unmeasured"
    bands = CONTINUITY_THRESHOLDS[key]
    if value <= bands["ok"]:
        return "ok"
    if value <= bands["attention"]:
        return "attention"
    return "fail"


# ---------------------------------------------------------------------------
# the legal sets, derived from the metarig's own structure
# ---------------------------------------------------------------------------

def source_bone_names(rig, metarig=None):
    """Every name a deform bone could have been generated *from*.

    Either the metarig's own bone table, or — for a rig whose metarig is gone —
    the bone names the stored tag mapping itself talks about.  Without one of
    those, :func:`metarig_base` cannot tell Rigify's subdivision suffix from a
    name that legitimately ends in digits, and that mistake is not theoretical:
    see its docstring.
    """
    if metarig is not None:
        return {bone.name for bone in metarig.data.bones}
    stored = rigforge_rig._stored_json(rig, rigforge_rig.PROP_TAG_BONES, {})
    known = set()
    if isinstance(stored, dict):
        for bones in stored.values():
            if isinstance(bones, (list, tuple)):
                known.update(str(bone) for bone in bones)
    return known


def metarig_base(name, known=()):
    """``DEF-upper_arm.L.001`` -> ``upper_arm.L``: the bone a DEF bone came from.

    Rigify both prefixes (``DEF-``) and *subdivides* (``upper_arm.L`` becomes
    ``DEF-upper_arm.L`` **and** ``DEF-upper_arm.L.001``), so the naive rule is
    "strip a trailing ``.NNN``".  **The naive rule is wrong, and it was wrong
    live**: Rigify's human metarig already has bones called ``spine.001`` …
    ``spine.006``, so stripping turns ``DEF-spine.006`` — the *head* — into
    ``spine``, the hips.  On the werewolf that handed the head bone to both
    legs as their hip hinge and the stray-influence mass went up eightfold.

    So ``known`` — the metarig's own bone names — decides: a stem that **is** a
    source bone is one, and only a stem that is not gets its suffix stripped.
    """
    prefix = rigforge_rig.DEF_PREFIX
    if not str(name).startswith(prefix):
        return None
    stem = str(name)[len(prefix):]
    if known and stem in known:
        return stem
    head, _dot, tail = stem.rpartition(".")
    if head and tail.isdigit():
        return head
    return stem or None


def def_bones_of(rig, known, source):
    """Every deform bone Rigify generated from the source bone ``source``.

    The inverse of :func:`metarig_base`, and deliberately *not*
    :func:`~forge.tools.rigforge_rig.def_bones_for`, which answers the same
    question by prefix match and therefore claims ``DEF-spine.006`` for
    ``spine``.
    """
    return sorted(name for name in rigforge_rig.deform_bones(rig)
                  if metarig_base(name, known) == source)


def deform_parent(rig, name, metarig=None, known=None):
    """The deform bone ``name`` hangs from, or ``None`` — read structurally.

    **The metarig's tree first, when there is one.**  This was measured the hard
    way: on the live werewolf rig, walking Rigify's *generated* hierarchy found
    a deforming ancestor for exactly one bone in thirty-five.  Rigify re-parents
    every ``DEF-`` bone under its own ``MCH-``/``ORG-`` scaffolding and the
    deform bones are very largely siblings of each other, so "the deform tree"
    is not a tree at all — while the metarig, which the artist and this add-on
    both built, is.  ``shoulder.L``'s parent there is the chest, ``thigh.L``'s
    is the hips, and that is the structure the tag contract needs.

    The generated rig's own hierarchy is the fallback, for a rig this add-on did
    not build and therefore has no metarig for.  Where a metarig bone resolves
    to several DEF bones (a subdivided limb), the **most distal** one is the
    parent, because that is the one that actually touches the child.
    """
    if metarig is not None:
        if known is None:
            known = source_bone_names(rig, metarig)
        base = metarig_base(name, known)
        bone = metarig.data.bones.get(base) if base else None
        if bone is not None:
            parent = bone.parent
            while parent is not None:
                candidates = def_bones_of(rig, known, parent.name)
                if candidates:
                    return candidates[-1]
                parent = parent.parent
            return None
    bone = rig.data.bones.get(name)
    if bone is None:
        return None
    parent = bone.parent
    while parent is not None:
        if parent.use_deform:
            return parent.name
        parent = parent.parent
    return None


def bone_owners(rig, metarig=None, regions=None):
    """``{deform bone: tag}`` plus how each one was decided.

    Three rungs, in order, and the report names the rung each bone stopped on so
    a surprising legal set can be read back rather than guessed at:

    ``mapping``     the tag -> metarig-bone map ``rigforge_metarig`` stored,
                    resolved through Rigify's subdivision;
    ``hierarchy``   inherited from the nearest deform ancestor that has a tag;
    ``geometry``    the tag whose measured region the bone's midpoint is
                    nearest — the only rung available for a rig Forge did not
                    build, and the last resort for one it did.
    """
    owner = {}
    source = {}
    names = list(rigforge_rig.deform_bones(rig))
    deform = set(names)
    known = source_bone_names(rig, metarig)

    stored = rigforge_rig._stored_json(metarig, rigforge_rig.PROP_TAG_BONES, {}) \
        if metarig is not None else {}
    if not isinstance(stored, dict) or not stored:
        stored = rigforge_rig._stored_json(rig, rigforge_rig.PROP_TAG_BONES, {})
    if isinstance(stored, dict):
        for tag, bones in sorted(stored.items()):
            if not isinstance(bones, (list, tuple)):
                continue
            for bone in bones:
                resolved = (def_bones_of(rig, known, str(bone)) if known
                            else rigforge_rig.def_bones_for(rig, str(bone)))
                for name in resolved:
                    if name in deform and name not in owner:
                        owner[name] = str(tag)
                        source[name] = "mapping"

    # hierarchy: walk up the structural tree until a tagged ancestor is found.
    for name in names:
        if name in owner:
            continue
        chain = []
        cursor = deform_parent(rig, name, metarig, known)
        seen = set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            if cursor in owner:
                for entry in chain + [name]:
                    if entry not in owner:
                        owner[entry] = owner[cursor]
                        source[entry] = "hierarchy"
                break
            chain.append(cursor)
            cursor = deform_parent(rig, cursor, metarig, known)

    # geometry: whatever is left goes to the nearest tagged region.
    if regions:
        matrix = rig.matrix_world
        for name in names:
            if name in owner:
                continue
            bone = rig.data.bones.get(name)
            if bone is None:
                continue
            middle = matrix @ ((bone.head_local + bone.tail_local) * 0.5)
            best, best_distance = None, None
            for tag, region in sorted(regions.items()):
                distance = region.distance_to(middle)
                if best_distance is None or distance < best_distance:
                    best, best_distance = tag, distance
            if best is not None:
                owner[name] = best
                source[name] = "geometry"
    return owner, source


def legal_bone_sets(rig, metarig=None, regions=None):
    """``{tag: set(deform bone names)}`` — the tag contract, derived not listed.

    Ownership comes from :func:`bone_owners`.  Legality adds exactly one rule to
    it, and the rule is **asymmetric on purpose**:

        a tag may be moved by the bone it hangs *from*, never by the bones that
        hang *off* it.

    Concretely, for every bone a tag owns whose deform parent belongs to a
    *different* tag, that parent — the **hinge** — joins this tag's legal set.
    ``Arm.L`` gets ``DEF-shoulder.L``; ``Leg.L`` gets ``DEF-spine``, the hips;
    ``Head`` gets the last neck bone.  The converse is refused: the torso does
    not get ``DEF-upper_arm.L``, and that refusal is the motivating defect's
    cure.  What keeps the seam from creasing is not legality but the blend zone
    (:func:`blend_zones`), which is measured rather than assumed.

    A hinge is returned separately from the rest of the set because it is legal
    only *near the joint* — :func:`constrain_weights` holds it to the same
    :data:`BLEND_REACH` a borrowed bone gets.  A leg hangs from the hips; its
    toes do not.
    """
    owner, source = bone_owners(rig, metarig, regions)
    known = source_bone_names(rig, metarig)
    legal = {}
    hinges = {}
    for name, tag in owner.items():
        legal.setdefault(tag, set()).add(name)
    for name, tag in sorted(owner.items()):
        parent = deform_parent(rig, name, metarig, known)
        if parent is None:
            continue
        parent_tag = owner.get(parent)
        if parent_tag is None or parent_tag == tag:
            continue
        legal.setdefault(tag, set()).add(parent)
        hinges.setdefault(tag, set()).add(parent)
    return {
        "legal": legal,
        "owner": owner,
        "source": source,
        "hinges": {tag: sorted(names) for tag, names in sorted(hinges.items())},
    }


# ---------------------------------------------------------------------------
# the mesh, as a graph with tags on it
# ---------------------------------------------------------------------------

def tag_membership(obj):
    """``[frozenset(tags), ...]`` per vertex, from the ``tag_*`` vertex groups."""
    per_group = {}
    for group in rigforge.tag_groups(obj):
        per_group[group.index] = rigforge.tag_display_name(group.name)
    out = []
    for vertex in obj.data.vertices:
        tags = set()
        for element in vertex.groups:
            name = per_group.get(element.group)
            if name is not None and element.weight > 0.0:
                tags.add(name)
        out.append(frozenset(tags))
    return out


def vertex_edges(obj):
    """``(E, 2)`` int array of the mesh's own edges — the graph everything walks."""
    _require_numpy()
    count = len(obj.data.edges)
    flat = _np.empty(count * 2, dtype="i4")
    obj.data.edges.foreach_get("vertices", flat)
    return flat.reshape(count, 2).astype("i8")


def _dilate(flags, edges, rings=1):
    """Grow a ``(V, B)`` boolean region outwards over the given edges."""
    _require_numpy()
    if rings <= 0 or not len(edges):
        return flags
    a, b = edges[:, 0], edges[:, 1]
    out = flags
    for _ring in range(int(rings)):
        spread = _np.zeros(out.shape, dtype="i4")
        _np.add.at(spread, a, out[b].astype("i4"))
        _np.add.at(spread, b, out[a].astype("i4"))
        out = out | (spread > 0)
    return out


def taper_edges(obj, edges, tags, regions, girth_fraction=BLEND_GIRTH_FRACTION):
    """The edges short enough to carry a falloff, and only those.

    A tapered mask edge is worth one ring of geometry — but only if a ring of
    geometry is *finer* than the band the taper belongs to.  On the werewolf's
    retopo a ring is 25 mm against a 34 mm band, and the taper is a falloff.  On
    a mesh coarse enough that a ring is 340 mm — the synthetic test biped's arm
    is three rings from shoulder to wrist — "one ring" is most of a limb, and
    dilating along it walked ``DEF-shoulder.L``'s weight onto
    ``DEF-forearm.L``'s vertices (stray mass 8.9 -> 35.1).

    So an edge carries the taper when it is no longer than ``girth_fraction``
    times the girth of the thinner tag at its ends — the same width
    :func:`blend_zones` gives that seam.  On a coarse tube this keeps the edges
    that run *around* the limb, where a falloff is harmless, and drops the ones
    that run *along* it, where it is not.
    """
    _require_numpy()
    girths = tag_girths(regions)
    fallback = min(girths.values()) if girths else 0.0
    limits = _np.empty(len(obj.data.vertices), dtype="f8")
    for index in range(limits.shape[0]):
        here = [girths[tag] for tag in tags[index] if tag in girths]
        limits[index] = girth_fraction * (min(here) if here else fallback)
    matrix = obj.matrix_world
    world = _np.array([list(matrix @ vertex.co) for vertex in obj.data.vertices],
                      dtype="f8")
    a, b = edges[:, 0], edges[:, 1]
    lengths = _np.linalg.norm(world[a] - world[b], axis=1)
    keep = lengths <= _np.minimum(limits[a], limits[b])
    return edges[keep]


def _adjacency(edges, count):
    """``[[neighbour, ...], ...]`` — the same graph, walkable one vertex at a time."""
    out = [[] for _ in range(count)]
    for a, b in edges:
        out[int(a)].append(int(b))
        out[int(b)].append(int(a))
    return out


def tag_girths(regions):
    """``{tag: metres}`` — each tag's own median radius about its own axis.

    The same measurement :mod:`~forge.tools.rigforge_autotag` caps its cylinders
    with, taken here on the tag that actually exists rather than on the axis
    that proposed it, because the blend band has to follow the flesh that is
    there.
    """
    out = {}
    for tag, region in regions.items():
        axis = Vector(region.axis).normalized()
        centre = Vector(region.centre)
        radii = []
        for point in region._points:
            offset = Vector(point) - centre
            radii.append((offset - axis * offset.dot(axis)).length)
        if not radii:
            out[tag] = 0.0
            continue
        radii.sort()
        middle = len(radii) // 2
        out[tag] = (radii[middle] if len(radii) % 2
                    else 0.5 * (radii[middle - 1] + radii[middle]))
    return out


def blend_zones(obj, tags, edges, regions, girth_fraction=BLEND_GIRTH_FRACTION):
    """Which extra tags each vertex may borrow bones from, and how wide that band is.

    A **seam** is an edge whose two ends carry different tags.  From every seam
    of a given tag pair the band is walked outwards over the mesh's own edges,
    accumulating real distance, and it stops at

        ``girth_fraction x min(girth[A], girth[B])``

    — the *thinner* limb's girth, because a seam is only as forgiving as the
    thinner thing meeting at it, and at least :data:`MIN_BLEND_RINGS` ring so a
    coarse retopo still has somewhere to put a falloff.

    Reaching a vertex is necessary but not sufficient: :func:`constrain_weights`
    still holds every borrowed bone to :data:`BLEND_REACH` times the lending
    tag's girth, so the armpit borrows the shoulder and the chest and does not
    borrow the hand.

    Returns ``(blend, report)`` where ``blend[v]`` is a set of tag names.
    """
    _require_numpy()
    count = len(obj.data.vertices)
    matrix = obj.matrix_world
    world = [matrix @ vertex.co for vertex in obj.data.vertices]
    adjacency = _adjacency(edges, count)
    girths = tag_girths(regions)

    lengths = []
    seams = {}
    for a, b in edges:
        a, b = int(a), int(b)
        lengths.append((world[a] - world[b]).length)
        ta, tb = tags[a], tags[b]
        if ta == tb or not ta or not tb:
            continue
        for one in ta - tb:
            for other in tb - ta:
                key = (one, other) if one <= other else (other, one)
                seams.setdefault(key, set()).update((a, b))
    lengths.sort()
    median_edge = lengths[len(lengths) // 2] if lengths else 0.0

    blend = [set() for _ in range(count)]
    rows = []
    for (one, other), seeds in sorted(seams.items()):
        girth = min(girths.get(one, 0.0), girths.get(other, 0.0))
        width = max(girth_fraction * girth, MIN_BLEND_RINGS * median_edge)
        both = {one, other}
        distance = {}
        queue = []
        for seed in sorted(seeds):
            distance[seed] = 0.0
            heapq.heappush(queue, (0.0, seed))
        while queue:
            here, index = heapq.heappop(queue)
            if here > distance.get(index, here) + 1e-12:
                continue
            blend[index].update(both)
            for neighbour in adjacency[index]:
                # The band only spreads through the two tags that meet at this
                # seam. Without that a shoulder's band would leak down the arm
                # into the wrist's band and every tag would end up legal
                # everywhere, which is the bleed this module exists to stop.
                if not (tags[neighbour] & both):
                    continue
                step = here + (world[index] - world[neighbour]).length
                if step > width:
                    continue
                if step < distance.get(neighbour, step + 1.0):
                    distance[neighbour] = step
                    heapq.heappush(queue, (step, neighbour))
        rows.append({
            "tags": [one, other],
            "girth_mm": round(girth * M_TO_MM, 1),
            "width_mm": round(width * M_TO_MM, 1),
            "rings": round(width / median_edge, 2) if median_edge > 0.0 else None,
            "seam_vertices": len(seeds),
            "zone_vertices": len(distance),
        })
    total = sum(1 for entry in blend if entry)
    if rows:
        widest = max(rows, key=lambda row: row["width_mm"])
        says = ("%d of %d vertices (%.1f%%) sit in a blend band where two tags' bones "
                "are both legal; the widest seam is %s/%s at %.0f mm (%s rings), the "
                "narrowest %.0f mm - the band follows each seam's own girth rather "
                "than one distance for the whole character."
                % (total, count, 100.0 * total / max(count, 1),
                   widest["tags"][0], widest["tags"][1], widest["width_mm"],
                   widest["rings"], min(row["width_mm"] for row in rows)))
    else:
        says = ("No two tags touch along an edge, so there is no seam to blend and "
                "the tag contract is enforced hard everywhere.")
    return blend, {
        "seams": rows,
        "girth_fraction": girth_fraction,
        "min_rings": MIN_BLEND_RINGS,
        "median_edge_mm": round(median_edge * M_TO_MM, 2),
        "girths_mm": {tag: round(value * M_TO_MM, 1)
                      for tag, value in sorted(girths.items())},
        "blend_vertices": total,
        "blend_pct": round(100.0 * total / max(count, 1), 2),
        "says": says,
    }


# ---------------------------------------------------------------------------
# weights as a matrix
# ---------------------------------------------------------------------------

def read_weights(obj, bone_names):
    """``(V, B)`` float array of the mesh's deform weights, in ``bone_names`` order."""
    _require_numpy()
    index_of = {}
    for group in obj.vertex_groups:
        if group.name in bone_names:
            index_of[group.index] = bone_names.index(group.name)
    weights = _np.zeros((len(obj.data.vertices), len(bone_names)), dtype="f8")
    for vertex in obj.data.vertices:
        row = weights[vertex.index]
        for element in vertex.groups:
            column = index_of.get(element.group)
            if column is not None:
                row[column] = float(element.weight)
    return weights


def write_weights(obj, bone_names, weights, epsilon=WEIGHT_EPSILON):
    """Put a ``(V, B)`` array back on the mesh, replacing those groups entirely."""
    _require_numpy()
    count = len(obj.data.vertices)
    everything = list(range(count))
    written = 0
    for column, name in enumerate(bone_names):
        group = obj.vertex_groups.get(name)
        if group is None:
            group = obj.vertex_groups.new(name=name)
        group.remove(everything)
        values = weights[:, column]
        for index in _np.nonzero(values > epsilon)[0]:
            group.add([int(index)], float(values[index]), "REPLACE")
            written += 1
    obj.data.update()
    return written


def _normalize_rows(weights):
    totals = weights.sum(axis=1)
    live = totals > 0.0
    weights[live] /= totals[live][:, None]
    return live


def _limit_rows(weights, limit):
    """Keep the ``limit`` strongest influences per vertex. Returns rows trimmed."""
    if limit is None or limit >= weights.shape[1]:
        return 0
    order = _np.argsort(-weights, axis=1)
    keep = _np.zeros_like(weights, dtype=bool)
    rows = _np.arange(weights.shape[0])[:, None]
    keep[rows, order[:, :limit]] = True
    trimmed = int(_np.count_nonzero(_np.any((weights > 0.0) & ~keep, axis=1)))
    weights *= keep
    return trimmed


def smooth_weights(weights, mask, edges, passes=SMOOTH_PASSES, factor=SMOOTH_FACTOR,
                   ratio=HOLE_RATIO, min_drop=HOLE_MIN_DROP):
    """Laplacian passes over the **weights** — but only where the bind is broken.

    ``mask`` is ``(V, B)`` booleans: the region :func:`constrain_weights` worked
    out that this bone may move — its tag contract, its reach, and the one ring
    of taper at the boundary.  Inside it, a pass would ordinarily replace every
    weight with a blend of itself and the mean of its topological neighbours'.
    **This one moves only two kinds of weight**, and refusing to move the rest
    is the whole design:

    * a **hole** — a weight sitting ``ratio`` below its neighbours' mean by at
      least ``min_drop``, which is the patchiness an automatic bind leaves;
    * a **mask edge** — a weight one ring from where the region ends, which is
      where a mask with no falloff leaves a cliff.

    Everything else is bone heat's own answer, and bone heat's answer *along a
    limb* is good: it is only across limb boundaries that it is wrong, and the
    mask has already dealt with that.  Blurring the healthy middle of a limb
    costs real deformation — on the synthetic test biped, whose leg is three
    rings from hip to ankle, four unrestricted passes mixed ``DEF-thigh.L`` and
    ``DEF-shin.L.001`` together and the stray-influence mass went from 8.9 to
    34.1; restricted to repairs it does not move at all.

    A neighbour outside the region contributes a hard ``0``, which is what makes
    the edge case a falloff rather than a fade to nothing in particular, and the
    region is re-applied after every pass so no weight ever leaks out of it.
    The repair set is re-measured each pass, so a hole that has been filled
    stops being smoothed and the sequence converges.  Same smoother as
    ``silhouette._smooth_field``; different field, and a stencil over it.
    """
    _require_numpy()
    if passes <= 0 or factor <= 0.0 or not len(edges):
        return weights * mask
    a = edges[:, 0]
    b = edges[:, 1]
    degree = _np.zeros(weights.shape[0], dtype="f8")
    _np.add.at(degree, a, 1.0)
    _np.add.at(degree, b, 1.0)
    has = degree > 0.0

    # One ring inside the mask's edge, plus the ring of taper outside it: the
    # cells where a hard boundary would show as a cliff.
    inner = ~_dilate(~mask, edges, 1)
    rim = mask & ~inner

    weights = weights * mask
    for _pass in range(int(passes)):
        total = _np.zeros_like(weights)
        _np.add.at(total, a, weights[b])
        _np.add.at(total, b, weights[a])
        average = weights.copy()
        average[has] = total[has] / degree[has][:, None]

        # The two repairs want two different averages, and using one for both
        # was measured wrong. A *rim* weight falls off towards its whole
        # neighbourhood, zeros included, because that is what a falloff is. A
        # *hole* is filled back up to the level of the neighbours that actually
        # carry weight -- averaging in the zeros around a ragged region makes a
        # real puncture look like an edge and it is left unrepaired, which is
        # how a first attempt filled 150 holes on the werewolf by blurring
        # everything and this one filled none of the thigh's.
        live = (weights >= REGION_FLOOR).astype("f8")
        near_count = _np.zeros_like(weights)
        near_total = _np.zeros_like(weights)
        _np.add.at(near_count, a, live[b])
        _np.add.at(near_count, b, live[a])
        _np.add.at(near_total, a, weights[b] * live[b])
        _np.add.at(near_total, b, weights[a] * live[a])
        enough = near_count >= float(HOLE_MIN_NEIGHBOURS)
        level = _np.zeros_like(weights)
        level[enough] = near_total[enough] / near_count[enough]
        holes = enough & (weights < ratio * level) & ((level - weights) >= min_drop)

        target = _np.where(holes, level, average)
        movable = mask & (rim | holes)
        weights = _np.where(movable,
                            (1.0 - factor) * weights + factor * target,
                            weights)
        weights *= mask
    return weights


# ---------------------------------------------------------------------------
# the stage
# ---------------------------------------------------------------------------

def _distance_matrix(obj, rig, bone_names):
    """``(V, B)`` distance from every vertex to every deform bone's **segment**."""
    _require_numpy()
    matrix = obj.matrix_world
    points = _np.array([list(matrix @ vertex.co) for vertex in obj.data.vertices],
                       dtype="f8")
    rig_matrix = rig.matrix_world
    out = _np.empty((points.shape[0], len(bone_names)), dtype="f8")
    for column, name in enumerate(bone_names):
        bone = rig.data.bones[name]
        head = _np.array(list(rig_matrix @ bone.head_local), dtype="f8")
        tail = _np.array(list(rig_matrix @ bone.tail_local), dtype="f8")
        segment = tail - head
        length_sq = float(segment.dot(segment))
        if length_sq < 1e-18:
            out[:, column] = _np.linalg.norm(points - head, axis=1)
            continue
        t = _np.clip(((points - head) @ segment) / length_sq, 0.0, 1.0)
        nearest = head[None, :] + t[:, None] * segment[None, :]
        out[:, column] = _np.linalg.norm(points - nearest, axis=1)
    return out


def constrain_weights(obj, rig, metarig=None, regions=None, max_influences=4,
                      girth_fraction=BLEND_GIRTH_FRACTION, passes=SMOOTH_PASSES,
                      factor=SMOOTH_FACTOR, reach=BLEND_REACH, warnings=None):
    """Mask, blend, smooth — the whole stage, on an already-skinned mesh.

    Order matters and is the rigger's: the automatic weights are *input*, the
    tag contract cuts them down, the blend zone widens the cut back out at the
    seams only, the smoother fixes what the cut left ragged, and the result is
    limited and renormalised.  Nothing here invents an influence a bone did not
    already have except in a blend zone or through the smoother's own
    neighbourhood, so a shoulder the artist painted by hand survives its own
    limb untouched.
    """
    _require_numpy()
    warnings = [] if warnings is None else warnings
    started = time.monotonic()
    bone_names = sorted(rigforge_rig.deform_bones(rig))
    if not bone_names:
        raise ForgeError("Rig %r has no deforming bones, so there is no skinning to "
                         "constrain." % rig.name)
    if regions is None:
        regions, _empty = rigforge_rig.measure_tags(obj)
    if not regions:
        raise ForgeError(
            "Tag-constrained skinning needs tags: %r has no tagged geometry, so there "
            "is no per-limb membership to constrain the weights to. Run "
            "rigforge_autotag (or tag by hand) first." % obj.name)

    column_of = {name: index for index, name in enumerate(bone_names)}
    contract = legal_bone_sets(rig, metarig, regions)
    legal = contract["legal"]

    tags = tag_membership(obj)
    edges = vertex_edges(obj)
    blend, blend_report = blend_zones(obj, tags, edges, regions, girth_fraction)

    count = len(obj.data.vertices)
    girths = tag_girths(regions)
    columns_for = {tag: [column_of[name] for name in sorted(names)
                         if name in column_of]
                   for tag, names in legal.items()}
    hinge_of = {tag: set(names) for tag, names in contract["hinges"].items()}
    owned_for = {tag: frozenset(column_of[name] for name in names
                                if name in column_of
                                and name not in hinge_of.get(tag, ()))
                 for tag, names in legal.items()}
    # One reach per tag, off that tag's own measured girth: a bone is legal on a
    # vertex when the tag says so *and* the bone is near enough to be part of
    # that flesh. See BLEND_REACH for the three attempts this replaced.
    allowance = {tag: reach * girths.get(tag, 0.0) for tag in legal}
    distances = _distance_matrix(obj, rig, bone_names)
    def licence(distance, limit):
        """1 inside the reach, ramping to 0 over its last :data:`REACH_BAND`."""
        if limit <= 0.0 or distance >= limit:
            return 0.0
        band = limit * REACH_BAND
        if band <= 0.0:
            return 1.0
        return min(1.0, (limit - distance) / band)

    ramp = _np.zeros((count, len(bone_names)), dtype="f8")
    radius = _np.zeros(count, dtype="f8")
    untagged = 0
    borrowed = 0
    stranded_rows = []
    for index in range(count):
        own = tags[index]
        radius[index] = max([allowance.get(tag, 0.0)
                             for tag in (own | set(blend[index]))] or [0.0])
        if not own and not blend[index]:
            # A vertex in no tag at all has no contract to enforce. Leaving it
            # unconstrained is the honest answer (and it is counted and said
            # out loud); zeroing it would delete flesh nobody asked about.
            ramp[index, :] = 1.0
            untagged += 1
            continue
        row = distances[index]
        for tag in own:
            limit = allowance.get(tag, 0.0)
            own_columns = owned_for.get(tag, ())
            for column in columns_for.get(tag, ()):
                # A tag's own chain runs the length of its own limb and is not
                # subject to the reach: a knee vertex is a long way from the top
                # of the thigh and the thigh still drives it. Measured -- with
                # the reach applied to a tag's own bones the werewolf's knees
                # gained 474 new self-intersections at the extremes, because
                # scaling the thigh down at the knee sharpens the bend into a
                # fold. The reach is about *borrowed* influence.
                value = 1.0 if column in own_columns else licence(row[column], limit)
                if value > ramp[index, column]:
                    ramp[index, column] = value
        for tag in blend[index]:
            if tag in own:
                continue
            limit = allowance.get(tag, 0.0)
            for column in columns_for.get(tag, ()):
                value = licence(row[column], limit)
                if value > ramp[index, column]:
                    if ramp[index, column] <= 0.0:
                        borrowed += 1
                    ramp[index, column] = value
        if not ramp[index].any():
            # Reach is a rule about a limb's own scale, and a limb whose bones
            # all sit further away than that is a measurement this module got
            # wrong rather than flesh nobody should move. The nearest bone the
            # tag allows wins, and the count is reported.
            candidates = [column for tag in (own or blend[index])
                          for column in columns_for.get(tag, ())]
            if candidates:
                ramp[index, min(candidates, key=lambda c: row[c])] = 1.0
                stranded_rows.append(index)
            else:
                ramp[index, :] = 1.0
                untagged += 1
    if stranded_rows:
        warnings.append(
            "%d vertex/vertices of %r sit further from every bone their tag allows "
            "than that tag's own girth allows for; each kept its single nearest "
            "legal bone." % (len(stranded_rows), obj.name))

    # The contract, enforced with one ring of tolerance at its boundary -- and
    # the tolerance is not a fudge, it is the taper. A mask cut to the exact
    # vertex is a weight cliff, and a weight cliff creases: enforcing this
    # hard measured 738 punctured vertices on the werewolf against the
    # unconstrained bind's 417. The ring is still held to the same reach, or
    # one ring of a coarse retopo is 150 mm and the smoother walks a shoulder
    # down to a forearm (measured on the synthetic test biped: stray mass 8.9
    # -> 60.3).
    hard = ramp > 0.0
    fine = taper_edges(obj, edges, tags, regions, girth_fraction)
    mask = _dilate(hard, fine, SMOOTH_DILATION) & (distances <= radius[:, None])
    tolerance_slots = int(_np.count_nonzero(mask & ~hard))

    before = read_weights(obj, bone_names)
    before_total = float(before.sum())
    removed = before * (1.0 - ramp)
    removed_mass = float(removed.sum())
    per_bone_removed = removed.sum(axis=0)

    weights = before * ramp
    live = _normalize_rows(weights)
    emptied = _np.nonzero(~live)[0]
    refilled = 0
    if emptied.size:
        # Everything this vertex was weighted to was illegal. Rather than leave
        # a hole in the skin, fill it from the nearest bones it *is* allowed —
        # the same inverse-square fallback ``distance_weights`` uses, restricted
        # to the contract.
        for index in emptied:
            allowed = _np.nonzero(mask[index])[0]
            if not allowed.size:
                continue
            local = 1.0 / _np.maximum(distances[index, allowed], 1e-4) ** 2
            order = _np.argsort(-local)[:max(1, int(max_influences))]
            weights[index, allowed[order]] = local[order] / local[order].sum()
            refilled += 1
        _normalize_rows(weights)
        if refilled:
            warnings.append(
                "%d vertex/vertices of %r were weighted only to bones their tag does "
                "not allow; they were re-filled from the nearest bone their tag does."
                % (refilled, obj.name))

    weights = smooth_weights(weights, mask, edges, passes, factor)
    _normalize_rows(weights)
    trimmed = _limit_rows(weights, max(1, int(max_influences)))
    live = _normalize_rows(weights)
    stranded = int(_np.count_nonzero(~live))

    weights[weights < WEIGHT_EPSILON] = 0.0
    _normalize_rows(weights)
    written = write_weights(obj, bone_names, weights)
    refresh_view_layer()

    moved = float(_np.abs(weights - before).sum())
    per_tag = []
    for tag in sorted(regions):
        members = [index for index in range(count) if tag in tags[index]]
        if not members:
            continue
        rows = _np.array(members, dtype="i8")
        per_tag.append({
            "tag": tag,
            "vertices": len(members),
            "legal_bones": sorted(legal.get(tag, ())),
            "hinges": contract["hinges"].get(tag, []),
            "weight_removed": round(float(removed[rows].sum()), 4),
            "blend_vertices": int(sum(1 for index in members if blend[index])),
        })

    contract_bones = sum(len(names) for names in legal.values())

    offenders = sorted(
        ((bone_names[column], float(per_bone_removed[column]))
         for column in range(len(bone_names)) if per_bone_removed[column] > 1e-6),
        key=lambda row: -row[1])
    return {
        "object": obj.name,
        "rig": rig.name,
        "deform_bones": len(bone_names),
        "vertices": count,
        "untagged_vertices": untagged,
        "borrowed_slots": borrowed,
        "out_of_reach_vertices": len(stranded_rows),
        "taper_slots": tolerance_slots,
        "reach_factor": float(reach),
        "reach_mm": {tag: round(value * M_TO_MM, 1)
                     for tag, value in sorted(allowance.items())},
        "contract_bone_slots": contract_bones,
        "weight_before": round(before_total, 3),
        "weight_removed": round(removed_mass, 4),
        "weight_removed_pct": round(100.0 * removed_mass / max(before_total, 1e-9), 3),
        "weight_moved": round(moved, 4),
        "refilled_vertices": refilled,
        "stranded_vertices": stranded,
        "trimmed_vertices": trimmed,
        "entries_written": written,
        "worst_bones": [{"bone": name, "weight_removed": round(mass, 4)}
                        for name, mass in offenders[:12]],
        "tags": per_tag,
        "blend": blend_report,
        "contract": {
            "legal": {tag: sorted(names) for tag, names in sorted(legal.items())},
            "hinges": contract["hinges"],
            "source_counts": _count_sources(contract["source"]),
            "bones_by_source": _bones_by_source(contract["source"]),
        },
        "smoothing": {"passes": int(passes), "factor": float(factor),
                      "method": "Laplacian over the weights, masked to each bone's "
                                "legal region (silhouette._smooth_field's smoother, "
                                "a different field)"},
        "max_influences": int(max_influences),
        "says": ("%.2f vertex-weights (%.2f%% of the skin) sat on bones the vertex's "
                 "own tag does not allow and were removed; %d vertices sit in a blend "
                 "band where two tags' bones are both legal. Worst offender: %s."
                 % (removed_mass,
                    100.0 * removed_mass / max(before_total, 1e-9),
                    blend_report["blend_vertices"],
                    ("%s, %.2f vertex-weights outside its own limb"
                     % (offenders[0][0], offenders[0][1])) if offenders
                    else "none - every weight was already inside its tag")),
        "seconds": round(time.monotonic() - started, 3),
    }


def _count_sources(source):
    out = {}
    for rung in source.values():
        out[rung] = out.get(rung, 0) + 1
    return dict(sorted(out.items()))


def _bones_by_source(source):
    out = {}
    for name, rung in sorted(source.items()):
        out.setdefault(rung, []).append(name)
    return out


# ---------------------------------------------------------------------------
# the gate this adds: in-limb continuity
# ---------------------------------------------------------------------------

def weight_continuity(rig, mesh, floor=REGION_FLOOR, max_bones=400,
                      ratio=HOLE_RATIO, min_drop=HOLE_MIN_DROP,
                      min_neighbours=HOLE_MIN_NEIGHBOURS):
    """Per bone: how many **holes** its weight map has, as a number.

    ``weight_report`` counts influences and ``influence_overlap`` names bones
    that share flesh they should not.  Neither can see the other half of the
    live defect: ``DEF-thigh.L``'s map was *patchy* — islands of weight with
    punctures between them, all of it inside the one limb, so no pair of bones
    was wrong and no vertex was unweighted.  It was only visible as a **shape**,
    in a render.

    A shape is a number if you say what you mean by it.  A vertex is a hole in a
    bone's region when:

    * at least ``min_neighbours`` of its topological neighbours are moved by
      that bone above ``floor``, so it is **surrounded** rather than at an edge;
    * its own weight is under ``ratio`` times the median of theirs;
    * and the drop is at least ``min_drop`` in absolute weight, so a legitimate
      falloff at the rim of a region is not counted as a puncture.

    The count is reported per bone next to that bone's region size, plus the
    worst single hole with its vertex index, so the number can be looked at.
    """
    _require_numpy()
    names = sorted(rigforge_rig.deform_bones(rig))
    groups = {}
    for group in mesh.vertex_groups:
        if group.name in names:
            groups[group.name] = group.index
    if not groups:
        return {"verdict": "unmeasured", "bones": 0, "holes": 0,
                "says": "%r has no vertex group named after a deform bone of %r, so "
                        "there is no weight map to check for holes."
                        % (mesh.name, rig.name)}
    order = [name for name in names if name in groups][:max_bones]
    weights = read_weights(mesh, order)
    edges = vertex_edges(mesh)
    a, b = edges[:, 0], edges[:, 1]

    inside = weights >= floor
    neighbours = _np.zeros_like(weights, dtype="i8")
    _np.add.at(neighbours, a, inside[b].astype("i8"))
    _np.add.at(neighbours, b, inside[a].astype("i8"))

    # The median of a vertex's *weighted* neighbours, per bone. numpy has no
    # segmented median, so it is taken the honest way: gather the neighbour
    # weights per vertex once, over the adjacency, and take the median in
    # Python for the handful of candidates that can possibly be holes.
    candidate = (neighbours >= int(min_neighbours))
    adjacency = None
    rows = []
    total_holes = 0
    total_region = 0
    worst = None
    for column, name in enumerate(order):
        region = int(_np.count_nonzero(inside[:, column]))
        total_region += region
        # A hole's weight is under ``ratio`` times a median that can never
        # exceed 1.0, so ``weight < ratio`` is an exact prefilter and the
        # per-vertex median below only ever runs on a handful of candidates.
        suspects = _np.nonzero(candidate[:, column] &
                               (weights[:, column] < ratio))[0]
        if suspects.size and adjacency is None:
            adjacency = _adjacency(edges, weights.shape[0])
        holes = 0
        worst_here = None
        for index in suspects:
            index = int(index)
            around = sorted(float(weights[n, column]) for n in adjacency[index]
                            if weights[n, column] >= floor)
            if len(around) < int(min_neighbours):
                continue
            middle = len(around) // 2
            median = (around[middle] if len(around) % 2
                      else 0.5 * (around[middle - 1] + around[middle]))
            mine = float(weights[index, column])
            drop = median - mine
            if mine < ratio * median and drop >= min_drop:
                holes += 1
                if worst_here is None or drop > worst_here[0]:
                    worst_here = (drop, index, mine, median)
        if not region and not holes:
            continue
        total_holes += holes
        pct = 100.0 * holes / max(region, 1)
        row = {"bone": name, "region_vertices": region, "holes": holes,
               "hole_pct": round(pct, 3), "verdict": _band(pct, "hole_pct")}
        if worst_here is not None:
            row["worst"] = {"vertex": worst_here[1],
                            "weight": round(worst_here[2], 4),
                            "neighbour_median": round(worst_here[3], 4),
                            "drop": round(worst_here[0], 4)}
            if worst is None or worst_here[0] > worst[0]:
                worst = (worst_here[0], name, worst_here[1], worst_here[2],
                         worst_here[3])
        rows.append(row)

    rows.sort(key=lambda row: (-row["holes"], row["bone"]))
    overall = 100.0 * total_holes / max(total_region, 1)
    verdict = _band(overall, "hole_pct")
    if total_holes:
        says = ("%d punctured vertex/vertices across %d bone region(s) (%.2f%% of all "
                "weighted flesh). Worst: %s is %.2f at vertex %d while its neighbours "
                "average %.2f."
                % (total_holes, len([row for row in rows if row["holes"]]), overall,
                   worst[1], worst[3], worst[2], worst[4]))
    else:
        says = ("No bone's weight map has a hole in it: every vertex inside a bone's "
                "region is as strongly weighted as the vertices around it, which is "
                "what a falloff looks like and what a patchy automatic bind does not.")
    return {
        "bones": len(rows),
        "holes": total_holes,
        "region_vertices": total_region,
        "hole_pct": round(overall, 3),
        "verdict": verdict,
        "thresholds": CONTINUITY_THRESHOLDS,
        "threshold_tier": ("heuristic (proxy tier): the bands are visible-dent "
                           "guesses, not artist accept/reject data. The count is "
                           "the measurement and outlives the band."),
        "floor": floor,
        "ratio": ratio,
        "min_drop": min_drop,
        "min_neighbours": int(min_neighbours),
        "per_bone": rows[:max_bones],
        "says": says,
    }


# ---------------------------------------------------------------------------
# command surface
# ---------------------------------------------------------------------------

@command("rigforge_skin")
def cmd_rigforge_skin(params):
    """Constrain an existing skin to its tags, or just report the contract.

    ``rigforge_skin {"object"?, "rig"?, "action"?: "apply"|"report"|"continuity",
    "max_influences"?, "blend"?, "smooth_passes"?, "smooth_factor"?}``

    ``apply`` (the default) runs mask -> blend -> smooth over the weights that
    are already on the mesh and reports what it removed and where it blended.
    ``report`` changes nothing and answers *what would be legal where*.
    ``continuity`` changes nothing and answers *how patchy is this bind*.
    """
    obj = resolve_object(params, mesh_only=True)
    rig = rigforge_rig._rig_for_mesh(obj, params)
    action = str(params.get("action") or "apply").strip().lower()
    if action not in ("apply", "report", "continuity"):
        raise ForgeError("action must be 'apply', 'report' or 'continuity'; got %r."
                         % (params.get("action"),))
    max_influences = get_int(params, "max_influences", 4, minimum=1, maximum=12)
    girth_fraction = get_float(params, "blend", BLEND_GIRTH_FRACTION,
                               minimum=0.0, maximum=4.0)
    passes = get_int(params, "smooth_passes", SMOOTH_PASSES, minimum=0, maximum=64)
    factor = get_float(params, "smooth_factor", SMOOTH_FACTOR, minimum=0.0, maximum=1.0)
    reach = get_float(params, "reach", BLEND_REACH, minimum=0.0, maximum=20.0)
    warnings = []

    metarig = None
    stored = str(rigforge_rig._prop(obj, rigforge_rig.PROP_METARIG, "") or "")
    if stored:
        metarig = bpy.data.objects.get(stored)

    if action == "continuity":
        return {"object": obj.name, "rig": rig.name, "action": action,
                "continuity": weight_continuity(rig, obj), "changed": 0,
                "warnings": warnings}

    regions, _empty = rigforge_rig.measure_tags(obj)
    if not regions:
        raise ForgeError(
            "Tag-constrained skinning needs tags: %r has no tagged geometry. Run "
            "rigforge_autotag first." % obj.name)

    if action == "report":
        contract = legal_bone_sets(rig, metarig, regions)
        tags = tag_membership(obj)
        edges = vertex_edges(obj)
        _blend, blend_report = blend_zones(obj, tags, edges, regions, girth_fraction)
        return {
            "object": obj.name, "rig": rig.name, "action": action, "changed": 0,
            "contract": {
                "legal": {tag: sorted(names)
                          for tag, names in sorted(contract["legal"].items())},
                "hinges": contract["hinges"],
                "source_counts": _count_sources(contract["source"]),
                "bones_by_source": _bones_by_source(contract["source"]),
            },
            "blend": blend_report,
            "warnings": warnings,
            "says": ("%d tag(s), %d deform bone(s). %s"
                     % (len(regions), len(rigforge_rig.deform_bones(rig)),
                        blend_report["says"])),
        }

    with object_mode():
        result = constrain_weights(obj, rig, metarig, regions,
                                   max_influences=max_influences,
                                   girth_fraction=girth_fraction,
                                   passes=passes, factor=factor, reach=reach,
                                   warnings=warnings)
    result["action"] = action
    result["changed"] = result["entries_written"]
    result["continuity"] = weight_continuity(rig, obj)
    result["report"] = rigforge_rig.weight_report(obj, rig)
    result["warnings"] = warnings
    return result
