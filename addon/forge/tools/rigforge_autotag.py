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
    "Axis",
    "segments_of",
    "classify_segments",
    "build_axes",
    "assign_vertices",
    "limb_plausible",
    "box_band_groups",
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


def _girth_profile(samples, stations=GIRTH_STATIONS):
    """Median radial distance at each station along an axis, gaps filled.

    The **median**, not a high percentile, and that is the load-bearing choice.
    The set being measured still contains whatever the axis over-claimed — the
    ribs an arm is hanging beside — and a p90 of a contaminated set measures the
    contamination.  A median survives it as long as the limb's own flesh is the
    majority of its own neighbourhood, which for a cylinder around its own
    centreline it always is.
    """
    buckets = [[] for _ in range(stations)]
    for distance, t in samples:
        index = max(0, min(stations - 1, int(round(t * (stations - 1)))))
        buckets[index].append(distance)
    profile = [_percentile(bucket, 0.5) if len(bucket) >= 4 else None
               for bucket in buckets]
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
    return _smooth_profile(filled)


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
            profiles[role] = _girth_profile(samples)
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
