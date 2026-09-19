"""Tags that follow each limb's own axis, built from a joint detector's skeleton.

The failure this exists to fix
------------------------------
The landmark rigger (:mod:`~forge.tools.rigforge_landmarks`) is only ever as
good as the **tags** it is handed.  It measures a limb by slicing it
perpendicular to the limb's own centreline, so a tag that is not shaped like a
limb has no centreline to slice, and the fitter — correctly — refuses:

    The tag 'Arm.L' is not shaped like a limb: its own principal axis is not the
    direction an arm runs in …

That is the live, measured failure on the werewolf character.  Its ``Arm.L`` tag
was an **axis-aligned box band**: everything outboard of a fixed ``x`` above a
fixed ``z``.  On a figure whose arms hang down at its sides, that band is a
wedge of shoulder, deltoid and rib, 220 mm wide and 636 mm tall, whose principal
axis points down the *body* rather than down the arm.  The fitter fell back to
the old fraction-of-the-blob fit, the arm bones splayed off the centreline, and
``rig_check`` reported it as 3 centering failures and an overlap matrix with
strays like *"96 vertices are moved by both DEF-shin.R and DEF-spine, two bones
360 mm apart"*.

A box band cannot describe a limb, because a limb is a **tube with its own
direction** and a box has only the world's three.  What does know a limb's
direction is a *skeleton*, and there is a detector for those.

The method
----------
1. **The skeleton** comes from UniRig, run out of process by
   ``rigbridge/detector_runner.py`` — an unnamed tree of joints in the mesh's own
   frame.  Nothing is matched by name (UniRig emits ``bone_0``, ``bone_1``, …);
   everything below is geometry.
2. **The tree is cut into segments** (:func:`segments_of`): maximal runs of
   single-child joints between the root, the branch points and the leaves.  On a
   biped that is exactly one segment per anatomical part — a spine, a neck/head,
   four limbs, and a twig per finger and toe.
3. **Segments are named from geometry** (:func:`classify_segments`), never from
   the detector: the segment that leaves the root going *up* near the midplane is
   the spine, the one continuing above it is the neck and head, the longest sided
   segment ending low is that side's leg, the longest remaining sided segment
   starting high is that side's arm.  Sides come from the mesh's measured
   midplane and its measured facing, not from a name.
4. **A limb axis is that segment's polyline**, extended distally through its
   longest descendant twig so the hand rides the arm's axis and the toes ride
   the leg's, and extended past the last joint by however far its own flesh
   overshoots it (the top of a skull sits well above the last neck joint).
5. **Vertices are tagged by distance to an axis** (:func:`assign_vertices`) —
   this is the whole point.  Every vertex joins the axis it is nearest to, which
   already partitions the mesh along the body rather than along the world's
   axes.  Then each *limb* axis measures its own **local girth** — the median
   radial distance of its own vertices at each station, smoothed, re-measured
   after every reassignment — and a vertex further out than ``radius_factor``
   times the girth there is not on that limb.  It is the rib the arm is hanging
   beside, and it is offered to the axis that *does* have room for it.  The
   torso is the residual around the spine and the head the residual around the
   neck; neither can reject, so nothing is ever dropped.

Credibility, per limb
---------------------
UniRig scores F1 ≈ 0.105 on out-of-domain skeletons and misses extremities
outright (``docs/automation-thesis.md``), so a detection is a *proposal*, not an
authority.  Each limb is checked against the mesh it claims to describe
(:func:`limb_plausible`): long enough relative to the figure, enough vertices,
thinner than it is long, and — the check that actually bites — the resulting
tag's own principal axis must agree with the chain it came from, because that is
precisely the test the landmark fitter will apply next.  A limb that fails falls
back on its own, and the report says which rung each tag stopped on:

===========  ==========================================================
``unirig``   a cylinder built along that limb's detected axis
``hand``     the tag that was already on the mesh, kept untouched
``box``      this module's own axis-aligned band — the last resort, and
             the method whose failure is quoted above
===========  ==========================================================

Nothing here needs a GPU to be *testable*: the tag build takes a joints
document, and a document can be written by hand.  The detector is a source of
documents, not a dependency of the math.

Stdlib + ``bpy``/``mathutils``.  Nothing opens a window; the detector runs in
its own interpreter, in its own virtualenv, behind a JSON file.
"""

import json
import math
import os
import sys
import time

import bpy
from mathutils import Vector

from . import rigforge
from . import rigforge_joints
from . import rigforge_landmarks
from .common import (
    M_TO_MM,
    get_bool,
    get_choice,
    get_float,
    get_int,
    get_scene,
    object_mode,
    op_kwargs,
    refresh_view_layer,
    resolve_object,
    resolve_path,
    selection,
)
from .registry import ForgeError, command

__all__ = [
    "TAG_ROLES",
    "LIMB_ROLES",
    "DEFAULT_RADIUS_FACTOR",
    "PROP_TAG_SOURCE",
    "PROP_TAG_AXES",
    "TORSO_SUB_TAGS",
    "SPLIT_PARENT",
    "SPLIT_STATIONS",
    "LEG_SUB_TAG_SUFFIXES",
    "LEG_SPLIT_PREFIX",
    "Axis",
    "segments_of",
    "classify_segments",
    "build_axes",
    "assign_vertices",
    "limb_plausible",
    "box_band_groups",
    "axis_from_cloud",
    "spine_split",
    "leg_sub_tags",
    "leg_split",
    "sub_tag_at",
    "sub_tags_spanning",
    "split_membership",
    "split_from_clouds",
    "split_from_groups",
    "split_from_leg_cloud",
    "detector_status",
    "detect_joints_for",
    "auto_tag",
]

#: Axis role -> the Forge tag it writes.  Five roles, six tags: the spine's
#: residual is the torso and the neck's is the head.
TAG_ROLES = {
    "spine": "Torso",
    "head": "Head",
    "arm.L": "Arm.L",
    "arm.R": "Arm.R",
    "leg.L": "Leg.L",
    "leg.R": "Leg.R",
}

#: The roles a girth cap applies to.  The torso and the head are the *residual*
#: — whatever the limbs did not claim has to live somewhere, and capping the
#: residual would leave vertices in no tag at all.
LIMB_ROLES = ("arm.L", "arm.R", "leg.L", "leg.R")

#: How many times its own local girth a vertex may sit from a limb's axis and
#: still be that limb.  **Credibility tier: heuristic.**  1.0 would cut the
#: cylinder at the median radius and throw away half of every limb; 1.6 keeps the
#: flesh and still rejects a rib 2x the arm's radius away.  Measured on the
#: werewolf: the arm tags stop at the armpit rather than running into the chest.
DEFAULT_RADIUS_FACTOR = 1.6

#: Stations along a limb axis at which its girth is measured.  Same order of
#: magnitude as the landmark fitter's 33 slices, coarser on purpose: this is a
#: *cap*, not a landmark, and a coarse cap is a stable one.
GIRTH_STATIONS = 17

#: Passes of (measure girth -> reject outliers -> measure again).  Two is
#: enough: the first pass's median already survives the contamination it is
#: about to remove, and the second confirms it.  A third changed no tag on the
#: test meshes.
GIRTH_PASSES = 3

#: How many stations at a limb's **distal** end are capped on a high percentile
#: of their own flesh rather than on its median, and how high.
#:
#: The median is the right statistic along a limb's shaft and the wrong one at
#: its tip, and the two failures are opposite.  Along the shaft the contamination
#: is flesh the axis over-reached *into* — the ribs an arm hangs beside — which
#: sits far out, and a median survives it.  At the tip there is no rib: a hand is
#: the only thing at the end of an arm, and it is a **fan**, not a tube.  A
#: median of a paw's cross-section measures the gaps between the fingers, and
#: capping the palm at 1.6x that clips the palm off the limb it belongs to.
#:
#: Measured on the werewolf's ``Arm.L``: the girth profile falls monotonically
#: to **28.8 mm** at the last station while the palm's own flesh reaches
#: **75 mm** from the hand bone — a cap of 46 mm against a 75 mm palm.  The palm
#: survived only because no other axis had room for it, which is luck, not a
#: measurement; on this figure the hand hangs beside a thigh whose girth is
#: 79 mm and would happily have taken it.
#:
#: Three stations of seventeen is the last 12% of a limb, which is about what a
#: hand or a foot is; p75 is the flesh three quarters of the way out, so it
#: reads the palm and still ignores one stray vertex.
DISTAL_STATIONS = 3
DISTAL_PERCENTILE = 0.75

#: A limb chain shorter than this fraction of the figure's height is not a limb
#: — it is a finger twig, or a detection that collapsed.
MIN_LIMB_FRACTION = 0.10

#: A tag with fewer vertices than this cannot be cross-sectioned by the landmark
#: fitter (:class:`~forge.tools.rigforge_landmarks.Limb` refuses under 12, and a
#: 33-station slice of 40 points is already thin).
MIN_TAG_VERTICES = 40

#: The tag's own principal axis must agree with the chain that built it to
#: within this angle.  It is the same 60 degrees ``Limb`` uses against its
#: anatomical hint, deliberately: a tag that would fail *there* must fail
#: **here**, where there is still a fallback to take.
AXIS_AGREE_DEG = 60.0

#: A limb is longer than it is thick.  Below this ratio of axis length to median
#: girth the tag is a blob and the cross-section fitter has nothing to measure.
MIN_ASPECT = 2.0

#: Object custom properties.  ``forge_tag_source`` is the provenance ladder, so
#: a later ``rig_check`` or a later run can say where each tag came from without
#: guessing; ``forge_tag_axes`` carries each limb's measured direction to the
#: landmark fitter, stamped with the bbox it was measured on so a mesh that has
#: since been rotated or rebuilt is not fitted to a stale hint.
PROP_TAG_SOURCE = "forge_tag_source"
PROP_TAG_AXES = "forge_tag_axes"

#: The three slabs the ``Torso`` tag is cut into along its own spine, **proximal
#: to distal**.  They are a *derived view* and never a vertex group: nothing is
#: written to the mesh, so :func:`~forge.tools.rigforge_rig.measure_tags`, the
#: landmark fitter and every other consumer keep seeing exactly one ``Torso``
#: (see :func:`spine_split` for why that matters and what the split is for).
TORSO_SUB_TAGS = ("Torso.pelvis", "Torso.abdomen", "Torso.chest")

#: The tag :data:`TORSO_SUB_TAGS` are slabs of.  One name, in one place, so the
#: merge back is a lookup rather than a string split — ``Arm.L`` also has a dot
#: in it and parsing would turn it into ``Arm``.
SPLIT_PARENT = "Torso"

#: Stations along the spine at which the split is measured and to which both
#: cuts are snapped.  The **same grid** :data:`GIRTH_STATIONS` caps the limb
#: cylinders with, deliberately: a cut that lands between stations is a cut the
#: girth profile has no opinion about.
SPLIT_STATIONS = GIRTH_STATIONS

#: Where the legs join the spine, as a percentile of the leg tags' own position
#: along it.  **Not the maximum**: a limb tag's topmost vertices are the ragged
#: seam it shares with the torso, and one of them is not a junction.  Measured
#: on the werewolf, the leg tags' p95 is t=0.318 and their max t=0.421 — 68 mm
#: apart, the difference between a cut at the hip and a cut through the belly.
LEG_JUNCTION_PERCENTILE = 0.95

#: Stations at each end of the spine that the waist search ignores.  A tag's
#: girth always falls away at its own ends, because that is where it runs out of
#: flesh rather than where it narrows (werewolf: 159 mm at station 14, 96 mm at
#: station 16, and the shoulders are not a waist).
WAIST_END_MARGIN = 2

#: Stations the two cuts must be apart, so the abdomen is a slab rather than a
#: seam.
MIN_CUT_SEPARATION = 2

#: How far below its own band's mean a local girth minimum must sit to be called
#: a waist.  **Credibility tier: heuristic.**  Measured on the werewolf, whose
#: waist is a 3.3% dip (152.0 mm against the band's 157.2 mm mean) — real, and
#: shallow.  A uniform tube has a dip of a tenth of a percent, which is the
#: median's own noise, and that must fall back rather than cut at a coincidence.
WAIST_MIN_DIP = 0.02

#: A sub-tag with fewer vertices than this cannot be measured — it has no girth,
#: no blend band and nothing for a bone to hold — and a split that produces one
#: is refused whole rather than applied lopsided.
MIN_SUB_TAG_VERTICES = 12

#: The slabs a ``Leg`` tag is cut into, **proximal to distal**, as suffixes on
#: the parent tag's own name — ``Leg.L`` becomes ``Leg.L.thigh``,
#: ``Leg.L.shin``, ``Leg.L.foot``.  Suffixes rather than whole names because
#: there are two legs and each is split against **its own** measurements; see
#: :func:`leg_split` for the defect this exists to fix.
LEG_SUB_TAG_SUFFIXES = ("thigh", "shin", "foot")

#: The tag-name prefix whose tags get the leg split.  ``Leg.L`` and ``Leg.R``
#: on a biped; anything else named ``Leg*`` on a figure with more of them, which
#: is why this is a prefix rather than a pair of names.
LEG_SPLIT_PREFIX = "Leg"

#: Where the out-of-process detector runner lives, relative to the add-on.  The
#: add-on is installed from ``<repo>/addon``, so the sibling is
#: ``<repo>/rigbridge``.  ``FORGE_RIGBRIDGE`` overrides it for an install that
#: moved.
_RIGBRIDGE_ENV = "FORGE_RIGBRIDGE"
_RUNNER_MODULE = "detector_runner"


# ---------------------------------------------------------------------------
# the polyline axis
# ---------------------------------------------------------------------------

class Axis(object):
    """A limb's centreline: a polyline in world space, with an arclength.

    Everything downstream asks it one question — *how far is this vertex from
    me, and how far along am I when it is nearest?* — so that is the whole
    interface.  The answer is exact (point-to-segment, not point-to-vertex): a
    game mesh's edge loops are tens of millimetres apart and snapping to the
    nearest joint would quantise every radius to the joint spacing.
    """

    def __init__(self, role, points, is_limb=False, source_joints=None):
        points = [Vector(p) for p in points]
        cleaned = [points[0]]
        for point in points[1:]:
            if (point - cleaned[-1]).length > 1e-7:
                cleaned.append(point)
        if len(cleaned) < 2:
            raise ForgeError(
                "The %s axis collapsed to a single point; a detected chain of %d "
                "joints has no direction." % (role, len(points)))
        self.role = role
        self.points = cleaned
        self.is_limb = bool(is_limb)
        self.source_joints = list(source_joints or ())
        self._measure()

    def _measure(self):
        self.lengths = []
        total = 0.0
        for index in range(len(self.points) - 1):
            step = (self.points[index + 1] - self.points[index]).length
            self.lengths.append(step)
            total += step
        self.length = total
        self.cumulative = [0.0]
        for step in self.lengths:
            self.cumulative.append(self.cumulative[-1] + step)

    # -- geometry ---------------------------------------------------------

    @property
    def direction(self):
        """Proximal -> distal, normalised. The anatomical hint, measured."""
        return (self.points[-1] - self.points[0]).normalized()

    def closest(self, point):
        """``(distance, t)`` — how far off the axis, and where along it (0..1)."""
        best_distance = None
        best_arc = 0.0
        for index in range(len(self.points) - 1):
            a = self.points[index]
            b = self.points[index + 1]
            span = b - a
            span_length = self.lengths[index]
            if span_length < 1e-9:
                continue
            factor = (point - a).dot(span) / (span_length * span_length)
            factor = max(0.0, min(1.0, factor))
            foot = a + span * factor
            distance = (point - foot).length
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_arc = self.cumulative[index] + factor * span_length
        if best_distance is None:
            best_distance = (point - self.points[0]).length
        return best_distance, (best_arc / self.length if self.length > 1e-9 else 0.0)

    def at(self, t):
        """The world-space point at arclength fraction ``t`` — :meth:`closest` inverted.

        A cut is a ``t``, and everything a human reads a cut back against is a
        *place*: "the knee is 507 mm up" is checkable against the rig and
        "t=0.417" is not.  Clamped at both ends, so a bone span that runs past
        the flesh still names a point on the axis.
        """
        if len(self.points) < 2 or self.length <= 1e-9:
            return self.points[0].copy()
        arc = max(0.0, min(1.0, float(t))) * self.length
        for index in range(len(self.points) - 1):
            step = self.lengths[index]
            if step <= 1e-9:
                continue
            if arc <= self.cumulative[index + 1] or index == len(self.points) - 2:
                factor = (arc - self.cumulative[index]) / step
                factor = max(0.0, min(1.0, factor))
                return self.points[index].lerp(self.points[index + 1], factor)
        return self.points[-1].copy()

    def extend_distal(self, amount):
        if amount <= 1e-9:
            return
        tail = self.points[-1] - self.points[-2]
        if tail.length < 1e-9:
            return
        self.points[-1] = self.points[-1] + tail.normalized() * amount
        self._measure()

    def extend_proximal(self, amount):
        if amount <= 1e-9:
            return
        head = self.points[0] - self.points[1]
        if head.length < 1e-9:
            return
        self.points[0] = self.points[0] + head.normalized() * amount
        self._measure()

    def as_dict(self):
        return {
            "role": self.role,
            "limb": self.is_limb,
            "joints": self.source_joints,
            "length_mm": round(self.length * M_TO_MM, 2),
            "direction": [round(v, 5) for v in self.direction],
            "points_mm": [[round(v * M_TO_MM, 1) for v in p] for p in self.points],
        }


# ---------------------------------------------------------------------------
# the tree
# ---------------------------------------------------------------------------

def segments_of(parents):
    """Cut a joint tree into maximal single-child runs.

    Returns a list of index lists, each running from a *node of interest* (the
    root, a branch point, or a leaf) through single-child joints to the next
    one.  A biped's arm arrives as one segment from the chest to the wrist, and
    its fingers arrive as separate twigs off the wrist, which is exactly the cut
    a tagger wants: the arm is one tube, the fingers are not part of its axis.

    The tree is taken as given but never trusted: a cycle, a parent pointing off
    the end of the array or a second root is a malformed detection, and this
    returns what it can rather than looping for ever.
    """
    count = len(parents)
    children = {}
    roots = []
    for index in range(count):
        parent = parents[index]
        if parent is None or not isinstance(parent, int) or parent < 0 or parent >= count \
                or parent == index:
            roots.append(index)
            continue
        children.setdefault(parent, []).append(index)

    interesting = set(roots)
    for index in range(count):
        kids = children.get(index, ())
        if len(kids) != 1:
            interesting.add(index)  # a leaf or a branch point

    out = []
    seen_edges = set()
    for start in sorted(interesting):
        for first in children.get(start, ()):
            chain = [start, first]
            guard = 0
            while guard < count + 2:
                guard += 1
                last = chain[-1]
                if last in interesting:
                    break
                kids = children.get(last, ())
                if len(kids) != 1:
                    break
                chain.append(kids[0])
            edge = (chain[0], chain[-1])
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            out.append(chain)
    return out, children, roots


def _longest_descent(index, children, points):
    """The longest chain of joints descending from ``index``, by arclength.

    Used to run a limb's axis out through its own extremity: an arm's axis
    should continue down the longest finger rather than stop dead at the wrist,
    because the hand's vertices have to belong to *something* and their nearest
    axis should be the arm they are on the end of.
    """
    best = []
    best_length = -1.0
    for child in children.get(index, ()):
        tail = _longest_descent(child, children, points)
        chain = [child] + tail
        length = 0.0
        previous = points[index]
        for joint in chain:
            length += (points[joint] - previous).length
            previous = points[joint]
        if length > best_length:
            best_length = length
            best = chain
    return best


# ---------------------------------------------------------------------------
# naming the segments, from geometry alone
# ---------------------------------------------------------------------------

def classify_segments(segments, children, points, low, high, midplane,
                      character_left, root=None):
    """``{role: [joint indices]}`` for spine, head and the four limbs.

    Geometry decides every one of them.  The detector's joint *names* are
    positional placeholders (``bone_0``…) and are never read; its joint *order*
    is an decode order, not an anatomy, and is never read either.

    Returns the roles it could name and a per-role reason, plus the segments it
    could not place, so the report can say what was left over rather than
    quietly dropping it.
    """
    height = max(high.z - low.z, 1e-6)
    mid_z = low.z + 0.5 * height
    roles = {}
    why = {}
    unplaced = []

    def centroid(chain):
        total = Vector((0.0, 0.0, 0.0))
        for index in chain:
            total += points[index]
        return total / float(len(chain))

    def arclength(chain):
        total = 0.0
        for position in range(len(chain) - 1):
            total += (points[chain[position + 1]] - points[chain[position]]).length
        return total

    def lateral(chain):
        """How far off the midplane this chain sits, signed to the mesh's left."""
        return (centroid(chain).x - midplane) * character_left

    # --- the spine: out of the root, upwards, near the midplane ------------
    if root is None:
        # The root of the tree is the only joint that starts a segment without
        # ending another one.
        for chain in segments:
            if all(chain[0] != other[-1] for other in segments):
                root = chain[0]
                break
    candidates = [c for c in segments
                  if c[0] == root and points[c[-1]].z > points[c[0]].z + 0.02 * height]
    spine = None
    if candidates:
        spine = min(candidates, key=lambda c: abs(lateral(c)))
        roles["spine"] = spine
        why["spine"] = ("the chain leaving the root joint upwards nearest the midplane "
                        "(%.0f mm long, %.0f mm off centre)"
                        % (arclength(spine) * M_TO_MM, abs(lateral(spine)) * M_TO_MM))

    # --- the head: continuing up from the spine's top, near the midplane ---
    if spine is not None:
        tip = spine[-1]
        upward = [c for c in segments
                  if c is not spine and c[0] == tip
                  and points[c[-1]].z > points[c[0]].z]
        if upward:
            head_chain = min(upward, key=lambda c: abs(lateral(c)))
            roles["head"] = head_chain
            why["head"] = ("the chain continuing upwards from the top of the spine "
                           "(%.0f mm long)" % (arclength(head_chain) * M_TO_MM))

    claimed = {id(chain) for chain in roles.values()}

    # --- the limbs: the long sided chains ---------------------------------
    for side in ("L", "R"):
        want = 1.0 if side == "L" else -1.0
        sided = [c for c in segments
                 if id(c) not in claimed and lateral(c) * want > 0.0]
        sided.sort(key=arclength, reverse=True)

        leg = None
        for chain in sided:
            if points[chain[-1]].z < low.z + 0.40 * height \
                    and arclength(chain) >= MIN_LIMB_FRACTION * height:
                leg = chain
                break
        if leg is not None:
            roles["leg.%s" % side] = leg
            claimed.add(id(leg))
            why["leg.%s" % side] = (
                "the longest chain on the character's %s that ends in the bottom 40%% of "
                "the figure (%.0f mm long, ending %.0f mm above the floor)"
                % (side, arclength(leg) * M_TO_MM,
                   (points[leg[-1]].z - low.z) * M_TO_MM))

        arm = None
        for chain in sided:
            if id(chain) in claimed:
                continue
            if points[chain[0]].z > mid_z and arclength(chain) >= MIN_LIMB_FRACTION * height:
                arm = chain
                break
        if arm is not None:
            roles["arm.%s" % side] = arm
            claimed.add(id(arm))
            why["arm.%s" % side] = (
                "the longest remaining chain on the character's %s that starts above the "
                "waist (%.0f mm long, from %.0f mm up)"
                % (side, arclength(arm) * M_TO_MM, (points[arm[0]].z - low.z) * M_TO_MM))

    for chain in segments:
        if id(chain) not in claimed:
            unplaced.append(list(chain))
    return roles, why, unplaced


def build_axes(roles, children, points, world_points_list=None):
    """Turn named joint chains into :class:`Axis` polylines, run out to the flesh.

    Two extensions, both because a detected skeleton stops short of the body it
    describes:

    * a **limb** continues through its longest descendant twig, so the hand is
      on the arm's axis and the toes on the leg's;
    * every axis then grows at its far end by however far its own vertices
      overshoot it (and the spine also grows *downwards*, because the pelvis
      hangs below the root joint).  Without this the top of a skull is 200 mm
      past the last neck joint and measures as an outlier of everything.
    """
    axes = {}
    for role, chain in sorted(roles.items()):
        joints = list(chain)
        if role != "spine" and len(joints) >= 3:
            # The first joint of a limb or neck segment is the **branch point it
            # hangs off** — the chest for an arm, the pelvis for a leg — and that
            # joint sits on the midplane. An axis starting there runs diagonally
            # through the torso, and the tag built along it claims the buttock
            # and the ribcage. Measured on the werewolf: the leg tag reached
            # 1125 mm up a 1879 mm figure and took the whole pelvis with it. The
            # limb begins at its own first joint; where it *joins* the body is
            # the landmark fitter's question, not the tagger's.
            joints = joints[1:]
        if role in LIMB_ROLES:
            joints = joints + _longest_descent(joints[-1], children, points)
        axes[role] = Axis(role, [points[i] for i in joints],
                          is_limb=(role in LIMB_ROLES), source_joints=joints)

    if not world_points_list or not axes:
        return axes

    # How far past each end does the flesh that is nearest to this axis run?
    overshoot = {role: [0.0, 0.0] for role in axes}
    for point in world_points_list:
        best_role = None
        best_distance = None
        for role, axis in axes.items():
            distance, _t = axis.closest(point)
            if best_distance is None or distance < best_distance:
                best_role, best_distance = role, distance
        axis = axes[best_role]
        beyond_distal = (point - axis.points[-1]).dot(
            (axis.points[-1] - axis.points[-2]).normalized())
        beyond_proximal = (point - axis.points[0]).dot(
            (axis.points[0] - axis.points[1]).normalized())
        record = overshoot[best_role]
        record[1] = max(record[1], beyond_distal)
        record[0] = max(record[0], beyond_proximal)

    # How far the spine may reach *down*: not as far as its flesh goes, which is
    # between the legs and most of the way to the knees. The bound that means
    # something is the limbs themselves — a residual axis may reach towards the
    # pelvis by as much as the limbs hanging off it are away from it, and no
    # further. Without it the spine runs down between the thighs, the torso tag
    # swallows the top of both legs, and the landmark fitter then reads a hip
    # 37 mm above its own knee and refuses the leg (measured on the test biped).
    spine = axes.get("spine")
    proximal_limit = None
    if spine is not None:
        for role, axis in axes.items():
            if not axis.is_limb:
                continue
            reach = (axis.points[0] - spine.points[0]).length
            proximal_limit = reach if proximal_limit is None else min(proximal_limit, reach)

    for role, axis in axes.items():
        # Capped at the axis's own length: an axis may reach past its last joint,
        # it may not double itself into territory it has no evidence about.
        axis.extend_distal(min(overshoot[role][1], axis.length))
        if role == "spine":
            limit = min(overshoot[role][0], axis.length)
            if proximal_limit is not None:
                limit = min(limit, proximal_limit)
            axis.extend_proximal(limit)
    return axes


# ---------------------------------------------------------------------------
# the tagging itself
# ---------------------------------------------------------------------------

def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1,
                          int(round(fraction * (len(ordered) - 1)))))
    return ordered[position]


def _smooth_profile(values):
    """A [1 2 1] pass, ends held — the same smoother the landmark fitter uses."""
    if len(values) < 3:
        return list(values)
    out = list(values)
    nxt = list(out)
    for index in range(1, len(out) - 1):
        nxt[index] = (out[index - 1] + 2.0 * out[index] + out[index + 1]) / 4.0
    return nxt


def _girth_profile(samples, stations=GIRTH_STATIONS, distal=0):
    """Radial girth at each station along an axis, gaps filled.

    The **median**, not a high percentile, and that is the load-bearing choice.
    The set being measured still contains whatever the axis over-claimed — the
    ribs an arm is hanging beside — and a p90 of a contaminated set measures the
    contamination.  A median survives it as long as the limb's own flesh is the
    majority of its own neighbourhood, which for a cylinder around its own
    centreline it always is.

    **Except at the last ``distal`` stations**, where the shape is a hand or a
    foot rather than a tube and the median measures the gaps between the fingers
    — see :data:`DISTAL_STATIONS` for the millimetres.  Those stations use
    :data:`DISTAL_PERCENTILE`, and the smoother is not allowed to pull them back
    below their own measurement afterwards: a [1 2 1] pass over a taper that
    ends in a bulge is a pass that removes the bulge, which is the one feature
    they are there to record.
    """
    buckets = [[] for _ in range(stations)]
    for distance, t in samples:
        index = max(0, min(stations - 1, int(round(t * (stations - 1)))))
        buckets[index].append(distance)
    first_distal = stations - max(0, int(distal))
    profile = [_percentile(bucket,
                           DISTAL_PERCENTILE if index >= first_distal else 0.5)
               if len(bucket) >= 4 else None
               for index, bucket in enumerate(buckets)]
    known = [value for value in profile if value is not None]
    if not known:
        return None
    fallback = _percentile(known, 0.5)
    filled = []
    for index, value in enumerate(profile):
        if value is not None:
            filled.append(value)
            continue
        # Nearest measured station wins; a station with no flesh at all is a gap
        # in the tag, not a reason to invent a radius for it.
        best = None
        for other, candidate in enumerate(profile):
            if candidate is None:
                continue
            if best is None or abs(other - index) < abs(best[0] - index):
                best = (other, candidate)
        filled.append(best[1] if best else fallback)
    smoothed = _smooth_profile(filled)
    for index in range(first_distal, stations):
        smoothed[index] = max(smoothed[index], filled[index])
    return smoothed


def _profile_at(profile, t):
    if not profile:
        return None
    position = max(0.0, min(1.0, t)) * (len(profile) - 1)
    low = int(math.floor(position))
    high = min(len(profile) - 1, low + 1)
    blend = position - low
    return profile[low] * (1.0 - blend) + profile[high] * blend


def assign_vertices(world_points_list, axes, radius_factor=DEFAULT_RADIUS_FACTOR):
    """``{role: [vertex index]}`` — every vertex on the axis it belongs to.

    Two rules, in order:

    1. **Nearest axis wins.**  Distance to the polyline, not to a joint and not
       to a plane.  This alone already partitions the mesh along the *body*
       rather than along the world, which is the whole difference between a tag
       that follows an arm and a box band that cannot.
    2. **A limb may not exceed its own girth.**  Each limb axis measures the
       median radius of its own claim at each station along it; a vertex beyond
       ``radius_factor`` of it is offered to the other axes, nearest first, and
       moves only to one whose *own* girth there accepts it.  This is what keeps
       the arm tag out of the ribcage when the arm hangs beside it.

    Two things about that were found by getting them wrong first, and both are
    pinned by tests:

    * **The acceptance half of rule 2 is not optional.**  "Rejected, so give it
      to the torso" put the **soles of the feet** in the torso tag (measured: a
      Torso reaching from 7 mm to 1420 mm on a 1879 mm figure), because a heel
      that a thin toe station will not have has no better owner — it is a metre
      from the spine.  A rib 180 mm from the spine *is* inside the torso's own
      girth and moves; a heel 900 mm from it is not, and stays on the leg it
      belongs to.  A vertex no axis accepts keeps the nearest one, so nothing is
      ever dropped.
    * **Scoring every axis in units of its own girth does not work**, though it
      is the more elegant rule and was implemented first.  The torso is the
      residual and cannot reject, so its claim grows, so its measured girth
      grows, so it claims more: on the werewolf it ran away from 1279 vertices
      to 4678 and took most of both legs.  Rule 1 stays in millimetres, and the
      girth is a **cap** rather than a scale.

    The **median** is what makes the bootstrap survivable: the first pass's
    claim still contains whatever the axis over-reached into, and a median of a
    contaminated set is still the limb as long as the limb's own flesh is the
    majority of its own neighbourhood — which, for a tube around its own
    centreline, it is.
    """
    roles = sorted(axes)
    if not roles:
        return {}, {}
    limb_roles = [role for role in roles if axes[role].is_limb]

    # Distance and parameter to every axis, computed once: the polylines do not
    # move, only the ownership does.
    table = []
    for point in world_points_list:
        row = {}
        for role in roles:
            row[role] = axes[role].closest(point)
        table.append(row)

    owner = [min(roles, key=lambda role: row[role][0]) for row in table]

    profiles = {}
    rejected = {role: 0 for role in limb_roles}
    passes = 0
    moves = []
    for _pass in range(GIRTH_PASSES if limb_roles else 0):
        passes += 1
        for role in roles:
            samples = [table[i][role] for i, held in enumerate(owner) if held == role]
            # Only a limb has an extremity. The torso and the head are the
            # residual and cannot reject anything, so widening their far end
            # would only make them accept more of what the limbs threw out.
            profiles[role] = _girth_profile(
                samples, distal=(DISTAL_STATIONS if axes[role].is_limb else 0))
        moved = 0
        for index, held in enumerate(owner):
            if held not in limb_roles:
                continue
            distance, t = table[index][held]
            radius = _profile_at(profiles.get(held), t)
            if radius is None or distance <= radius_factor * radius:
                continue
            others = sorted((role for role in roles if role != held),
                            key=lambda role: table[index][role][0])
            for candidate in others:
                candidate_distance, candidate_t = table[index][candidate]
                candidate_radius = _profile_at(profiles.get(candidate), candidate_t)
                if candidate_radius is None:
                    continue
                if candidate_distance <= radius_factor * candidate_radius:
                    owner[index] = candidate
                    rejected[held] += 1
                    moved += 1
                    break
        moves.append(moved)
        if not moved:
            break

    groups = {role: [] for role in roles}
    for index, held in enumerate(owner):
        groups[held].append(index)
    stats = {
        "passes": passes,
        "moved_per_pass": moves,
        "rejected_by_girth": rejected,
        "radius_factor": round(float(radius_factor), 3),
        "girth_mm": {role: [round(v * M_TO_MM, 2) for v in (profiles.get(role) or [])]
                     for role in roles},
        "limbs": limb_roles,
    }
    return groups, stats


# ---------------------------------------------------------------------------
# the torso, cut along its own spine
# ---------------------------------------------------------------------------

def axis_from_cloud(points, role="spine", seed=None, proximal=None):
    """The centreline of a point cloud as a two-point :class:`Axis`.

    The cloud's own principal axis through its own centroid, spanning exactly
    its own projections — so a ``Torso`` tag's axis covers the torso's *flesh*
    rather than the detected spine chain, which stops at the shoulder girdle and
    leaves everything above it piled on one station.

    ``seed`` is the direction to start the power iteration from — the detected
    spine's, when there is one.  Ends are ordered **proximal to distal**, which
    for a torso means upwards: the same "the spine leaves the root going up"
    convention :func:`classify_segments` reads the skeleton with.

    ``proximal`` is a world-space point that says which end *is* the proximal
    one, and it overrides the upwards rule.  A **leg hangs the other way**: its
    proximal end is the hip, at the *top*, so ordering it upwards would put
    ``t=0`` at the toe and make every sub-tag read back to front.  Passing the
    hip joint is the measurement that settles it, and it is a measurement rather
    than a "legs point down" assumption — a figure modelled on any convention,
    or a limb that is not a leg at all, orders itself off its own chain root.
    """
    points = [Vector(p) for p in points]
    if len(points) < 2:
        raise ForgeError("A %s axis needs at least two points; got %d."
                         % (role, len(points)))
    centre = Vector((0.0, 0.0, 0.0))
    for point in points:
        centre += point
    centre /= float(len(points))
    axis = rigforge_landmarks._principal_axis(points, centre, seed=seed)
    projections = [(point - centre).dot(axis) for point in points]
    low = centre + axis * min(projections)
    high = centre + axis * max(projections)
    if proximal is not None:
        near = Vector(proximal)
        if (low - near).length > (high - near).length:
            low, high = high, low
    elif low.z > high.z:
        low, high = high, low
    return Axis(role, [low, high])


def spine_split(axis, torso_points, leg_points, stations=SPLIT_STATIONS):
    """Where to cut a ``Torso`` tag into pelvis, abdomen and chest.  Never raises.

    The failure this exists to fix
    ------------------------------
    The tag contract (:func:`~forge.tools.rigforge_skin.legal_bone_sets`) is
    enforced **per tag**, and on the werewolf the single ``Torso`` tag is one
    bucket holding **thirteen** deform bones — the whole spine, both breasts,
    both pelvis bones and both shoulders.  Cross-tag stray influence is 0.000
    and it is still wrong: a shoulder bone at z=1500 putting weight on pelvis
    flesh at z=900 is *legal*, because both are ``Torso``.  That flesh is in the
    ``Torso``/``Leg`` blend band, so it shares its neighbourhood with the thighs,
    and the owner sees it in the walk — **the arm swing tugs the thighs**.

    Two cuts, and only one of them is a free choice
    -----------------------------------------------
    1. **The pelvis ends where the legs join.**  That is a measurement, not a
       fraction: the :data:`LEG_JUNCTION_PERCENTILE` of the leg tags' own
       positions along the spine, snapped to the nearest station.  It is the
       boundary that matters for the observed defect, because it is exactly
       where torso flesh starts blending into thigh flesh.
    2. **The chest begins at the waist** — the narrowest station of the spine's
       own girth profile between that junction and the shoulders, accepted only
       when it is narrower than both its neighbours *and* at least
       :data:`WAIST_MIN_DIP` below its band's mean.  A uniform tube has no
       waist, and inventing one from the median's noise would be a cut at a
       coincidence; there the fallback is the midpoint between the leg junction
       and the top of the spine, and the report says which rule fired.

    Both are snapped to the station grid the girth cap already uses, both are
    held :data:`MIN_CUT_SEPARATION` stations apart, and the ends of the profile
    are excluded (:data:`WAIST_END_MARGIN`) because a tag always thins where it
    runs out of flesh.

    Measured on the werewolf (``werewolf-wip-9.blend``, 1435 torso vertices,
    a 667 mm spine): the legs' p95 lands on station 5 (t=0.3125, z=1066 mm) and
    the waist on station 10 (t=0.625, z=1274 mm, a 3.3% dip) — and the fallback
    midpoint lands on the same station 10, which is the corroboration that the
    dip is a waist rather than an artefact.

    Returns a report dict.  A split that cannot be measured carries ``refused``
    with the reason and no ``cuts``; the caller then keeps the one merged tag,
    which is the behaviour every consumer already has.
    """
    stations = max(2 * WAIST_END_MARGIN + MIN_CUT_SEPARATION + 2, int(stations))
    samples = [axis.closest(point) for point in torso_points]
    profile = _girth_profile(samples, stations)
    report = {
        "parent": SPLIT_PARENT,
        "names": list(TORSO_SUB_TAGS),
        "stations": stations,
        "axis_mm": [[round(v * M_TO_MM, 1) for v in point] for point in axis.points],
        "axis_length_mm": round(axis.length * M_TO_MM, 1),
        "girth_mm": [round(value * M_TO_MM, 1) for value in (profile or ())],
        "torso_vertices": len(torso_points),
    }

    if len(torso_points) < 3 * MIN_SUB_TAG_VERTICES:
        report["refused"] = (
            "the Torso tag has %d vertices, and three slabs of at least %d each "
            "need %d" % (len(torso_points), MIN_SUB_TAG_VERTICES,
                         3 * MIN_SUB_TAG_VERTICES))
        return report
    if not leg_points:
        report["refused"] = (
            "no leg tag has any flesh, so nothing measures where the pelvis ends; "
            "the Torso stays one tag")
        return report

    # --- cut 1: where the legs join the spine ------------------------------
    leg_t = sorted(axis.closest(point)[1] for point in leg_points)
    junction = _percentile(leg_t, LEG_JUNCTION_PERCENTILE)
    highest = stations - 1 - WAIST_END_MARGIN - MIN_CUT_SEPARATION
    first = max(1, min(highest, int(round(junction * (stations - 1)))))
    how_first = ("the station nearest where the legs join the spine (the leg tags' "
                 "p%.0f sits at t=%.3f, %.0f mm up this axis)"
                 % (100.0 * LEG_JUNCTION_PERCENTILE, junction,
                    junction * axis.length * M_TO_MM))

    # --- cut 2: the waist, or the midpoint that says it is not one ---------
    band = list(range(first + MIN_CUT_SEPARATION, stations - WAIST_END_MARGIN))
    second = None
    how_second = ""
    if profile and band:
        mean = sum(profile[i] for i in band) / float(len(band))
        dips = [i for i in band
                if 0 < i < stations - 1
                and profile[i] < profile[i - 1] and profile[i] < profile[i + 1]
                and profile[i] <= (1.0 - WAIST_MIN_DIP) * mean]
        if dips:
            second = min(dips, key=lambda i: profile[i])
            how_second = ("the narrowest station of the spine's own girth profile "
                          "between the legs and the shoulders - the waist, %.0f mm "
                          "against the band's %.0f mm mean (a %.1f%% dip)"
                          % (profile[second] * M_TO_MM, mean * M_TO_MM,
                             100.0 * (1.0 - profile[second] / max(mean, 1e-9))))
    if second is None and band:
        second = max(band[0], min(band[-1], (first + stations - 1) // 2))
        how_second = ("the midpoint between the leg junction and the top of the "
                      "spine: no station between them is narrower than both its "
                      "neighbours by %.0f%%, so this trunk has no measurable waist"
                      % (100.0 * WAIST_MIN_DIP))
    if second is None or second - first < MIN_CUT_SEPARATION:
        report["refused"] = (
            "the legs join this spine at station %d of %d, which leaves no room for "
            "an abdomen %d stations deep above it"
            % (first, stations - 1, MIN_CUT_SEPARATION))
        return report

    cuts = [first / float(stations - 1), second / float(stations - 1)]
    counts = {name: 0 for name in TORSO_SUB_TAGS}
    for _distance, t in samples:
        counts[sub_tag_at(t, cuts)] += 1
    thin = sorted(name for name, count in counts.items()
                  if count < MIN_SUB_TAG_VERTICES)
    if thin:
        report["refused"] = (
            "%s would hold %s vertices and a sub-tag under %d cannot be measured, "
            "so the Torso stays one tag"
            % (", ".join(thin), ", ".join(str(counts[name]) for name in thin),
               MIN_SUB_TAG_VERTICES))
        report["would_be"] = dict(counts)
        return report

    report["cut_stations"] = [first, second]
    report["cuts"] = [round(value, 6) for value in cuts]
    report["cut_mm"] = [round(value * axis.length * M_TO_MM, 1) for value in cuts]
    report["how"] = [how_first, how_second]
    report["vertices"] = dict(counts)
    report["says"] = (
        "Torso split into %s at stations %d and %d of %d along its own %.0f mm "
        "spine (%d / %d / %d vertices). %s; %s."
        % (", ".join(TORSO_SUB_TAGS), first, second, stations - 1,
           axis.length * M_TO_MM, counts[TORSO_SUB_TAGS[0]],
           counts[TORSO_SUB_TAGS[1]], counts[TORSO_SUB_TAGS[2]],
           how_first, how_second))
    return report


# ---------------------------------------------------------------------------
# a leg, cut at its own knee and its own ankle
# ---------------------------------------------------------------------------

def chain_split(axis, points, parent, joints, names, stations=SPLIT_STATIONS,
                merge_thin=True, min_slab=0.0):
    """Cut a tag into one slab per segment of the chain that deforms it.

    The engine under :func:`leg_split` and :func:`torso_chain_split`, and the
    third and most general statement of the same recipe: **a tag should be no
    coarser than the chain that moves it**.  The contract is enforced per tag,
    so a tag holding a chain of N bones is a bucket in which all N are legal on
    all of its flesh — and two bones at opposite ends of that chain then hold
    the same vertices while sitting half a metre apart.  Cutting the tag at the
    chain's own joints gives each bone a slab of its own, and everything else
    (the span rule, the hinge rule, the bands) already knows what to do with it.

    ``joints`` is ``[(name, world point), ...]``, proximal to distal: the point
    where each slab *after the first* begins.  ``names`` is one longer, the
    first being the proximal slab's.  Cuts are the joints' own positions
    projected onto ``axis`` — **full precision and unsnapped**, for the reason
    :func:`leg_split` gives: the bone at a joint has its head at exactly that
    ``t``, and rounding decides whether the span rule sees it.

    ``merge_thin`` decides what happens to a slab too small to measure.  A leg
    wants three slabs or none, because "thigh, shin, foot" is the claim being
    made and two of them is a different claim — so it refuses.  A spine wants as
    many slabs as its vertebrae will carry: the werewolf's ``DEF-spine.004`` and
    ``DEF-spine.005`` are 28 mm each and a slab there would hold almost nothing,
    so those cuts are **dropped** and the neighbouring slabs merge, keeping the
    proximal one's name.  Merging is the honest answer there because the result
    is still a correct — just coarser — statement of the same contract.

    Returns a report dict of the shape :func:`spine_split` returns.
    """
    stations = max(MIN_CUT_SEPARATION + 3, int(stations))
    samples = [axis.closest(point) for point in points]
    report = {
        "parent": parent,
        "names": list(names),
        "stations": stations,
        "axis_mm": [[round(v * M_TO_MM, 1) for v in point] for point in axis.points],
        "axis_length_mm": round(axis.length * M_TO_MM, 1),
        "points": len(points),
        "joints": [str(label) for label, _point in joints],
        "joint_mm": [[round(v * M_TO_MM, 1) for v in Vector(point)]
                     for _label, point in joints],
    }
    if len(names) != len(joints) + 1:
        report["refused"] = ("%d slab name(s) were offered for %d joint(s); a chain "
                             "split names one slab more than it has cuts"
                             % (len(names), len(joints)))
        return report
    if not joints:
        report["refused"] = ("this chain has no joint inside it, so there is nothing "
                             "to cut %s at" % parent)
        return report
    if len(points) < 2 * MIN_SUB_TAG_VERTICES:
        report["refused"] = (
            "the %s tag has %d vertices, and slabs of at least %d each need %d"
            % (parent, len(points), MIN_SUB_TAG_VERTICES, 2 * MIN_SUB_TAG_VERTICES))
        return report

    cuts = [axis.closest(Vector(point))[1] for _label, point in joints]
    report["joint_t"] = [round(value, 6) for value in cuts]
    report["joint_height_mm"] = [round(axis.at(value).z * M_TO_MM, 1) for value in cuts]

    # A joint outside the flesh this tag actually holds cuts nothing, and a cut
    # at t=0 or t=1 opens a slab with nothing in it. Both are dropped rather
    # than refused: a chain routinely runs past the end of the tag it moves.
    #
    # **Strictly at the ends, and not "within a station of" them.** The station
    # grid is a reporting unit here, not a measurement -- these cuts are joint
    # positions -- and how much flesh a slab holds is asked directly, below, in
    # vertices. Measured the hard way: the artist moved this werewolf's ankles
    # down 44 mm, which put the ankle cut at t=0.943 of the leg's own axis, and
    # a one-station margin refused **both legs** over a foot slab that in fact
    # held 300-odd vertices. An artist's joint placement is an input, not an
    # error, and a guard that turns one into a refusal is the guard's bug.
    kept = [(cut, names[index + 1]) for index, cut in enumerate(cuts)
            if 0.0 < cut < 1.0]
    kept.sort()
    live = [names[0]] + [name for _cut, name in kept]
    live_cuts = [cut for cut, _name in kept]

    # Drop the cut whose slab is thinnest until every slab can be measured.
    #
    # **Thin means two different things and a slab has to survive both.** Too
    # few vertices and there is nothing to measure. Too *short* and there is
    # nothing left of it once its own blend bands are cut -- ``min_slab`` is the
    # caller's statement, in metres along this axis, of how long a slab must be
    # to still have an interior. See ``rigforge_skin.MIN_SLAB_BANDS`` for why
    # that is a correctness rule rather than a preference.
    while live_cuts:
        counts = {name: 0 for name in live}
        for _distance, t in samples:
            counts[sub_tag_at(t, live_cuts, live)] += 1
        edges = [0.0] + list(live_cuts) + [1.0]
        extent = {live[index]: (edges[index + 1] - edges[index]) * axis.length
                  for index in range(len(live))}
        thin = [name for name in live if counts[name] < MIN_SUB_TAG_VERTICES]
        short = []
        if min_slab > 0.0 and len(live) > 2:
            short = [name for name in live if extent[name] < min_slab]
            thin += [name for name in short if name not in thin]
        if not thin:
            break
        if not merge_thin:
            report["refused"] = (
                "%s would hold %s vertices and a sub-tag under %d cannot be "
                "measured, so the %s stays one tag"
                % (", ".join(thin), ", ".join(str(counts[name]) for name in thin),
                   MIN_SUB_TAG_VERTICES, parent))
            report["would_be"] = dict(counts)
            return report
        # The thinnest slab merges into its proximal neighbour, which means
        # dropping the cut that opens it. The proximal-most slab has no cut of
        # its own to drop, so it merges by dropping the cut above it instead.
        #
        # A slab flagged for being **short** is merged by length rather than by
        # vertex count, or the merge does not converge on even slabs: a long
        # sparse slab would keep losing cuts to a short dense one and the split
        # would collapse further than the rule asked for.
        victim = (min(short, key=lambda name: extent[name]) if short
                  else min(thin, key=lambda name: counts[name]))
        position = live.index(victim)
        del live_cuts[position - 1 if position > 0 else 0]
        del live[position if position > 0 else 1]

    if len(live) < 2:
        report["refused"] = ("no joint of this chain leaves two slabs with enough "
                             "flesh on either side to measure")
        return report
    if min_slab > 0.0:
        # **A tag that cannot carry even two slabs is not split at its chain at
        # all.** Merging stops at two because one slab is not a split; if the
        # two that remain are still shorter than their own bands, the honest
        # answer is that this topology has no chain split in it and the caller
        # should use whatever coarser rule it has. Measured on the synthetic
        # test biped: its 290 mm trunk is sampled every 59 mm, so its bands
        # floor to 169 mm and *any* cut leaves slabs that are entirely band.
        # Forcing two anyway put the whole trunk in one blend zone, which made
        # the shoulder legal on pelvis flesh and moved the lower body **54 mm**
        # when the arms swung -- the exact defect this lane exists to prevent.
        edges = [0.0] + list(live_cuts) + [1.0]
        shortest = min((edges[index + 1] - edges[index]) * axis.length
                       for index in range(len(live)))
        if shortest < min_slab:
            report["refused"] = (
                "this chain's slabs come out %.0f mm at best and a slab needs %.0f mm "
                "to have an interior once its own blend bands are cut, so %s is not "
                "fine enough to split at its chain"
                % (shortest * M_TO_MM, min_slab * M_TO_MM, parent))
            return report

    counts = {name: 0 for name in live}
    for _distance, t in samples:
        counts[sub_tag_at(t, live_cuts, live)] += 1
    report["names"] = list(live)
    report["cuts"] = [float(value) for value in live_cuts]
    report["cut_mm"] = [round(value * axis.length * M_TO_MM, 1) for value in live_cuts]
    report["cut_height_mm"] = [round(axis.at(value).z * M_TO_MM, 1)
                               for value in live_cuts]
    report["dropped"] = [name for name in names if name not in live]
    report["vertices"] = dict(counts)
    report["says"] = (
        "%s split into %d slab(s) at its own chain's joints (%s), %s vertices%s."
        % (parent, len(live), ", ".join("%.0f mm" % h
                                        for h in report["cut_height_mm"]),
           " / ".join(str(counts[name]) for name in live),
           ("; %d cut(s) dropped because the slab would have been too thin to "
            "measure: %s" % (len(report["dropped"]), ", ".join(report["dropped"])))
           if report["dropped"] else ""))
    return report


def leg_sub_tags(parent):
    """``('Leg.L.thigh', 'Leg.L.shin', 'Leg.L.foot')`` for ``parent='Leg.L'``.

    Per side, from that side's own tag name.  Nothing here assumes a left and a
    right, and nothing assumes there are two of them.
    """
    return tuple("%s.%s" % (parent, suffix) for suffix in LEG_SUB_TAG_SUFFIXES)


def leg_split(axis, leg_points, parent, joints, stations=SPLIT_STATIONS):
    """Where to cut a ``Leg`` tag into thigh, shin and foot.  Never raises.

    The failure this exists to fix
    ------------------------------
    The same one :func:`spine_split` fixes, one tag over.  The contract
    (:func:`~forge.tools.rigforge_skin.legal_bone_sets`) is enforced **per tag**,
    and one ``Leg.R`` tag is a single bucket holding the whole leg chain — both
    thigh segments, both shin segments, the foot and the toe.  Cross-*tag* stray
    influence is 0.000 and it is still wrong: ``DEF-foot.R`` putting weight on
    shin flesh 340 mm away is *legal*, because both are ``Leg.R``.  Measured on
    the werewolf that is **4.67 vertex-weights** shared by ``DEF-foot.R`` and
    ``DEF-thigh.R.001``, and 3.59 more shared by ``DEF-shin.R.001`` and
    ``DEF-thigh.R`` — the leg's own ends holding each other's flesh.

    Where the two cuts come from, and neither is a free choice
    ---------------------------------------------------------
    A leg is not a trunk and its cuts are not a percentile and a girth dip.  A
    leg already **has** its landmarks, fitted: the knee and the ankle are the
    joints of its own deform chain, which the landmark fitter placed at the
    girth minima it measured.  So both cuts are the chain's own segment
    boundaries — the point where the thigh bones stop and the shin bones start,
    and the point where the shin bones stop and the foot starts — projected onto
    this leg's own axis and snapped to the same station grid the girth cap and
    the spine split use.

    That makes the cut a **measurement of the rig that is actually there**
    rather than a fraction of a limb, and it is per side: each leg is cut at its
    own joints, off its own axis, and a figure whose legs are not mirrored is
    cut correctly twice rather than once and copied.  Symmetry is a mode the
    caller chooses, never an assumption this function makes.

    ``joints`` is ``[(label, world point), ...]``, proximal to distal — the knee
    first, the ankle second, as :func:`~forge.tools.rigforge_skin.leg_chain_joints`
    reads them off the chain.

    Returns a report dict of the same shape :func:`spine_split` returns.  A split
    that cannot be measured carries ``refused`` with the reason and no ``cuts``;
    the caller then keeps the one merged ``Leg`` tag, which is the behaviour
    every consumer already has.
    """
    names = leg_sub_tags(parent)
    stations = max(MIN_CUT_SEPARATION + 3, int(stations))
    samples = [axis.closest(point) for point in leg_points]
    profile = _girth_profile(samples, stations)
    report = {
        "parent": parent,
        "names": list(names),
        "stations": stations,
        "axis_mm": [[round(v * M_TO_MM, 1) for v in point] for point in axis.points],
        "axis_length_mm": round(axis.length * M_TO_MM, 1),
        "girth_mm": [round(value * M_TO_MM, 1) for value in (profile or ())],
        "leg_vertices": len(leg_points),
        "joints": [str(label) for label, _point in joints],
        "joint_mm": [[round(v * M_TO_MM, 1) for v in Vector(point)]
                     for _label, point in joints],
    }

    if len(leg_points) < 3 * MIN_SUB_TAG_VERTICES:
        report["refused"] = (
            "the %s tag has %d vertices, and three slabs of at least %d each need %d"
            % (parent, len(leg_points), MIN_SUB_TAG_VERTICES,
               3 * MIN_SUB_TAG_VERTICES))
        return report
    if len(joints) < 2:
        report["refused"] = (
            "this leg's deform chain has %d joint(s) inside it and a thigh / shin / "
            "foot split needs two; there is nothing measuring where its knee and its "
            "ankle are, so the %s tag stays one tag" % (len(joints), parent))
        return report

    # --- both cuts: the chain's own joints, on this leg's own axis ---------
    joint_t = [axis.closest(Vector(point))[1] for _label, point in joints[:2]]
    report["joint_t"] = [round(value, 6) for value in joint_t]
    report["joint_height_mm"] = [round(axis.at(value).z * M_TO_MM, 1)
                                 for value in joint_t]
    if joint_t[0] >= joint_t[1]:
        report["refused"] = (
            "this leg's %s joint projects onto its own axis at t=%.3f and its %s joint "
            "at t=%.3f, so the chain doubles back on itself and there is no slab "
            "between them" % (joints[0][0], joint_t[0], joints[1][0], joint_t[1]))
        return report

    # **Not snapped to the station grid, and that is the one place this departs
    # from the spine's recipe.**  The spine's cuts are snapped because they are
    # *read off* the girth profile, and a cut between two stations is a cut that
    # profile has no opinion about.  A leg's cuts are read off a **joint**,
    # which is a position, and quantising it throws away precision to buy
    # nothing: measured on this werewolf the ankle sits at t=0.849 and the
    # nearest of 17 stations is t=0.875, putting the cut **30 mm below the
    # ankle** and handing the foot bone a slab of shin. The stations stay in the
    # report as corroboration and as the unit the separation rule is in, because
    # "the two cuts must leave a slab between them" is a statement about the
    # grid the girth is sampled on.
    last = stations - 1
    first, second = joint_t[0], joint_t[1]
    stride = 1.0 / float(last)
    if second - first < MIN_CUT_SEPARATION * stride:
        report["refused"] = (
            "this leg's knee sits at t=%.3f and its ankle at t=%.3f, %.1f stations of "
            "%d apart, which leaves no room for a shin %d stations deep between them"
            % (first, second, (second - first) / stride, last, MIN_CUT_SEPARATION))
        return report
    if not (0.0 < first and second < 1.0):
        report["refused"] = (
            "this leg's knee sits at t=%.3f and its ankle at t=%.3f on its own axis, "
            "and a cut at an end of the axis opens a slab with nothing in it"
            % (first, second))
        return report

    how = [("the %s joint of this leg's own deform chain (t=%.3f on its own %.0f mm "
            "axis, %.0f mm up, nearest station %d of %d)"
            % (label, value, axis.length * M_TO_MM, axis.at(value).z * M_TO_MM,
               int(round(value * last)), last))
           for (label, _point), value in zip(joints[:2], joint_t)]

    cuts = [first, second]
    counts = {name: 0 for name in names}
    for _distance, t in samples:
        counts[sub_tag_at(t, cuts, names)] += 1
    thin = sorted(name for name, count in counts.items()
                  if count < MIN_SUB_TAG_VERTICES)
    if thin:
        report["refused"] = (
            "%s would hold %s vertices and a sub-tag under %d cannot be measured, "
            "so the %s stays one tag"
            % (", ".join(thin), ", ".join(str(counts[name]) for name in thin),
               MIN_SUB_TAG_VERTICES, parent))
        report["would_be"] = dict(counts)
        return report

    report["cut_stations"] = [int(round(value * last)) for value in cuts]
    # **Full precision, unlike the spine's.** The spine's cuts are snapped to
    # the station grid, so they are exact fractions and rounding them for the
    # report costs nothing. A leg's cut is a joint's own position, and the bone
    # at that joint has a head at *exactly* that t -- so whether
    # :func:`sub_tags_spanning` sees it as crossing the cut turns on the last
    # few bits. Rounded to six places the cut came out a hair below the head and
    # ``DEF-shin.R`` dropped out of the thigh slab's legal set entirely, leaving
    # the knee with no shin bone on the thigh side of it at all.
    report["cuts"] = [float(value) for value in cuts]
    report["cut_mm"] = [round(value * axis.length * M_TO_MM, 1) for value in cuts]
    report["cut_height_mm"] = [round(axis.at(value).z * M_TO_MM, 1) for value in cuts]
    report["how"] = how
    report["vertices"] = dict(counts)
    report["says"] = (
        "%s split into %s at t=%.3f and t=%.3f along its own %.0f mm axis "
        "(%d / %d / %d vertices), cut at its own %s and %s joints. %s; %s."
        % (parent, ", ".join(names), cuts[0], cuts[1],
           axis.length * M_TO_MM, counts[names[0]], counts[names[1]], counts[names[2]],
           joints[0][0], joints[1][0], how[0], how[1]))
    return report


def torso_sub_tags(bases):
    """``Torso.spine``, ``Torso.spine.002``… — one slab name per spine segment.

    Named after the metarig bone each slab is cut to hold, exactly as a leg's
    slabs are named after thigh / shin / foot, so a legal set reads back to the
    vertebra it belongs to instead of to an index nobody can check.
    """
    return tuple("%s.%s" % (SPLIT_PARENT, base) for base in bases)


def torso_chain_split(axis, torso_points, parent, root, joints, names,
                      stations=SPLIT_STATIONS, min_slab=0.0):
    """The ``Torso`` cut at **every joint of its own spine**, not at three landmarks.

    The failure this exists to fix, measured on ``werewolf-wip-11``
    ---------------------------------------------------------------
    :func:`spine_split` cuts the trunk into pelvis / abdomen / chest, and on
    this figure that leaves ``Torso.chest`` holding **five** spine bones plus
    both shoulders — a 250 mm slab in which ``DEF-spine.001`` and
    ``DEF-spine.005`` are both legal everywhere, because both are the slab's
    *own* bones and a tag's own chain is exempt from the reach.  They duly
    shared 45 vertices while sitting **361 mm apart**, and after every other
    stray pair on the figure had been cleared that one pair was the entire
    remaining 6.37 of stray mass.

    Three slabs was never the claim — it was as fine as the landmarks available
    at the time allowed.  The spine's own joints are landmarks too, they are
    already fitted, and there is one for every bone.  So the trunk is cut the
    way a leg is: one slab per segment, at the segment's own joint, full
    precision.  A vertebra that is too short to own a measurable slab has its
    cut dropped and merges upward — see :func:`chain_split`.
    """
    return chain_split(axis, torso_points, parent, joints, names,
                       stations=stations, merge_thin=True,
                       min_slab=min_slab)


def split_from_torso_cloud(parent, torso_points, root, joints, names, seed=None,
                           stations=SPLIT_STATIONS, min_slab=0.0):
    """``(report, axis | None)`` — :func:`torso_chain_split` over the trunk's cloud.

    ``root`` is the head of the spine chain — the hips — and it orients the
    axis, so ``t=0`` is the pelvis end on a figure modelled to any convention,
    the same way a leg is oriented off its hip.
    """
    if len(torso_points) < 2:
        return ({"parent": parent, "names": list(names),
                 "refused": "there is no %s tag to split" % parent}, None)
    try:
        axis = axis_from_cloud(torso_points, parent, seed=seed, proximal=root)
    except ForgeError as exc:
        return ({"parent": parent, "names": list(names), "refused": str(exc)}, None)
    report = torso_chain_split(axis, torso_points, parent, root, joints, names,
                               stations=stations,
                               min_slab=min_slab)
    return report, (None if report.get("refused") else axis)


def split_from_leg_cloud(parent, leg_points, root, joints, seed=None,
                         stations=SPLIT_STATIONS):
    """``(report, axis | None)`` — :func:`leg_split` over one leg's point cloud.

    The one entry point both sides of the lane use, the way
    :func:`split_from_clouds` is for the spine: the split is **derived** from the
    tag and the chain that exist rather than stored anywhere, so the tagger's
    report and the skinner's contract cannot drift apart.  ``root`` is the head
    of the leg's own chain — the hip — and it is what orients the axis, so
    ``t=0`` is the hip on a figure modelled to any convention.
    """
    names = list(leg_sub_tags(parent))
    if len(leg_points) < 2:
        return ({"parent": parent, "names": names,
                 "refused": "there is no %s tag to split" % parent}, None)
    try:
        axis = axis_from_cloud(leg_points, parent, seed=seed, proximal=root)
    except ForgeError as exc:
        return ({"parent": parent, "names": names, "refused": str(exc)}, None)
    report = leg_split(axis, leg_points, parent, joints, stations=stations)
    return report, (None if report.get("refused") else axis)


def sub_tag_at(t, cuts, names=TORSO_SUB_TAGS):
    """Which sub-tag the axis parameter ``t`` lands in.  Half-open, proximal first.

    ``names`` is the slab naming — :data:`TORSO_SUB_TAGS` by default, or one
    leg's own :func:`leg_sub_tags`, or one per vertebra.  Any number of cuts,
    with ``len(names) == len(cuts) + 1``.  The *rule* is the same however many
    there are and that is the point of the parameter: one cut function, so a
    leg's slabs cannot drift into meaning something different from a torso's.
    """
    for index, cut in enumerate(cuts):
        if (t <= cut) if index == 0 else (t < cut):
            return names[index]
    return names[-1]


def sub_tags_spanning(t_low, t_high, cuts, names=TORSO_SUB_TAGS):
    """Every sub-tag a span along the axis touches — a bone's membership.

    A vertex is a point and lands in one slab; a **bone is a span**, and a bone
    that crosses a cut belongs to the slabs on both sides of it.  That is the
    blend zone's own rule moved up a level: where two regions meet, both sides'
    influence is legal, or the boundary is a crease.  ``DEF-spine.001`` on the
    werewolf runs t=0.256 to 0.505 across a cut at 0.3125, so it moves both the
    pelvis and the abdomen — which is what a lumbar vertebra does.
    """
    low, high = (t_low, t_high) if t_low <= t_high else (t_high, t_low)
    out = []
    for index in range(len(names)):
        under = cuts[index - 1] if index > 0 else None
        over = cuts[index] if index < len(cuts) else None
        if under is None:
            touches = low <= over
        elif over is None:
            touches = high >= under
        else:
            touches = high > under and low < over
        if touches:
            out.append(names[index])
    return tuple(out) or (sub_tag_at(0.5 * (low + high), cuts, names),)


def split_membership(axis, cuts, points):
    """``[sub-tag, ...]`` for ``points``, in order — the vertex-level split."""
    return [sub_tag_at(axis.closest(point)[1], cuts) for point in points]


def split_from_clouds(torso_points, leg_points, seed=None, stations=SPLIT_STATIONS):
    """``(report, axis | None)`` — :func:`spine_split` over two point clouds.

    The one entry point both sides of the lane use, so the tagger's report and
    the skinner's contract cannot drift apart: the split is **derived** from the
    tags that exist rather than stored anywhere, which is also why a mesh
    re-tagged by hand can never be skinned against a stale one.  The report is
    JSON-clean (it goes out over the wire); the axis is handed back beside it
    for the caller that has to classify vertices with it.
    """
    if len(torso_points) < 2:
        return ({"parent": SPLIT_PARENT, "names": list(TORSO_SUB_TAGS),
                 "refused": "there is no %s tag to split" % SPLIT_PARENT}, None)
    try:
        axis = axis_from_cloud(torso_points, "spine", seed=seed)
    except ForgeError as exc:
        return ({"parent": SPLIT_PARENT, "names": list(TORSO_SUB_TAGS),
                 "refused": str(exc)}, None)
    report = spine_split(axis, torso_points, leg_points, stations=stations)
    return report, (None if report.get("refused") else axis)


def split_from_groups(world_points_list, groups, seed=None,
                      stations=SPLIT_STATIONS):
    """:func:`split_from_clouds` from ``{tag: [vertex index]}``."""
    torso = [world_points_list[i] for i in groups.get(SPLIT_PARENT, ())]
    legs = [world_points_list[i]
            for tag in sorted(groups)
            if tag.startswith("Leg")
            for i in groups[tag]]
    return split_from_clouds(torso, legs, seed=seed, stations=stations)


# ---------------------------------------------------------------------------
# is this limb believable?
# ---------------------------------------------------------------------------

def limb_plausible(role, axis, indices, world_points_list, height):
    """``(ok, reasons)`` — does this tag describe the limb it claims to?

    The checks are chosen to be the ones the **landmark fitter** is about to
    apply, so a tag that cannot survive there is caught *here*, where a fallback
    still exists.  In particular the principal-axis check is
    :class:`~forge.tools.rigforge_landmarks.Limb`'s own 60-degree test against
    its anatomical hint, run early and against the detected direction.
    """
    reasons = []
    if axis.length < MIN_LIMB_FRACTION * height:
        reasons.append("its chain is %.0f mm on a %.0f mm figure (under %.0f%%)"
                       % (axis.length * M_TO_MM, height * M_TO_MM,
                          100.0 * MIN_LIMB_FRACTION))
    if len(indices) < MIN_TAG_VERTICES:
        reasons.append("it caught %d vertices, and a 33-station cross-section fit needs "
                       "at least %d" % (len(indices), MIN_TAG_VERTICES))
    if reasons:
        return False, reasons

    cloud = [world_points_list[i] for i in indices]
    centre = Vector((0.0, 0.0, 0.0))
    for point in cloud:
        centre += point
    centre /= float(len(cloud))
    principal = rigforge_landmarks._principal_axis(cloud, centre,
                                                   seed=axis.direction)
    angle = math.degrees(principal.angle(axis.direction, math.pi))
    if angle > 90.0:
        angle = 180.0 - angle  # a principal axis has no sign
    if angle > AXIS_AGREE_DEG:
        reasons.append("the flesh it caught runs %.0f degrees away from the chain that "
                       "built it (limit %.0f), so it is not a tube around that axis"
                       % (angle, AXIS_AGREE_DEG))

    radii = [axis.closest(point)[0] for point in cloud]
    median_radius = _percentile(radii, 0.5) or 0.0
    aspect = axis.length / median_radius if median_radius > 1e-9 else 0.0
    if aspect < MIN_ASPECT:
        reasons.append("it is %.1f times longer than it is thick (needs %.1f), which is "
                       "a blob and not a limb" % (aspect, MIN_ASPECT))
    return (not reasons), reasons


# ---------------------------------------------------------------------------
# the last resort: the box band this module exists to replace
# ---------------------------------------------------------------------------

def box_band_groups(world_points_list, low, high, midplane, character_left):
    """The crude tagger, kept honest by being named.

    Axis-aligned slabs off the bounding box: legs below the hip line, arms above
    the waist and outboard of the torso's own width, head above the neck line,
    torso the rest.  It is *the method whose failure this module exists to fix*
    — a limb that hangs along the body comes out as a wedge with the body's own
    direction — and it is here for exactly one reason: a limb with no detected
    chain and no tag already on the mesh needs something, and something labelled
    ``box`` in the report is better than a tag that pretends to be measured.
    """
    height = max(high.z - low.z, 1e-6)
    hip_z = low.z + 0.48 * height
    neck_z = low.z + 0.82 * height
    waist_z = low.z + 0.45 * height
    half_width = max(abs(high.x - midplane), abs(midplane - low.x), 1e-6)
    groups = {role: [] for role in TAG_ROLES}
    for index, point in enumerate(world_points_list):
        lateral = (point.x - midplane) * character_left
        side = "L" if lateral >= 0.0 else "R"
        if point.z >= neck_z:
            groups["head"].append(index)
        elif point.z < hip_z:
            groups["leg.%s" % side].append(index)
        elif point.z >= waist_z and abs(lateral) > 0.45 * half_width:
            groups["arm.%s" % side].append(index)
        else:
            groups["spine"].append(index)
    return groups


# ---------------------------------------------------------------------------
# the detector, out of process
# ---------------------------------------------------------------------------

def _rigbridge_dir(explicit=None):
    """Where ``detector_runner.py`` lives, or ``None``.

    Never imported from a guessed path: the module is only loaded from a
    directory that actually holds the runner, so a wrong ``FORGE_RIGBRIDGE``
    fails as "not installed" rather than as an import of something else.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get(_RIGBRIDGE_ENV)
    if env and env.strip():
        candidates.append(env.strip())
    here = os.path.dirname(os.path.abspath(__file__))
    for up in range(2, 6):
        root = os.path.normpath(os.path.join(here, *([os.pardir] * up)))
        candidates.append(os.path.join(root, "rigbridge"))
    for candidate in candidates:
        try:
            path = os.path.abspath(candidate)
        except (TypeError, ValueError):
            continue
        if os.path.isfile(os.path.join(path, _RUNNER_MODULE + ".py")):
            return path
    return None


def _load_runner(explicit=None):
    """Import ``detector_runner`` by path, or return ``None``.

    The runner is stdlib-only and imports nothing from UniRig — that is why it
    is safe to pull into Blender's interpreter at all.  The detector itself
    stays behind a subprocess boundary, which is where the licence boundary is
    too (``rigbridge/README.md``).
    """
    directory = _rigbridge_dir(explicit)
    if directory is None:
        return None, None
    if directory not in sys.path:
        sys.path.insert(0, directory)
    try:
        module = __import__(_RUNNER_MODULE)
    except Exception:  # noqa: BLE001 - a broken runner is "not installed"
        return None, directory
    return module, directory


def detector_status(explicit=None, check_gpu=True):
    """Is joint detection available on this machine?  Never raises."""
    module, directory = _load_runner(explicit)
    if module is None:
        return {
            "installed": False,
            "reason": "runner_missing",
            "rigbridge": directory,
            "says": ("The detector runner (rigbridge/detector_runner.py) was not found, "
                     "so joints cannot be detected here. Set %s to the rigbridge "
                     "directory if this add-on was installed away from the repo."
                     % _RIGBRIDGE_ENV),
        }
    try:
        report = module.diagnose(check_gpu=check_gpu)
    except Exception as exc:  # noqa: BLE001
        return {"installed": False, "reason": "runner_missing", "rigbridge": directory,
                "says": "The detector runner could not be asked (%s: %s)."
                        % (type(exc).__name__, exc)}
    report = dict(report)
    report["rigbridge"] = directory
    return report


def _export_for_detector(obj, path):
    """A world-space, modifier-free copy of ``obj`` as a ``.glb``.

    The transform is **baked into the mesh data** and the copy is exported with
    an identity object matrix, which makes the detector's answer arrive in world
    space with nothing left to guess.  ``detect_joints.py`` documents its output
    as ``mesh_local`` because that is true of the file it was given; by handing
    it a file whose local frame *is* the world frame, the ambiguity is removed
    at the producer rather than papered over at the consumer.

    Shape keys and modifiers are dropped on the copy: the detector should see
    the rest shape the tags will be painted on, not a posed or subdivided one.
    """
    mesh = obj.data.copy()
    mesh.transform(obj.matrix_world)
    temp = bpy.data.objects.new("_forge_autotag_export", mesh)
    get_scene().collection.objects.link(temp)
    # ``link`` only tags the depsgraph; until the view layer is re-evaluated the
    # object is absent from ``view_layer.objects`` and every operator refuses to
    # touch it — including the exporter.
    refresh_view_layer()
    try:
        try:
            temp.shape_key_clear()
        except (AttributeError, RuntimeError):
            pass
        with selection([temp], temp):
            status = bpy.ops.export_scene.gltf(**op_kwargs(
                bpy.ops.export_scene.gltf, {
                    "filepath": path,
                    "export_format": "GLB",
                    "use_selection": True,
                    "export_apply": False,
                    "export_yup": True,
                    # The detector wants shape; textures would only make the
                    # file bigger and the extraction slower.
                    "export_materials": "NONE",
                    "export_texcoords": False,
                    "export_normals": True,
                    "export_animations": False,
                    "export_skins": False,
                }))
        if "FINISHED" not in status:
            raise ForgeError("Exporting %r for the joint detector returned %s."
                             % (obj.name, ", ".join(sorted(status)) or "nothing"))
    finally:
        bpy.data.objects.remove(temp, do_unlink=True)
        try:
            bpy.data.meshes.remove(mesh)
        except (ReferenceError, RuntimeError):
            pass
        refresh_view_layer()
    return path


def detect_joints_for(obj, cache=True, seed=12345, refresh=False, rigbridge=None,
                      timeout=None, keep_export=None):
    """Run the detector on ``obj`` and return the runner's result dict.

    Never raises for a missing install, a busy GPU or a detector that died: the
    caller is a tagging command and every one of those is a *fallback*, not a
    failure of the command.
    """
    module, directory = _load_runner(rigbridge)
    if module is None:
        status = detector_status(rigbridge)
        return dict(status, ok=False, joints=None,
                    reason=status.get("reason") or "runner_missing")
    import tempfile

    workspace = keep_export or tempfile.mkdtemp(prefix="forge_autotag_")
    path = os.path.join(workspace, "%s.glb" % _safe_name(obj.name))
    try:
        _export_for_detector(obj, path)
    except Exception as exc:  # noqa: BLE001 - an export failure is a fallback too
        return {"ok": False, "reason": "export_failed", "joints": None,
                "says": "The mesh could not be exported for the detector (%s: %s), so "
                        "the detector did not run." % (type(exc).__name__, exc)}
    try:
        kwargs = {"seed": int(seed), "use_cache": bool(cache), "refresh": bool(refresh)}
        if timeout:
            kwargs["timeout"] = float(timeout)
        result = module.detect(path, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the runner promises not to raise; belt and braces
        result = {"ok": False, "reason": "detector_failed", "joints": None,
                  "says": "The detector runner raised (%s: %s)."
                          % (type(exc).__name__, exc)}
    finally:
        if keep_export is None:
            import shutil

            shutil.rmtree(workspace, ignore_errors=True)
    result = dict(result)
    result["rigbridge"] = directory
    result["export"] = path
    if result.get("ok") and isinstance(result.get("joints"), dict):
        # The file we handed the detector had its world transform baked in and an
        # identity object matrix, so the frame it calls ``mesh_local`` **is** the
        # world frame. Saying so here is the difference between a correct fit and
        # a whole skeleton offset by the object's own translation — measured on
        # the werewolf, whose object sits 933 mm up, which would have put every
        # joint outside the mesh and failed the frame gate.
        document = dict(result["joints"])
        frame = dict(document.get("frame") or {})
        frame["space"] = "world"
        document["frame"] = frame
        document["notes"] = list(document.get("notes") or []) + [
            "rigforge_autotag exported this mesh with its world transform baked in "
            "and an identity object matrix, so these coordinates are world space"]
        result["joints"] = document
    return result


def _safe_name(name):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(name))[:60]


# ---------------------------------------------------------------------------
# writing the tags
# ---------------------------------------------------------------------------

def _existing_tag_vertices(obj):
    """``{tag display name: set(vertex index)}`` for the tags already on the mesh."""
    out = {}
    for group in rigforge.tag_groups(obj):
        index = group.index
        members = set()
        for vertex in obj.data.vertices:
            for entry in vertex.groups:
                if entry.group == index and entry.weight > 0.0:
                    members.add(vertex.index)
                    break
        out[rigforge.tag_display_name(group.name)] = members
    return out


def write_tag(obj, tag, indices, replace=True):
    """Assign ``indices`` to ``tag_<tag>`` at weight 1.0, replacing what was there."""
    name = rigforge.tag_group_name(tag)
    group = obj.vertex_groups.get(name)
    created = group is None
    if group is None:
        group = obj.vertex_groups.new(name=name)
    elif replace:
        existing = [v.index for v in obj.data.vertices]
        group.remove(existing)
    if indices:
        group.add(sorted(set(int(i) for i in indices)), 1.0, "REPLACE")
    return group, created


def snapshot_tags(obj):
    """Everything an auto-tag pass is about to overwrite, so it can be undone.

    A rebuild that does not help has to be *taken back*, not merely reported —
    the tag fit downstream will happily produce a rig from worse tags and say
    nothing.  Measured on the Phase 3 blob sculpt: the detector's tags left the
    landmark fit still refusing and moved ``thigh.L``'s head from 726 mm to
    252 mm, which is a worse rig arrived at more expensively.
    """
    return {
        "tags": _existing_tag_vertices(obj),
        "axes": obj.get(PROP_TAG_AXES),
        "sources": obj.get(PROP_TAG_SOURCE),
    }


def restore_tags(obj, snapshot):
    """Put back exactly what :func:`snapshot_tags` recorded, tag groups and all."""
    with object_mode():
        wanted = snapshot["tags"]
        for group in list(rigforge.tag_groups(obj)):
            display = rigforge.tag_display_name(group.name)
            if display not in wanted:
                obj.vertex_groups.remove(group)
        for tag, members in wanted.items():
            write_tag(obj, tag, sorted(members), replace=True)
        for key, value in ((PROP_TAG_AXES, snapshot["axes"]),
                           (PROP_TAG_SOURCE, snapshot["sources"])):
            if value is None:
                if key in obj.keys():
                    del obj[key]
            else:
                obj[key] = value
        obj.data.update()
    refresh_view_layer()


def stored_axis_hints(obj):
    """The limb directions a previous auto-tag measured, if they still apply.

    Stamped with the mesh's own world bounding box: a mesh that has since been
    rotated onto the convention, symmetrized or rebuilt is a different mesh, and
    fitting it to a hint measured on the old one would be worse than having no
    hint at all.
    """
    raw = obj.get(PROP_TAG_AXES)
    if not raw:
        return None, None
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return None, "the stored limb axes could not be read"
    axes = data.get("axes")
    stamp = data.get("bbox_mm")
    if not isinstance(axes, dict) or not isinstance(stamp, list):
        return None, "the stored limb axes are not in the expected shape"
    points = rigforge_landmarks.world_points(obj)
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    current = [round(v * M_TO_MM, 1) for v in tuple(low) + tuple(high)]
    if len(stamp) != 6 or any(abs(a - b) > 1.0 for a, b in zip(stamp, current)):
        return None, ("the stored limb axes were measured on a different shape of %r "
                      "(its bounding box has moved), so they were ignored" % obj.name)
    out = {}
    for role, vector in axes.items():
        if isinstance(vector, (list, tuple)) and len(vector) == 3:
            try:
                candidate = Vector((float(vector[0]), float(vector[1]), float(vector[2])))
            except (TypeError, ValueError):
                continue
            if candidate.length > 1e-6:
                out[str(role)] = candidate.normalized()
    return (out or None), None


def _store_axis_hints(obj, axes, points):
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    payload = {
        "axes": {role: [round(v, 6) for v in axis.direction]
                 for role, axis in axes.items() if axis.is_limb},
        "bbox_mm": [round(v * M_TO_MM, 1) for v in tuple(low) + tuple(high)],
    }
    obj[PROP_TAG_AXES] = json.dumps(payload)


# ---------------------------------------------------------------------------
# the whole pass
# ---------------------------------------------------------------------------

def auto_tag(obj, joints=None, midplane=None, character_left=None,
             radius_factor=DEFAULT_RADIUS_FACTOR, apply=True, replace=True,
             warnings=None, detector=None):
    """Build axis-following tags for ``obj``.  Returns the report.

    ``joints`` is a ``forge.joints/1`` document (already in whatever frame it
    declares).  With none, only the fallback ladder runs — which is still a
    useful answer, and says so.

    Nothing here raises for a limb it cannot build: every limb has a ladder
    beneath it, and the report names the rung.
    """
    warnings = warnings if warnings is not None else []
    points = rigforge_landmarks.world_points(obj)
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    height = max(high.z - low.z, 1e-6)

    if midplane is None:
        midplane = rigforge_landmarks.measure_symmetry(obj)["midplane"]
    if character_left is None:
        orientation = rigforge_landmarks.detect_orientation(obj)
        character_left = (rigforge_landmarks._left_sign(orientation["faces"])
                          if orientation.get("confident") else 1.0)

    existing = _existing_tag_vertices(obj)
    report = {
        "object": obj.name,
        "midplane_mm": round(midplane * M_TO_MM, 3),
        "character_left": "+X" if character_left > 0 else "-X",
        "height_mm": round(height * M_TO_MM, 1),
        "radius_factor": round(float(radius_factor), 3),
        "detector": detector,
        "roles": {},
        "axes": {},
        "unplaced_chains": [],
        "girth": {},
        "tags": {},
        "sources": {},
    }

    axis_groups = {}
    axes = {}
    if joints:
        hints = rigforge_joints.JointHints(joints, obj, weight=0.0, path="(detector)",
                                           warnings=warnings)
        report["joint_frame"] = {
            "joints": hints.count,
            "inside_bbox": hints.inside,
            "inside_fraction": round(hints.inside_fraction, 3),
            "enabled": hints.enabled,
        }
        if not hints.enabled:
            warnings.append(
                "The detected joints were rejected before a tag was built: only %d of %d "
                "land inside %r's own bounding box, so the detector's frame and this "
                "mesh's do not agree. Every tag below came from the fallback ladder."
                % (hints.inside, hints.count, obj.name))
        else:
            joint_points = list(hints.points)
            parents = list(hints.parents)
            segments, children, roots = segments_of(parents)
            roles, why, unplaced = classify_segments(
                segments, children, joint_points, low, high, midplane, character_left,
                root=(roots[0] if roots else None))
            report["roles"] = {role: {"joints": list(chain), "why": why.get(role, "")}
                               for role, chain in sorted(roles.items())}
            report["unplaced_chains"] = unplaced
            report["skeleton"] = {"joints": len(joint_points),
                                  "segments": len(segments), "roots": roots}
            if roles:
                axes = build_axes(roles, children, joint_points, points)
                axis_groups, stats = assign_vertices(points, axes, radius_factor)
                report["axes"] = {role: axis.as_dict() for role, axis in sorted(axes.items())}
                report["girth"] = stats
            else:
                warnings.append(
                    "The detected skeleton could not be read as a body: no chain left its "
                    "root going upwards, so nothing could be called a spine. The fallback "
                    "ladder built every tag.")

    # --- the ladder, per tag ---------------------------------------------
    box_groups = None
    final = {}
    sources = {}
    notes = {}
    for role, tag in sorted(TAG_ROLES.items()):
        indices = axis_groups.get(role)
        if indices and role in LIMB_ROLES:
            ok, reasons = limb_plausible(role, axes[role], indices, points, height)
            if not ok:
                notes[tag] = ("the detected chain was refused: %s" % "; ".join(reasons))
                indices = None
        elif indices is not None and role not in LIMB_ROLES and len(indices) < 3:
            notes[tag] = "the detected chain caught %d vertices" % len(indices)
            indices = None
        elif not indices and role not in axes:
            notes[tag] = (
                "no chain in the detected skeleton could be read as this part"
                if joints else "no skeleton was detected")
        if indices:
            final[tag] = indices
            sources[tag] = "unirig"
            continue
        if tag in existing and len(existing[tag]) >= 3:
            final[tag] = sorted(existing[tag])
            sources[tag] = "hand"
            continue
        if box_groups is None:
            box_groups = box_band_groups(points, low, high, midplane, character_left)
        final[tag] = box_groups.get(role, [])
        sources[tag] = "box"

    # A limb that fell back while its axis siblings did not leaves the torso
    # holding flesh the limb should own. Say so rather than let the next stage
    # find out by measuring a fat torso.
    fallen = sorted(tag for tag, source in sources.items()
                    if source != "unirig" and tag not in ("Head", "Torso"))
    if fallen and any(source == "unirig" for source in sources.values()):
        warnings.append(
            "These limbs were not built from the detected skeleton and fell back to %s: "
            "%s. The tags around them were still built from it, so the two kinds of tag "
            "meet somewhere neither of them measured."
            % (", ".join(sorted({sources[t] for t in fallen})), ", ".join(fallen)))

    for tag in sorted(final):
        report["tags"][tag] = {
            "vertices": len(final[tag]),
            "source": sources[tag],
            "why": notes.get(tag, ""),
        }
    report["sources"] = dict(sources)
    report["applied"] = bool(apply)

    # The torso's own split, measured and reported but **never written**: it is
    # a derived view of the one Torso tag, so nothing downstream that expects
    # six tags sees seven. See spine_split.
    report["sub_tags"], _split_axis = split_from_groups(
        points, final, seed=(axes["spine"].direction if "spine" in axes else None))
    if report["sub_tags"].get("refused"):
        warnings.append(
            "The Torso tag was left whole: %s. Its bones stay in one legal set, "
            "which is the behaviour before this split existed."
            % report["sub_tags"]["refused"])

    if apply:
        with object_mode():
            for tag in sorted(final):
                write_tag(obj, tag, final[tag], replace=replace)
            obj.data.update()
            obj[PROP_TAG_SOURCE] = json.dumps(sources)
            if axes:
                _store_axis_hints(obj, axes, points)
            elif PROP_TAG_AXES in obj.keys():
                del obj[PROP_TAG_AXES]
        refresh_view_layer()

    report["axis_hints"] = {role: [round(v, 5) for v in axes[role].direction]
                            for role in sorted(axes) if axes[role].is_limb
                            and sources.get(TAG_ROLES[role]) == "unirig"}
    report["warnings"] = warnings
    report["says"] = _autotag_sentence(report)
    return report


def _autotag_sentence(report):
    counts = {}
    for source in report["sources"].values():
        counts[source] = counts.get(source, 0) + 1
    order = ("unirig", "hand", "box")
    parts = ["%d %s" % (counts[key], key) for key in order if key in counts]
    lines = ["%s: %s tag(s) — %s."
             % (report["object"], len(report["sources"]), ", ".join(parts))]
    limbs = [tag for tag in ("Arm.L", "Arm.R", "Leg.L", "Leg.R")
             if report["sources"].get(tag) == "unirig"]
    if limbs:
        lines.append("%s follow their own detected axis rather than a box band."
                     % ", ".join(limbs))
    split = report.get("sub_tags") or {}
    if split.get("cuts"):
        lines.append("The Torso reads as %s along its own spine (a derived view; the "
                     "tag itself is still one group)."
                     % ", ".join("%s %d" % (name, split["vertices"][name])
                                 for name in split["names"]))
    elif split.get("refused"):
        lines.append("The Torso stays one tag: %s." % split["refused"])
    detector = report.get("detector") or {}
    if detector.get("cached"):
        lines.append("The detection came from the cache; no GPU work was done.")
    return " ".join(lines)


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

@command("rigforge_autotag")
def cmd_rigforge_autotag(params):
    """Tag a character's limbs by following their own axes, not the world's.

    ``rigforge_autotag {"object"?, "action"?: "report"|"apply", "source"?:
    "auto"|"detector"|"box", "joints_file"?, "radius_factor"?, "seed"?,
    "refresh"?, "replace"?, "rigbridge"?, "timeout"?}``

    ``report`` measures and writes nothing; ``apply`` (the default) writes the
    six ``tag_*`` vertex groups and records, on the object, where each one came
    from and which direction each limb runs in.

    ``source: "detector"`` refuses rather than falling back, which is what a
    test wants and what an artist debugging an install wants.  ``source: "box"``
    skips the detector entirely and builds the crude bands — useful only to see
    what the detector is being compared against.
    """
    obj = resolve_object(params, mesh_only=True)
    started = time.monotonic()
    warnings = []
    action = get_choice(params, "action", {"REPORT": "report", "APPLY": "apply"}, "apply")
    source = get_choice(params, "source",
                        {"AUTO": "auto", "DETECTOR": "detector", "UNIRIG": "detector",
                         "BOX": "box"}, "auto")
    radius_factor = get_float(params, "radius_factor", DEFAULT_RADIUS_FACTOR,
                              minimum=1.0, maximum=6.0)
    seed = get_int(params, "seed", 12345, minimum=0, maximum=2 ** 31 - 1)
    refresh = get_bool(params, "refresh", False)
    replace = get_bool(params, "replace", True)
    timeout = params.get("timeout")

    joints = None
    detector = None
    joints_path = params.get("joints_file")
    if isinstance(joints_path, str) and joints_path.strip():
        joints_path = resolve_path(joints_path.strip())
        joints = rigforge_joints.load_joints(joints_path)
        detector = {"ok": True, "reason": "ok", "cached": None, "source": "file",
                    "path": joints_path,
                    "says": "Joints were read from %s; the detector did not run."
                            % joints_path}
    elif source != "box":
        result = detect_joints_for(
            obj, seed=seed, refresh=refresh, rigbridge=params.get("rigbridge"),
            timeout=timeout)
        detector = {key: value for key, value in result.items() if key != "joints"}
        if result.get("ok"):
            joints = result["joints"]
        elif source == "detector":
            raise ForgeError(
                "source='detector' was asked for and the detector could not run: %s"
                % (result.get("says") or result.get("reason")))
        else:
            warnings.append(
                "Joint detection did not run (%s), so the tags came from the fallback "
                "ladder: %s" % (result.get("reason"), result.get("says") or ""))

    report = auto_tag(obj, joints=joints,
                      midplane=(get_float(params, "midplane", None)
                                if params.get("midplane") is not None else None),
                      character_left=(get_float(params, "character_left", None)
                                      if params.get("character_left") is not None
                                      else None),
                      radius_factor=radius_factor, apply=(action == "apply"),
                      replace=replace, warnings=warnings, detector=detector)
    report["action"] = action
    report["source_requested"] = source
    report["seconds"] = round(time.monotonic() - started, 3)
    return report
