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
from mathutils import Matrix, Vector

try:  # pragma: no cover - numpy is part of every Blender build
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None

from . import rigforge
from . import rigforge_autotag
from . import rigforge_landmarks
from . import rigforge_rig
from .common import (
    M_TO_MM,
    get_bool,
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
    "MIN_ARTICULATION_RINGS",
    "SMOOTH_PASSES",
    "SMOOTH_FACTOR",
    "SUB_TAG_BLEND_FRACTION",
    "CONTINUITY_THRESHOLDS",
    "ISOLATION_THRESHOLDS",
    "ISOLATION_SWING_DEG",
    "TagSplit",
    "TorsoSplit",
    "SplitSet",
    "torso_split",
    "chain_groups",
    "leg_chain_joints",
    "tag_ring_spacing",
    "leg_splits",
    "body_split",
    "split_regions",
    "sub_tag_seam_widths",
    "source_bone_names",
    "metarig_base",
    "def_bones_of",
    "deform_parent",
    "bone_owners",
    "legal_bone_sets",
    "articulations",
    "merge_legal",
    "tag_membership",
    "vertex_edges",
    "tag_girths",
    "blend_zones",
    "articulated_edges",
    "taper_edges",
    "read_weights",
    "write_weights",
    "smooth_weights",
    "fill_holes",
    "constrain_weights",
    "weight_continuity",
    "arm_swing_isolation",
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

#: The floor under an **articulating** seam's band, in rings of the topology at
#: that seam.  :data:`MIN_BLEND_RINGS` is a floor against a band being
#: *degenerate*; this is a floor against it being a **cliff**, and those are two
#: different failures that need two different numbers.
#:
#: **It is the same rule as :data:`REACH_BAND`, so it is the same number.** A
#: band's weights fall off over its outer ``REACH_BAND`` fraction, and a falloff
#: with no vertex in it is not a falloff — it is a step from full weight to
#: nothing across one edge, which creases.  For the falloff to contain at least
#: one ring the band must be at least ``1 / REACH_BAND`` rings wide.  Nothing
#: here is tuned: the constant is the one already governing the ramp.
#:
#: The coarser the mesh the **wider** the band this gives, which is the right
#: way round and is the point.  A low-resolution figure does not need its joints
#: refused or cut hard; it needs proportionally more room to fall off in.
#:
#: Measured in **local** rings — the tag's own median edge, not the figure's —
#: because a retopo is not uniform: the synthetic test biped's arm is sampled
#: every 15 mm and its trunk every 59 mm, and a floor quoted in the whole
#: figure's rings is a floor for neither.
#:
#: **Measured, and the neighbouring values are not equivalent.** On the biped,
#: at 2.0 rings the skin came out with 14 punctures against the unconstrained
#: bind's 9; at 3.0 it came out clean but a second ``apply`` stopped converging
#: (the suite pins the second pass under 0.05).  At ``1 / REACH_BAND`` both hold
#: — 8 punctures and a settled second pass — and the werewolf's leg-internal
#: stray mass stays at 0.0000 throughout, so this floor is not what buys that.
MIN_ARTICULATION_RINGS = 1.0 / REACH_BAND

#: How many blend bands long a slab must be for the cut that opens it to be
#: worth making.  **Two: one growing in from each end.**
#:
#: A band grows inward from every cut, so a slab carries one at each end.  If
#: the two meet, there is no interior where only that slab's own bones are
#: legal — and a slab with no interior is exactly the single merged bucket the
#: whole split exists to replace.  :data:`SUB_TAG_BLEND_FRACTION` already makes
#: this argument about the band *fraction* (that 0.5 is the value which cannot
#: work); this is the same statement as a length, which is the form a cut can
#: actually be refused on.
#:
#: Measured in the split tag's **own** ring spacing, because that is what its
#: bands are floored against (:data:`MIN_ARTICULATION_RINGS`) and the two have
#: to be quoted in the same units to be compared at all.
MIN_SLAB_BANDS = 2.0

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

#: How wide the blend band between two **sub-tags of the same tag** is, as a
#: fraction of the shorter of the two slabs' own length **along the spine**.
#:
#: Not a girth, and that is the point.  :data:`BLEND_GIRTH_FRACTION` measures a
#: seam in the girth of the thinner limb meeting there, which is right where two
#: *limbs* meet: the seam runs around a tube and the band runs along it.  Two
#: slabs of one trunk meet the other way round — the seam runs around the trunk
#: and the band runs *along the spine* — so the trunk's girth says how wide the
#: body is, not how far an influence should travel.  On the werewolf the torso's
#: girth is 145 mm and one girth of band would swallow 22% of the whole spine at
#: every internal cut, handing the shoulder most of the belly back.  The band is
#: measured in the direction it actually grows.
#:
#: **A quarter, and 0.5 is the value that cannot work.**  A slab has a band
#: growing in from each end, so at 0.5 the two meet exactly in the middle and
#: the slab has no interior where only its own bones are legal — which is the
#: single bucket the split exists to replace.  Measured on the werewolf (stray
#: influence mass / punctured vertices, at the same cuts and reach)::
#:
#:     0.50   12.931 / 366   the bands meet: 69 vertices still shared by
#:                           DEF-breast.R and DEF-pelvis.L, 382 mm apart
#:     0.35    3.806 / 377
#:     0.25    0.586 / 395   <-- the knee: 22x less stray for 29 more holes
#:     0.10    0.100 / 415   another 0.49 of stray for another 20 holes
#:
#: Below about 0.10 the ring floor (:data:`MIN_BLEND_RINGS` x the median edge,
#: 25 mm here) takes over and the number stops moving at all.
SUB_TAG_BLEND_FRACTION = 0.25


#: How far the isolation measurement swings the arms, in degrees, about the
#: character's own lateral axis — a walk's forward/back swing, mirrored L/R.
#: Big enough that a stray influence is millimetres rather than rounding, small
#: enough to stay inside the range a walk cycle actually uses.
ISOLATION_SWING_DEG = 30.0

#: Bands for the isolation gate, in millimetres of displacement at that swing.
#: **Credibility tier: heuristic (proxy tier).**  The measurement is the
#: millimetres and it outlives the band: a pelvis that moves a millimetre when
#: the arm swings 30 degrees is not visible, and five is the flesh following the
#: arm the way the owner saw it in the walk.
ISOLATION_THRESHOLDS = {"max_mm": {"ok": 1.0, "attention": 5.0}}


def _require_numpy():
    if _np is None:  # pragma: no cover - numpy is part of Blender
        raise ForgeError(
            "This Blender build has no numpy, so tag-constrained skinning cannot "
            "run - the constraint, the blend walk and the smoothing are one field "
            "solve over every vertex and every deform bone, and there is no sane "
            "pure-Python version of it.")


def _band(value, key, thresholds=None):
    if value is None:
        return "unmeasured"
    bands = (thresholds or CONTINUITY_THRESHOLDS)[key]
    if value <= bands["ok"]:
        return "ok"
    if value <= bands["attention"]:
        return "attention"
    return "fail"


# ---------------------------------------------------------------------------
# a tag, read as three slabs of its own axis
# ---------------------------------------------------------------------------

class TagSplit(object):
    """One tag read as three slabs of its own axis — a **derived view**.

    The failure this exists to fix
    ------------------------------
    The tag contract is enforced per tag, and a tag can be too coarse to *be* a
    contract.  On the werewolf ``Torso`` is one bucket holding **thirteen**
    deform bones: ``rig_check``'s cross-tag stray mass is 0.000 and the walk
    still shows the arm swing tugging the thighs, because nothing in that number
    is wrong — a shoulder bone putting weight on pelvis flesh is *legal*, both
    are ``Torso``, and pelvis flesh sits in the ``Torso``/``Leg`` blend band
    where it is shared with the thighs.  ``Leg.R`` is the same failure one tag
    over: one bucket holding the whole leg chain, where ``DEF-foot.R`` on thigh
    flesh 340 mm away is legal because both are ``Leg.R`` — measured at **4.67
    vertex-weights** on this figure.  The contract needs to be finer than the
    tag, and :func:`~forge.tools.rigforge_autotag.spine_split` (for a trunk) and
    :func:`~forge.tools.rigforge_autotag.leg_split` (for a limb with joints of
    its own) measure where.

    One class, two recipes, because the *rule* is the same either way: three
    slabs along the tag's own axis, cut at measured landmarks, with a blend band
    only where the two slabs meeting at a cut actually articulate.  What differs
    is where the cuts come from, and that lives in the two split functions.

    Nothing is written to the mesh
    ------------------------------
    There is no ``tag_Torso.chest`` vertex group, no ``tag_Leg.R.foot`` group
    and no stored property.  The split is recomputed from the six tags that
    exist, every time it is needed, so
    :func:`~forge.tools.rigforge_rig.measure_tags`, the landmark fitter and
    every other consumer keep seeing exactly one ``Torso`` and one ``Leg.R`` —
    and a mesh that has since been re-tagged by hand can never be skinned
    against a stale split, because there is no stale split to be had.
    """

    def __init__(self, report, axis, membership, regions):
        self.report = report
        self.axis = axis
        self.cuts = list(report["cuts"])
        self.names = tuple(report["names"])
        self.parent = report["parent"]
        #: ``{vertex index: sub-tag}`` for the parent tag's own vertices.
        self.membership = membership
        #: ``{sub-tag: Region}`` — each slab measured about the spine, not about
        #: its own principal axis (a squat slab of a wide trunk has a *lateral*
        #: principal axis, and its "girth" would then be its own height).
        self.regions = regions

    # -- geometry ---------------------------------------------------------

    def t_of(self, point):
        """Where a world-space point sits along this tag's axis, 0 to 1, proximal first."""
        return self.axis.closest(Vector(point))[1]

    def of_point(self, point):
        return rigforge_autotag.sub_tag_at(self.t_of(point), self.cuts, self.names)

    def of_span(self, t_low, t_high):
        return rigforge_autotag.sub_tags_spanning(t_low, t_high, self.cuts, self.names)

    def of_bone(self, rig, name):
        """``(primary sub-tag, every sub-tag the bone spans)`` — the bone rule.

        A vertex is a point and lands in one slab.  A **bone is a span**, and a
        bone that crosses a cut moves the flesh on both sides of it, so it joins
        both — the blend zone's own rule, one level up.  The *primary* (the slab
        its midpoint is in) is what the hinge rule reads, so a bone still has
        exactly one owner and "hangs from" still means something.
        """
        bone = rig.data.bones.get(name)
        if bone is None:
            return None, ()
        matrix = rig.matrix_world
        head = matrix @ bone.head_local
        tail = matrix @ bone.tail_local
        spans = self.of_span(self.t_of(head), self.t_of(tail))
        return self.of_point((head + tail) * 0.5), spans

    # -- the merged view every other consumer has --------------------------

    def merged(self, tag):
        """A sub-tag folded back into the tag it is a slab of; anything else kept."""
        return self.parent if tag in self.names else tag

    def parent_of(self, tag):
        """The tag a sub-tag is a slab of, or ``None`` for anything else."""
        return self.parent if tag in self.names else None

    def substitute(self, tags):
        """``tag_membership``'s per-vertex sets, with the parent tag refined."""
        out = []
        for index, entry in enumerate(tags):
            if self.parent not in entry:
                out.append(entry)
                continue
            sub = self.membership.get(index)
            out.append(frozenset((entry - {self.parent}) | {sub or self.parent}))
        return out

    # -- the one-member case of the composite protocol ---------------------
    # So every consumer can walk ``split.members`` without asking whether it was
    # handed one split or several.

    @property
    def members(self):
        return (self,)

    @property
    def parents(self):
        return (self.parent,)

    def member_for_tag(self, tag):
        return self if tag in self.names else None

    def member_for_parent(self, parent):
        return self if parent == self.parent else None


class SplitSet(object):
    """Every derived sub-tag view on one mesh, read as one split.

    A figure has more than one tag too coarse to be a contract — a trunk and two
    legs on this one — and they are cut against different landmarks by different
    functions.  What every consumer downstream wants is *one* object answering
    "what slab is this vertex in, what slabs does this bone span, what does this
    sub-tag fold back to", so this is that object and each :class:`TagSplit`
    keeps its own axis, its own cuts and its own naming underneath it.

    Holding them together rather than threading two parameters everywhere is
    what keeps the contract honest: ``legal_bone_sets`` re-owns a bone against
    **its own** parent's split, and a bone the trunk owns is never measured
    against a leg's axis.
    """

    def __init__(self, members, torso_report=None, leg_reports=None):
        self.members = tuple(members)
        #: The spine split's own report, for the ``sub_tags`` key every existing
        #: consumer already reads.
        self.torso_report = dict(torso_report or {})
        #: ``{parent tag: report}`` for the legs, beside it rather than inside
        #: it, because a caller reading ``sub_tags`` must not silently start
        #: getting something of a different shape.
        self.leg_reports = dict(leg_reports or {})
        self._by_parent = {member.parent: member for member in self.members}
        self._by_name = {name: member
                         for member in self.members for name in member.names}
        self.names = tuple(name for member in self.members for name in member.names)
        self.parents = tuple(member.parent for member in self.members)
        self.regions = {name: region for member in self.members
                        for name, region in member.regions.items()}

    @property
    def parent(self):
        """The trunk's parent tag when there is one, for the messages that name one."""
        torso = self._by_parent.get(rigforge_autotag.SPLIT_PARENT)
        if torso is not None:
            return torso.parent
        return self.parents[0] if self.parents else None

    def member_for_tag(self, tag):
        return self._by_name.get(tag)

    def member_for_parent(self, parent):
        return self._by_parent.get(parent)

    def merged(self, tag):
        member = self._by_name.get(tag)
        return member.parent if member is not None else tag

    def parent_of(self, tag):
        member = self._by_name.get(tag)
        return member.parent if member is not None else None

    def substitute(self, tags):
        out = list(tags)
        for member in self.members:
            out = member.substitute(out)
        return out

    def without(self, member):
        """A copy with one member dropped — how a starved slab is taken back.

        Per member, deliberately: a leg slab no bone reaches is a reason to stop
        splitting **that leg**, and taking the trunk's split back with it would
        cost a fix that is working to pay for one that is not.
        """
        return SplitSet([other for other in self.members if other is not member],
                        self.torso_report, self.leg_reports)


#: The old name, from when the trunk was the only tag too coarse to be a
#: contract.  Kept so ``isinstance(split, TorsoSplit)`` and every import of it
#: still mean what they meant.
TorsoSplit = TagSplit


class _SubRegion(rigforge_rig.Region):
    """One slab of a split tag, measured **about the spine** it was cut along.

    ``Region`` picks a cloud's own principal axis, which for a slab of a wide
    trunk is lateral — 338 pelvis vertices 350 mm across and 200 mm tall have
    their dominant direction across the body, and :func:`tag_girths` would then
    report the slab's own height as its girth and size every blend band off it.
    The slab's axis is the spine's, by construction, because that is the
    direction it was cut along.
    """

    def __init__(self, tag, points, axis, parent=None):
        rigforge_rig.Region.__init__(self, tag, points, axis_hint=axis)
        self.parent = parent
        self.axis = Vector(axis).normalized()
        self._projections = [(Vector(point) - self.centre).dot(self.axis)
                             for point in points]
        low, high = min(self._projections), max(self._projections)
        self.length = high - low
        half = max(self.length * 0.08, 1e-6)
        self.start = rigforge_rig._slab_centroid(points, self._projections, low, half)
        self.mid = rigforge_rig._slab_centroid(points, self._projections,
                                               (low + high) * 0.5, half)
        self.end = rigforge_rig._slab_centroid(points, self._projections, high, half)


def tag_ring_spacing(obj, tag):
    """One tag's own median edge length, in metres — the ring spacing of its flesh.

    The same measurement :func:`_tag_edge_spacing` makes for the blend bands,
    taken here from the mesh's vertex groups because it is needed *before* the
    split exists and so before there are any sub-tags to key it by.
    """
    group = obj.vertex_groups.get(rigforge.tag_group_name(tag))
    if group is None:
        return 0.0
    inside = set()
    for vertex in obj.data.vertices:
        for element in vertex.groups:
            if element.group == group.index and element.weight > 0.0:
                inside.add(vertex.index)
                break
    matrix = obj.matrix_world
    vertices = obj.data.vertices
    lengths = []
    for edge in obj.data.edges:
        one, other = edge.vertices
        if one in inside and other in inside:
            lengths.append(((matrix @ vertices[one].co)
                            - (matrix @ vertices[other].co)).length)
    if not lengths:
        return 0.0
    lengths.sort()
    middle = len(lengths) // 2
    return (lengths[middle] if len(lengths) % 2
            else 0.5 * (lengths[middle - 1] + lengths[middle]))


def _slab_view(obj, region, parent, axis, report):
    """``TagSplit | None`` — a measured split report turned into the derived view.

    The half every split shares: classify the parent tag's vertices and its
    measured cloud into the slabs the report cut, and measure each slab about
    the axis it was cut along.
    """
    names = tuple(report["names"])
    cuts = report["cuts"]
    # The Region's own point list is in the same order tag_points() walked the
    # mesh in, so the membership can be keyed back to vertex indices only by
    # walking the group again. Cheaper and less fragile: classify by position.
    membership = {}
    matrix = obj.matrix_world
    group = obj.vertex_groups.get(rigforge.tag_group_name(parent))
    if group is not None:
        for vertex in obj.data.vertices:
            for element in vertex.groups:
                if element.group == group.index and element.weight > 0.0:
                    membership[vertex.index] = rigforge_autotag.sub_tag_at(
                        axis.closest(matrix @ vertex.co)[1], cuts, names)
                    break
    clouds = {name: [] for name in names}
    for point in region._points:
        clouds[rigforge_autotag.sub_tag_at(axis.closest(point)[1],
                                           cuts, names)].append(point)
    sub_regions = {name: _SubRegion(name, points, axis.direction, parent)
                   for name, points in clouds.items() if len(points) >= 2}
    if len(sub_regions) != len(names):
        return None
    return TagSplit(report, axis, membership, sub_regions)


def torso_split(obj, regions, rig=None, metarig=None, enabled=True):
    """``(TagSplit | None, report)`` — the Torso read as slabs of its own spine.

    **One slab per vertebra where the rig allows it**, cut at the spine chain's
    own joints (:func:`~forge.tools.rigforge_autotag.torso_chain_split`); the
    three-landmark pelvis / abdomen / chest split
    (:func:`~forge.tools.rigforge_autotag.spine_split`) is the fallback for a
    trunk whose chain cannot be read, and was the whole story before the chain
    was available here.  Both are the same recipe at different resolutions, and
    the report says which one fired.

    Refuses rather than guesses, and the report says which: a torso with no leg
    tag beside it has nothing measuring where its pelvis ends, and a slab too
    thin to measure is worse than no split at all.  A refusal costs the merged
    ``Torso`` contract that was there before this existed.
    """
    parent = rigforge_autotag.SPLIT_PARENT
    fallback_names = list(rigforge_autotag.TORSO_SUB_TAGS)
    if not enabled:
        return None, {"parent": parent, "names": fallback_names,
                      "refused": "the split was switched off for this run"}
    region = regions.get(parent)
    if region is None:
        return None, {"parent": parent, "names": fallback_names,
                      "refused": "there is no %s tag on %r to split" % (parent, obj.name)}

    # --- the fine cut: one slab per segment of the spine -------------------
    chain_report = None
    if rig is not None:
        owner, _source = bone_owners(rig, metarig, regions)
        root, groups = chain_groups(rig, owner, parent, metarig)
        if root is not None and len(groups) >= 2:
            names = rigforge_autotag.torso_sub_tags(base for base, _p in groups)
            joints = [(groups[position][0], groups[position][1])
                      for position in range(1, len(groups))]
            chain_report, axis = rigforge_autotag.split_from_torso_cloud(
                parent, region._points, root, joints, names,
                seed=Vector(region.axis),
                min_slab=(MIN_SLAB_BANDS * MIN_ARTICULATION_RINGS
                          * tag_ring_spacing(obj, parent)))
            if axis is not None:
                view = _slab_view(obj, region, parent, axis, chain_report)
                if view is not None:
                    chain_report["measured_in"] = "the spine chain's own joints"
                    return view, chain_report
                chain_report = dict(chain_report)
                chain_report.pop("cuts", None)
                chain_report["refused"] = (
                    "a slab of the chain split came out with under two vertices")

    # --- the fallback: the three landmarks ---------------------------------
    legs = [point for tag in sorted(regions)
            if tag.startswith("Leg") for point in regions[tag]._points]
    report, axis = rigforge_autotag.split_from_clouds(
        region._points, legs, seed=Vector(region.axis))
    if chain_report is not None:
        report = dict(report)
        report["chain_refused"] = chain_report.get("refused")
    if axis is None:
        return None, report
    view = _slab_view(obj, region, parent, axis, report)
    if view is None:
        report = dict(report)
        report.pop("cuts", None)
        report["refused"] = ("a slab of the split came out with under two vertices, "
                             "so it cannot be measured")
        return None, report
    report["measured_in"] = "the leg junction and the waist"
    return view, report


def chain_groups(rig, owner, tag, metarig=None, known=None):
    """``(root point, [(base, head point), ...])`` — a tag's own chain, proximal first.

    Where a tag's cuts come from, read off the rig rather than guessed at.  The
    tag's deform bones are walked **in chain order** from the bone that hangs
    off something else, and consecutive bones are grouped by the *metarig bone
    they were generated from* — so ``DEF-thigh.R`` and ``DEF-thigh.R.001`` are
    one group, and Rigify's subdivision does not turn one thigh into two.  The
    boundary between one group and the next is a **joint**: on a Rigify leg the
    first is the knee and the second is the ankle, and on a spine every one of
    them is a vertebral joint.  All of them sit where the landmark fitter put
    them, which is the girth minimum it measured.

    Grouping by source bone rather than matching the names ``thigh``/``shin``/
    ``foot`` is what makes this a structural read: a chain of four groups (a leg
    with a toe) gives the same two joints as a chain of three, because a leg
    split asks only for the first two, and a limb whose bones are named
    something else entirely still splits at its own joints.  It is also what
    lets the **trunk** use the same walk: ``Torso``'s owned bones are the spine
    chain with the pelvis and shoulder bones hanging off it, and the longest
    path through them is the spine.

    Where the chain branches — a foot with several toes — the **longest** branch
    is followed, which is the one that runs the length of the limb.  Ties go to
    the lower name, so two runs over the same rig give the same answer.

    The walk is at **group** level and that is not a detail.
    :func:`deform_parent` answers the metarig's question — *what does this limb
    hang off* — so it resolves every segment of one metarig bone to the same
    ancestor: on this rig both ``DEF-thigh.R`` and ``DEF-thigh.R.001`` report
    ``DEF-spine``, which as a bone-level tree is two roots and no chain at all
    (measured: it found **0 joints** and refused both legs).  Between *groups*
    the same function is exactly right — ``DEF-shin.R`` reports
    ``DEF-thigh.R.001``, the thigh group's distal end — so the groups chain up
    cleanly, and the ordering *inside* a group comes from the generated rig's
    own hierarchy, which does link a segment to the one before it.
    """
    known = known if known is not None else source_bone_names(rig, metarig)
    members = sorted(name for name, held in owner.items() if held == tag)
    if not members:
        return None, []

    # --- the groups: one per metarig bone this limb was generated from ----
    groups = {}
    for name in members:
        groups.setdefault(metarig_base(name, known) or name, []).append(name)

    def ordered(names):
        """One group's segments, proximal to distal.

        The generated rig *does* parent ``DEF-shin.R.001`` to ``DEF-shin.R``, so
        a segment with no parent inside its own group is the proximal one and
        the rest follow it.  Rigify's ``base``, ``base.001``, ``base.002``
        naming sorts the same way and is the fallback for a rig whose segments
        are not parented to each other.
        """
        inside = set(names)
        parent_of = {}
        for name in names:
            bone = rig.data.bones.get(name)
            cursor = bone.parent if bone is not None else None
            while cursor is not None and cursor.name not in inside:
                cursor = cursor.parent
            parent_of[name] = cursor.name if cursor is not None else None
        out = []
        cursor = sorted(name for name in names if parent_of[name] is None)
        cursor = cursor[0] if cursor else sorted(names)[0]
        seen = set()
        while cursor is not None and cursor not in seen:
            seen.add(cursor)
            out.append(cursor)
            nxt = sorted(name for name in names
                         if parent_of[name] == cursor and name not in seen)
            cursor = nxt[0] if nxt else None
        return out + sorted(set(names) - seen)

    groups = {base: ordered(names) for base, names in groups.items()}

    # --- the group tree, from the metarig's own structure -----------------
    children = {}
    roots = []
    for base, names in sorted(groups.items()):
        held = deform_parent(rig, names[0], metarig, known)
        held = metarig_base(held, known) if held else None
        if held in groups and held != base:
            children.setdefault(held, []).append(base)
        else:
            roots.append(base)
    if not roots:
        return None, []
    root = sorted(roots)[0]

    def longest(base, seen):
        best = []
        for child in sorted(children.get(base, ())):
            if child in seen:
                continue
            branch = longest(child, seen | {child})
            if len(branch) > len(best):
                best = branch
        return [base] + best

    chain = longest(root, {root})
    matrix = rig.matrix_world
    head = rig.data.bones.get(groups[chain[0]][0])
    root_point = (matrix @ head.head_local) if head is not None else None
    out = []
    for position, base in enumerate(chain):
        bone = rig.data.bones.get(groups[base][0])
        if bone is None:
            continue
        out.append((base, matrix @ bone.head_local, position))
    return root_point, [(base, point) for base, point, _p in out]


def leg_chain_joints(rig, owner, tag, metarig=None, known=None):
    """``(hip, [(label, point), ...])`` — one limb's joints, from :func:`chain_groups`.

    The joints are the boundaries *between* the chain's groups, so a chain of
    four groups (a leg with a toe) has three of them and a leg split asks for
    the first two — the knee and the ankle.
    """
    root, groups = chain_groups(rig, owner, tag, metarig, known)
    if root is None:
        return None, []
    joints = [("%s/%s" % (groups[position - 1][0], groups[position][0]),
               groups[position][1])
              for position in range(1, len(groups))]
    return root, joints


def leg_splits(obj, regions, rig, metarig=None, enabled=True):
    """``([TagSplit, ...], {parent: report})`` — every ``Leg`` tag read as three slabs.

    Per side and per leg, each against **its own** axis and its own chain's own
    joints.  Nothing here mirrors one side onto the other, and nothing checks
    whether they match: on this werewolf they happen to be mirrored, and a
    figure whose legs are not — a limp, a prosthesis, ``symmetry: as_designed``
    — is cut correctly twice rather than once and copied.

    Refuses per leg rather than as a set.  One leg with no readable chain costs
    that leg's split and leaves the other's standing, because the coarse
    contract it falls back to is exactly the contract that was there before.
    """
    tags = sorted(tag for tag in regions
                  if tag == rigforge_autotag.LEG_SPLIT_PREFIX
                  or tag.startswith(rigforge_autotag.LEG_SPLIT_PREFIX + "."))
    if not enabled:
        return [], {tag: {"parent": tag,
                          "names": list(rigforge_autotag.leg_sub_tags(tag)),
                          "refused": "the split was switched off for this run"}
                    for tag in tags}
    reports = {}
    splits = []
    if rig is None:
        return [], {tag: {"parent": tag,
                          "names": list(rigforge_autotag.leg_sub_tags(tag)),
                          "refused": ("there is no rig here to read this leg's own "
                                      "knee and ankle off")}
                    for tag in tags}
    known = source_bone_names(rig, metarig)
    owner, _source = bone_owners(rig, metarig, regions)
    matrix = obj.matrix_world
    for tag in tags:
        region = regions[tag]
        hip, joints = leg_chain_joints(rig, owner, tag, metarig, known)
        if hip is None:
            reports[tag] = {
                "parent": tag,
                "names": list(rigforge_autotag.leg_sub_tags(tag)),
                "refused": ("no deform bone belongs to %s, so there is no chain to "
                            "read its knee and its ankle off" % tag),
            }
            continue
        report, axis = rigforge_autotag.split_from_leg_cloud(
            tag, region._points, hip, joints, seed=Vector(region.axis))
        reports[tag] = report
        if axis is None:
            continue
        membership = {}
        group = obj.vertex_groups.get(rigforge.tag_group_name(tag))
        names = rigforge_autotag.leg_sub_tags(tag)
        if group is not None:
            for vertex in obj.data.vertices:
                for element in vertex.groups:
                    if element.group == group.index and element.weight > 0.0:
                        membership[vertex.index] = rigforge_autotag.sub_tag_at(
                            axis.closest(matrix @ vertex.co)[1], report["cuts"], names)
                        break
        clouds = {name: [] for name in names}
        for point in region._points:
            clouds[rigforge_autotag.sub_tag_at(
                axis.closest(point)[1], report["cuts"], names)].append(point)
        sub_regions = {name: _SubRegion(name, points, axis.direction, tag)
                       for name, points in clouds.items() if len(points) >= 2}
        if len(sub_regions) != len(names):
            report = dict(report)
            report.pop("cuts", None)
            report["refused"] = ("a slab of the split came out with under two "
                                 "vertices, so it cannot be measured")
            reports[tag] = report
            continue
        splits.append(TagSplit(report, axis, membership, sub_regions))
    return splits, reports


def body_split(obj, regions, rig=None, metarig=None, enabled=True):
    """``(SplitSet | None, torso report, {leg tag: report})`` — every split on one mesh.

    The one place the derived views are built, so a caller cannot enforce the
    contract against a subset of them by accident.  A refusal anywhere costs
    that tag's split and nothing else; a refusal everywhere costs the whole
    ``SplitSet``, and the merged contract that was there before this existed is
    what the caller falls back to.
    """
    torso, torso_report = torso_split(obj, regions, rig, metarig, enabled=enabled)
    legs, leg_reports = leg_splits(obj, regions, rig, metarig, enabled=enabled)
    members = ([torso] if torso is not None else []) + list(legs)
    if not members:
        return None, torso_report, leg_reports
    return SplitSet(members, torso_report, leg_reports), torso_report, leg_reports


def split_regions(regions, split):
    """``regions`` with every split tag replaced by its slabs.  A copy, never in place."""
    if split is None:
        return dict(regions)
    parents = set(split.parents)
    out = {tag: region for tag, region in regions.items() if tag not in parents}
    out.update(split.regions)
    return out


def sub_tag_seam_widths(split, fraction=SUB_TAG_BLEND_FRACTION):
    """``{(tagA, tagB): metres}`` for the seams *inside* each split tag.

    See :data:`SUB_TAG_BLEND_FRACTION` for why a sibling seam is not measured in
    girths.  Only sibling pairs of the **same** parent are listed — ``Leg.R``'s
    thigh and shin, never ``Leg.R``'s shin and ``Leg.L``'s — and every other
    seam keeps the girth rule, which is what it is for.
    """
    if split is None:
        return {}
    out = {}
    for member in split.members:
        names = list(member.names)
        for position in range(len(names) - 1):
            one, other = names[position], names[position + 1]
            lengths = [member.regions[name].length for name in (one, other)
                       if name in member.regions]
            if not lengths:
                continue
            key = (one, other) if one <= other else (other, one)
            out[key] = fraction * min(lengths)
    return out


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


def legal_bone_sets(rig, metarig=None, regions=None, split=None):
    """``{tag: set(deform bone names)}`` — the tag contract, derived not listed.

    With a ``split`` (a :class:`TagSplit`, or a :class:`SplitSet` of them) the
    contract is enforced at **sub-tag granularity**: every bone a split parent
    tag owns is re-owned by the slab its own midpoint is in — measured against
    *that parent's* own axis — and a bone whose **span** crosses a cut is legal
    on both slabs, the blend zone's rule one level up.  On the werewolf that
    turns one bucket of thirteen bones into ``Torso.pelvis`` (the hips and both
    pelvis bones), ``Torso.abdomen`` and ``Torso.chest`` (both breasts, both
    shoulders and the upper spine), with ``DEF-spine.001`` and ``DEF-spine.002``
    in two slabs each because they cross a cut.  The shoulder is then no longer
    legal on pelvis flesh, which is the whole fix.

    The legs are cut the same way and for the same reason: ``Leg.R`` becomes
    ``Leg.R.thigh`` (``DEF-thigh.R`` and ``DEF-thigh.R.001``), ``Leg.R.shin``
    and ``Leg.R.foot`` (the foot and the toe), so ``DEF-foot.R`` stops being
    legal on thigh flesh 340 mm away.  Its hinge — ``DEF-shin.R.001``, the bone
    it hangs from — joins the foot slab's legal set and is held to
    :data:`BLEND_REACH`, which is the ankle band, and there is no band at all
    anywhere the leg does not bend.

    Without one, the behaviour is exactly what it was.

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
    spans = {}
    if split is not None:
        matrix = rig.matrix_world
        # Per member, and against **that** member's own axis: a bone the trunk
        # owns is never measured along a leg's centreline, and each leg's own
        # ends are found on its own chain rather than on its sibling's.
        for member in split.members:
            raw = {}
            for name, tag in sorted(owner.items()):
                if tag != member.parent:
                    continue
                bone = rig.data.bones.get(name)
                if bone is None:
                    continue
                head = matrix @ bone.head_local
                tail = matrix @ bone.tail_local
                raw[name] = (member.t_of(head), member.t_of(tail), (head + tail) * 0.5)
            lowest = min((min(a, b) for a, b, _mid in raw.values()), default=0.0)
            highest = max((max(a, b) for a, b, _mid in raw.values()), default=1.0)
            for name, (first, second, middle) in sorted(raw.items()):
                low, high = (first, second) if first <= second else (second, first)
                # **The flesh past the end of the chain belongs to the bone at
                # that end.** Measured on the synthetic test biped, whose Torso
                # tag runs 80 mm below its lowest torso bone (the residual takes
                # the groin): without this the pelvis slab came out with an
                # *empty* legal set and the whole split had to be taken back. A
                # slab below the lowest bone has no other owner, and a skin with
                # no owner is not a skin. It holds a leg the same way, where the
                # Leg tag runs past the toe and past the top of the thigh.
                if low <= lowest + 1e-9:
                    low = -1.0
                if high >= highest - 1e-9:
                    high = 2.0
                owner[name] = member.of_point(middle)
                spans[name] = rigforge_autotag.sub_tags_spanning(
                    low, high, member.cuts, member.names)
    legal = {}
    hinges = {}
    if split is not None:
        # Every slab is a key even before a bone lands in it, so an empty legal
        # set is visible to the caller as an empty set rather than as a missing
        # tag that silently reads as "no contract here".
        for name in split.names:
            legal.setdefault(name, set())
    for name, tag in owner.items():
        legal.setdefault(tag, set()).add(name)
        for crossed in spans.get(name, ()):
            legal.setdefault(crossed, set()).add(name)
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
        "spans": {name: list(value) for name, value in sorted(spans.items())},
        "hinges": {tag: sorted(names) for tag, names in sorted(hinges.items())},
    }


def articulations(contract, split=None):
    """``{(tag, tag)}`` — the tag pairs that actually **articulate**.

    The failure this exists to fix, measured on the werewolf
    --------------------------------------------------------
    A blend band exists so a **joint** does not crease.  :func:`blend_zones`
    opened one at every seam instead — every place two tags happen to touch —
    and on a figure whose arms hang at its sides the hand touches the thigh.
    That is not a joint; it is two limbs standing next to each other, and the
    band handed ``DEF-hand.R`` and ``DEF-forearm.R.001`` a full licence on
    **leg-tagged flesh**.  Measured: swinging the arms 30 degrees moved thigh
    vertices **353 mm**, and the worst of them carried 0.23 of the hand.  It is
    the same mistake bone heat makes at the armpit — spatial adjacency read as
    anatomical connection — one level up, and it is the whole of the *"the arm
    swing tugs the thighs"* the owner saw in the walk.

    What connection means here is not a guess.  The contract already knows it:
    a tag's **hinge** is the bone it hangs from, and the tag that owns that bone
    is the tag it articulates with.  ``Arm.L`` hinges on ``DEF-shoulder.L``, so
    ``Arm.L`` and the tag owning the shoulder blend; ``Leg.L`` hinges on the
    hips, so ``Leg.L`` and the tag owning the hips blend.  ``Arm.R`` and
    ``Leg.R`` share no bone at all, so they do not — whatever their flesh is
    doing.  Sibling slabs of a split tag are added, because they are one body
    cut for the contract's sake and every cut between them is a joint's worth of
    spine.  On a **leg** that is not an approximation at all: the two cuts are
    the knee and the ankle, so a sibling seam there is an articulation in the
    strictest sense, and the band the articulation rule opens sits exactly where
    the joint bends and nowhere else.
    """
    owner = contract["owner"]
    spans = contract.get("spans") or {}
    out = set()
    for tag, bones in contract["hinges"].items():
        for bone in bones:
            # Every tag that **owns** the hinge — its primary slab and any other
            # it spans into. Deliberately not "every tag whose legal set lists
            # it": the hips are the hinge of *both* legs, and reading legality
            # backwards would make the two legs articulate with each other.
            holders = set(spans.get(bone, ()))
            if bone in owner:
                holders.add(owner[bone])
            for other in holders:
                if other == tag:
                    continue
                out.add((tag, other) if tag <= other else (other, tag))
    if split is not None:
        for member in split.members:
            names = list(member.names)
            for position in range(len(names) - 1):
                one, other = names[position], names[position + 1]
                out.add((one, other) if one <= other else (other, one))
    return out


def merge_legal(legal, split):
    """A split contract folded back into the one tag every other consumer sees."""
    if split is None:
        return {tag: sorted(names) for tag, names in sorted(legal.items())}
    out = {}
    for tag, names in legal.items():
        out.setdefault(split.merged(tag), set()).update(names)
    return {tag: sorted(names) for tag, names in sorted(out.items())}


# ---------------------------------------------------------------------------
# the mesh, as a graph with tags on it
# ---------------------------------------------------------------------------

def tag_membership(obj, split=None):
    """``[frozenset(tags), ...]`` per vertex, from the ``tag_*`` vertex groups.

    With a ``split``, the parent tag is replaced per vertex by the slab that
    vertex is in — the only place the finer view enters, and it never touches
    the groups on the mesh.
    """
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
    return split.substitute(out) if split is not None else out


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


def articulated_edges(edges, tags, connected):
    """``edges`` without the ones that cross a seam between tags that do not articulate.

    The mesh's edge graph says what is *next to* what.  Everything this module
    walks over it — the blend band, the taper, the smoother — is asking what is
    *attached to* what, and on a figure whose arms hang down those are not the
    same graph: the hand is next to the thigh and attached to the forearm.  The
    contract already knows which is which (:func:`articulations`), so the graph
    is cut where it does not articulate and every walk over it stops there.

    **Measured, and both halves cost a run to find.**  Leaving the *taper* on
    those edges put the hand's weight straight back onto the thigh through the
    smoother's hole filler and the werewolf's arm swing went from 353 mm to
    545 mm.  Leaving the *Laplacian* on them made every rim weight fade towards
    a neighbour across the cut, so a second ``apply`` eroded it again and the
    stage stopped converging (synthetic biped: worst second-pass weight change
    0.077, against a suite that pins it under 0.05).
    """
    _require_numpy()
    if connected is None or not len(edges):
        return edges
    keep = _np.ones(len(edges), dtype=bool)
    for position in range(len(edges)):
        one, other = tags[int(edges[position, 0])], tags[int(edges[position, 1])]
        if one == other or not one or not other or (one & other):
            continue
        if not any(((a, b) if a <= b else (b, a)) in connected
                   for a in one for b in other):
            keep[position] = False
    return edges[keep]


def taper_edges(obj, edges, tags, regions, girth_fraction=BLEND_GIRTH_FRACTION,
                connected=None):
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

    **And it never crosses a seam between tags that do not articulate** — see
    :func:`articulated_edges`, which is where that cut is made and measured.  A
    taper is a falloff towards a neighbour that shares the joint; where there is
    no joint there is nothing to fall off towards, and the cliff is the right
    answer.
    """
    _require_numpy()
    edges = articulated_edges(edges, tags, connected)
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


def _tag_edge_spacing(tags, edges, world):
    """``{tag: metres}`` — each tag's **own** median edge, its ring spacing.

    The ruler :data:`MIN_ARTICULATION_RINGS` is quoted in.  A retopo is not
    uniform, so the mesh's overall median edge is the wrong ruler for a band at
    one particular joint: on the synthetic biped the arm is sampled every 15 mm
    and the trunk every 59 mm, and a floor quoted in the whole figure's rings is
    a floor for neither.

    Per **tag**, not per seam, and that is not a detail.  A seam is a handful of
    vertices where two separately-built tubes are welded together, and those
    weld edges are as long as the gap they bridge rather than as long as a ring:
    measured at the biped's armpit, ten seam vertices whose incident edges run
    59 mm, on an arm whose rings are 15 mm.  Floored off those the arm/torso
    band came out 118 mm — wider than the leg/torso band, on a figure whose arm
    is the thinner limb, which inverts the ordering this module is built on.
    The band is walked *into* the tag, so the tag's own spacing is what it has
    to cross.
    """
    lengths = {}
    for a, b in edges:
        a, b = int(a), int(b)
        shared = tags[a] & tags[b]
        if not shared:
            continue
        step = (world[a] - world[b]).length
        for tag in shared:
            lengths.setdefault(tag, []).append(step)
    out = {}
    for tag, values in lengths.items():
        values.sort()
        middle = len(values) // 2
        out[tag] = (values[middle] if len(values) % 2
                    else 0.5 * (values[middle - 1] + values[middle]))
    return out


def blend_zones(obj, tags, edges, regions, girth_fraction=BLEND_GIRTH_FRACTION,
                seam_widths=None, connected=None):
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
    spacing = _tag_edge_spacing(tags, edges, world)

    overrides = dict(seam_widths or {})
    blend = [set() for _ in range(count)]
    rows = []
    refused = []
    for (one, other), seeds in sorted(seams.items()):
        girth = min(girths.get(one, 0.0), girths.get(other, 0.0))
        if connected is not None and (one, other) not in connected:
            # Two tags touching is not two tags articulating: see
            # :func:`articulations` for the hand that touches the thigh.
            refused.append({
                "tags": [one, other],
                "seam_vertices": len(seeds),
                "girth_mm": round(girth * M_TO_MM, 1),
                "why": ("no bone of either tag hangs off the other, so this is two "
                        "pieces of flesh standing next to each other rather than a "
                        "joint, and a band here would be a licence across a gap"),
            })
            continue
        # A seam between two slabs of the same tag is not measured in girths:
        # see SUB_TAG_BLEND_FRACTION. Everything else is.
        override = overrides.get((one, other))
        measured = girth_fraction * girth if override is None else override
        # ...and whatever it is measured in, it is floored at the topology it
        # has to cross. This seam articulates -- the ones that do not were
        # refused above -- so a band here is a joint's falloff, and a falloff
        # needs somewhere to happen. See MIN_ARTICULATION_RINGS.
        #
        # **The floor is per side, because the two sides of a joint are not the
        # same mesh.** One width floored at the coarser side's rings lets a
        # trunk dictate the band on a limb -- measured, that gave the synthetic
        # biped the *same* 118 mm band at its arm/torso and leg/torso seams,
        # which is the "one distance for the whole character" MIN_BLEND_RINGS
        # exists to avoid. One width floored at the finer side's rings leaves
        # the coarse side still cliffed, which on the same figure left the
        # foot's own map punctured at the ankle (``DEF-foot.L`` at 0.20 where
        # its neighbours averaged 0.48). So each side gets a band two of **its
        # own** rings deep: the band reaches 29.6 mm into that biped's foot and
        # 49.4 mm into its shin, and neither side sets the other's.
        both = {one, other}
        side_width = {}
        for tag in both:
            side_width[tag] = max(measured, MIN_BLEND_RINGS * median_edge,
                                  MIN_ARTICULATION_RINGS
                                  * (spacing.get(tag) or median_edge))
        # The seam's one reported width is the **thinner** tag's, which is the
        # side that governed ``measured`` in the first place -- so a caller
        # comparing seams across a figure still sees a number that tracks the
        # limbs' girths rather than the trunk's topology.
        thinner = (one if girths.get(one, 0.0) <= girths.get(other, 0.0) else other)
        local = spacing.get(thinner) or median_edge
        width = side_width[thinner]
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
                # How deep the band runs is asked of the side it is running
                # into, not of the seam as a whole.
                if step > max(side_width[tag] for tag in (tags[neighbour] & both)):
                    continue
                if step < distance.get(neighbour, step + 1.0):
                    distance[neighbour] = step
                    heapq.heappush(queue, (step, neighbour))
        rows.append({
            "tags": [one, other],
            "girth_mm": round(girth * M_TO_MM, 1),
            "width_mm": round(width * M_TO_MM, 1),
            "measured_in": ("girth" if override is None else "slab length")
                           + (", floored at %.1f local rings" % MIN_ARTICULATION_RINGS
                              if width > measured + 1e-12 else ""),
            "rings": round(width / median_edge, 2) if median_edge > 0.0 else None,
            "local_edge_mm": round(local * M_TO_MM, 2),
            "local_rings": round(width / local, 2) if local > 0.0 else None,
            "unfloored_mm": round(measured * M_TO_MM, 1),
            "width_by_tag_mm": {tag: round(value * M_TO_MM, 1)
                                for tag, value in sorted(side_width.items())},
            "floor_by_tag_mm": {
                tag: round(MIN_ARTICULATION_RINGS
                           * (spacing.get(tag) or median_edge) * M_TO_MM, 1)
                for tag in sorted(both)},
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
    if refused:
        says += (" %d seam(s) were refused a band because the two tags do not "
                 "articulate: %s."
                 % (len(refused),
                    ", ".join("%s/%s (%d vertices)"
                              % (row["tags"][0], row["tags"][1],
                                 row["seam_vertices"]) for row in refused)))
    return blend, {
        "seams": rows,
        "refused_seams": refused,
        "girth_fraction": girth_fraction,
        "min_rings": MIN_BLEND_RINGS,
        "min_articulation_rings": MIN_ARTICULATION_RINGS,
        "median_edge_mm": round(median_edge * M_TO_MM, 2),
        "tag_edge_mm": {tag: round(value * M_TO_MM, 2)
                        for tag, value in sorted(spacing.items())},
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
                      factor=SMOOTH_FACTOR, reach=BLEND_REACH, warnings=None,
                      split=True):
    """Mask, blend, smooth — the whole stage, on an already-skinned mesh.

    Order matters and is the rigger's: the automatic weights are *input*, the
    tag contract cuts them down, the blend zone widens the cut back out at the
    seams only, the smoother fixes what the cut left ragged, and the result is
    limited and renormalised.  Nothing here invents an influence a bone did not
    already have except in a blend zone or through the smoother's own
    neighbourhood, so a shoulder the artist painted by hand survives its own
    limb untouched.

    ``split`` (default on) enforces the contract at **sub-tag granularity**
    where a tag is too coarse to be a contract — the ``Torso`` as three slabs of
    its own spine and each ``Leg`` as thigh / shin / foot of its own chain; see
    :class:`TagSplit`.  It is a derived view: nothing is written to the mesh,
    and a split that cannot be measured costs a warning and the merged contract
    for **that tag**, never the stage and never the other tags' splits.
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
    if isinstance(split, SplitSet):
        split_view = split
        split_report, leg_reports = split.torso_report, split.leg_reports
    elif isinstance(split, TagSplit):
        # A single tag's view, handed in on its own: wrap it so everything below
        # walks ``members`` without a second code path.
        split_view = SplitSet([split], split.report, {})
        split_report, leg_reports = split.report, {}
    else:
        split_view, split_report, leg_reports = body_split(
            obj, regions, rig, metarig, enabled=bool(split))
    if split:
        for report in [split_report] + [leg_reports[tag] for tag in sorted(leg_reports)]:
            if report.get("refused") and (split_view is None
                                          or report.get("parent") not in
                                          split_view.parents):
                warnings.append("The %s tag was not split: %s. Its bones stay in one "
                                "legal set." % (report.get("parent"),
                                                report["refused"]))
    contract = legal_bone_sets(rig, metarig, regions, split_view)
    legal = contract["legal"]
    while split_view is not None:
        # A slab no bone reaches has no contract to enforce and would leave its
        # flesh unconstrained. Better the coarse contract that worked -- but
        # only for **that tag**: taking the trunk's split back because a leg's
        # foot slab is starved would pay for a broken fix with a working one.
        starved = None
        for member in split_view.members:
            empty = sorted(name for name in member.names if not legal.get(name))
            if empty:
                starved = (member, empty)
                break
        if starved is None:
            break
        member, empty = starved
        warnings.append(
            "The %s split was taken back: %s would have no deform bone at all, and "
            "flesh with an empty legal set is flesh with no contract. That tag stays "
            "whole." % (member.parent, ", ".join(empty)))
        taken = dict(member.report)
        taken.pop("cuts", None)
        taken["refused"] = "%s would hold no deform bone" % ", ".join(empty)
        if member.parent == rigforge_autotag.SPLIT_PARENT:
            split_report = taken
        else:
            leg_reports = dict(leg_reports)
            leg_reports[member.parent] = taken
        split_view = split_view.without(member)
        if not split_view.members:
            split_view = None
        contract = legal_bone_sets(rig, metarig, regions, split_view)
        legal = contract["legal"]

    regions_used = split_regions(regions, split_view)
    tags = tag_membership(obj, split_view)
    edges = vertex_edges(obj)
    blend, blend_report = blend_zones(obj, tags, edges, regions_used, girth_fraction,
                                      sub_tag_seam_widths(split_view),
                                      articulations(contract, split_view))

    count = len(obj.data.vertices)
    girths = tag_girths(regions_used)
    columns_for = {tag: [column_of[name] for name in sorted(names)
                         if name in column_of]
                   for tag, names in legal.items()}
    hinge_of = {tag: set(names) for tag, names in contract["hinges"].items()}
    # A tag's **own** bones — the ones exempt from the reach below. "Own" is
    # ownership, not legality, and the distinction cost a run to find. A bone is
    # legal on a slab for three different reasons and only one of them makes it
    # that slab's own: it is *owned* there (its midpoint is in the slab), it is
    # the slab's **hinge** (the bone the slab hangs from), or its **span**
    # crosses a cut into the slab. The last two are borrowed influence and the
    # reach is what holds them near the joint they came from.
    #
    # Measured on the werewolf's legs, where the coarse rule breaks down. With
    # the span-crossers exempt, ``DEF-foot.R`` -- legal on ``Leg.R.shin`` only
    # because its head sits at the ankle cut -- had licence 1.0 over the **whole
    # 330 mm shin slab**, and so did ``DEF-thigh.R.001`` reaching down from the
    # knee: the two ends of the leg held the middle of it jointly, and the
    # leg-internal stray mass stalled at 5.92 with the split otherwise working.
    # Holding them to the reach is the articulation rule applied where it was
    # already meant to apply -- a band at the knee and a band at the ankle,
    # nothing in between.
    #
    # It is also what keeps the stage **converging**. Exempting a span-crosser
    # lets it hold flesh the next ``apply`` will not: on the synthetic biped a
    # second pass moved a weight by 0.056 against a suite that pins it under
    # 0.05, and ownership rather than legality settles it.
    owner_of = contract["owner"]
    owned_for = {tag: frozenset(column_of[name] for name in names
                                if name in column_of
                                and name not in hinge_of.get(tag, ())
                                and owner_of.get(name, tag) == tag)
                 for tag, names in legal.items()}
    # One reach per tag, off that tag's own measured girth: a bone is legal on a
    # vertex when the tag says so *and* the bone is near enough to be part of
    # that flesh. See BLEND_REACH for the three attempts this replaced.
    allowance = {tag: reach * girths.get(tag, 0.0) for tag in legal}
    # ...except across a **sibling cut**, where the reach is that cut's own
    # blend band -- the width :func:`blend_zones` just used, read back rather
    # than recomputed so the two can never disagree. Same argument as
    # SUB_TAG_BLEND_FRACTION, one step further along: a girth says how thick a
    # limb is, and what is bounded here is how far an influence travels *along*
    # it, away from a joint. A vertex may borrow across a seam exactly as far as
    # the band reaches, and no further.
    #
    # Measured on the werewolf's knee -- the shin slab's girth gives a 186 mm
    # reach, long enough for ``DEF-shin.R.001`` (167 mm below the knee, the
    # *far* end of the shin) to hold thigh flesh in the knee band, and that pair
    # alone was 1.48 of stray mass. The knee's own band is 84 mm, which reaches
    # ``DEF-shin.R`` -- the bone that actually starts at the knee -- and nothing
    # past it.
    #
    # A band too thin to hold its own falloff is not a bound but a cliff, and
    # bounding a reach with one is worse than not bounding it -- which is why
    # this used to skip any seam under ``MIN_ARTICULATION_RINGS`` of the
    # figure's **median** edge. That guard is gone, and deliberately:
    # :func:`blend_zones` now floors every articulating band at that same number
    # of the seam's own **local** rings, so the precondition holds everywhere by
    # construction and re-testing it against a global median only gets the
    # answer wrong. Measured -- the werewolf's median edge is 25.1 mm, so the
    # old guard demanded 71.7 mm and skipped the 67.7 mm arm/chest seam, which
    # is 2.86 local rings and perfectly solid. That skip was the whole reason
    # the fix below appeared to do nothing.
    #
    # **Every articulating seam, not only the sibling cuts.** Restricting this
    # to slabs of one tag was an accident of where it was found, and the trunk
    # is where that shows: ``Torso.chest``'s girth is 152.7 mm, so it lent its
    # bones **458 mm** in every direction, and the band only had to put a vertex
    # within 68 mm of the shoulder seam for the whole chest to become legal on
    # it. Measured on the werewolf: 31 ``Arm.L``-tagged deltoid vertices carried
    # ``DEF-spine.005`` from 180-230 mm away while ``DEF-upper_arm.L.001`` --
    # the arm's own bone, exempt -- carried them too, and that pair alone was
    # **2.86 of stray mass** at a bone gap of 344 mm. The seam between them is
    # 68 mm wide. Nothing about a trunk being thick says its bones should reach
    # half a metre down someone's arm; what a seam lends, the seam bounds.
    median_edge = (blend_report.get("median_edge_mm") or 0.0) / M_TO_MM
    # Keyed ``(borrower, lender)`` and taken from the band's **borrower-side**
    # width, because that is the side the borrowed weight lands on: the same
    # seam reaches 68 mm into the werewolf's arm and 82 mm into its chest, and
    # which of those bounds a bone depends on whose flesh it is being lent to.
    seam_widths = {}
    for row in blend_report.get("seams", ()):
        one, other = row["tags"]
        by_tag = row.get("width_by_tag_mm") or {}
        for borrower, lender in ((one, other), (other, one)):
            seam_widths[(borrower, lender)] = (
                by_tag.get(borrower, row["width_mm"]) / M_TO_MM)

    hinge_columns = {tag: frozenset(column_of[name] for name in names
                                    if name in column_of)
                     for tag, names in hinge_of.items()}
    owner_column = {column_of[name]: tag for name, tag in owner_of.items()
                    if name in column_of}

    def across(tag, lender, base):
        """The reach for a bone of ``lender`` on ``tag``'s flesh."""
        if lender is None or lender == tag:
            return base
        width = seam_widths.get((tag, lender))
        return base if width is None else width

    distances = _distance_matrix(obj, rig, bone_names)
    def licence(distance, limit, band=REACH_BAND):
        """1 inside the reach, ramping to 0 over its last ``band`` fraction.

        ``band`` is :data:`REACH_BAND` for a *reach* — a rule about how far a
        bone's own flesh extends, where the falloff is a rounding of the edge.
        It is **1.0 for a seam band**, where the falloff is the whole point: a
        band exists to hand a vertex from one tag to the next, so the influence
        it lends should fade across all of it rather than sit at full strength
        for two thirds and drop off a step. Measured on the werewolf, where
        cliffing at the band's edge left a rim of punctures on every bone whose
        bound had just been tightened.
        """
        if limit <= 0.0 or distance >= limit:
            return 0.0
        width = limit * band
        if width <= 0.0:
            return 1.0
        return min(1.0, (limit - distance) / width)

    # One ring of *this vertex's own* topology, for the taper below. A figure
    # whose trunk is sampled four times more coarsely than its limbs -- the
    # synthetic test biped, 59 mm against 15 mm -- has no single ring size, and
    # a taper quoted in the figure's median is too thin on the coarse half.
    _spacing = {tag: value / M_TO_MM
                for tag, value in (blend_report.get("tag_edge_mm") or {}).items()}
    ring = _np.array([max([_spacing.get(tag, median_edge) for tag in tags[index]]
                          or [median_edge]) for index in range(count)], dtype="f8")
    ramp = _np.zeros((count, len(bone_names)), dtype="f8")
    radius = _np.zeros(count, dtype="f8")
    # Where the sibling-band rule above held a bone to less than its tag's own
    # reach, and how far. The **smoothing mask has to be told**, or the stage
    # stops converging: the mask is what bounds the smoother, it was bounded by
    # the loose ``radius`` alone, and a weight the ramp refused but the mask
    # allowed is a weight the smoother puts back and the *next* ``apply`` cuts
    # again. Measured on the synthetic biped -- a second pass removed 23.5 of
    # the 30.1 the first one did and moved a weight by 0.100, against a suite
    # that pins the second pass under 0.05. A column any tag grants at the full
    # reach is not capped at all, so every pre-existing taper is untouched.
    capped = _np.zeros((count, len(bone_names)), dtype="f8")
    # Marked whenever a seam held a bone to less than its tag's own reach --
    # separately from the width, because a seam can hold a bone to zero and zero
    # is still a bound: without the flag that read as "never capped" and the mask
    # fell back to the loose radius.
    narrowed = _np.zeros((count, len(bone_names)), dtype=bool)
    # Where a bone is the vertex's own tag's own bone -- not borrowed across any
    # seam, at any licence. The only place a hole-fill may lift: see fill_holes.
    owned_here = _np.zeros((count, len(bone_names)), dtype=bool)
    uncapped = _np.zeros((count, len(bone_names)), dtype=bool)
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
            hinges_here = hinge_columns.get(tag, ())
            for column in columns_for.get(tag, ()):
                # A tag's own chain runs the length of its own limb and is not
                # subject to the reach: a knee vertex is a long way from the top
                # of the thigh and the thigh still drives it. Measured -- with
                # the reach applied to a tag's own bones the werewolf's knees
                # gained 474 new self-intersections at the extremes, because
                # scaling the thigh down at the knee sharpens the bend into a
                # fold. The reach is about *borrowed* influence -- and which
                # bones those are is the ``owned_for`` question above.
                if column in own_columns:
                    value = 1.0
                    bound = limit
                    owned_here[index, column] = True
                elif column in hinges_here:
                    # **The hinge keeps the girth reach.** A tag hangs off this
                    # bone and rotates *about* it, so its influence reaching
                    # into the tag is the joint working, not a bleed -- which is
                    # what BLEND_REACH is for and why it is three girths.
                    # Measured: bounding hinges to the seam band as well takes
                    # the werewolf's stray to 0.0000 and is not worth having --
                    # it strands 481 vertices onto a refill and puts the skin at
                    # 619 punctures against a single-pass baseline of 474.
                    bound = limit
                    value = licence(row[column], bound)
                else:
                    # A **span-crosser**: legal on this tag only because the
                    # bone pokes over the cut. It has no claim on the slab's
                    # interior, so the seam that lends it bounds it -- the same
                    # rule the blend band below obeys. Measured on the
                    # werewolf's thigh, whose girth lends 245 mm: ``DEF-shin.R``
                    # reached up from across the knee to meet the hips coming
                    # down, and that pair was 1.26 of stray mass at a bone gap
                    # of 392 mm. The knee seam is 86 mm wide.
                    bound = across(tag, owner_column.get(column), limit)
                    value = licence(row[column], bound)
                if bound < limit:
                    capped[index, column] = max(capped[index, column], bound)
                    narrowed[index, column] = True
                else:
                    uncapped[index, column] = True
                if value > ramp[index, column]:
                    ramp[index, column] = value
        for tag in blend[index]:
            if tag in own:
                continue
            limit = allowance.get(tag, 0.0)
            lent = limit
            for mine in own:
                lent = min(lent, across(mine, tag, limit))
            for column in columns_for.get(tag, ()):
                # Same rule as above: what a neighbouring **slab** lends across
                # a cut is held to that cut's band. A bone this flesh's own slab
                # hangs from needs no exception here -- it is already in its own
                # tag's legal set and took the girth reach in the loop above.
                value = licence(row[column], lent)
                if lent < limit:
                    capped[index, column] = max(capped[index, column], lent)
                    narrowed[index, column] = True
                else:
                    uncapped[index, column] = True
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
    # One cut of the edge graph, used by everything that walks it: the taper
    # below and the smoother further down. See articulated_edges.
    linked = articulated_edges(edges, tags, articulations(contract, split_view))
    fine = taper_edges(obj, linked, tags, regions_used, girth_fraction)
    reachable = _np.broadcast_to(radius[:, None], capped.shape).copy()
    # Only the capped slots move, so a run with no split -- or one where no seam
    # was tight enough to bound a reach -- gets exactly the mask it always got.
    # A capped slot is still never narrower than the ramp it is a taper on:
    # without that floor a vertex rescued by the out-of-reach fallback above,
    # which grants its single nearest legal bone *past* every reach on purpose,
    # had that one weight masked straight back off and came out of the stage
    # with no weight at all.
    tightened = narrowed & ~uncapped
    # **Plus one ring**, and that ring is the difference between a bound and a
    # cliff. The ramp already fades a borrowed bone out over the last
    # REACH_BAND of its bound, but the mask cut the weight off at the bound
    # exactly, so the outermost ramped weight sat next to a hard zero and the
    # hole detector -- rightly -- called it a puncture: on the werewolf that was
    # about a hundred extra holes, concentrated on the spine bones whose bounds
    # had just been tightened. One ring is what SMOOTH_DILATION gives every
    # other boundary in this stage for exactly the same reason, and it is small
    # enough that the next ``apply`` still finds the weights where it left them.
    reachable[tightened] = _np.maximum(
        capped + ring[:, None], _np.where(hard, distances, 0.0))[tightened]
    mask = _dilate(hard, fine, SMOOTH_DILATION) & (distances <= reachable)
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

    weights = smooth_weights(weights, mask, linked, passes, factor)
    _normalize_rows(weights)
    # The dents the smoother's own repair does not reach, taken out directly
    # against the gate's definition of one. It runs on the graph the contract
    # joined -- a "neighbour" across a seam the articulation rule cut is not a
    # neighbour -- and only where the licence is whole, so it can lift a weight
    # back to the flesh around it without ever handing out a bone the bound
    # refused. See fill_holes for why the median is the level it lifts to.
    filled = fill_holes(weights, ramp >= 1.0 - 1e-9, linked)
    if filled:
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
    for tag in sorted(regions_used):
        members = [index for index in range(count) if tag in tags[index]]
        if not members:
            continue
        rows = _np.array(members, dtype="i8")
        entry = {
            "tag": tag,
            "vertices": len(members),
            "legal_bones": sorted(legal.get(tag, ())),
            "hinges": contract["hinges"].get(tag, []),
            "weight_removed": round(float(removed[rows].sum()), 4),
            "blend_vertices": int(sum(1 for index in members if blend[index])),
        }
        if split_view is not None and split_view.parent_of(tag):
            entry["parent"] = split_view.parent_of(tag)
        per_tag.append(entry)

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
        "filled_holes": filled,
        "stranded_vertices": stranded,
        "trimmed_vertices": trimmed,
        "entries_written": written,
        "worst_bones": [{"bone": name, "weight_removed": round(mass, 4)}
                        for name, mass in offenders[:12]],
        "tags": per_tag,
        "blend": blend_report,
        "sub_tags": split_report,
        "leg_sub_tags": {tag: leg_reports[tag] for tag in sorted(leg_reports)},
        "contract": {
            "legal": {tag: sorted(names) for tag, names in sorted(legal.items())},
            # The same contract as every consumer that expects one tag sees it,
            # beside the one this stage enforced, so the two can be compared.
            "legal_merged": merge_legal(legal, split_view),
            "spans": contract.get("spans", {}),
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

def fill_holes(weights, allowed, edges, floor=REGION_FLOOR, ratio=HOLE_RATIO,
               min_drop=HOLE_MIN_DROP, min_neighbours=HOLE_MIN_NEIGHBOURS):
    """Lift each punctured weight to its own neighbourhood's median.  In place.

    The repair for the defect :func:`weight_continuity` measures, written from
    **that function's own predicate** rather than beside it: a weight is a hole
    when at least ``min_neighbours`` of its neighbours carry the bone above
    ``floor``, it sits under ``ratio`` of their median, and the gap is at least
    ``min_drop``.  Gate and repair are two views of one definition and share one
    set of constants, so they cannot drift into disagreeing about what a hole is.

    **Median, and that is what makes it idempotent.**  A repaired vertex *is*
    the neighbourhood median, so the predicate that selected it is false on the
    next pass and a second run moves nothing — unlike a partial step towards a
    mean, which keeps moving and never arrives.  It is also exactly what a
    weight-painter does to a spot dent: not a blur of the neighbourhood, but
    that one vertex brought up to the level around it.

    ``allowed`` is the ``(V, B)`` boolean of what the contract **fully**
    licenses.  A hole-fill must never hand a vertex a bone the seam bound denied
    it, so it repairs only where the licence is whole: where a bone is borrowed
    at a partial licence the low weight is the taper doing its job, which is the
    same reason :data:`HOLE_MIN_DROP` exists.

    Returns the number of weights lifted.
    """
    _require_numpy()
    if not len(edges) or not weights.size:
        return 0
    a, b = edges[:, 0], edges[:, 1]
    inside = weights >= floor
    neighbours = _np.zeros(weights.shape, dtype="i8")
    _np.add.at(neighbours, a, inside[b].astype("i8"))
    _np.add.at(neighbours, b, inside[a].astype("i8"))
    # The same exact prefilter weight_continuity uses: a hole sits under
    # ``ratio`` times a median that cannot exceed 1.0, so anything at or above
    # ``ratio`` cannot be one and the per-vertex median runs on a few rows.
    # ``inside`` on the vertex itself as well as on its neighbours, and that is
    # the one place this departs from the gate's predicate -- deliberately, and
    # it is the difference between a repair and an expansion. The gate counts a
    # vertex at zero surrounded by carriers as a hole, which it is; but lifting
    # it does not mend a dent, it hands the bone a vertex it did not reach, and
    # a bone that reaches further shares flesh with whatever else is there.
    # Measured on the werewolf: filling from zero put ``DEF-hand.R`` on one
    # vertex that already carried ``DEF-upper_arm.R`` 353 mm away and took the
    # stray mass from 0.0000 to 0.2956 -- two vertices, and still a bone holding
    # flesh it has no business holding. A vertex already inside the region is a
    # vertex the bone demonstrably reaches, and raising it to the level around
    # it cannot extend anything.
    candidate = ((neighbours >= int(min_neighbours))
                 & (weights >= floor) & (weights < ratio) & allowed)
    adjacency = None
    filled = 0
    for column in range(weights.shape[1]):
        suspects = _np.nonzero(candidate[:, column])[0]
        if not suspects.size:
            continue
        if adjacency is None:
            adjacency = _adjacency(edges, weights.shape[0])
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
            if mine < ratio * median and (median - mine) >= min_drop:
                weights[index, column] = median
                filled += 1
    return filled


def weight_continuity(rig, mesh, floor=REGION_FLOOR, max_bones=400,
                      ratio=HOLE_RATIO, min_drop=HOLE_MIN_DROP,
                      min_neighbours=HOLE_MIN_NEIGHBOURS, edges=None):
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

    ``edges`` defaults to the mesh's own graph, which is the number ``rig_check``
    reports and the one to compare across runs.  Passing
    :func:`articulated_edges` instead answers the narrower question *"is this
    bone's map patchy across flesh that is actually attached to it"* — which is
    the honest one wherever the contract has deliberately cut a cliff at a
    non-joint, because a hand next to a thigh is supposed to stop dead there and
    the whole-graph count reads that stop as a puncture.
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
    if edges is None:
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
# the gate the owner's eyes were: does the arm swing move the lower body?
# ---------------------------------------------------------------------------

def _evaluated_points(mesh):
    """World-space vertex positions **after** the armature modifier."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = mesh.evaluated_get(depsgraph)
    data = evaluated.to_mesh()
    matrix = mesh.matrix_world
    try:
        return [matrix @ vertex.co.copy() for vertex in data.vertices]
    finally:
        evaluated.to_mesh_clear()


def _chain_root(rig, owner, tag, metarig, known):
    """The bone of ``tag`` that hangs off something else — that limb's own root."""
    members = sorted(name for name, held in owner.items() if held == tag)
    for name in members:
        parent = deform_parent(rig, name, metarig, known)
        if parent is None or owner.get(parent) != tag:
            return name
    return members[0] if members else None


def arm_swing_isolation(rig, mesh, regions=None, split=None, metarig=None,
                        swing_deg=ISOLATION_SWING_DEG, worst=5,
                        include_hinges=True):
    """Swing the arms and measure how far the **pelvis and the thighs** move.

    The gate this adds, and why the existing ones could not see the defect
    -------------------------------------------------------------------------
    ``influence_overlap`` reported 0.000 stray mass and ``continuity`` reported
    a clean bind while the owner watched the walk and saw *the arm swing tug the
    thighs*.  Neither number was wrong.  Overlap only names bones in **different
    tags** sharing flesh; the coupling ran entirely inside one ``Torso`` tag —
    a shoulder bone legal on pelvis flesh, and pelvis flesh sharing a blend band
    with the thighs.  Nothing measured the thing the owner actually saw, which
    is not a weight at all but a **displacement**.

    So this measures the displacement.  The arm chains are swung
    ``swing_deg`` about the character's own lateral axis — mirrored left and
    right, which is a walk — and the mean and maximum movement of the
    pelvis-tagged and thigh-weighted vertices is reported **in millimetres**.
    An isolated lower body does not move: near zero passes.

    What is posed, and what is put back
    -----------------------------------
    The question is about the *weights*, so the deform bones are driven directly
    and their rig-side constraints are muted for the duration — otherwise
    Rigify's ``COPY_TRANSFORMS`` would overwrite every rotation this applies and
    the measurement would be of nothing.  The baseline is captured in that same
    muted state, so it is the same body either way.  Every pose bone's
    ``matrix_basis`` and every constraint's ``mute`` is snapshotted and restored,
    and the restoration is **verified** rather than asserted: ``restored_max_mm``
    is how far any vertex ends up from where it started, and it is 0.0 or the
    number is in the report.

    What swings is the arm tag's **whole legal set** — its own chain *and* its
    hinge, the shoulder it hangs from (``include_hinges``, on by default).  That
    is deliberate, and it is the difference between a gate and a formality: a
    contract that leaves a shoulder bone legal on pelvis flesh is exactly the
    defect this lane exists to fix, and with the shoulder held still the flesh
    it strands down there never moves and the number never notices.  A walk
    carries a clavicle with the arm anyway.  ``arms[].bones`` lists what swung,
    so the number can always be read back to the bones that caused it.
    """
    _require_numpy()
    names = sorted(rigforge_rig.deform_bones(rig))
    unmeasured = {"verdict": "unmeasured", "swing_deg": float(swing_deg),
                  "groups": [], "mean_mm": None, "max_mm": None}
    if not names:
        return dict(unmeasured, says="%r has no deforming bones to swing." % rig.name)
    if not any(modifier.type == "ARMATURE" and modifier.object is rig
               for modifier in mesh.modifiers):
        return dict(unmeasured,
                    says="%r is not driven by %r (no armature modifier), so nothing "
                         "it does to the weights can be measured as movement."
                         % (mesh.name, rig.name))
    if regions is None:
        regions, _empty = rigforge_rig.measure_tags(mesh)
    if split is None:
        split, _torso, _legs = body_split(mesh, regions, rig, metarig)
    if isinstance(split, TagSplit):
        split = SplitSet([split], split.report, {})

    known = source_bone_names(rig, metarig)
    contract = legal_bone_sets(rig, metarig, regions, split)
    owner = contract["owner"]
    if split is not None:
        # **Merged, and this qualifier is load-bearing.** The contract re-owns a
        # split tag's bones to its slabs, so with the legs split there is no
        # bone whose owner is ``Leg.R`` any more and the thigh group below came
        # out empty -- the gate would have gone on reporting 0.00 mm because it
        # had stopped measuring anything. Chain resolution asks the *tag*'s
        # question ("which bones are this limb"), so it reads the merged view.
        owner = {name: split.merged(tag) for name, tag in owner.items()}
    torso_split_view = (split.member_for_parent(rigforge_autotag.SPLIT_PARENT)
                        if split is not None else None)
    arm_tags = sorted(tag for tag in regions if tag.startswith("Arm"))
    leg_tags = sorted(tag for tag in regions if tag.startswith("Leg"))
    if not arm_tags:
        return dict(unmeasured,
                    says="%r has no Arm tag, so there is no arm swing to isolate."
                         % mesh.name)

    # --- what moves ------------------------------------------------------
    # The character's own up, and from it its own lateral: a swing about the
    # lateral axis is the forward/back one a walk uses. Both are measured off
    # the body rather than taken from the world, so a character modelled on a
    # different convention swings its arms rather than its shoulders.
    trunk = regions.get(rigforge_autotag.SPLIT_PARENT)
    if torso_split_view is not None:
        up = Vector(torso_split_view.axis.direction)
    elif trunk is not None:
        up = Vector(trunk.axis).normalized()
    else:
        up = Vector((0.0, 0.0, 1.0))
    if up.z < 0.0:
        up = -up
    swings = []
    for tag in arm_tags:
        root = _chain_root(rig, owner, tag, metarig, known)
        if root is None:
            continue
        chain = set(name for name, held in owner.items() if held == tag)
        if include_hinges:
            chain |= set(contract["hinges"].get(tag, ()))
        chain = sorted(chain & set(names))
        bone = rig.data.bones[root]
        pivot = rig.matrix_world @ bone.head_local
        anchor = Vector(trunk.centre) if trunk is not None \
            else Vector(regions[tag].centre)
        offset = pivot - anchor
        lateral = offset - up * offset.dot(up)
        if lateral.length < 1e-6:
            continue
        swings.append({"tag": tag, "root": root, "bones": chain,
                       "pivot": pivot, "axis": lateral.normalized()})
    if not swings:
        return dict(unmeasured,
                    says="No arm chain could be resolved on %r, so there is no swing "
                         "to drive." % rig.name)

    # --- what must not move ----------------------------------------------
    groups = {}
    if torso_split_view is not None:
        pelvis_name = torso_split_view.names[0]
        groups[pelvis_name] = sorted(
            index for index, name in torso_split_view.membership.items()
            if name == pelvis_name)
        pelvis_how = "the %s slab of the split" % pelvis_name
    else:
        parent = rigforge_autotag.SPLIT_PARENT
        region = regions.get(parent)
        members = []
        if region is not None:
            axis = Vector(region.axis).normalized()
            if axis.z < 0.0:
                axis = -axis
            matrix = mesh.matrix_world
            group = mesh.vertex_groups.get(rigforge.tag_group_name(parent))
            heights = []
            for vertex in mesh.data.vertices:
                if group is None:
                    break
                for element in vertex.groups:
                    if element.group == group.index and element.weight > 0.0:
                        heights.append(((matrix @ vertex.co).dot(axis), vertex.index))
                        break
            if heights:
                heights.sort()
                members = sorted(index for _height, index
                                 in heights[:max(1, len(heights) // 3)])
        groups["%s (lower third)" % rigforge_autotag.SPLIT_PARENT] = members
        pelvis_how = ("the lowest third of the %s tag - there is no split to read a "
                      "pelvis off" % rigforge_autotag.SPLIT_PARENT)

    thigh_bones = []
    for tag in leg_tags:
        root = _chain_root(rig, owner, tag, metarig, known)
        if root is None:
            continue
        base = metarig_base(root, known)
        thigh_bones.extend(def_bones_of(rig, known, base) if base else [root])
    thigh_bones = sorted(set(thigh_bones) & set(names))
    thigh = []
    if thigh_bones:
        # Thigh-**tagged**, not merely thigh-weighted, and that qualifier was
        # measured: on the werewolf the hanging hand sits beside the thigh, so
        # twelve ``Arm.R`` vertices carry 0.17 of ``DEF-thigh.R.001`` across the
        # blend band. Counting them as thighs made the swing's worst vertex a
        # *hand* vertex moving 648 mm, which is a hand doing what a hand does,
        # and would have buried the number this gate exists to report.
        tagged = tag_membership(mesh)
        weights = read_weights(mesh, thigh_bones)
        thigh = sorted(int(index) for index in
                       _np.nonzero((weights >= REGION_FLOOR).any(axis=1))[0]
                       if any(tag.startswith("Leg") for tag in tagged[int(index)]))
    groups["thighs"] = thigh

    # --- pose, measure, put back -----------------------------------------
    # The state to come back to is the one the caller handed over, **before**
    # the constraints were muted -- comparing against the muted baseline would
    # only ever prove that the rotations were undone, which is the easy half.
    refresh_view_layer()
    original = _evaluated_points(mesh)
    pose_snapshot = [(bone, bone.matrix_basis.copy()) for bone in rig.pose.bones]
    deform_names = set(names)
    muted = []
    for bone in rig.pose.bones:
        if bone.name not in deform_names:
            continue
        for constraint in bone.constraints:
            if not constraint.mute:
                constraint.mute = True
                muted.append(constraint)

    def restore():
        for constraint in muted:
            constraint.mute = False
        for bone, basis in pose_snapshot:
            bone.matrix_basis = basis.copy()
        refresh_view_layer()

    try:
        refresh_view_layer()
        before = _evaluated_points(mesh)
        inverse = rig.matrix_world.inverted_safe()
        angle = math.radians(float(swing_deg))
        wanted = {}
        for swing in swings:
            pivot = inverse @ swing["pivot"]
            axis = (inverse.to_3x3() @ swing["axis"]).normalized()
            rotation = (Matrix.Translation(pivot)
                        @ Matrix.Rotation(angle, 4, axis)
                        @ Matrix.Translation(-pivot))
            for name in swing["bones"]:
                bone = rig.pose.bones.get(name)
                if bone is not None:
                    wanted[name] = rotation @ bone.matrix.copy()
        for name, matrix in wanted.items():
            rig.pose.bones[name].matrix = matrix
        refresh_view_layer()
        after = _evaluated_points(mesh)
    finally:
        restore()
    settled = _evaluated_points(mesh)

    moved = _np.linalg.norm(
        _np.array([list(point) for point in after], dtype="f8")
        - _np.array([list(point) for point in before], dtype="f8"), axis=1)
    restored_max = float(_np.linalg.norm(
        _np.array([list(point) for point in settled], dtype="f8")
        - _np.array([list(point) for point in original], dtype="f8"),
        axis=1).max()) if original else 0.0

    arm_columns = sorted({name for swing in swings for name in swing["bones"]}
                         & set(names))
    arm_weights = read_weights(mesh, arm_columns) if arm_columns else None
    rows = []
    overall_max = 0.0
    overall_mean = 0.0
    counted = 0
    for label, members in sorted(groups.items()):
        if not members:
            rows.append({"group": label, "vertices": 0, "mean_mm": None,
                         "max_mm": None, "verdict": "unmeasured"})
            continue
        selection = _np.array(members, dtype="i8")
        values = moved[selection]
        mean_mm = float(values.mean()) * M_TO_MM
        max_mm = float(values.max()) * M_TO_MM
        overall_max = max(overall_max, max_mm)
        overall_mean += float(values.sum()) * M_TO_MM
        counted += len(members)
        row = {"group": label, "vertices": len(members),
               "mean_mm": round(mean_mm, 4), "max_mm": round(max_mm, 4),
               "verdict": _band(max_mm, "max_mm", ISOLATION_THRESHOLDS)}
        order = _np.argsort(-values)[:max(0, int(worst))]
        row["worst"] = []
        for position in order:
            index = int(selection[int(position)])
            entry = {"vertex": index,
                     "mm": round(float(moved[index]) * M_TO_MM, 4)}
            if arm_weights is not None:
                entry["arm_weights"] = {
                    arm_columns[column]: round(float(arm_weights[index, column]), 4)
                    for column in range(len(arm_columns))
                    if arm_weights[index, column] > WEIGHT_EPSILON}
            row["worst"].append(entry)
        row["worst"] = [entry for entry in row["worst"] if entry["mm"] > 1e-6][:worst]
        rows.append(row)
    overall_mean = overall_mean / counted if counted else 0.0
    verdict = _band(overall_max, "max_mm", ISOLATION_THRESHOLDS) if counted \
        else "unmeasured"
    return {
        "verdict": verdict,
        "swing_deg": float(swing_deg),
        "include_hinges": bool(include_hinges),
        "arms": [{"tag": swing["tag"], "root": swing["root"],
                  "bones": swing["bones"],
                  "pivot_mm": [round(value * M_TO_MM, 1) for value in swing["pivot"]],
                  "axis": [round(value, 4) for value in swing["axis"]]}
                 for swing in swings],
        "pelvis_from": pelvis_how,
        "thigh_bones": thigh_bones,
        "groups": rows,
        "mean_mm": round(overall_mean, 4),
        "max_mm": round(overall_max, 4),
        "restored_max_mm": round(restored_max * M_TO_MM, 6),
        "thresholds": ISOLATION_THRESHOLDS,
        "threshold_tier": ("heuristic (proxy tier): the bands are visible-drag "
                           "guesses, not artist accept/reject data. The "
                           "millimetres are the measurement and outlive them."),
        "says": ("Swinging %s by %.0f degrees moves the lower body by %.2f mm on "
                 "average and %.2f mm at worst (%s). The pose was put back to "
                 "within %.4f mm."
                 % (" and ".join(swing["tag"] for swing in swings), swing_deg,
                    overall_mean, overall_max,
                    "; ".join("%s %.2f mm max over %d vertices"
                              % (row["group"], row["max_mm"] or 0.0, row["vertices"])
                              for row in rows if row["vertices"]),
                    restored_max * M_TO_MM)),
    }


# ---------------------------------------------------------------------------
# command surface
# ---------------------------------------------------------------------------

@command("rigforge_skin")
def cmd_rigforge_skin(params):
    """Constrain an existing skin to its tags, or just report the contract.

    ``rigforge_skin {"object"?, "rig"?, "action"?:
    "apply"|"report"|"continuity"|"isolation", "max_influences"?, "blend"?,
    "smooth_passes"?, "smooth_factor"?, "split"?: bool, "swing_deg"?}``

    ``apply`` (the default) runs mask -> blend -> smooth over the weights that
    are already on the mesh and reports what it removed and where it blended.
    ``report`` changes nothing and answers *what would be legal where*.
    ``continuity`` changes nothing and answers *how patchy is this bind*.
    ``isolation`` changes nothing and answers *does an arm swing move the
    pelvis and the thighs*, in millimetres.

    ``split: false`` enforces the contract at whole-tag granularity, which is
    what it did before :class:`TagSplit` existed — useful for measuring the
    difference rather than taking it on trust.
    """
    obj = resolve_object(params, mesh_only=True)
    rig = rigforge_rig._rig_for_mesh(obj, params)
    action = str(params.get("action") or "apply").strip().lower()
    if action not in ("apply", "report", "continuity", "isolation"):
        raise ForgeError("action must be 'apply', 'report', 'continuity' or "
                         "'isolation'; got %r." % (params.get("action"),))
    max_influences = get_int(params, "max_influences", 4, minimum=1, maximum=12)
    girth_fraction = get_float(params, "blend", BLEND_GIRTH_FRACTION,
                               minimum=0.0, maximum=4.0)
    passes = get_int(params, "smooth_passes", SMOOTH_PASSES, minimum=0, maximum=64)
    factor = get_float(params, "smooth_factor", SMOOTH_FACTOR, minimum=0.0, maximum=1.0)
    reach = get_float(params, "reach", BLEND_REACH, minimum=0.0, maximum=20.0)
    use_split = get_bool(params, "split", True)
    swing_deg = get_float(params, "swing_deg", ISOLATION_SWING_DEG,
                          minimum=1.0, maximum=170.0)
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
    if not regions and action == "isolation":
        raise ForgeError(
            "The isolation swing needs tags: %r has no tagged geometry, so there is "
            "no pelvis and no thigh to measure. Run rigforge_autotag first." % obj.name)
    if not regions:
        raise ForgeError(
            "Tag-constrained skinning needs tags: %r has no tagged geometry. Run "
            "rigforge_autotag first." % obj.name)

    split_view, split_report, leg_reports = body_split(obj, regions, rig, metarig,
                                                       enabled=use_split)
    leg_sub_tags = {tag: leg_reports[tag] for tag in sorted(leg_reports)}

    if action == "isolation":
        with object_mode():
            isolation = arm_swing_isolation(rig, obj, regions, split_view, metarig,
                                            swing_deg=swing_deg)
        return {"object": obj.name, "rig": rig.name, "action": action, "changed": 0,
                "isolation": isolation, "sub_tags": split_report,
                "leg_sub_tags": leg_sub_tags,
                "warnings": warnings, "says": isolation["says"]}

    if action == "report":
        contract = legal_bone_sets(rig, metarig, regions, split_view)
        tags = tag_membership(obj, split_view)
        edges = vertex_edges(obj)
        _blend, blend_report = blend_zones(obj, tags, edges,
                                           split_regions(regions, split_view),
                                           girth_fraction,
                                           sub_tag_seam_widths(split_view),
                                           articulations(contract, split_view))
        return {
            "object": obj.name, "rig": rig.name, "action": action, "changed": 0,
            "contract": {
                "legal": {tag: sorted(names)
                          for tag, names in sorted(contract["legal"].items())},
                "legal_merged": merge_legal(contract["legal"], split_view),
                "spans": contract.get("spans", {}),
                "hinges": contract["hinges"],
                "source_counts": _count_sources(contract["source"]),
                "bones_by_source": _bones_by_source(contract["source"]),
            },
            "blend": blend_report,
            "sub_tags": split_report,
            "leg_sub_tags": leg_sub_tags,
            "warnings": warnings,
            "says": ("%d tag(s)%s, %d deform bone(s). %s"
                     % (len(regions),
                        (" read as %d with the %s split"
                         % (len(regions) + len(split_view.names)
                            - len(split_view.parents),
                            ", ".join(sorted(split_view.parents))))
                        if split_view is not None else "",
                        len(rigforge_rig.deform_bones(rig)),
                        blend_report["says"])),
        }

    with object_mode():
        result = constrain_weights(obj, rig, metarig, regions,
                                   max_influences=max_influences,
                                   girth_fraction=girth_fraction,
                                   passes=passes, factor=factor, reach=reach,
                                   warnings=warnings,
                                   split=(split_view if split_view is not None
                                          else use_split))
    result["action"] = action
    result["changed"] = result["entries_written"]
    result["continuity"] = weight_continuity(rig, obj)
    # And the same gate over the graph the contract actually joined, because a
    # cliff the articulation gate cut on purpose is not a puncture: see
    # weight_continuity's ``edges`` and articulated_edges.
    contract = legal_bone_sets(rig, metarig, regions, split_view)
    linked = articulated_edges(vertex_edges(obj), tag_membership(obj, split_view),
                               articulations(contract, split_view))
    result["continuity_articulated"] = weight_continuity(rig, obj, edges=linked)
    try:
        # A measurement that poses the rig must never cost weights that are
        # already written: it reports itself unmeasured instead.
        result["isolation"] = arm_swing_isolation(rig, obj, regions, split_view,
                                                  metarig, swing_deg=swing_deg)
    except Exception as exc:  # noqa: BLE001
        warnings.append("The isolation swing could not be measured (%s: %s); the "
                        "weights above were still written."
                        % (type(exc).__name__, exc))
        result["isolation"] = {"verdict": "unmeasured", "groups": [],
                               "says": "%s: %s" % (type(exc).__name__, exc)}
    result["report"] = rigforge_rig.weight_report(obj, rig)
    result["warnings"] = warnings
    return result
