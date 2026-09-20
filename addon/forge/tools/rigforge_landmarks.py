"""The human rigger's workflow, codified: orient, symmetrize, landmark, mirror, inspect.

Why this module exists
----------------------
An audit of a live character measured three defects that **no human rigger
using Blender's own tools could have produced**:

1. left/right bone asymmetry of **6-24 mm on every limb** (``DEF-foot`` 23.5 mm,
   shin 18.8, hand 17.1) — an artist who places one side and presses
   ``Armature > Symmetrize`` gets exactly **0.0**;
2. bones sitting off the limb's centreline, visible in a render;
3. worst of all, **side names mirrored**: ``DEF-shin.L`` at ``x = -191 mm``
   while the character's left leg centres at ``x = +182 mm``.  Automatic weights
   hid it (they bind by proximity, so the wrongly-named bone still drove the
   flesh next to it), but X-mirror tooling, mocap retargeting and Godot's
   humanoid mapping all read the ``.L``/``.R`` suffix and all break silently.

Every one of those is a *solved problem* in Blender.  The fix is therefore not a
better guess; it is to stop guessing and **replicate the steps a rigger takes**,
in order, with a number reported at each one:

1. **Orientation gate** (:func:`detect_orientation`, :func:`orient_to_convention`)
   — which way does this character face?  Measured from the geometry, not
   assumed: a body is **lopsided front to back and symmetric side to side**, so
   the horizontal axis whose bottom and top slabs are lopsided — **the toes and
   the nose** — is the facing axis, and the other one is left/right.  The mirror
   residual about both axes is measured as a corroborating second opinion and
   reported, never as a veto.  The Blender convention is *face* ``-Y``, so ``+X``
   is the character's **left**.  A mesh that measurably faces the wrong way is
   rotated by a whole multiple of 90 degrees; one with no measurable front (a
   blob, a barrel) is **assumed** to be on the convention and told so, loudly,
   because refusing would block work that has nothing to fix.
2. **Symmetrize first** (:func:`measure_symmetry`, :func:`symmetrize_mesh`) —
   the mesh is made X-symmetric *before* a bone is placed, about the measured
   midplane, and the residual it removed is reported in millimetres.  An
   **already-unwrapped** mesh is measured and left alone (rewriting half of it
   would mirror that half's UVs and orphan the normal map baked against them):
   the rig is still authored on one side and mirrored, and the warning says how
   far that leaves the right side's bones from the right side's flesh.  Fitting
   two independent sides happens only when the caller passes ``symmetry: false``
   (the artist's deliberately asymmetric character is legitimate; a generated
   accident is not, and the two are told apart by *the artist saying so*).
   Sided tags are **re-derived from the geometry**
   (:func:`retag_sides_from_geometry`), which is where defect (3) dies: the tag
   at ``x > 0`` is the ``.L`` tag, whatever it was called before.
3. **Landmarks from geometry, one side only** (:class:`Limb`,
   :func:`biped_landmarks`) — every joint is a **cross-section centroid** of the
   mesh itself.  A limb is sliced perpendicular to its own centreline at 33
   stations; the knee and the elbow are the **minimum-girth station inside the
   anatomical band** (the crease), the hip and the shoulder are the junction
   where the girth explodes into the torso, the wrist and the ankle are the
   distal girth minimum, and the spine sits **on the midplane by construction**.
   Only the character-left limbs are authored.  Two safety gates hand the job
   back to the old tag fit rather than lie about it: a principal axis more than
   60 degrees off the anatomical hint is the point cloud's shape talking rather
   than the limb's, and three joints that do not march down the limb are not
   landmarks.
4. **X-mirror to the right** (:func:`mirror_edit_bones`) — an exact reflection of
   every ``.L`` bone onto its ``.R`` twin, heads, tails and rolls.  Asymmetry is
   **0.0 by construction**, and a test pins it there.
5. **Inspect the maps like a human would** — :func:`influence_overlap` (the
   bone-to-bone overlap matrix: *what does one movement do to another*) and
   :func:`render_weight_maps` (per-bone blue-to-red weight renders), plus
   :func:`render_skeleton_echo`, the skeleton drawn over the ghosted body that
   the artist eyeballs before anything is skinned.

And the same math, inverted, is the **self-check**: :func:`bone_centering`,
:func:`bone_asymmetry` and :func:`side_naming` are what ``rig_check`` reports on
every run, so the three defects above can never again be something the owner has
to find by squinting at a render.

*Optional assist, queued, not implemented here:* skeleton-annotated **reference
images** (the artist draws the skeleton over the reference), whose 2D landmarks
would be projected onto the mesh.  That is for references where the geometry
genuinely cannot say where a joint is — baggy clothing, fur, armour.  Everything
in this module reads the mesh.

Stdlib + ``bpy``/``bmesh``/``mathutils`` only.  Nothing downloads, nothing opens
a window, everything runs under ``blender --background``.
"""

import math
import os
import re
import time

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from . import rigforge
from .common import (
    M_TO_MM,
    PREVIEW_MARGIN,
    PREVIEW_MAX_RESOLUTION,
    PREVIEW_MIN_RESOLUTION,
    PREVIEW_VIEWS,
    _preview_configure_workbench,
    _preview_frame,
    _preview_restore,
    _preview_snapshot,
    active_only,
    get_bool,
    get_choice,
    get_float,
    get_int,
    get_scene,
    object_mode,
    refresh_view_layer,
    resolve_object,
    resolve_path,
)
from .registry import ForgeError, command

__all__ = [
    "CONVENTION",
    "SYMMETRY_TOLERANCE_MM",
    "CENTERING_THRESHOLDS",
    "ASYMMETRY_THRESHOLDS",
    "OVERLAP_THRESHOLDS",
    "world_points",
    "mirror_residual",
    "find_midplane",
    "detect_orientation",
    "orient_to_convention",
    "measure_symmetry",
    "symmetrize_mesh",
    "retag_sides_from_geometry",
    "Limb",
    "PREBEND_FRACTION",
    "PREBEND_DIRECTION",
    "STANCE_SPAN_FRACTION",
    "stance_flex",
    "facing_vector",
    "prebend_joint",
    "foot_landmarks",
    "hand_landmarks",
    "hand_axis",
    "hand_region",
    "HAND_AXIS_TOLERANCE_DEG",
    "HAND_KNUCKLE_FRACTION",
    "FOOT_ANKLE_COLLAPSE_FRACTION",
    "FOOT_TOE_LIFT_FRACTION",
    "FOOT_TIP_MARGIN_MM",
    "biped_landmarks",
    "mirror_edit_bones",
    "bone_centering",
    "bone_asymmetry",
    "side_naming",
    "influence_overlap",
    "render_skeleton_echo",
    "render_weight_maps",
]


# ---------------------------------------------------------------------------
# the convention
# ---------------------------------------------------------------------------

#: Blender's own character convention, which Rigify, every mocap retargeter and
#: Godot's humanoid mapping all assume: the character faces **-Y**, up is +Z,
#: and therefore the character's **left** hand is at **+X**.  (Stand behind the
#: character looking the way it looks: its left is your left, which is +X.)
CONVENTION = {
    "faces": "-Y",
    "up": "+Z",
    "character_left": "+X",
    "why": ("Rigify's templates, glTF/Godot humanoid mapping and every mocap "
            "retargeter read the .L/.R suffix and assume this frame."),
}

#: Mean mirror residual (mm) at or below which a mesh counts as X-symmetric.
#: Half a millimetre on a character: below the vertex noise of any real sculpt,
#: far below the 15 mm a generated asymmetry lands at.
SYMMETRY_TOLERANCE_MM = 0.5

#: How far the left/right axis must beat the other horizontal axis before the
#: orientation gate believes it: 1.5x the residual **and** a millimetre, so a
#: genuinely round object (a ball, a barrel) is called ambiguous instead of
#: being rotated on noise.
ORIENT_MARGIN_FACTOR = 1.5
ORIENT_MARGIN_MM = 1.0

#: Toe/nose protrusion must be at least this fraction of the body's own depth
#: before it is read as "this is the front".
ORIENT_PROTRUSION_FRACTION = 0.02

#: Bone-centering bands.  **Credibility tier: heuristic (proxy).**  Measured as
#: a percentage of the limb's own cross-section radius at that station, so a
#: mouse and a giant are judged the same way; the millimetres are always
#: reported next to it.
CENTERING_THRESHOLDS = {"offset_pct_of_radius": {"ok": 35.0, "attention": 60.0}}

#: L/R bone asymmetry bands, in millimetres.  A human X-mirror gives 0.0, so
#: "ok" is float dust and anything a person could see is a failure.
ASYMMETRY_THRESHOLDS = {"asymmetry_mm": {"ok": 0.5, "attention": 5.0}}

#: Influence-overlap bands for **stray** pairs (bones three or more joints apart
#: in the skeleton that nevertheless share vertices).  Mass is in vertex-weight
#: units: 1.0 is one whole vertex owned jointly.
OVERLAP_THRESHOLDS = {"stray_mass": {"ok": 0.05, "attention": 1.0}}

#: Fewer edge crossings than this and the "section" is a sliver of something,
#: not a limb's cross-section: the station is skipped rather than measured
#: wrongly.  Six is a triangle prism's worth.
MIN_SECTION_POINTS = 6

#: Bones whose names mean "a limb", for the gates that only judge limbs.
LIMB_BONE_RE = re.compile(
    r"(thigh|shin|calf|foot|toe|upper_?arm|forearm|hand|shoulder|clavicle)",
    re.IGNORECASE)

#: The bones the centering gate actually **judges**: the four long bones, whose
#: flesh really is a tube with a centreline a cross-section can find.  A hand, a
#: toe, a clavicle, a pelvis bone — or a **foot**, which correctly runs along the
#: top of its flesh rather than through the middle of it — has no such
#: centreline, and a threshold on them would fail correct rigs and teach
#: everyone to ignore the gate.  They are still measured and reported.
CENTERED_BONE_RE = re.compile(r"(thigh|shin|calf|upper_?arm|forearm)",
                              re.IGNORECASE)

#: The two-bone chains whose **rest flex** the centering gate expects to see,
#: proximal stem first.  A limb that rests flexed (:func:`stance_flex`) puts its
#: own bones off the straight line through its flesh by a distance the chain
#: itself states, and a mesh sculpted standing straight has its centreline on
#: that straight line.  Neither is a defect; the difference between them is
#: arithmetic, and :func:`bone_centering` subtracts it rather than failing the
#: rig for carrying the stance the pipeline just authored.
CENTERING_FLEX_CHAINS = (
    ("front_thigh", "front_shin"),
    ("upper_arm", "forearm"),
    ("thigh", "shin"),
    ("thigh", "calf"),
)

#: The axis a limb's rest flex may **not** use.  A knee folds forward and an
#: elbow backward — in the plane of the body's facing, never across it — and
#: this module orients every character so that across is ``X``
#: (:data:`CONVENTION`).  So the flex allowance is flattened onto the sagittal
#: plane before anything is forgiven: a joint dragged sideways out of its limb
#: earns nothing, which is the defect :func:`bone_centering` exists for.
CENTERING_FLEX_FORBIDDEN_AXIS = Vector((1.0, 0.0, 0.0))

#: A cross-section has to go most of the way round the bone to have a
#: meaningful centre.  A gap this wide between two neighbouring crossings means
#: the plane caught an open patch — the end of a limb, a flat slab — and the
#: station is skipped instead of reporting the centroid of half a ring.
MAX_SECTION_GAP_DEG = 120.0

#: **The foot.**  Everything below exists because the foot chain was the one
#: part of the skeleton nothing measured: ``biped_landmarks`` stopped at the
#: ankle and the ``foot``/``toe`` bones were **dragged along from Rigify's
#: template** by whatever the shin's tail moved.  On the werewolf that left
#: ``DEF-toe.L``'s tail **80.1 mm short of the boot's forward extent**, so the
#: character's toes end inside its shoe and the foot roll pivots about a point
#: that is not the ball of anything.
#:
#: The sole slab: the bottom of the foot, as a fraction of the foot's own height
#: (ground to ankle).  Thin enough to be the sole rather than the whole shoe,
#: thick enough to survive a mesh whose sole is one edge loop.
FOOT_SOLE_FRACTION = 0.30

#: How many stations the sole is cut into, front to back, to find the ball.
FOOT_STATIONS = 17

#: **Where the ball is**: walking forward from the sole's widest station, the
#: first station whose width has fallen below this fraction of that maximum.
#: That is the definition a bootmaker uses — the ball is where the last is
#: widest and the toe box begins to taper — and it is measurable on any shoe.
FOOT_TAPER_FRACTION = 0.85

#: How far back from the mesh's own forward extent the toe bone stops, in
#: millimetres.  A bone that runs to the exact last vertex pokes out of the
#: surface the moment the toe rolls; a small honest margin does not, and the
#: report quotes it rather than hiding it inside a fudge.
FOOT_TIP_MARGIN_MM = 12.0

#: The ankle is searched for between the ground and this fraction of the way to
#: the knee.  Above that is calf, which also tapers, and a search that ran the
#: whole shin would sometimes find it.
ANKLE_SEARCH_FRACTION = 0.45

#: A foot has to be at least this many times longer, front to back, than the
#: ankle above it before the leg tag is treated as containing one at all.  A tag
#: that stops at the ankle gets no foot landmarks and says so, rather than
#: inventing a boot out of the last two edge loops.
FOOT_LENGTH_FACTOR = 1.35

#: ...and it has to **point forward**: the sole must reach this many times
#: further in front of the **leg column** than behind it — the column's own
#: extent, midway between its front and its back, not the ankle joint's mass
#: centroid, because lopsidedness is a property of the shape and not of where
#: the retopo put its vertices.  Length alone is not enough and
#: that was found by being wrong first — a bare leg's end cap flares out to
#: 1.5x the ankle's own width and read as a 126 mm foot.  A cone's cap is
#: centred under the leg; a foot is not, and that asymmetry is the whole
#: difference between a foot and the bottom of a tube.
FOOT_FORWARD_FACTOR = 1.6

#: **How high the ankle is.**  The rule above finds *which* height by taking the
#: station whose front-to-back length is smallest.  An ``argmin`` over a profile
#: that is flat above the foot wanders, and on the live werewolf it wandered into
#: a 13-vertex slab at ``z = 191 mm`` whose length read 95.7 mm against its
#: neighbours' 150: ``DEF-foot.L``'s head landed at **z = 173 mm** on a 1.88 m
#: character whose foot mass tops out at 110 — the ankle joint sat a hand's width
#: up the shin, and the whole foot chain hung off it.
#:
#: What a person reads instead is a **step**, not a minimum, and the evidence is
#: the one :func:`detect_orientation` already argues from: **a foot reaches
#: forward and an ankle does not.**  So the profile measured here is how far each
#: height reaches along the facing direction, and the ankle is where that reach
#: *collapses* — the same "walk out from the peak until it falls below a fraction
#: of it" shape as the ball, the hip and the shoulder.  On the werewolf the
#: forward reach holds 150-167 mm all the way up the foot and falls to 66 mm in
#: one 7 mm step; the ankle lands at **108 mm**, and the number it is judged
#: against is the character's own foot, not a constant.
FOOT_ANKLE_STATIONS = 33

#: The fraction of its own peak the forward reach must fall below before the leg
#: has stopped being a foot.  Deliberately well clear of both sides of the step —
#: measured on the werewolf the two neighbouring stations are 116 mm and 84 mm
#: against a 160 mm peak, so anything between 55% and 70% picks the same height.
FOOT_ANKLE_COLLAPSE_FRACTION = 0.60

#: **How high the foot and toe bones run.**  Their cross-section centroid is the
#: middle of the *whole* foot, which on a 117 mm thick paw put ``DEF-toe.L`` at
#: **z = 79 mm** — 8 cm of air under a bone that is supposed to roll on the
#: ground.  A toe rolls about a point just above the sole, so the chain is placed
#: at this fraction of **the foot's own local thickness** above the sole at that
#: station: scale-free, per-station, per-side, and on the werewolf it is 27 mm.
FOOT_TOE_LIFT_FRACTION = 0.25

#: **The hand.**  The same defect as the foot, at the other end of the body and
#: for the same reason: ``biped_landmarks`` stopped at the wrist and ``hand.L``
#: was dragged from Rigify's template, so on a clean rest-pose render the wrist
#: sat **deep in the palm** and the hand bone reached the **fingertips**.  A
#: hand bone that ends at the fingertips has no knuckle in it, and a wrist
#: inside the palm bends the hand from the wrong place.
#:
#: How many stations the arm's distal half is cut into to find them.
HAND_STATIONS = 21

#: Where the search for the wrist starts, as a fraction of the distance from the
#: **elbow** to the far end of the arm.  It is measured from the elbow and not
#: from the shoulder for one reason, found by being wrong first: the elbow is
#: itself a girth minimum, and a search that began at the middle of the *whole
#: limb* found the elbow's own crease and called it the wrist.
HAND_BAND = 0.25

#: **Where the knuckles are**: walking distally from the palm's widest station,
#: the first station whose girth has fallen below this fraction of it.  The palm
#: is the widest part of a hand and the fingers are the taper off it, so this is
#: the knuckle line on a hand, on a mitten and on a glove.
HAND_TAPER_FRACTION = 0.85

#: A palm has to be at least this much fatter than the wrist above it before the
#: arm tag is treated as containing a hand at all.  An arm tagged to the wrist
#: is a legitimate thing to be handed.  **Reported, no longer a veto** — see
#: :data:`HAND_REACH_FACTOR`, which is what decides now.
HAND_PALM_FACTOR = 1.12

#: A girth minimum is only the wrist if the limb **fattens again after it** by at
#: least this much.  Found by being wrong first, on the live werewolf: its left
#: arm's girth profile wobbles by 2.3% at the second station of the search band,
#: which is a local minimum in the strict sense and nothing at all in the flesh.
#: The old rule took it, called it the wrist, found no palm past it and returned
#: "this arm has no hand" — so ``hand.L`` kept Rigify's template proportions and
#: the whole defect below followed from that one 2.3% wobble.  The real wrist,
#: eight stations further down, is followed by a 9.0% rise.
HAND_WRIST_RISE = 0.04

#: **Which way the hand points.**  The wrist above was right and the hand bone
#: was still wrong, because ``knuckle`` was the centroid of a slab cut
#: perpendicular to the **arm's** axis.  A hand does not continue the arm — it
#: hangs, it curls, it lies along the thigh — so slicing the hand in the
#: forearm's frame walks the knuckle *outboard*, along the arm's own outward
#: direction.  Measured on the live werewolf: ``DEF-hand.L`` ran from
#: (297.6, 8.9, 950.2) mm to (358.4, 1.3, 905.1) mm — almost pure ``+X``, with
#: its tail **29.7 mm outside the mesh**, in the air beside a hand that actually
#: hangs straight down beside the thigh.
#:
#: The fix is the sole-fit recipe at the other end of the body: **stop measuring
#: the hand in the arm's frame and measure it in its own.**  The hand region is
#: the same slab the wrist rule already cuts — everything distal to the wrist
#: along the arm axis — and the palm direction is that region's own long axis,
#: walked out from the wrist:
#:
#: 1. aim at the region's centroid;
#: 2. take the centroid of the distal :data:`HAND_TIP_SLAB` of the region
#:    *measured along the current aim*, and aim at that instead;
#: 3. repeat until it stops turning.
#:
#: Three lines of pure geometry, deterministic, and it converges in two passes on
#: both of the werewolf's hands.  The principal axis of the same cloud is
#: measured alongside it and **reported**, never used: a paw is a flat splayed
#: blob whose dominant covariance axis is its *width*, which on the werewolf's
#: right hand pointed 73 degrees off the way the fingers actually go.
HAND_AXIS_PASSES = 4
HAND_TIP_SLAB = 0.25

#: Where the hand **stops**, as a percentile of its region's own projections
#: onto the palm axis.  Not the maximum: the werewolf's right arm tag carries a
#: four-vertex island 80 mm past the last real cross-section, which on its own
#: stretched the measured hand from 99 mm to 184 mm.  A fingertip is many
#: vertices and an island is a handful, so the last few percent are trimmed —
#: and the untrimmed number is reported next to it, always.
HAND_REACH_PERCENTILE = 0.97

#: Where the hand bone's tail lands when the palm has no measurable taper: this
#: fraction of the hand's own reach past the wrist, which is the knuckle line on
#: a hand and the middle of the mitt on a mitten.
HAND_KNUCKLE_FRACTION = 0.65

#: ...and the band the *measured* taper has to fall inside before it is believed.
#: A taper at 5% of the hand is the wrist ring again and one at 95% is the end
#: cap of the mesh; neither is a knuckle line, and outside this band the fixed
#: fraction above is used and **said**.
HAND_KNUCKLE_BAND = (0.40, 0.85)

#: **Is there a hand past the wrist at all?**  It has to reach at least this many
#: times the wrist's own girth past it — the hand equivalent of
#: :data:`FOOT_LENGTH_FACTOR`, and for the same reason: an arm tagged to the
#: wrist is a legitimate thing to be handed and inventing a palm from its end cap
#: is not.  This replaced the palm-girth veto, which on the werewolf's left hand
#: missed by 3%: the palm measured 36.95 mm against a 33.90 mm wrist where
#: :data:`HAND_PALM_FACTOR` wanted 37.97, and a real hand was thrown away on one
#: millimetre of girth.  Reach is the honest question; girth is a proxy for it.
HAND_REACH_FACTOR = 0.9

#: Below this many points there is no region to take a direction from.
HAND_REGION_MIN_POINTS = 8

#: How far the hand bone may sit off the palm direction the mesh measures, in
#: degrees, before it is a defect rather than a fit.  **Credibility tier:
#: heuristic (proxy).**  25 degrees is the slack a hand bone genuinely needs (a
#: knuckle line is not square to the palm) and a long way under the 62 degrees
#: the live defect measured.
HAND_AXIS_TOLERANCE_DEG = 25.0

#: **The anatomical pre-bend.**  A rigger never leaves a limb straight at rest:
#: the knee goes a little forward, the elbow a little back, *before* a single
#: key is set.  It is not decoration.  An IK solver given three collinear points
#: has no plane to solve in, Rigify derives a limb's pole angle from that same
#: **rest plane**, and a limb with no plane bends whichever way the solver's
#: floating-point dust happens to fall — which on the live werewolf was
#: **backwards**.
#:
#: The size is the 3% of the limb's own span that :data:`~.rigforge_rig.
#: LANDMARK_BEND` already uses, and that number is not a taste: it was lowered
#: to 0.5% once to flatter the centering gate and ``headless_rigik``'s pole test
#: measured the knee moving **0.02 mm** over a 25% pole sweep.  It went back to
#: 3% and the centering band absorbs it.
PREBEND_FRACTION = 0.03

#: How far into the limb's own flesh the nudge is allowed to reach, as a
#: fraction of the cross-section's own half-width in the push direction.  A
#: pre-bend that pushes the joint *out of the body* has traded one visible
#: defect for another, so the nudge is clamped at two thirds of the way to the
#: skin and the clamp is reported rather than silently applied.
PREBEND_SECTION_FRACTION = 0.667

#: Which way each joint's apex must point, relative to the direction the
#: character faces.  ``+1`` is forward, ``-1`` is backward.  This is the whole
#: of the anatomy: **a knee apexes forward, an elbow apexes backward.**
PREBEND_DIRECTION = {"knee": 1.0, "elbow": -1.0}

#: **The flexed rest stance**, as a fraction of the leg's own chain length —
#: ``hip-to-ankle distance / (thigh + shin)`` at rest.
#:
#: The pre-bend above is a *lateral apex*: it gives IK a plane to solve in and
#: it is 3% of the limb's span, which on an 800 mm leg shortens hip-to-ankle by
#: **under a millimetre**.  It is not a stance, and the wip-14 audit measured
#: what that costs: the shipped werewolf stood at **0.9984 of its own reach**,
#: 1.3 mm of headroom, so the first hip translation of every clip hit the limit
#: and Rigify's ``IK_Stretch`` grew the bones rather than folding the knee
#: (walk ``+32.58%``, jump ``-4.77%``, jump launch rise clamped to **0.0 mm**).
#:
#: **Credibility tier: derived, not heuristic.**  0.97 is the number
#: ``rigcheck.IK_REACH_THRESHOLDS`` warns at (0.98) with a margin under it, and
#: the knee angle is *not* a constant here: it falls out of this fraction and
#: the limb's own two segment lengths (:func:`stance_flex` reports the degrees
#: it derived).  For equal segments the identity is ``span/chain = sin(phi/2)``
#: with ``phi`` the interior knee angle, so 0.97 is a **28.1 degree** flex —
#: the audit's "~12 degrees" is 0.9945 of chain and leaves 4 mm on an 800 mm
#: leg, which is the defect again with a smaller number on it.
#:
#: 1.0 turns the stance off (the rig rests straight, as it did before).
STANCE_SPAN_FRACTION = 0.97

#: Which joint each limb kind flexes to make its stance, and which end of the
#: chain moves to pay for it.  Only the leg has a stance: an arm hangs.
STANCE_JOINT = {"leg": "knee"}

#: A side is a whole dot/underscore-delimited **component** of a name, not just a
#: suffix: Blender writes ``thigh.L`` but also ``brow.B.L.001``, and a mirror
#: that only understood the suffix would treat the second as a centre bone and
#: flatten it onto the midplane.  The *last* such component wins.
_SIDE_COMPONENT_RE = re.compile(r"(?P<sep>[._ -])(?P<side>L|R|Left|Right)(?=$|[._ -])",
                                re.IGNORECASE)


def _band(value, bands):
    if value is None:
        return "unmeasured"
    if value <= bands["ok"]:
        return "ok"
    if value <= bands["attention"]:
        return "attention"
    return "fail"


def _worst(verdicts):
    order = {"fail": 3, "attention": 2, "ok": 1, "unmeasured": 0}
    return max(verdicts, key=lambda v: order.get(v, 0), default="unmeasured")


def _last_side_match(name):
    match = None
    for candidate in _SIDE_COMPONENT_RE.finditer(str(name)):
        match = candidate
    return match


def _side_of(name):
    """``"DEF-shin.L"`` -> ``("DEF-shin", "L")``; unsided -> ``(name, None)``.

    The base is the name with its side component removed, so the two sides of a
    pair share one key whatever shape the name is.
    """
    text = str(name)
    match = _last_side_match(text)
    if match is None:
        return text, None
    base = text[:match.start("sep")] + text[match.end("side"):]
    return base, match.group("side").upper()[0]


def _twin_name(name):
    """The same bone on the other side, or ``None`` for a centre bone."""
    text = str(name)
    match = _last_side_match(text)
    if match is None:
        return None
    side = match.group("side")
    flipped = ("R" if side.upper().startswith("L") else "L")
    if len(side) > 1:  # "Left"/"Right" spelled out: keep the spelling's case
        flipped = ("Right" if side.upper().startswith("L") else "Left")
        if side.islower():
            flipped = flipped.lower()
    elif side.islower():
        flipped = flipped.lower()
    return text[:match.start("side")] + flipped + text[match.end("side"):]


# ---------------------------------------------------------------------------
# point clouds and the mirror residual
# ---------------------------------------------------------------------------

def world_points(obj, limit=None):
    """World-space vertex positions of ``obj``, optionally strided down.

    Strided, never sampled randomly: the same mesh must measure the same number
    twice or none of the gates below mean anything.
    """
    matrix = obj.matrix_world
    vertices = obj.data.vertices
    count = len(vertices)
    if not count:
        raise ForgeError("%r has no vertices to measure." % obj.name)
    if limit is None or count <= limit:
        return [matrix @ v.co for v in vertices]
    step = max(1, count // limit)
    return [matrix @ vertices[i].co for i in range(0, count, step)]


def _bounds(points):
    low = Vector((min(p.x for p in points), min(p.y for p in points),
                  min(p.z for p in points)))
    high = Vector((max(p.x for p in points), max(p.y for p in points),
                   max(p.z for p in points)))
    return low, high


def _centroid(points):
    total = Vector((0.0, 0.0, 0.0))
    for point in points:
        total += point
    return total / float(len(points))


def surface_tree(obj):
    """A BVH of ``obj``'s **surface** in world space.

    Distance to the surface, not to the nearest vertex: a sculpt and its mirror
    image rarely share topology, and a vertex-to-vertex measure would score a
    perfectly symmetric shape with a re-meshed half as wildly asymmetric.  What
    the artist means by symmetric is that the *surfaces* coincide, so that is
    what is measured.
    """
    matrix = obj.matrix_world
    verts = [matrix @ vertex.co for vertex in obj.data.vertices]
    triangles = []
    for polygon in obj.data.polygons:
        loop = list(polygon.vertices)
        for index in range(1, len(loop) - 1):
            triangles.append((loop[0], loop[index], loop[index + 1]))
    if not triangles:
        raise ForgeError("%r has no faces, so its symmetry cannot be measured "
                         "against a surface." % obj.name)
    return BVHTree.FromPolygons(verts, triangles, all_triangles=True)


def mirror_residual(points, tree, axis=0, offset=0.0, samples=1500):
    """How far this surface is from being a mirror image of itself.

    Every sampled point is reflected about the plane ``axis = offset`` and the
    distance back to the **surface** is measured.  Three statistics come out and
    all three are reported, because they answer different questions:

    * ``mean_mm`` — the whole body's average, which a small lopsided limb barely
      moves;
    * ``p95_mm`` — the number that decides, because a character is asymmetric
      when *a part of it* is, not when all of it is;
    * ``max_mm`` — the worst single place.
    """
    if not points:
        raise ForgeError("Nothing to measure symmetry on.")
    step = max(1, len(points) // max(1, samples))
    distances = []
    for index in range(0, len(points), step):
        point = points[index]
        mirrored = point.copy()
        mirrored[axis] = 2.0 * offset - point[axis]
        _location, _normal, _index, distance = tree.find_nearest(mirrored)
        if distance is not None:
            distances.append(distance)
    if not distances:
        return {"mean_mm": None, "max_mm": None, "p95_mm": None, "samples": 0}
    distances.sort()
    return {
        "mean_mm": round(sum(distances) / len(distances) * M_TO_MM, 3),
        "max_mm": round(distances[-1] * M_TO_MM, 3),
        "p95_mm": round(distances[min(len(distances) - 1,
                                      int(0.95 * len(distances)))] * M_TO_MM, 3),
        "samples": len(distances),
    }


#: How far from the centroid the midplane search may wander, as a fraction of
#: the body's own width.  Deliberately narrow.  For a symmetric shape the
#: centroid **is** the midplane exactly, and for a nearly symmetric one it is
#: within a hair; a wide search instead lets the plane chase the asymmetry — park
#: it halfway into a shifted leg and the leg scores zero while the torso, being a
#: minority of the vertices, is swallowed by the statistic.  Measured: with a 10%
#: search width a 60 mm leg shift reported as 1.1 mm of asymmetry.
MIDPLANE_SEARCH_FRACTION = 0.03


def find_midplane(points, tree, axis=0):
    """The plane position that makes the surface most symmetric: coarse, then fine.

    Scored on the **mean** residual, not the p95: every point votes on where the
    plane is, which is what stops a quarter of the body from carrying it away.
    The p95 is then what judges the asymmetry *about* that plane.
    """
    low, high = _bounds(points)
    centre = _centroid(points)[axis]
    best = (None, None)
    width = (high[axis] - low[axis]) * MIDPLANE_SEARCH_FRACTION
    for _pass in range(2):
        for step in range(9):
            offset = centre - width + (2.0 * width) * step / 8.0
            result = mirror_residual(points, tree, axis, offset, samples=500)
            score = result["mean_mm"]
            if score is None:
                continue
            if best[0] is None or score < best[0]:
                best = (score, offset)
        if best[1] is None:
            break
        centre = best[1]
        width *= 0.25
    return best[1] if best[1] is not None else _centroid(points)[axis]


# ---------------------------------------------------------------------------
# step 1: the orientation gate
# ---------------------------------------------------------------------------

def _protrusion(points, axis, centre_value, low_z, high_z):
    """How much further a Z slab reaches one way than the other along ``axis``.

    Toes stick out in front of the ankle and a nose in front of the skull, so
    the bottom and top slabs of a character are lopsided along the facing axis
    and symmetric across it.  That lopsidedness, signed, *is* the facing.
    """
    slab = [p for p in points if low_z <= p.z <= high_z]
    if len(slab) < 8:
        return None
    ahead = max(p[axis] for p in slab) - centre_value
    behind = centre_value - min(p[axis] for p in slab)
    return ahead - behind


def detect_orientation(obj):
    """Which way this character faces, measured from its own geometry.

    The evidence that decides is the one a person uses without thinking: **a
    body is lopsided front to back and symmetric side to side.**  Toes reach in
    front of the ankle, a nose in front of the skull, and nothing reaches out to
    one side that does not reach equally to the other.  So the horizontal axis
    whose **bottom and top slabs are lopsided** is the facing axis, the sign of
    that lopsidedness is the facing, and the other horizontal axis is left/right.

    The mirror residual (:func:`mirror_residual`) is measured about both axes as
    a **corroborating** second opinion — the left/right axis should be the more
    symmetric one — and a disagreement drops the confidence rather than being
    quietly overruled.

    Never raises for an ambiguous result.  It says so in ``confident`` and
    ``why``, and the gate above it refuses.
    """
    points = world_points(obj, limit=6000)
    low, high = _bounds(points)
    centre = _centroid(points)
    tree = surface_tree(obj)

    axes = {}
    for axis, name in ((0, "X"), (1, "Y")):
        offset = find_midplane(points, tree, axis)
        residual = mirror_residual(points, tree, axis, offset, samples=1200)
        feet = _protrusion(points, axis, centre[axis],
                           low.z, low.z + (high.z - low.z) * 0.10)
        head = _protrusion(points, axis, centre[axis],
                           high.z - (high.z - low.z) * 0.12, high.z)
        votes = [value for value in (feet, head) if value is not None]
        score = sum(votes) / float(len(votes)) if votes else 0.0
        extent = max(high[axis] - low[axis], 1e-6)
        axes[name] = {
            "axis": name,
            "midplane_mm": round(offset * M_TO_MM, 2),
            "midplane": offset,
            "mirror_residual_mm": residual["p95_mm"],
            "mean_residual_mm": residual["mean_mm"],
            "worst_residual_mm": residual["max_mm"],
            "toe_protrusion_mm": None if feet is None else round(feet * M_TO_MM, 2),
            "nose_protrusion_mm": None if head is None else round(head * M_TO_MM, 2),
            "protrusion": score,
            "protrusion_fraction": abs(score) / extent,
            "votes": len(votes),
        }

    forward = max(axes, key=lambda name: axes[name]["protrusion_fraction"])
    sideways = "Y" if forward == "X" else "X"
    front = axes[forward]
    side = axes[sideways]
    strong = (front["protrusion_fraction"] >= ORIENT_PROTRUSION_FRACTION
              and front["votes"] > 0)
    decisive = front["protrusion_fraction"] >= 2.0 * side["protrusion_fraction"]
    sign = 1.0 if front["protrusion"] > 0 else -1.0
    faces = "%s%s" % ("+" if sign > 0 else "-", forward)

    agrees = True
    if (front["mirror_residual_mm"] is not None
            and side["mirror_residual_mm"] is not None):
        agrees = (side["mirror_residual_mm"]
                  <= ORIENT_MARGIN_FACTOR * front["mirror_residual_mm"] + ORIENT_MARGIN_MM)

    # The protrusion evidence decides on its own.  The mirror residual is a
    # second opinion and it is *reported*, never a veto: a body whose asymmetry
    # is concentrated in one limb can measure less symmetric about its real
    # sagittal plane than about the other one, and refusing to rig on that would
    # be the tool overruling the toes and the nose with a statistic.
    confident = bool(strong and decisive)
    why = []
    if not strong:
        why.append(
            "nothing protrudes far enough to say which way is forward: the feet and "
            "head slabs are lopsided by only %.1f mm along %s and %.1f mm along X/Y's "
            "other axis, against a body %.0f mm wide"
            % (abs(front["protrusion"]) * M_TO_MM, forward,
               abs(side["protrusion"]) * M_TO_MM,
               max(high[0] - low[0], high[1] - low[1]) * M_TO_MM))
    elif not decisive:
        why.append(
            "the body is almost equally lopsided along both horizontal axes (%.1f mm "
            "along %s, %.1f mm along %s), so which one is the facing axis cannot be "
            "measured" % (abs(front["protrusion"]) * M_TO_MM, forward,
                          abs(side["protrusion"]) * M_TO_MM, sideways))
    note = ""
    if not agrees:
        note = ("The toes and the nose say %s is the facing axis, and that is what was "
                "used, but the body measures more symmetric about %s (%.2f mm) than "
                "about %s (%.2f mm) — which usually means its asymmetry is concentrated "
                "in one limb. Worth a look at the symmetry numbers."
                % (forward, forward, front["mirror_residual_mm"], sideways,
                   side["mirror_residual_mm"]))

    return {
        "object": obj.name,
        "left_right_axis": sideways,
        "forward_axis": forward,
        "faces": faces,
        "character_left": "%s%s" % ("+" if _left_sign(faces) > 0 else "-", sideways),
        "midplane_mm": side["midplane_mm"],
        "mirror_residual_mm": side["mirror_residual_mm"],
        "other_axis_residual_mm": front["mirror_residual_mm"],
        "symmetry_agrees": bool(agrees),
        "decisive": bool(decisive and strong),
        "toe_protrusion_mm": front["toe_protrusion_mm"],
        "nose_protrusion_mm": front["nose_protrusion_mm"],
        "confident": confident,
        "convention": CONVENTION,
        "matches_convention": bool(confident and faces == "-Y" and sideways == "X"),
        "why": "; ".join(why),
        "note": note,
        "axes": {name: {k: v for k, v in entry.items() if k != "midplane"}
                 for name, entry in axes.items()},
    }


def _left_sign(faces):
    """With Z up and the character facing ``faces``, which way is its left.

    ``-Y`` -> ``+X`` (the Blender convention), ``+Y`` -> ``-X``, ``+X`` -> ``+Y``,
    ``-X`` -> ``-Y``.  Cross product of up with forward, by hand.
    """
    forward = {"+X": Vector((1, 0, 0)), "-X": Vector((-1, 0, 0)),
               "+Y": Vector((0, 1, 0)), "-Y": Vector((0, -1, 0))}[faces]
    left = Vector((0.0, 0.0, 1.0)).cross(forward)
    return 1.0 if (left.x + left.y) > 0 else -1.0


def orient_to_convention(obj, report=None, allow_rotate=True, strict=False,
                         warnings=None):
    """Rotate ``obj`` by a whole 90 degrees until it faces ``-Y``. Or refuse.

    Whole multiples of 90 only: a character is modelled on an axis, and a
    fractional "correction" would be this tool inventing a pose.  The rotation
    is **applied** into the mesh data, so everything downstream — the metarig
    fit, the export, Godot — sees the convention rather than a transform that
    happens to encode it.

    **A character the gate cannot read is assumed to be on the convention, and
    told so.**  Not refused: a featureless blob, a barrel, an abstract creature
    with no nose and no toes genuinely *has* no measurable facing, and there is
    nothing to fix — refusing would block work that a person would simply get on
    with.  What refusing is for is a character that measurably faces the wrong
    way, which is a defect with a known fix.  ``strict=True`` refuses the
    unreadable case too, for a pipeline that would rather stop.
    """
    found = report if report is not None else detect_orientation(obj)
    warnings = warnings if warnings is not None else []
    outcome = dict(found)
    outcome["rotated_deg"] = 0.0
    outcome["action"] = "none"
    outcome["assumed"] = False
    if found["matches_convention"]:
        outcome["action"] = "already correct"
        outcome["says"] = ("%r already faces -Y with +X on its left, which is the "
                           "Blender convention." % obj.name)
        return outcome
    if not found["confident"]:
        message = (
            "The orientation gate could not tell which way %r faces (%s), so it was "
            "assumed to be on the convention already: facing -Y, +X on its left. Every "
            "side name below is only as right as that assumption. If it is wrong, "
            "rotate the mesh in front view (Numpad 1) until you are looking at its "
            "face, apply the rotation, and run this again."
            % (obj.name, found["why"] or "the evidence was contradictory"))
        if strict:
            raise ForgeError("Refusing to rig %r: %s" % (obj.name, message))
        outcome["action"] = "assumed"
        outcome["assumed"] = True
        outcome["faces"] = "-Y"
        outcome["left_right_axis"] = "X"
        outcome["character_left"] = "+X"
        outcome["says"] = message
        warnings.append(message)
        return outcome
    if not allow_rotate:
        raise ForgeError(
            "Refusing to rig %r: it faces %s, but the convention is -Y with the "
            "character's left at +X (%s). Pass orient='fix' to rotate it, or rotate "
            "and apply it yourself." % (obj.name, found["faces"], CONVENTION["why"]))

    # Standard math angles about +Z: +X is 0, +Y is 90, -X is 180, -Y is -90.
    # The rotation that takes the *found* forward direction onto -Y is the
    # difference, rounded to the whole 90 it must already be.
    current = {"+X": 0.0, "+Y": 90.0, "-X": 180.0, "-Y": -90.0}[found["faces"]]
    angle_deg = round((-90.0 - current) / 90.0) * 90.0
    angle_deg = ((angle_deg + 180.0) % 360.0) - 180.0
    if abs(angle_deg) < 1e-6:
        outcome["action"] = "already correct"
        return outcome
    with object_mode():
        obj.matrix_world = Matrix.Rotation(math.radians(angle_deg), 4, "Z") @ obj.matrix_world
        refresh_view_layer()
        with active_only(obj):
            try:
                bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
            except RuntimeError as exc:
                raise ForgeError("Could not apply %r's orientation fix: %s"
                                 % (obj.name, exc))
        refresh_view_layer()
    after = detect_orientation(obj)
    outcome = dict(after)
    outcome["rotated_deg"] = angle_deg
    outcome["action"] = "rotated"
    outcome["was_facing"] = found["faces"]
    outcome["says"] = (
        "%r faced %s, so it was rotated %+.0f degrees about Z to face -Y (Blender's "
        "convention: +X is then the character's left). It now measures %s."
        % (obj.name, found["faces"], angle_deg, after["faces"]))
    return outcome


# ---------------------------------------------------------------------------
# step 2: symmetrize first
# ---------------------------------------------------------------------------

def measure_symmetry(obj, axis=0):
    """The mesh's own asymmetry about its best X plane, in millimetres.

    The verdict is taken on ``p95_mm``, not on the mean: a character with one
    leg 15 mm out of place is an asymmetric character, and averaging that over a
    whole body turns it into half a millimetre of nothing.
    """
    points = world_points(obj, limit=6000)
    tree = surface_tree(obj)
    offset = find_midplane(points, tree, axis)
    result = mirror_residual(points, tree, axis, offset, samples=1500)
    result["midplane_mm"] = round(offset * M_TO_MM, 3)
    result["midplane"] = offset
    result["axis"] = "XYZ"[axis]
    result["measured_mm"] = result["p95_mm"]
    result["statistic"] = ("p95 of the distance from every mirrored surface point back "
                           "to the surface")
    result["symmetric"] = bool(result["p95_mm"] is not None
                               and result["p95_mm"] <= SYMMETRY_TOLERANCE_MM)
    result["tolerance_mm"] = SYMMETRY_TOLERANCE_MM
    return result


def symmetrize_mesh(obj, midplane, keep="+X", threshold=0.0001):
    """Make the mesh an exact mirror of one of its halves about ``midplane``.

    Blender's own ``symmetrize`` mirrors about the object's **local x = 0**, so
    the mesh is slid onto that plane, symmetrized, and slid back — the object
    never moves, and the midplane it is now symmetric about is the one that was
    *measured* rather than the one the exporter happened to leave it on.
    """
    matrix = obj.matrix_world
    local_mid = (matrix.inverted() @ Vector((midplane, 0.0, 0.0))).x
    directions = {"+X": "X", "-X": "-X"}
    if keep not in directions:
        raise ForgeError("symmetry_keep must be '+X' or '-X', got %r." % (keep,))
    before = len(obj.data.vertices)
    with object_mode():
        mesh = obj.data
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bmesh.ops.translate(bm, verts=bm.verts[:], vec=Vector((-local_mid, 0.0, 0.0)))
            geometry = list(bm.verts) + list(bm.edges) + list(bm.faces)
            used = None
            for candidate in (directions[keep], keep, "POSITIVE_X" if keep == "+X"
                              else "NEGATIVE_X"):
                try:
                    bmesh.ops.symmetrize(bm, input=geometry, direction=candidate,
                                         dist=threshold)
                except (TypeError, ValueError):
                    continue
                used = candidate
                break
            if used is None:
                raise ForgeError(
                    "This Blender build's bmesh.ops.symmetrize accepted none of the "
                    "spellings for %r, so the mesh was left alone." % keep)
            bm.verts.ensure_lookup_table()
            bmesh.ops.translate(bm, verts=bm.verts[:], vec=Vector((local_mid, 0.0, 0.0)))
            bm.to_mesh(mesh)
            mesh.update()
        finally:
            bm.free()
    after = measure_symmetry(obj)
    return {
        "kept": keep,
        "method": "bmesh.ops.symmetrize",
        "midplane_mm": round(midplane * M_TO_MM, 3),
        "vertices_before": before,
        "vertices_after": len(obj.data.vertices),
        "residual_after": after,
    }


def retag_sides_from_geometry(obj, midplane=0.0, character_left=1.0):
    """Re-derive every sided tag's side from **where the geometry is**.

    This is the fix for the defect that hid behind automatic weights: a tag
    called ``Leg.R`` sitting at ``x = +182 mm`` is, under the convention, the
    character's **left** leg, and every bone fitted to it inherits the lie.  The
    geometry cannot be wrong about which side of the midplane it is on, so the
    geometry decides and the rename is reported.

    Symmetrizing makes this mandatory rather than merely wise: ``symmetrize``
    copies one half's vertex groups onto the other, so straight afterwards both
    legs are tagged ``Leg.L`` until this runs.
    """
    groups = {rigforge.tag_display_name(g.name): g for g in rigforge.tag_groups(obj)}
    pairs = {}
    for name in groups:
        base, side = _side_of(name)
        if side is None:
            continue
        pairs.setdefault(base, {})[side] = name
    matrix = obj.matrix_world
    moved = []
    for base, sides in sorted(pairs.items()):
        if len(sides) != 2:
            continue
        left_name = sides["L"]
        right_name = sides["R"]
        left_group = obj.vertex_groups.get(rigforge.tag_group_name(left_name))
        right_group = obj.vertex_groups.get(rigforge.tag_group_name(right_name))
        if left_group is None or right_group is None:
            continue
        indices = {left_group.index, right_group.index}
        want_left = []
        want_right = []
        for vertex in obj.data.vertices:
            if not any(entry.group in indices and entry.weight > 0.0
                       for entry in vertex.groups):
                continue
            x = (matrix @ vertex.co).x
            if (x - midplane) * character_left >= 0.0:
                want_left.append(vertex.index)
            else:
                want_right.append(vertex.index)
        before_left = {v.index for v in obj.data.vertices
                       for entry in v.groups
                       if entry.group == left_group.index and entry.weight > 0.0}
        reassigned = len(set(want_left) ^ before_left)
        # Was the tag on the wrong half *before* this ran? The group's own mean
        # X answers it: a tag called .L whose vertices average out on the
        # character's right was named backwards, whatever the count says.
        was_x = None
        if before_left:
            was_x = sum((matrix @ obj.data.vertices[i].co).x for i in before_left) \
                / float(len(before_left))
        swapped = bool(was_x is not None
                       and (was_x - midplane) * character_left < 0.0
                       and abs(was_x - midplane) > 1e-6)
        left_group.remove(list(range(len(obj.data.vertices))))
        right_group.remove(list(range(len(obj.data.vertices))))
        if want_left:
            left_group.add(want_left, 1.0, "REPLACE")
        if want_right:
            right_group.add(want_right, 1.0, "REPLACE")
        moved.append({
            "tag": base,
            "left_vertices": len(want_left),
            "right_vertices": len(want_right),
            "reassigned": reassigned,
            "was_left_x_mm": None if was_x is None else round(was_x * M_TO_MM, 1),
            "swapped": swapped,
        })
    obj.data.update()
    swapped_tags = [entry["tag"] for entry in moved if entry["swapped"]]
    says = ""
    if swapped_tags:
        says = ("The sided tags %s named the wrong halves of the body: under the "
                "convention (%s is the character's left) the geometry decides, so they "
                "were swapped before a single bone was placed. This is the defect that "
                "automatic weights hide and retargeting cannot."
                % (", ".join(swapped_tags), CONVENTION["character_left"]))
    return {"tags": moved, "swapped_tags": swapped_tags, "says": says}


# ---------------------------------------------------------------------------
# step 3: landmarks from geometry — cross-section centroids
# ---------------------------------------------------------------------------

#: How many cross-sections a limb is sliced into.  33 stations on a 400 mm thigh
#: is a slice every 12 mm: fine enough to find a crease, coarse enough that each
#: slab still holds enough vertices to have a meaningful centroid.
STATIONS = 33

#: The band, as a fraction of the limb's length, inside which a knee or an elbow
#: is allowed to be.  Anatomy, not a guess: the crease of a limb is near the
#: middle, and a "minimum girth" found at 5% is the armpit, not the elbow.
CREASE_BAND = (0.30, 0.70)

#: The distal band for a wrist or an ankle: the girth minimum before the hand or
#: the foot flares out again.
DISTAL_BAND = (0.78, 0.98)

#: A girth this many times the limb's own median means the slice is no longer
#: the limb — it is the torso the limb is growing out of.  That step is the
#: junction, and the last slice before it is the shoulder / the hip.
JUNCTION_FACTOR = 1.6

#: A crease has to be at least this much thinner than its band's mean girth
#: before "minimum girth" is a landmark rather than sampling noise.
MIN_CREASE_DIP = 0.02

#: ... and failing that, the centreline has to turn at least this much for the
#: curvature landmark to mean anything.
MIN_CREASE_ANGLE_DEG = 3.0


def _principal_axis(points, centre, seed=None):
    """Dominant direction of a cloud (power iteration on its covariance)."""
    cov = [[0.0] * 3 for _ in range(3)]
    for point in points:
        d = point - centre
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    vector = Vector(seed) if seed is not None else Vector((0.577, 0.577, 0.577))
    if vector.length < 1e-9:
        vector = Vector((0.0, 0.0, 1.0))
    vector.normalize()
    for _ in range(32):
        nxt = Vector((
            cov[0][0] * vector.x + cov[0][1] * vector.y + cov[0][2] * vector.z,
            cov[1][0] * vector.x + cov[1][1] * vector.y + cov[1][2] * vector.z,
            cov[2][0] * vector.x + cov[2][1] * vector.y + cov[2][2] * vector.z,
        ))
        if nxt.length < 1e-12:
            break
        nxt.normalize()
        if (nxt - vector).length < 1e-9:
            return nxt
        vector = nxt
    return vector


def _smooth(values, passes=2):
    """A [1 2 1] pass over a list of Vectors or floats, ends held."""
    out = list(values)
    for _ in range(passes):
        nxt = list(out)
        for i in range(1, len(out) - 1):
            nxt[i] = (out[i - 1] + 2.0 * out[i] + out[i + 1]) / 4.0
        out = nxt
    return out


class Limb(object):
    """One tagged limb, sliced into cross-sections perpendicular to its own axis.

    This is the whole of "landmark from geometry": the centreline is the list of
    **cross-section centroids**, the girth is each section's mean radius, and
    every joint below is one of those stations chosen by a rule about the girth
    rather than by a fraction somebody typed.

    Two passes.  The first slices perpendicular to the limb's principal axis —
    which is right for a straight limb and slightly wrong for a bent one — and
    the second re-slices each station perpendicular to the **local tangent** of
    the centreline the first pass found.  That is what makes a bent arm's elbow
    land on the crease instead of on a diagonal smear across it.
    """

    def __init__(self, tag, points, axis_hint=None, stations=STATIONS):
        if len(points) < 12:
            raise ForgeError(
                "The tag %r has only %d vertices; a cross-section landmark needs a "
                "limb, not a handful of points." % (tag, len(points)))
        self.tag = tag
        self.points = list(points)
        self.count = len(points)
        self.low, self.high = _bounds(self.points)
        self.centre = _centroid(self.points)
        axis = _principal_axis(self.points, self.centre, seed=axis_hint)
        if axis_hint is not None and axis.dot(Vector(axis_hint)) < 0.0:
            axis = -axis
        # A tagged limb is not always a tube. A blob sculpt's "leg" can be wider
        # than it is long, and then its principal axis is *sideways* — measured
        # on the synthetic sculpt: a leg whose landmarks came out as a zigzag,
        # the knee 50 mm above the hip. Anatomy outranks the covariance: a leg
        # runs down and an arm runs out, so a principal axis more than 60 degrees
        # from the anatomical hint is the cloud's shape talking, not the limb's.
        self.axis_from_hint = False
        #: Set by :func:`biped_landmarks` when the hint came from a measurement
        #: (a detected skeleton) rather than from the T-pose default.
        self.hint_measured = False
        if axis_hint is not None:
            hint = Vector(axis_hint).normalized()
            span = sorted(self.size if hasattr(self, "size") else
                          (self.high - self.low), reverse=True)
            isotropic = span[0] <= 1.5 * max(span[2], 1e-9)
            if isotropic or axis.dot(hint) < 0.5:
                axis = hint
                self.axis_from_hint = True
        self.axis = axis
        self.stations = max(9, int(stations))

        projections = [(p - self.centre).dot(axis) for p in self.points]
        self.t_lo = min(projections)
        self.t_hi = max(projections)
        self.length = self.t_hi - self.t_lo
        self.half = max(self.length / (self.stations - 1) * 0.75, 1e-6)

        centreline, girth, counts = self._slice(axis_per_station=None,
                                                projections=projections)
        centreline = _smooth(centreline)
        tangents = self._tangents(centreline)
        centreline, girth, counts = self._slice(axis_per_station=tangents,
                                                projections=projections)
        self.centreline = _smooth(centreline)
        self.girth = _smooth(girth)
        self.slice_counts = counts
        self.tangents = self._tangents(self.centreline)
        self.fractions = [i / float(self.stations - 1) for i in range(self.stations)]
        self.median_girth = sorted(self.girth)[len(self.girth) // 2]

    # -- slicing ----------------------------------------------------------

    def _slice(self, axis_per_station, projections):
        centreline = []
        girth = []
        counts = []
        for index in range(self.stations):
            target = self.t_lo + self.length * index / float(self.stations - 1)
            normal = (self.axis if axis_per_station is None
                      else axis_per_station[index])
            anchor = self.centre + self.axis * target
            # The slab **widens until the slice goes most of the way round the
            # limb**. This is the most important line in the file: a game mesh's
            # edge loops are tens of millimetres apart, so a fixed thin slab
            # catches a whole loop, half a loop or nothing depending on where it
            # lands — and the centroid of *half* a loop sits well off the limb's
            # axis. Measured on a 16-sided arm: landmarks 9.6 mm off the tube's
            # own centreline, which is exactly the "bones are not on the limb
            # centreline" the owner could see in a render.
            half = self.half
            chosen = []
            for _widen in range(6):
                chosen = []
                for position, point in enumerate(self.points):
                    if axis_per_station is None:
                        offset = projections[position] - target
                    else:
                        offset = (point - anchor).dot(normal)
                    if abs(offset) <= half:
                        chosen.append(point)
                if len(chosen) >= MIN_SECTION_POINTS and _angular_gap(
                        chosen, _centroid(chosen), normal) <= MAX_SECTION_GAP_DEG:
                    break
                half *= 1.6
            if len(chosen) < 3:
                order = sorted(range(self.count),
                               key=lambda i: abs(projections[i] - target))
                chosen = [self.points[i] for i in order[:max(3, self.count // 24)]]
            centre = _centroid(chosen)
            radius = sum(((p - centre) - normal * (p - centre).dot(normal)).length
                         for p in chosen) / float(len(chosen))
            centreline.append(centre)
            girth.append(radius)
            counts.append(len(chosen))
        return centreline, girth, counts

    def _tangents(self, centreline):
        out = []
        for index in range(len(centreline)):
            a = centreline[max(0, index - 1)]
            b = centreline[min(len(centreline) - 1, index + 1)]
            direction = b - a
            if direction.length < 1e-9:
                direction = self.axis.copy()
            out.append(direction.normalized())
        return out

    # -- landmarks --------------------------------------------------------

    def at(self, fraction):
        """The cross-section centroid ``fraction`` of the way along the limb."""
        position = max(0.0, min(1.0, float(fraction))) * (self.stations - 1)
        low = int(math.floor(position))
        high = min(self.stations - 1, low + 1)
        blend = position - low
        return self.centreline[low].lerp(self.centreline[high], blend)

    def _band_indices(self, band):
        lo = max(0, int(round(band[0] * (self.stations - 1))))
        hi = min(self.stations - 1, int(round(band[1] * (self.stations - 1))))
        return list(range(lo, max(lo + 1, hi + 1)))

    def girth_minimum(self, band, label):
        """The thinnest cross-section inside ``band`` — the crease.

        A knee **is** a girth minimum: the flesh is thinnest where the two
        segments hinge, which is why a rigger's snapped joint lands there.  When
        the limb has no such dip (a blob, a cylinder) the fallback ladder runs
        and says which rung it stopped on, because a landmark that was really a
        guess must not be reported as a measurement.
        """
        indices = self._band_indices(band)
        values = [self.girth[i] for i in indices]
        mean = sum(values) / float(len(values))
        best = min(indices, key=lambda i: self.girth[i])
        dip = (mean - self.girth[best]) / mean if mean > 1e-12 else 0.0
        if dip >= MIN_CREASE_DIP:
            return {
                "point": self.centreline[best], "fraction": self.fractions[best],
                "how": "minimum girth", "dip_pct": round(100.0 * dip, 2),
                "girth_mm": round(self.girth[best] * M_TO_MM, 2),
                "band": list(band), "label": label,
            }
        # No dip: the crease may still be visible as a bend in the centreline.
        turn = 0.0
        turn_index = None
        for index in indices[1:-1] or indices:
            a = self.tangents[max(0, index - 1)]
            b = self.tangents[min(self.stations - 1, index + 1)]
            angle = math.degrees(a.angle(b, 0.0))
            if angle > turn:
                turn, turn_index = angle, index
        if turn_index is not None and turn >= MIN_CREASE_ANGLE_DEG:
            return {
                "point": self.centreline[turn_index],
                "fraction": self.fractions[turn_index],
                "how": "centreline curvature", "turn_deg": round(turn, 2),
                "girth_mm": round(self.girth[turn_index] * M_TO_MM, 2),
                "band": list(band), "label": label,
            }
        middle = indices[len(indices) // 2]
        return {
            "point": self.centreline[middle], "fraction": self.fractions[middle],
            "how": "midpoint fallback",
            "why": ("%s has no girth minimum (deepest dip %.1f%%, needs %.0f%%) and no "
                    "bend (%.1f degrees, needs %.0f) inside %d-%d%% of its length, so "
                    "the joint is the middle of the band — a guess, not a landmark"
                    % (self.tag, 100.0 * dip, 100.0 * MIN_CREASE_DIP, turn,
                       MIN_CREASE_ANGLE_DEG, int(band[0] * 100), int(band[1] * 100))),
            "girth_mm": round(self.girth[middle] * M_TO_MM, 2),
            "band": list(band), "label": label,
        }

    def junction(self, label, from_end="proximal"):
        """Where the limb stops being a limb and becomes the body it grows from.

        Walking inward from the middle, the first cross-section whose girth is
        :data:`JUNCTION_FACTOR` times the limb's own median is the torso; the
        joint is the last section before it.  A hip and a shoulder are exactly
        that step, and it is measurable on any mesh where the tag runs a little
        way into the body — which is what a painted tag always does.
        """
        order = (list(range((self.stations - 1) // 2, -1, -1)) if from_end == "proximal"
                 else list(range((self.stations - 1) // 2, self.stations)))
        limit = JUNCTION_FACTOR * self.median_girth
        hit = None
        for index in order:
            if self.girth[index] > limit:
                hit = index
                break
        if hit is not None:
            step = 1 if from_end == "proximal" else -1
            index = max(0, min(self.stations - 1, hit + step))
            return {
                "point": self.centreline[index], "fraction": self.fractions[index],
                "how": "girth junction",
                "girth_mm": round(self.girth[index] * M_TO_MM, 2),
                "junction_girth_mm": round(self.girth[hit] * M_TO_MM, 2),
                "median_girth_mm": round(self.median_girth * M_TO_MM, 2),
                "label": label,
            }
        index = 1 if from_end == "proximal" else self.stations - 2
        return {
            "point": self.centreline[index], "fraction": self.fractions[index],
            "how": "end cross-section",
            "why": ("%s never widens to %.1fx its median girth, so its %s end is the "
                    "joint: the tag stops at the crease rather than running into the "
                    "body" % (self.tag, JUNCTION_FACTOR, from_end)),
            "girth_mm": round(self.girth[index] * M_TO_MM, 2),
            "label": label,
        }

    # -- the flesh, for the pre-bend ---------------------------------------

    def section_points(self, index):
        """The raw cross-section at station ``index``, on that station's tangent.

        The same widening slab :meth:`_slice` uses — one implementation of "the
        slice of this limb here" — handed back as points rather than reduced to
        a centroid and a radius, because the pre-bend needs to know **how far
        the skin is** in one particular direction, not on average.
        """
        index = max(0, min(self.stations - 1, int(index)))
        anchor = self.centreline[index]
        normal = self.tangents[index]
        half = self.half
        chosen = []
        for _widen in range(6):
            chosen = [point for point in self.points
                      if abs((point - anchor).dot(normal)) <= half]
            if len(chosen) >= MIN_SECTION_POINTS and _angular_gap(
                    chosen, _centroid(chosen), normal) <= MAX_SECTION_GAP_DEG:
                return chosen
            half *= 1.6
        return chosen

    def section_reach(self, index, direction):
        """How far this station's flesh reaches along ``direction``, in metres.

        ``(reach, why)``: ``reach`` is ``None`` when the slice is not a ring —
        fewer than :data:`MIN_SECTION_POINTS` crossings, or an angular gap wider
        than :data:`MAX_SECTION_GAP_DEG`, the same two gates
        :func:`bone_centering` applies before it trusts a section's centre.  A
        station that cannot be measured must not be allowed to *approve* a
        nudge, so the caller falls back to the girth instead of guessing.
        """
        index = max(0, min(self.stations - 1, int(index)))
        normal = self.tangents[index]
        push = Vector(direction)
        push = push - normal * push.dot(normal)
        if push.length < 1e-9:
            return None, "the push direction runs along the limb, so it has no cross-section"
        push.normalize()
        points = self.section_points(index)
        if len(points) < MIN_SECTION_POINTS:
            return None, ("the slice here has %d point(s), fewer than the %d a "
                          "cross-section needs" % (len(points), MIN_SECTION_POINTS))
        centre = _centroid(points)
        gap = _angular_gap(points, centre, normal)
        if gap > MAX_SECTION_GAP_DEG:
            return None, ("the slice here is an open patch (%.0f degree gap, limit %.0f)"
                          % (gap, MAX_SECTION_GAP_DEG))
        reach = max((point - centre).dot(push) for point in points)
        return max(reach, 0.0), None

    def nearest_station(self, fraction):
        """The station index closest to ``fraction`` of the way along the limb."""
        position = max(0.0, min(1.0, float(fraction))) * (self.stations - 1)
        return int(round(position))

    def as_dict(self):
        return {
            "tag": self.tag,
            "vertices": self.count,
            "stations": self.stations,
            "length_mm": round(self.length * M_TO_MM, 2),
            "axis": [round(v, 5) for v in self.axis],
            "axis_hint_measured": bool(getattr(self, "hint_measured", False)),
            "median_girth_mm": round(self.median_girth * M_TO_MM, 2),
            "girth_mm": [round(g * M_TO_MM, 2) for g in self.girth],
        }


def _tag_points(obj, group):
    matrix = obj.matrix_world
    index = group.index
    points = []
    for vertex in obj.data.vertices:
        for entry in vertex.groups:
            if entry.group == index and entry.weight > 0.0:
                points.append(matrix @ vertex.co)
                break
    return points


def tag_clouds(obj):
    """``{tag: [world points]}`` for every tag with geometry on it."""
    out = {}
    for group in rigforge.tag_groups(obj):
        points = _tag_points(obj, group)
        if points:
            out[rigforge.tag_display_name(group.name)] = points
    return out


def _pick(clouds, *candidates):
    lowered = {name.lower(): name for name in clouds}
    for candidate in candidates:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def _sided(clouds, pattern):
    out = {}
    for name in clouds:
        match = re.match(r"^(%s)[._ ]?(L|R|Left|Right)$" % pattern, name, re.IGNORECASE)
        if match:
            out["L" if match.group(2).upper().startswith("L") else "R"] = name
    return out


def _limb_is_measurable(obj, what, limb, crease):
    """Refuse a tag that is a blob rather than a limb, before it becomes bones.

    Two refusals, both meaning "the landmark method has nothing to measure here,
    so use the old one and say so":

    * the limb's own principal axis had to be **replaced by the anatomical
      hint** — it is as wide as it is long, or points somewhere a limb does not.
      A stack of spheres tagged ``Leg`` measures like that, and the joints that
      come out of it are a zigzag;
    * its crease is a **midpoint fallback** — the mesh has no girth minimum and
      no bend where a knee or an elbow belongs, so a "landmark" there would be
      the same fraction-of-the-blob guess the tag fit makes, wearing a
      measurement's clothes.
    """
    if limb.axis_from_hint:
        raise ForgeError(
            "The tag %r on %r is not shaped like a limb: its own principal axis is not "
            "the direction a %s runs in (it is as wide as it is long, or it points "
            "elsewhere), so every cross-section taken along it would be a slice of the "
            "wrong thing." % (limb.tag, obj.name, what))
    if str(crease.get("how", "")).endswith("fallback"):
        raise ForgeError(
            "The tag %r on %r has no crease to find: %s. A landmark there would be the "
            "same guess the tag fit makes, so the tag fit should make it."
            % (limb.tag, obj.name, crease.get("why") or "no girth minimum, no bend"))


def _plausible(obj, what, proximal, middle, distal, direction):
    """Three landmarks must march down the limb, or they are not landmarks.

    The cheapest possible sanity gate and the one that matters: a knee above its
    own hip is not a knee, however confidently it was measured, and a metarig
    fitted to it is a zigzag.  Raising here is the caller's signal to fall back
    to the tag fit **and say so**, which is what an honest fallback looks like.
    """
    total = (distal - proximal).length
    reasons = []
    for label, start, end in (("upper", proximal, middle), ("lower", middle, distal)):
        segment = end - start
        length = segment.length
        if total > 1e-9 and length < 0.12 * total:
            reasons.append("its %s segment is %.0f mm of a %.0f mm limb"
                           % (label, length * M_TO_MM, total * M_TO_MM))
            continue
        if length > 1e-9 and segment.dot(direction) / length < 0.5:
            reasons.append("its %s segment runs sideways (%.0f degrees off the limb's "
                           "own direction)"
                           % (label, math.degrees(math.acos(
                               max(-1.0, min(1.0, segment.dot(direction) / length))))))
    if not reasons:
        return
    raise ForgeError(
        "The landmark pass could not read %r's %s as a limb: %s. Its three joints came "
        "out at %s, %s and %s millimetres. A tagged region that is a blob rather than a "
        "limb — or a tag that swallowed the hip — measures like this, and a metarig "
        "fitted to it would be a zigzag."
        % (obj.name, what, "; ".join(reasons),
           [round(v * M_TO_MM) for v in proximal], [round(v * M_TO_MM) for v in middle],
           [round(v * M_TO_MM) for v in distal]))


def _limb_hint(axis_hints, key, default):
    """The direction a limb runs in: measured if anything measured it, else anatomy.

    The hard-coded defaults below — an arm runs sideways, a leg runs down — are
    a **T-pose assumption**, and they are the reason a correctly tagged limb can
    still be refused.  The werewolf's arms hang at its sides: a perfect
    cylindrical ``Arm.L`` tag around them has a near-vertical principal axis,
    which is more than 60 degrees from ``+X``, so :class:`Limb` replaces the axis
    with the hint and :func:`_limb_is_measurable` then refuses the tag for having
    needed that replacement.  The tag was right and the hint was wrong.

    So when something has actually measured the limb's direction — the joint
    detector, through ``rigforge_autotag`` — that measurement is the hint, for
    both the slicing axis and the plausibility gate.  With no measurement the
    behaviour is exactly what it always was.
    """
    if axis_hints:
        candidate = axis_hints.get(key)
        if candidate is not None:
            candidate = Vector(candidate)
            if candidate.length > 1e-6:
                return candidate.normalized(), True
    return Vector(default), False


def _horizontal(vector, up=None):
    up = Vector((0.0, 0.0, 1.0)) if up is None else Vector(up)
    flat = Vector(vector) - up * Vector(vector).dot(up)
    return flat.normalized() if flat.length > 1e-9 else Vector((0.0, -1.0, 0.0))


def foot_landmarks(points, ankle_hint, knee, forward, label="foot"):
    """The ankle, the ball, the toe tip and the heel — off the foot's own geometry.

    The rest of this module measures a limb by slicing it across its own axis.
    A foot is not that shape: it is a wedge lying on the ground, and the joints
    an animator needs from it are defined *along the direction the character
    faces*, not along the leg.  So this measures the foot in its own frame:

    * **the ankle** is the height where the leg's **forward reach collapses** —
      a foot reaches in front of the leg and an ankle does not, so the step off
      the foot's own reach *is* the joint.  Walking up from the station that
      reaches furthest, the first one under :data:`FOOT_ANKLE_COLLAPSE_FRACTION`
      of it is the ankle's height, and the joint sits on the **leg column's own
      centre** there (the centroid of the two stations above it, which are ankle
      and nothing else) rather than on the centroid of a slab that still has
      half a heel in it.  It is deliberately a *step* and not a minimum: the
      profile above the foot is flat, an ``argmin`` over a flat run wanders, and
      on the live werewolf it wandered 65 mm up the shin — ``DEF-foot.L``'s head
      sat at ``z = 173 mm`` against a foot that stops at 110;
    * **the ball** is where the sole's **forward taper begins**: cut the sole
      into stations front to back, take the widest, and walk forward to the first
      station under :data:`FOOT_TAPER_FRACTION` of it.  That is a bootmaker's
      definition and it works on a bare foot and on a boot;
    * **the toe tip** is the mesh's own forward extent at foot height, pulled
      back :data:`FOOT_TIP_MARGIN_MM` so the bone does not break the surface the
      instant the toe rolls.  The ball and the tip keep the *forward* position
      and the *sideways* centre their cross-sections give them and take their
      **height from the sole**: :data:`FOOT_TOE_LIFT_FRACTION` of the foot's own
      local thickness above it.  The centroid of a whole 117 mm thick paw is
      8 cm off the ground, and a toe that rolls about a point 8 cm up rolls the
      character into the floor;
    * **the heel** is the sole's rearmost point, at ground level, with the sole's
      own width there — which is what Rigify's ``heel.02`` spans and therefore
      where the foot roll pivots.

    Every one of the three bone positions is the **cross-section centroid** at
    its station, exactly as every other landmark in this file is, so a foot bone
    runs through the middle of the flesh rather than along the top of the sole.

    Returns ``None`` when the tag has no foot in it — a leg tagged to the ankle
    is a legitimate thing to be handed, and inventing a boot from its last two
    edge loops is not.  Otherwise a dict of points and how each was found.
    """
    if len(points) < 12:
        return None
    ground = min(p.z for p in points)
    reach = max(knee.z - ground, 1e-6)
    forward = _horizontal(forward)
    right = Vector((0.0, 0.0, 1.0)).cross(forward).normalized()

    # --- the ankle: the height where the leg's forward reach collapses
    #
    # A foot reaches in front of the leg and an ankle does not, so what is
    # profiled here is each height's forward extent and what is looked for is the
    # STEP off the foot's own. The old rule took the argmin of the front-to-back
    # *length*, which above the foot is flat to within noise: on the werewolf a
    # 13-vertex slab read 95.7 mm against its neighbours' 150 and carried the
    # ankle 65 mm up the shin.
    stations = []
    top = ground + reach * ANKLE_SEARCH_FRACTION
    step = max((top - ground) / float(FOOT_ANKLE_STATIONS - 1), 1e-6)
    for index in range(FOOT_ANKLE_STATIONS):
        height = ground + step * index
        slab = [p for p in points if abs(p.z - height) <= step * 0.75]
        if len(slab) < 4:
            continue
        stations.append({
            "z": height,
            "front": max(p.dot(forward) for p in slab),
            "length": (max(p.dot(forward) for p in slab)
                       - min(p.dot(forward) for p in slab)),
            "slab": slab,
        })
    if len(stations) < 4:
        return None
    # One smoothing pass, not two: the step is the signal and over-smoothing a
    # step is how you lose it. A single [1 2 1] is enough to absorb a sparse slab.
    smoothed = _smooth([entry["front"] for entry in stations], passes=1)
    for index, entry in enumerate(stations):
        entry["reach"] = smoothed[index]
    peak = max(range(len(stations)), key=lambda i: stations[i]["reach"])
    limit = stations[peak]["reach"] * FOOT_ANKLE_COLLAPSE_FRACTION
    collapse = None
    for index in range(peak + 1, len(stations)):
        # Smoothed *and* raw. The smoothing is what stops a sparse slab firing
        # the step early, and it is also what blurs the step across the station
        # either side of it: on the synthetic 100 mm boot the smoothed profile
        # collapses at 101 mm, on a slab that still has boot in it. Requiring the
        # station to be clear of the foot on its own numbers too moves that to
        # 108 -- genuinely above the shoe -- and leaves the werewolf's 105.7
        # exactly where it was, because there the raw profile had already fallen.
        if stations[index]["reach"] < limit and stations[index]["front"] < limit:
            collapse = index
            break
    how_ankle = "the height where the leg's forward reach collapses off the foot's"
    if collapse is None:
        # Nothing above the foot in the tag at all (a leg tagged to the ankle, a
        # cone): the old rule is the fallback and the report says it was used.
        shortest = min(entry["length"] for entry in stations)
        collapse = next(index for index, entry in enumerate(stations)
                        if entry["length"] <= shortest * 1.05)
        how_ankle = ("the shortest station front to back (this tag's forward reach "
                     "never collapses, so there is no foot-to-ankle step to find)")
    # The joint sits on the LEG COLUMN's centre at that height, not on the
    # centroid of a slab that still has half a heel in it: the stations above the
    # collapse are ankle and nothing else, so they are what says where the column
    # is. Measured on the werewolf the two answers are 35 mm apart.
    column = []
    for index in range(collapse + 1, min(collapse + 4, len(stations))):
        column.extend(stations[index]["slab"])
    if len(column) < 4:
        column = stations[collapse]["slab"]
    ankle = _centroid(column)
    ankle.z = stations[collapse]["z"]
    # Where the leg column *is*, front to back, as the midpoint of its own
    # extent rather than of its mass. That is the datum the "is this a foot or
    # the end cap of a bare leg" test below needs: the question it asks is
    # whether the sole is lopsided about the leg, and lopsidedness is a property
    # of the shape, not of how many vertices the retopo put where. Measured on
    # the werewolf the two answers are 11 mm apart, and the test passes on one
    # and fails by 3 mm on the other.
    column_forward = 0.5 * (max(p.dot(forward) for p in column)
                            + min(p.dot(forward) for p in column))
    ankle_length = stations[collapse]["length"]
    ankle_reach_mm = round(stations[collapse]["reach"] * M_TO_MM, 2)
    foot_reach_mm = round(stations[peak]["reach"] * M_TO_MM, 2)

    # --- is there a foot here at all?
    sole_top = ground + (ankle.z - ground) * FOOT_SOLE_FRACTION
    sole = [p for p in points if p.z <= sole_top]
    if len(sole) < 6:
        return None
    projections = [p.dot(forward) for p in sole]
    back, front = min(projections), max(projections)
    length = front - back
    if length < FOOT_LENGTH_FACTOR * max(ankle_length, 1e-6):
        return None
    at_ankle = column_forward
    ahead, behind = front - at_ankle, at_ankle - back
    if ahead < FOOT_FORWARD_FACTOR * max(behind, 1e-9):
        return None

    # --- the ball: where the sole's forward taper begins
    span = max(length / float(FOOT_STATIONS - 1), 1e-6)
    widths = []
    for index in range(FOOT_STATIONS):
        at = back + span * index
        slab = [p for p in sole if abs(p.dot(forward) - at) <= span * 0.75]
        if len(slab) < 3:
            widths.append((at, 0.0))
            continue
        widths.append((at, max(p.dot(right) for p in slab)
                       - min(p.dot(right) for p in slab)))
    widest = max(range(len(widths)), key=lambda i: widths[i][1])
    limit = widths[widest][1] * FOOT_TAPER_FRACTION
    ball_index = None
    for index in range(widest + 1, len(widths)):
        if widths[index][1] < limit:
            ball_index = index
            break
    how = "the sole's forward taper"
    if ball_index is None:
        # A sole with no taper (a block, a hoof): the ball is two thirds along,
        # which is where it is on a human foot, and the report says it is that.
        ball_index = int(round((len(widths) - 1) * 0.66))
        how = "two thirds along a sole with no measurable taper"
    ball_at = widths[ball_index][0]
    tip_at = front - FOOT_TIP_MARGIN_MM / M_TO_MM
    if tip_at <= ball_at:
        tip_at = ball_at + max((front - ball_at) * 0.5, 1e-4)

    def section_centre(at, band):
        """Where the foot bone runs at station ``at``: the cross-section's own
        centre **across** the foot, and a fixed fraction of that section's
        thickness above its **sole**.

        The sideways centre is the centroid's, as it always was — a bone runs
        through the middle of the toes, not along one wall of them.  The height
        is not.  The centroid of a whole 117 mm thick paw is 8 cm off the
        ground, and a toe bone 8 cm up pivots the foot roll about a point in the
        middle of the shoe: measured on the live werewolf, ``DEF-toe.L`` ran at
        ``z = 79 mm``.  A toe rolls just above the sole, so that is where it is
        put, by this station's own thickness rather than by a number.
        """
        slab = [p for p in points
                if abs(p.dot(forward) - at) <= band and p.z <= ankle.z]
        if len(slab) < 3:
            slab = [p for p in points if p.z <= ankle.z]
        centre = _centroid(slab)
        # Keep the station's own position along the facing axis: the centroid of
        # a slab is only its centre *across* the slab.
        centre = centre + forward * (at - centre.dot(forward))
        sole_z = min(p.z for p in slab)
        thickness = max(p.z for p in slab) - sole_z
        centre.z = sole_z + thickness * FOOT_TOE_LIFT_FRACTION
        return centre, sole_z, thickness

    ball, ball_sole_z, ball_thickness = section_centre(ball_at, span * 0.75)
    tip, tip_sole_z, tip_thickness = section_centre(tip_at, span * 0.75)
    # The toe runs level with the ball: a toe bone that dives at the tip rolls
    # the foot into the floor.
    tip.z = ball.z

    heel_slab = [p for p in sole if p.dot(forward) <= back + span * 0.75]
    heel = _centroid(heel_slab) if heel_slab else _centroid(sole)
    heel = heel + forward * (back - heel.dot(forward))
    heel.z = ground
    heel_width = ((max(p.dot(right) for p in heel_slab)
                   - min(p.dot(right) for p in heel_slab)) if len(heel_slab) >= 2
                  else length * 0.35)

    return {
        "ankle": ankle,
        "ball": ball,
        "toe_tip": tip,
        "heel": heel,
        "heel_width": heel_width,
        "right": right,
        "forward": forward,
        "ground": ground,
        "length_mm": round(length * M_TO_MM, 2),
        "ball_fraction": round((ball_at - back) / max(length, 1e-9), 3),
        "tip_margin_mm": FOOT_TIP_MARGIN_MM,
        "ankle_length_mm": round(ankle_length * M_TO_MM, 2),
        "ankle_height_mm": round((ankle.z - ground) * M_TO_MM, 2),
        "ankle_reach_mm": ankle_reach_mm,
        "foot_reach_mm": foot_reach_mm,
        "toe_height_mm": round((ball.z - ground) * M_TO_MM, 2),
        "ball_sole_mm": round((ball_sole_z - ground) * M_TO_MM, 2),
        "ball_thickness_mm": round(ball_thickness * M_TO_MM, 2),
        "toe_lift_fraction": FOOT_TOE_LIFT_FRACTION,
        "detail": {
            "ankle": {"how": "%s: the foot reaches %.1f mm in front of the leg and this "
                             "height reaches %.1f mm, %.0f%% of it (limit %.0f%%), so "
                             "the foot has stopped — %.1f mm above the sole, on the leg "
                             "column's own centre"
                             % (how_ankle, foot_reach_mm, ankle_reach_mm,
                                100.0 * ankle_reach_mm / max(foot_reach_mm, 1e-9),
                                100.0 * FOOT_ANKLE_COLLAPSE_FRACTION,
                                (ankle.z - ground) * M_TO_MM),
                      "height_mm": round((ankle.z - ground) * M_TO_MM, 2),
                      "foot_length_mm": round(length * M_TO_MM, 2)},
            "ball": {"how": "%s, %.0f%% of the foot's own %.1f mm thickness above the "
                            "sole there (%.1f mm up)"
                            % (how, 100.0 * FOOT_TOE_LIFT_FRACTION,
                               ball_thickness * M_TO_MM,
                               (ball.z - ground) * M_TO_MM),
                     "sole_fraction": round(
                         (ball_at - back) / max(length, 1e-9), 3),
                     "height_mm": round((ball.z - ground) * M_TO_MM, 2),
                     "thickness_mm": round(ball_thickness * M_TO_MM, 2)},
            "toe_tip": {"how": "the sole's forward extent, %.0f mm back from the last "
                               "vertex, level with the ball at %.1f mm above the sole"
                               % (FOOT_TIP_MARGIN_MM, (tip.z - ground) * M_TO_MM),
                        "height_mm": round((tip.z - ground) * M_TO_MM, 2),
                        "thickness_mm": round(tip_thickness * M_TO_MM, 2),
                        "sole_mm": round((tip_sole_z - ground) * M_TO_MM, 2)},
            "heel": {"how": "the sole's rearmost cross-section, at ground level"},
        },
    }


def hand_region(points, wrist, axis):
    """The hand itself: every point **distal to the wrist** along the arm axis.

    One line, and it is the same cut the wrist rule already makes — the wrist is
    a station on the arm axis, so "past it" is a half-space on that axis.  What
    matters is that this is the **last** thing the arm's frame is used for.
    Everything after it is measured in the hand's own.
    """
    wrist = Vector(wrist)
    axis = Vector(axis)
    if axis.length < 1e-9:
        return []
    axis = axis.normalized()
    return [p for p in points if (p - wrist).dot(axis) > 0.0]


def hand_axis(points, wrist, axis, label="hand"):
    """**Which way the palm points**, measured from the hand's own geometry.

    The bug this exists for is at the top of this module's hand constants: a
    knuckle taken from slabs cut perpendicular to the *arm's* axis walks outboard
    whenever the hand does not continue the arm, and no hand does.  On the live
    werewolf that put ``DEF-hand.L``'s tail 29.7 mm outside the mesh, pointing
    ``+X`` into the air beside a hand hanging straight down.

    So the hand region (:func:`hand_region`) is asked for its own long axis, by
    walking out from the wrist:

    1. aim at the region's centroid;
    2. take the centroid of its distal :data:`HAND_TIP_SLAB` — the slab of the
       hand's own **length** that far from the far end, measured along the
       current aim, widened if it does not hold a cross-section's worth of
       points — and aim at that instead;
    3. repeat, at most :data:`HAND_AXIS_PASSES` times or until it stops turning.

    It is a fixed-point iteration on "the middle of the far end", it is pure
    geometry, and on both of the werewolf's hands it converges in two passes —
    16.5 degrees, then 3.8, then 2.2, then 0.9.

    The slab is cut **by distance and not by rank**, and that is the whole
    difference between a fixed point and a hill: see the comment in the loop.

    The cloud's **principal axis** is measured too and *reported*, never used, as
    the second opinion this module always takes: a paw is a flat splayed blob
    whose dominant covariance axis is its width, and on the werewolf's right hand
    it pointed 73 degrees off the way the fingers actually go.  A statistic does
    not get to overrule walking to the end of the hand.

    ``reach`` is where the hand **stops**, and it is deliberately not the raw
    maximum projection: an arm tag carries loose islands, and the werewolf's
    right arm has four vertices 80 mm past the last real cross-section which on
    their own stretched the hand from 99 mm to 184 mm — nearly double.  It is
    the :data:`HAND_REACH_PERCENTILE` of the region's projections instead: a
    fingertip is *many* vertices and a stray island is a handful, so trimming the
    last few percent drops the island and leaves the fingers, and both numbers
    are reported so the trim is never silent.

    Returns ``None`` when there is no region to measure, else a dict.
    """
    axis = Vector(axis)
    if axis.length < 1e-9:
        return None
    axis = axis.normalized()
    wrist = Vector(wrist)
    region = hand_region(points, wrist, axis)
    if len(region) < HAND_REGION_MIN_POINTS:
        return None
    centre = _centroid(region)
    direction = centre - wrist
    direction = direction.normalized() if direction.length > 1e-9 else axis.copy()
    turns = []
    for _pass in range(HAND_AXIS_PASSES):
        projections = [(p - wrist).dot(direction) for p in region]
        near, far = min(projections), max(projections)
        if far <= 1e-9:
            return None
        # **The distal slab is a cross-section**, picked by how far along the
        # hand a point is — not "the distal quarter of the points". Ranking the
        # points looks equivalent and is not, and the difference is a runaway:
        # the moment the aim tilts by a hair, the top quarter *by rank* is a
        # partial ring rather than a whole one, that partial ring's centroid sits
        # out on the rim, and the next aim tilts further towards it. Measured on
        # the synthetic biped's 32-vertex two-ring hand, the loop walked off the
        # tube's own axis and settled 26 degrees away on the rim — and the
        # placement pass and the gate, starting from slightly different clouds,
        # settled on *opposite* rims of the same cone, 38 degrees apart. Cutting
        # by distance keeps whole rings together, so a cone's slab is a ring and
        # its centroid is on the axis, which is a fixed point rather than a hill.
        span = max(far - near, 1e-9)
        fraction = HAND_TIP_SLAB
        slab = []
        for _widen in range(5):
            cut = near + span * (1.0 - fraction)
            slab = [point for index, point in enumerate(region)
                    if projections[index] >= cut]
            if len(slab) >= MIN_SECTION_POINTS:
                break
            fraction = min(1.0, fraction * 1.6)
        if not slab:
            break
        nxt = _centroid(slab) - wrist
        if nxt.length < 1e-9:
            break
        nxt = nxt.normalized()
        turns.append(round(math.degrees(direction.angle(nxt, 0.0)), 3))
        direction = nxt
        if turns[-1] < 0.05:
            break

    ordered = sorted((p - wrist).dot(direction) for p in region)
    raw_reach = ordered[-1]
    if raw_reach <= 1e-9:
        return None
    reach = ordered[int(round(HAND_REACH_PERCENTILE * (len(ordered) - 1)))]
    if reach <= 1e-9:
        reach = raw_reach
    principal = _principal_axis(region, centre)
    if principal.dot(direction) < 0.0:
        principal = -principal
    return {
        "direction": direction,
        "region": region,
        "reach": reach,
        "raw_reach": raw_reach,
        "centroid": centre,
        "principal": principal,
        "turns_deg": turns,
        "points": len(region),
        "arm_angle_deg": round(math.degrees(direction.angle(axis, 0.0)), 2),
        "principal_angle_deg": round(
            math.degrees(direction.angle(principal, 0.0)), 2),
        "label": label,
        "how": ("the hand region's own long axis: %d points past the wrist, aimed at "
                "the centroid of their distal %.0f%% and re-aimed %d time(s) until it "
                "stopped turning (%s degrees). It reaches %.1f mm (%.1f mm before the "
                "distal %.0f%% of stray points were trimmed) and sits %.1f degrees off "
                "the arm's axis, which is the whole reason a slab cut in the arm's "
                "frame gets this wrong."
                % (len(region), 100.0 * HAND_TIP_SLAB, len(turns),
                   ", ".join("%.1f" % t for t in turns) or "0.0",
                   reach * M_TO_MM, raw_reach * M_TO_MM,
                   100.0 * (1.0 - HAND_REACH_PERCENTILE),
                   math.degrees(direction.angle(axis, 0.0)))),
    }


def _hand_section_centre(region, wrist, direction, at, band):
    """The hand's cross-section centroid ``at`` metres along the palm axis.

    The slab widens until it holds a section rather than a sliver, exactly as
    :meth:`Limb._slice` does, and the result is pushed back onto the station it
    was asked for — a slab's centroid is only its centre *across* the slab.
    """
    half = band
    slab = []
    for _widen in range(6):
        slab = [p for p in region if abs((p - wrist).dot(direction) - at) <= half]
        if len(slab) >= MIN_SECTION_POINTS:
            break
        half *= 1.6
    if len(slab) < 3:
        slab = list(region)
    centre = _centroid(slab)
    return centre + direction * (at - (centre - wrist).dot(direction))


def hand_landmarks(points, elbow, axis, label="hand"):
    """The wrist, the palm direction and the knuckle line — from the hand itself.

    The foot's problem, at the other end of the arm, and it took two passes to
    finish.  The first fixed *where* the wrist is; this one fixes **which way the
    hand points**, which is a different question and was still wrong after the
    first.

    * **the wrist** is the *first* local minimum of the arm's girth walking
      distally from mid-limb — the taper of the forearm bottoming out, with the
      palm widening after it.  It is deliberately not "the narrowest station in
      the distal band": the **fingers are narrower than the wrist**, so that rule
      walks straight past the palm and lands in them.  It is also not *any* local
      minimum: the limb has to fatten again after it by :data:`HAND_WRIST_RISE`,
      because a 2.3% wobble two stations into the band is a local minimum in the
      strict sense and nothing at all in the flesh — and on the live werewolf
      that wobble is what threw the left hand away;
    * **the palm direction** is :func:`hand_axis`: the hand region's own long
      axis, walked out from the wrist.  Everything below is measured along *it*
      and not along the arm, which is the fix for a hand bone that pointed
      outboard out of the mesh;
    * **the knuckle line** is where the palm tapers into fingers — from the
      palm's widest station, the first under :data:`HAND_TAPER_FRACTION` of it —
      now measured in the hand's own frame, and believed only inside
      :data:`HAND_KNUCKLE_BAND`.  Outside it (or with no taper at all) the tail
      goes to :data:`HAND_KNUCKLE_FRACTION` of the hand's reach and the report
      says which of the two it was.  Either way the tail is the **cross-section
      centroid** at that station, so it lands in the flesh rather than on a ray.

    Returns ``None`` when the arm tag has no hand in it — nothing reaching
    :data:`HAND_REACH_FACTOR` wrist-girths past the wrist — which is a legitimate
    thing to be handed and not a thing to invent a palm out of.
    """
    if len(points) < 12:
        return None
    axis = Vector(axis)
    if axis.length < 1e-9:
        return None
    axis = axis.normalized()
    projections = [(p - elbow).dot(axis) for p in points]
    far = max(projections)
    if far < 1e-6:
        return None
    start = far * HAND_BAND
    step = max((far - start) / float(HAND_STATIONS - 1), 1e-6)
    stations = []
    for index in range(HAND_STATIONS):
        at = start + step * index
        slab = [p for position, p in enumerate(points)
                if abs(projections[position] - at) <= step * 0.75]
        if len(slab) < 4:
            continue
        centre = _centroid(slab)
        girth = sum(((p - centre) - axis * (p - centre).dot(axis)).length
                    for p in slab) / float(len(slab))
        stations.append({"at": at, "girth": girth, "centre": centre, "slab": slab})
    if len(stations) < 5:
        return None
    girths = _smooth([entry["girth"] for entry in stations])
    for index, entry in enumerate(stations):
        entry["girth"] = girths[index]

    # --- the wrist: the first place the taper bottoms out AND the limb fattens
    # again afterwards. The second half of that sentence is the whole fix for the
    # werewolf's left hand: without it a 2.3% wobble at the second station is
    # "the wrist", nothing past it looks like a palm, and a real hand is thrown
    # away. The rise is measured against everything distal, because a palm can be
    # several stations further on than the wobble that pretended to be a wrist.
    wrist_index = None
    wrist_rise = None
    rejected = []
    for index in range(1, len(stations) - 1):
        if not (stations[index]["girth"] <= stations[index - 1]["girth"]
                and stations[index]["girth"] < stations[index + 1]["girth"]):
            continue
        after_max = max(entry["girth"] for entry in stations[index + 1:])
        rise = (after_max - stations[index]["girth"]) / max(stations[index]["girth"],
                                                            1e-9)
        if rise >= HAND_WRIST_RISE:
            wrist_index = index
            wrist_rise = rise
            break
        rejected.append((index, rise))
    how_wrist = ("the first girth minimum walking down the arm that the limb fattens "
                 "back up from (by %.1f%%, over the %.0f%% a wobble is allowed) — the "
                 "forearm's taper bottoming out, with the palm widening after it"
                 % (100.0 * (wrist_rise or 0.0), 100.0 * HAND_WRIST_RISE))
    if wrist_index is None:
        wrist_index = min(range(len(stations)), key=lambda i: stations[i]["girth"])
        how_wrist = ("the narrowest station in the arm's distal half (no girth minimum "
                     "with a palm behind it — this arm's girth falls all the way to "
                     "its end)")
    if rejected:
        how_wrist += ("; %d earlier minimum(s) were wobbles and were passed over (%s)"
                      % (len(rejected),
                         ", ".join("%.1f%%" % (100.0 * r) for _i, r in rejected)))

    wrist = stations[wrist_index]["centre"].copy()
    wrist = wrist + axis * (stations[wrist_index]["at"] - (wrist - elbow).dot(axis))
    wrist_girth = stations[wrist_index]["girth"]

    # --- is there a hand past it, and WHICH WAY DOES IT POINT?
    palm_axis = hand_axis(points, wrist, axis, label=label)
    if palm_axis is None:
        return None
    if palm_axis["reach"] < HAND_REACH_FACTOR * max(wrist_girth, 1e-9):
        return None
    direction = palm_axis["direction"]
    region = palm_axis["region"]
    reach = palm_axis["reach"]

    # --- the hand's own cross-sections, across the palm axis and not the arm's
    step = max(reach / float(HAND_STATIONS - 1), 1e-6)
    hand_stations = []
    for index in range(HAND_STATIONS):
        at = step * index
        slab = [p for p in region if abs((p - wrist).dot(direction) - at) <= step * 0.75]
        if len(slab) < 4:
            continue
        centre = _centroid(slab)
        girth = sum(((p - centre) - direction * (p - centre).dot(direction)).length
                    for p in slab) / float(len(slab))
        hand_stations.append({"at": at, "girth": girth, "centre": centre,
                              "count": len(slab)})
    if len(hand_stations) < 3:
        return None
    girths = _smooth([entry["girth"] for entry in hand_stations])
    for index, entry in enumerate(hand_stations):
        entry["girth"] = girths[index]

    widest = max(range(len(hand_stations)), key=lambda i: hand_stations[i]["girth"])
    limit = hand_stations[widest]["girth"] * HAND_TAPER_FRACTION
    knuckle_at = None
    knuckle_girth = None
    for index in range(widest + 1, len(hand_stations)):
        if hand_stations[index]["girth"] < limit:
            knuckle_at = hand_stations[index]["at"]
            knuckle_girth = hand_stations[index]["girth"]
            break
    fraction = None if knuckle_at is None else knuckle_at / max(reach, 1e-9)
    how_knuckle = ("the palm's taper into the fingers, measured across the hand's own "
                   "axis (%.0f%% of the way along it)" % (100.0 * (fraction or 0.0)))
    if knuckle_at is None:
        # A mitten with no taper at all, or a hand tagged only to the palm.
        knuckle_at = HAND_KNUCKLE_FRACTION * reach
        how_knuckle = ("%.0f%% of the hand's own reach past the wrist (its palm never "
                       "tapers, so there is no knuckle line to find)"
                       % (100.0 * HAND_KNUCKLE_FRACTION))
    elif not (HAND_KNUCKLE_BAND[0] <= fraction <= HAND_KNUCKLE_BAND[1]):
        how_knuckle = ("%.0f%% of the hand's own reach past the wrist (the measured "
                       "taper sits at %.0f%% along it, outside the %.0f-%.0f%% a "
                       "knuckle line can be — that is the wrist ring again or the end "
                       "cap, not a knuckle)"
                       % (100.0 * HAND_KNUCKLE_FRACTION, 100.0 * fraction,
                          100.0 * HAND_KNUCKLE_BAND[0], 100.0 * HAND_KNUCKLE_BAND[1]))
        knuckle_at = HAND_KNUCKLE_FRACTION * reach

    knuckle = _hand_section_centre(region, wrist, direction, knuckle_at, step * 0.75)
    palm = (knuckle - wrist).length
    bone = (knuckle - wrist)
    bone_angle = (math.degrees(bone.normalized().angle(direction, 0.0))
                  if bone.length > 1e-9 else 0.0)
    return {
        "wrist": wrist,
        "knuckle": knuckle,
        "palm_axis": direction,
        "palm_reach_mm": round(reach * M_TO_MM, 2),
        "palm_mm": round(palm * M_TO_MM, 2),
        "wrist_girth_mm": round(wrist_girth * M_TO_MM, 2),
        "palm_girth_mm": round(hand_stations[widest]["girth"] * M_TO_MM, 2),
        "tip_mm": round(palm_axis["raw_reach"] * M_TO_MM, 2),
        "knuckle_fraction": round(knuckle_at / max(reach, 1e-9), 3),
        "bone_vs_palm_axis_deg": round(bone_angle, 2),
        "arm_angle_deg": palm_axis["arm_angle_deg"],
        "principal_angle_deg": palm_axis["principal_angle_deg"],
        "axis_detail": palm_axis["how"],
        "detail": {
            "wrist": {"how": how_wrist,
                      "girth_mm": round(wrist_girth * M_TO_MM, 2)},
            "palm_axis": {"how": palm_axis["how"],
                          "axis": [round(v, 5) for v in direction],
                          "arm_angle_deg": palm_axis["arm_angle_deg"],
                          "principal_angle_deg": palm_axis["principal_angle_deg"],
                          "reach_mm": round(reach * M_TO_MM, 2)},
            "knuckle": {"how": how_knuckle,
                        "palm_girth_mm": round(
                            hand_stations[widest]["girth"] * M_TO_MM, 2),
                        "girth_mm": (None if knuckle_girth is None
                                     else round(knuckle_girth * M_TO_MM, 2)),
                        "bone_vs_palm_axis_deg": round(bone_angle, 2),
                        "fraction_of_hand": round(knuckle_at / max(reach, 1e-9), 3)},
        },
    }


def facing_vector(faces):
    """The unit vector a character described as ``"-Y"``/``"+X"``/… looks along."""
    table = {"+X": Vector((1.0, 0.0, 0.0)), "-X": Vector((-1.0, 0.0, 0.0)),
             "+Y": Vector((0.0, 1.0, 0.0)), "-Y": Vector((0.0, -1.0, 0.0))}
    if isinstance(faces, str):
        return table.get(faces.strip().upper(), Vector((0.0, -1.0, 0.0))).copy()
    vector = Vector(faces)
    return vector.normalized() if vector.length > 1e-9 else Vector((0.0, -1.0, 0.0))


def prebend_joint(limb, proximal, joint, distal, forward, what, side,
                  fraction=None, station_fraction=None):
    """Guarantee a limb's rest pose carries its **anatomical** pre-bend.

    This is the missed human practice, written out.  A rigger places a knee a
    little *forward* of the line from hip to ankle and an elbow a little *back*
    of the line from shoulder to wrist, always, before anything is animated,
    because a straight limb is **ambiguous to IK**: three collinear points give
    the solver no plane, Rigify derives the limb's pole angle from that same rest
    plane, and the limb then folds whichever way it falls.  On the live werewolf
    it fell backwards — the knee sat **25.9 mm behind** the hip-to-ankle line
    (4.2% of a 620 mm span) and lifting ``foot_ik.L`` drove the knee *backwards*.

    Three cases, and only one of them moves anything:

    * the chain already apexes the right way by at least ``fraction`` of its own
      span — **untouched**, and the report says so;
    * it apexes the right way but not far enough, or it is collinear — pushed out
      to exactly ``fraction``;
    * it apexes the **wrong** way — pushed through zero to ``+fraction``, which
      is the case the old magnitude-only test missed, because a knee 4.2% out of
      line *backwards* passes a test that only asks how far from straight it is.

    The push is clamped so it cannot leave the flesh:
    :meth:`Limb.section_reach` measures how far the limb's own cross-section
    extends in the push direction at that station (the same ring-and-gap gates
    :func:`bone_centering` applies), and the joint may travel at most
    :data:`PREBEND_SECTION_FRACTION` of it.  A clamp is reported, never silent.

    Returns ``(point, report)``.  ``point`` is the nudged joint; ``report`` names
    what happened, in millimetres, and why.
    """
    # Resolved here rather than as a default argument so that changing the
    # module constant changes the behaviour: a default is bound once, at import,
    # and a sweep of it would silently measure the same number four times.
    fraction = PREBEND_FRACTION if fraction is None else float(fraction)
    proximal = Vector(proximal)
    joint = Vector(joint)
    distal = Vector(distal)
    span = distal - proximal
    report = {"joint": "%s.%s" % (what, side), "limb": getattr(limb, "tag", None),
              "span_mm": round(span.length * M_TO_MM, 2),
              "target_fraction_pct": round(100.0 * fraction, 2)}
    if span.length < 1e-9:
        report.update({"nudged": False, "why": "the limb has no length to bend along"})
        return joint, report

    axis = span.normalized()
    want = PREBEND_DIRECTION.get(what, 1.0)
    push = Vector(forward) * want
    push = push - axis * push.dot(axis)
    if push.length < 1e-9:
        # The limb runs along the facing axis (a character lying down, a limb
        # pointing straight at the camera): there is no forward left to bend
        # into, so nothing is invented.
        report.update({"nudged": False,
                       "why": ("the limb runs along the direction the character faces, "
                               "so 'forward' has no component across it")})
        return joint, report
    push.normalize()

    offset = (joint - proximal) - axis * (joint - proximal).dot(axis)
    signed = offset.dot(push)
    need = fraction * span.length
    report.update({
        "direction": "forward" if want > 0 else "backward",
        "before_mm": round(signed * M_TO_MM, 2),
        "before_pct_of_span": round(100.0 * signed / span.length, 2),
        "off_line_mm": round(offset.length * M_TO_MM, 2),
        "required_mm": round(need * M_TO_MM, 2),
    })
    if signed >= need:
        report.update({"nudged": False, "after_mm": report["before_mm"],
                       "why": ("already apexes %s by %.1f mm (%.1f%% of its span), which "
                               "is more than the %.1f mm minimum — left exactly as "
                               "measured"
                               % (report["direction"], signed * M_TO_MM,
                                  100.0 * signed / span.length, need * M_TO_MM))})
        return joint, report

    wanted_push = need - signed
    limit = None
    limit_why = None
    if limb is not None and station_fraction is not None:
        station = limb.nearest_station(station_fraction)
        reach, why = limb.section_reach(station, push)
        if reach is None:
            limit_why = why
        else:
            # How much room is left between the joint and the skin, in the push
            # direction: the section's own half-width less whatever of it the
            # joint has already spent going the other way.
            room = reach * PREBEND_SECTION_FRACTION
            limit = max(room, 0.0)
            report["section_reach_mm"] = round(reach * M_TO_MM, 2)
            report["section_limit_mm"] = round(limit * M_TO_MM, 2)
    if limit is None and limb is not None:
        limit = getattr(limb, "median_girth", 0.0) * PREBEND_SECTION_FRACTION
        report["section_limit_mm"] = round(limit * M_TO_MM, 2)
        report["section_note"] = ("measured against the limb's median girth because %s"
                                  % (limit_why or "the station has no cross-section"))

    applied = wanted_push
    clamped = False
    if limit is not None and limit > 0.0 and applied > limit:
        applied = limit
        clamped = True
    elif limit is not None and limit <= 0.0:
        applied = 0.0
        clamped = True

    nudged = joint + push * applied
    after = signed + applied
    report.update({
        "nudged": applied > 1e-9,
        "nudge_mm": round(applied * M_TO_MM, 2),
        "after_mm": round(after * M_TO_MM, 2),
        "after_pct_of_span": round(100.0 * after / span.length, 2),
        "clamped_by_flesh": bool(clamped),
        "push": [round(v, 5) for v in push],
    })
    if applied <= 1e-9:
        report["why"] = ("the cross-section here leaves no room to bend %s, so the joint "
                         "was left where the mesh put it and the ambiguity stands"
                         % report["direction"])
    elif signed < 0.0:
        report["why"] = ("the chain apexed %s — the WRONG way — by %.1f mm, so the joint "
                         "was moved %.1f mm %s to %+.1f mm; a limb bent the wrong way at "
                         "rest is a limb IK bends the wrong way for ever"
                         % ("backward" if want > 0 else "forward",
                            abs(signed) * M_TO_MM, applied * M_TO_MM,
                            report["direction"], after * M_TO_MM))
    else:
        report["why"] = ("the chain was within %.1f mm of straight (%.1f mm of a %.1f mm "
                         "minimum), so the joint was moved %.1f mm %s to %+.1f mm to give "
                         "the IK solver an unambiguous plane"
                         % (need * M_TO_MM, signed * M_TO_MM, need * M_TO_MM,
                            applied * M_TO_MM, report["direction"], after * M_TO_MM))
    if clamped:
        report["why"] += ("; the full %.1f mm did not fit inside the limb's own "
                          "cross-section and was clamped to %.1f mm"
                          % (wanted_push * M_TO_MM, applied * M_TO_MM))
    return nudged, report


def stance_flex(limb, proximal, joint, distal, forward, what, side,
                span_fraction=None, station_fraction=None, up=None):
    """Stand the limb on a **flexed** joint, the way a person poses a rest stance.

    :func:`prebend_joint` above answers *which way* the joint folds.  This
    answers *how much slack the chain has left*, which is a different question
    and the one the wip-14 audit put a number on: a leg whose hip sits 0.9984 of
    its own chain length from its ankle has 1.3 mm of reach in hand, so the
    first hip translation of the first clip is already at the solver's limit.
    From there IK either pops or — with Rigify's stock ``IK_Stretch = 1.0`` —
    grows the bones, which is the 261.7 mm of stretched leg the artist saw.

    **The three joints do not all move.**  A rigger posing a relaxed stance
    leaves the foot on the floor, lets the hips sink, and the knee comes forward
    to meet them; that is what this does, in that order:

    * the **ankle does not move at all** — it is the sole-fitted landmark
      (:func:`foot_landmarks`) and every foot bone below it hangs off it, so
      moving it would undo the fit and the floor contact with it;
    * the **hip drops** along ``up`` until its distance to the ankle is
      ``span_fraction`` of the chain — the stance's whole purpose, solved as a
      quadratic rather than iterated;
    * the **knee** is then the one point that satisfies both bone lengths, and
      it lands *forward*, along the same :data:`PREBEND_DIRECTION` the pre-bend
      used, so the two rules cannot disagree about which way a knee goes.

    **Neither bone changes length**, which is what makes the fraction mean what
    ``rigcheck.ik_reach_headroom_rest`` measures: that gate's denominator is the
    summed DEF chain and its numerator is hip-to-ankle.  Any sideways offset the
    measured knee already carried (a bow-legged character) is **kept** — only
    the forward component is solved for.

    The knee angle is *derived*, never given: ``phi = acos((u^2 + l^2 - d^2) /
    (2ul))``.  It is reported in degrees so the number can be argued with.

    Returns ``(proximal, joint, report)`` — the moved hip, the moved knee, and
    what happened in millimetres.
    """
    fraction = STANCE_SPAN_FRACTION if span_fraction is None else float(span_fraction)
    proximal = Vector(proximal)
    joint = Vector(joint)
    distal = Vector(distal)
    upper = (joint - proximal).length
    lower = (distal - joint).length
    chain = upper + lower
    span = (distal - proximal).length
    report = {"joint": "%s.%s" % (what, side), "limb": getattr(limb, "tag", None),
              "target_span_fraction": round(fraction, 4),
              "chain_mm": round(chain * M_TO_MM, 2),
              "upper_mm": round(upper * M_TO_MM, 2),
              "lower_mm": round(lower * M_TO_MM, 2),
              "span_before_mm": round(span * M_TO_MM, 2),
              "extension_frac_before": (round(span / chain, 4) if chain > 1e-9
                                        else None)}
    if chain <= 1e-9 or span <= 1e-9:
        report.update({"flexed": False, "span_after_mm": report["span_before_mm"],
                       "extension_frac_after": report["extension_frac_before"],
                       "why": "the limb has no length to stand on"})
        return proximal, joint, report

    target = fraction * chain
    if span <= target + 1e-9:
        report.update({
            "flexed": False,
            "span_after_mm": report["span_before_mm"],
            "extension_frac_after": report["extension_frac_before"],
            "knee_flex_deg": round(_flex_degrees(upper, lower, span), 2),
            "hip_drop_mm": 0.0, "knee_travel_mm": 0.0,
            "why": ("already stands at %.4f of its own chain, inside the %.2f the "
                    "stance asks for — left exactly as measured"
                    % (span / chain, fraction)),
        })
        return proximal, joint, report

    up = Vector(up) if up is not None else Vector((0.0, 0.0, 1.0))
    up = up.normalized() if up.length > 1e-9 else Vector((0.0, 0.0, 1.0))
    v = proximal - distal
    rise = v.dot(up)
    disc = rise * rise - v.length_squared + target * target
    if rise > 1e-9 and disc >= 0.0:
        drop = rise - math.sqrt(disc)
        how = "lowered along %s" % _axis_word(up)
    else:
        # The hip is not above the ankle (a limb measured lying down, a
        # quadruped's foreleg): there is no "height" to lose, so the hip is
        # brought in along the chord instead and the report says which it was.
        drop = None
        how = ("pulled straight in along the hip-to-ankle chord, because the hip "
               "is not above the ankle and there is no height to lower")
    if drop is not None:
        new_proximal = proximal - up * drop
    else:
        new_proximal = distal + v.normalized() * target
        drop = (proximal - new_proximal).length

    axis_vector = distal - new_proximal
    distance = axis_vector.length
    if distance <= 1e-9:
        report.update({"flexed": False, "span_after_mm": report["span_before_mm"],
                       "extension_frac_after": report["extension_frac_before"],
                       "why": "the stance collapsed the chain onto a point"})
        return proximal, joint, report
    axis = axis_vector / distance

    want = PREBEND_DIRECTION.get(what, 1.0)
    push = Vector(forward) * want
    push = push - axis * push.dot(axis)
    if push.length < 1e-9:
        report.update({
            "flexed": False,
            "span_after_mm": report["span_before_mm"],
            "extension_frac_after": report["extension_frac_before"],
            "why": ("the limb runs along the direction the character faces, so there "
                    "is no forward left for the joint to travel into"),
        })
        return proximal, joint, report
    push.normalize()

    # Whatever sideways placement the measured joint already had is kept: only
    # the forward component is solved for.  ``side_offset`` is the part of the
    # old offset perpendicular to BOTH the chain and the push.
    old_offset = (joint - new_proximal) - axis * (joint - new_proximal).dot(axis)
    sideways = old_offset - push * old_offset.dot(push)
    along = (upper * upper - lower * lower + distance * distance) / (2.0 * distance)
    height_sq = upper * upper - along * along - sideways.length_squared
    trimmed = False
    if height_sq < 0.0:
        # A knee carrying a big sideways offset can leave no room for the
        # forward one. The forward travel is what the stance is for, so the
        # sideways component is what gives way - and it is said.
        room = max(upper * upper - along * along, 0.0)
        if sideways.length > 1e-9 and room > 0.0:
            sideways = sideways.normalized() * math.sqrt(room) * 0.5
            height_sq = room - sideways.length_squared
        else:
            sideways = Vector((0.0, 0.0, 0.0))
            height_sq = room
        trimmed = True
    height = math.sqrt(max(height_sq, 0.0))
    new_joint = new_proximal + axis * along + sideways + push * height

    achieved = (distal - new_proximal).length
    flex_deg = _flex_degrees(upper, lower, achieved)
    report.update({
        "flexed": True,
        "direction": "forward" if want > 0 else "backward",
        "hip_moved": how,
        "hip_drop_mm": round(drop * M_TO_MM, 2),
        "span_after_mm": round(achieved * M_TO_MM, 2),
        "extension_frac_after": round(achieved / chain, 4),
        "headroom_before_mm": round((chain - span) * M_TO_MM, 2),
        "headroom_after_mm": round((chain - achieved) * M_TO_MM, 2),
        "knee_flex_deg": round(flex_deg, 2),
        "knee_travel_mm": round((new_joint - joint).length * M_TO_MM, 2),
        "knee_offline_mm": round(height * M_TO_MM, 2),
        "sideways_trimmed": bool(trimmed),
        "push": [round(value, 5) for value in push],
    })
    if limb is not None and station_fraction is not None:
        station = limb.nearest_station(station_fraction)
        reach, why = limb.section_reach(station, push)
        if reach is None:
            report["section_note"] = why
        else:
            report["section_reach_mm"] = round(reach * M_TO_MM, 2)
            report["inside_flesh"] = bool(height <= reach)
    report["why"] = (
        "the chain stood at %.4f of its own %.1f mm length (%.1f mm of headroom); the "
        "hip was %s by %.1f mm and the %s travelled %.1f mm %s to %.1f mm off the "
        "hip-to-ankle line, which lands the stance at %.4f — a %.1f degree flex, "
        "derived from the %.2f span target and this limb's own %.1f / %.1f mm bones"
        % (span / chain, chain * M_TO_MM, (chain - span) * M_TO_MM, how,
           drop * M_TO_MM, what, (new_joint - joint).length * M_TO_MM,
           report["direction"], height * M_TO_MM, achieved / chain, flex_deg,
           fraction, upper * M_TO_MM, lower * M_TO_MM))
    return new_proximal, new_joint, report


def get_stance_span(params):
    """``stance_span`` off a command's parameters, or the module default.

    One reader for every command that fits landmarks, so ``rigforge_landmarks``,
    ``rigforge_metarig`` and ``rigforge_generate_rig`` cannot end up standing the
    same character three different ways.  ``rest_stance: "straight"`` is the
    task-config spelling of ``1.0`` and is accepted here too, so the setting can
    move into ``mcp/forge_mcp/task_config.py`` without this file changing again.
    """
    rest = params.get("rest_stance") if isinstance(params, dict) else None
    if isinstance(rest, str) and rest.strip():
        word = rest.strip().lower()
        if word in ("straight", "none", "off"):
            return 1.0
        if word not in ("flexed", "flex", "default"):
            raise ForgeError("'rest_stance' must be \"flexed\" or \"straight\", got %r."
                             % rest)
    if isinstance(params, dict) and params.get("stance_span") is not None:
        return get_float(params, "stance_span", STANCE_SPAN_FRACTION,
                         minimum=0.5, maximum=1.0)
    return STANCE_SPAN_FRACTION


def _flex_degrees(upper, lower, span):
    """How many degrees off straight the joint between two bones sits."""
    if upper <= 1e-9 or lower <= 1e-9:
        return 0.0
    cosine = (upper * upper + lower * lower - span * span) / (2.0 * upper * lower)
    cosine = max(-1.0, min(1.0, cosine))
    return 180.0 - math.degrees(math.acos(cosine))


def _axis_word(vector):
    """``+Z`` for the world up, or the vector itself when it is not an axis."""
    for index, name in ((0, "X"), (1, "Y"), (2, "Z")):
        if abs(vector[index]) > 0.999:
            return "%s%s" % ("+" if vector[index] > 0 else "-", name)
    return "(%.3f, %.3f, %.3f)" % (vector[0], vector[1], vector[2])


def biped_landmarks(obj, clouds=None, midplane=0.0, character_left=1.0,
                    warnings=None, sides=("L",), axis_hints=None, facing="-Y",
                    stance_span=None):
    """Every joint of a biped, measured off the mesh, **one side by default**.

    ``axis_hints`` (``{"arm.L": Vector, "leg.R": Vector, …}``, proximal to
    distal) replaces the anatomical guess about which way each limb runs with a
    *measurement* of it — see :func:`_limb_hint`.  Omitted, every hint is the
    T-pose default this function has always used.

    With the default ``sides=("L",)`` the right side is not computed at all: it
    is the mirror of this, produced by :func:`mirror_edit_bones` once the left is
    placed.  That is the human workflow and it is the reason the asymmetry is 0.0
    rather than 6-24 mm.  ``sides=("L", "R")`` is the ``symmetry: false`` path —
    a character that is *meant* to be asymmetric gets each side measured from its
    own geometry and nothing is mirrored.

    ``facing`` is the direction the character looks along (``"-Y"`` by
    convention, or a vector).  It is what makes the **anatomical pre-bend**
    anatomical: every knee is nudged forward and every elbow backward relative to
    it, by :func:`prebend_joint`, *before* the caller mirrors — so both sides get
    the identical nudge and the mirror stays at 0.0 mm.

    ``stance_span`` is the flexed rest stance (:data:`STANCE_SPAN_FRACTION`,
    0.97 of the leg's own chain): it runs straight after the pre-bend, on the
    same side, for the same reason — the hip drops, the knee comes forward, the
    **ankle does not move**, and the mirror copies the result.  ``1.0`` rests the
    legs straight, which is what every rig built before this did.

    Returns ``{"points": {role: Vector}, "detail": {role: how it was found},
    "limbs": {...}, "prebend": [...], "stance": [...], "warnings": [...]}``.  Raises
    :class:`ForgeError` when the mesh cannot support landmarks at all, which is
    the caller's signal to fall back to the old tag-fraction fit and *say so*.
    """
    warnings = warnings if warnings is not None else []
    clouds = clouds if clouds is not None else tag_clouds(obj)
    points = {}
    detail = {}
    limbs = {}
    forward = facing_vector(facing)
    stance_span = (STANCE_SPAN_FRACTION if stance_span is None
                   else max(0.5, min(1.0, float(stance_span))))
    prebends = []
    stances = []
    feet = {}
    hands = {}

    torso_tag = _pick(clouds, "Torso", "Body", "Chest", "Spine")
    head_tag = _pick(clouds, "Head", "Skull")
    arms = _sided(clouds, "Arm|UpperArm|Foreleg")
    legs = _sided(clouds, "Leg|Thigh|Hindleg")
    if torso_tag is None or head_tag is None:
        raise ForgeError(
            "Landmark fitting needs at least a Torso tag and a Head tag on %r; it "
            "found %s. Tag the body and run again, or pass method='tags' for the old "
            "fraction-of-the-blob fit." % (obj.name, ", ".join(sorted(clouds)) or "none"))
    if not legs and not arms:
        raise ForgeError(
            "Landmark fitting found no Arm or Leg tags on %r, so there is no limb to "
            "take a cross-section of." % obj.name)

    torso = clouds[torso_tag]
    head = clouds[head_tag]
    torso_low, torso_high = _bounds(torso)
    head_low, head_high = _bounds(head)

    def on_midplane(point):
        out = Vector(point)
        out.x = midplane
        return out

    def slab_centre(cloud, z_low, z_high):
        chosen = [p for p in cloud if z_low - 1e-9 <= p.z <= z_high + 1e-9]
        if not chosen:
            return _centroid(cloud)
        return _centroid(chosen)

    # --- the spine and the neck: on the midplane by construction ----------
    neck_base_z = 0.5 * (torso_high.z + head_low.z)
    half = max(torso_high.z - torso_low.z, 1e-6) * 0.06
    neck_base = on_midplane(slab_centre(torso, neck_base_z - half, neck_base_z + half))
    neck_base.z = neck_base_z
    points["neck_base"] = neck_base
    detail["neck_base"] = {"how": "torso cross-section centroid at the chest/head "
                                  "midpoint, snapped to the midplane"}

    hips_z = torso_low.z
    for step in range(5):
        z = hips_z + (neck_base_z - hips_z) * (step / 4.0)
        band = max((neck_base_z - hips_z) * 0.12, 1e-6)
        centre = on_midplane(slab_centre(torso, z - band, z + band))
        centre.z = z
        role = "hips" if step == 0 else ("neck_base" if step == 4 else "spine_%02d" % step)
        if role != "neck_base":
            points[role] = centre
            detail[role] = {"how": "torso cross-section centroid, on the midplane"}

    head_top_z = head_high.z
    neck_top_z = head_low.z + (head_top_z - head_low.z) * 0.15
    if neck_top_z <= neck_base_z:
        neck_top_z = neck_base_z + max((head_top_z - neck_base_z) * 0.25, 1e-4)
    neck_top = on_midplane(slab_centre(head, neck_top_z - half, neck_top_z + half))
    neck_top.z = neck_top_z
    points["neck_top"] = neck_top
    points["neck_mid"] = (neck_base + neck_top) * 0.5
    top = on_midplane(_centroid(head))
    top.z = head_top_z
    points["head_top"] = top
    for role in ("neck_top", "neck_mid", "head_top"):
        detail[role] = {"how": "head cross-section centroid, on the midplane"}

    # --- the limbs: one side, unless the caller asked for both -------------
    def side_cloud(table, what, side):
        """That side's limb points — chosen by **where the geometry is**.

        The tag's name is not evidence and never decides: whichever tag sits on
        the requested side of the midplane is that side's limb.  This is the
        third and last place the mirrored-side-names defect dies.  A character
        tagged on one side only is authored from the tag it has, reflected first
        when that tag is on the other side.
        """
        if not table:
            return None, None
        want = character_left * (1.0 if side == "L" else -1.0)
        offsets = {}
        for tag_side, tag in table.items():
            offsets[tag_side] = (_centroid(clouds[tag]).x - midplane) * want
        best = max(offsets, key=lambda s: offsets[s])
        tag = table[best]
        cloud = clouds[tag]
        if offsets[best] <= 0.0:
            if len(table) > 1:
                raise ForgeError(
                    "Both %s tags on %r sit on the same side of the midplane (%s). One "
                    "of them is mis-tagged and no landmark fit can guess which; fix the "
                    "tags, or pass method='tags'."
                    % (what, obj.name,
                       ", ".join("%s at %+.0f mm" % (table[s], offsets[s] * M_TO_MM)
                                 for s in sorted(offsets))))
            cloud = [Vector((2.0 * midplane - p.x, p.y, p.z)) for p in cloud]
            warnings.append(
                "Only %r was tagged and its geometry is on the other side, so it was "
                "reflected to author the %s %s." % (tag, side, what))
        elif len(table) == 1:
            warnings.append(
                "Only %r was tagged, so both %ss were authored from it." % (tag, what))
        return tag, cloud

    # Both sides' tag names, whichever side was authored: the mirror still has
    # to tell the *other* side's tag which bones ended up on it, or a perfectly
    # rigged right arm reports as a tag nothing was fitted to.
    all_tags = {}
    for side in ("L", "R"):
        for what, table in (("arm", arms), ("leg", legs)):
            want = character_left * (1.0 if side == "L" else -1.0)
            best = None
            for tag in table.values():
                offset = (_centroid(clouds[tag]).x - midplane) * want
                if offset > 0.0 and (best is None or offset > best[0]):
                    best = (offset, tag)
            if best is not None:
                all_tags["%s.%s" % (what, side)] = best[1]

    tags = {}
    for side in sides:
        sign = character_left * (1.0 if side == "L" else -1.0)
        leg_tag, leg_points = side_cloud(legs, "leg", side)
        arm_tag, arm_points = side_cloud(arms, "arm", side)
        tags["leg.%s" % side] = leg_tag
        tags["arm.%s" % side] = arm_tag

        if leg_tag is not None:
            leg_hint, leg_measured = _limb_hint(axis_hints, "leg.%s" % side,
                                                (0.0, 0.0, -1.0))
            leg = Limb(leg_tag, leg_points, axis_hint=leg_hint)
            leg.hint_measured = leg_measured
            limbs["leg.%s" % side] = leg
            hip = leg.junction("hip", from_end="proximal")
            knee = leg.girth_minimum(CREASE_BAND, "knee")
            ankle = leg.girth_minimum(DISTAL_BAND, "ankle")
            _limb_is_measurable(obj, "leg", leg, knee)
            hip_point = Vector(hip["point"])
            # A hip joint is inside the pelvis, not out on the thigh's own axis:
            # it is the femur head. Keep the thigh's X (the leg really is out
            # there), take the torso's own Y at that height (the femur head is
            # inside the body, not on the front of it) and never let it float
            # above the torso's floor.
            hip_slab = slab_centre(torso, hip_point.z - half, hip_point.z + half)
            hip_point.y = hip_slab.y
            hip_point.z = min(hip_point.z, torso_low.z)
            # A leg runs downwards. If the landmarks say otherwise the limb was
            # not read as a limb, and a metarig fitted to them would be a zigzag
            # — better to say so and let the caller fall back to the old fit.
            _plausible(obj, "leg.%s" % side, hip_point, Vector(knee["point"]),
                       Vector(ankle["point"]), leg_hint)
            # --- the foot, in its own frame ---------------------------------
            # The leg's cross-sections stop being the right tool at the ankle: a
            # foot is a wedge lying on the ground and its joints are defined
            # along the way the character faces. Measured separately, and the
            # ankle it finds replaces the distal girth minimum, which on a
            # booted character sits high and behind the real one. It runs BEFORE
            # the pre-bend because the pre-bend is measured against the
            # hip-to-ankle chord, and that chord has just moved.
            foot = foot_landmarks(leg_points, Vector(ankle["point"]),
                                  Vector(knee["point"]), forward,
                                  label="leg.%s" % side)
            ankle_point = Vector(ankle["point"])
            ankle_detail = dict(ankle)
            if foot is not None:
                ankle_point = Vector(foot["ankle"])
                ankle_detail = dict(foot["detail"]["ankle"])
                ankle_detail["distal_girth_minimum_mm"] = [
                    round(v * M_TO_MM, 2) for v in ankle["point"]]
                ankle_detail["moved_from_girth_minimum_mm"] = round(
                    (ankle_point - Vector(ankle["point"])).length * M_TO_MM, 2)
                points["ball.%s" % side] = Vector(foot["ball"])
                points["toe_tip.%s" % side] = Vector(foot["toe_tip"])
                points["heel.%s" % side] = Vector(foot["heel"])
                detail["ball.%s" % side] = foot["detail"]["ball"]
                detail["toe_tip.%s" % side] = foot["detail"]["toe_tip"]
                detail["heel.%s" % side] = foot["detail"]["heel"]
                feet["leg.%s" % side] = foot
            else:
                warnings.append(
                    "The %s leg tag has no foot in it (nothing below the ankle is long "
                    "enough front to back to be one), so foot.%s and toe.%s keep the "
                    "template's own proportions. Tag the foot with the leg if the "
                    "character has one." % (side, side, side))
            # The pre-bend, before the mirror: a knee apexes FORWARD.
            knee_point, knee_prebend = prebend_joint(
                leg, hip_point, Vector(knee["point"]), ankle_point,
                forward, "knee", side,
                station_fraction=knee.get("fraction"))
            prebends.append(knee_prebend)
            # ...and then the STANCE, which is the other half of the same idea:
            # the pre-bend says which way the knee folds, this says how much
            # reach the chain has left to fold WITH. Ankle untouched, hip
            # lowered, knee forward — and still before the mirror, so the right
            # side gets the identical stance and the asymmetry stays 0.0.
            hip_point, knee_point, knee_stance = stance_flex(
                leg, hip_point, knee_point, ankle_point,
                forward, "knee", side, span_fraction=stance_span,
                station_fraction=knee.get("fraction"))
            stances.append(knee_stance)
            points["hip.%s" % side] = hip_point
            points["knee.%s" % side] = knee_point
            points["ankle.%s" % side] = ankle_point
            hip = dict(hip)
            hip["stance"] = knee_stance
            if knee_stance.get("flexed"):
                hip["point"] = hip_point
            detail["hip.%s" % side] = hip
            knee = dict(knee)
            knee["prebend"] = knee_prebend
            knee["stance"] = knee_stance
            if knee_prebend.get("nudged") or knee_stance.get("flexed"):
                knee["point"] = knee_point
            detail["knee.%s" % side] = knee
            detail["ankle.%s" % side] = ankle_detail

        if arm_tag is not None:
            arm_hint, arm_measured = _limb_hint(axis_hints, "arm.%s" % side,
                                                (sign, 0.0, 0.0))
            arm = Limb(arm_tag, arm_points, axis_hint=arm_hint)
            arm.hint_measured = arm_measured
            limbs["arm.%s" % side] = arm
            shoulder = arm.junction("shoulder", from_end="proximal")
            elbow = arm.girth_minimum(CREASE_BAND, "elbow")
            wrist = arm.girth_minimum(DISTAL_BAND, "wrist")
            _limb_is_measurable(obj, "arm", arm, elbow)
            _plausible(obj, "arm.%s" % side, Vector(shoulder["point"]),
                       Vector(elbow["point"]), Vector(wrist["point"]), arm_hint)
            # --- the hand, the foot's problem at the other end --------------
            # The distal girth minimum walks past the palm and lands in the
            # fingers, because fingers are narrower than a wrist. Measured
            # properly it is the FIRST minimum, and the hand bone stops at the
            # knuckle line rather than at the fingertips. Before the pre-bend,
            # because the pre-bend is measured against the shoulder-to-wrist
            # chord and that chord has just moved.
            hand = hand_landmarks(arm_points, Vector(elbow["point"]), arm.axis,
                                  label="arm.%s" % side)
            wrist_point = Vector(wrist["point"])
            wrist_detail = dict(wrist)
            if hand is not None:
                wrist_point = Vector(hand["wrist"])
                wrist_detail = dict(hand["detail"]["wrist"])
                wrist_detail["distal_girth_minimum_mm"] = [
                    round(v * M_TO_MM, 2) for v in wrist["point"]]
                wrist_detail["moved_from_girth_minimum_mm"] = round(
                    (wrist_point - Vector(wrist["point"])).length * M_TO_MM, 2)
                points["knuckle.%s" % side] = Vector(hand["knuckle"])
                detail["knuckle.%s" % side] = hand["detail"]["knuckle"]
                detail["palm_axis.%s" % side] = hand["detail"]["palm_axis"]
                hands["arm.%s" % side] = hand
            else:
                warnings.append(
                    "The %s arm tag has no hand in it (nothing reaches %.0f%% of the "
                    "wrist's own girth past the wrist — a hand does), so hand.%s keeps "
                    "the template's own proportions and its wrist stays at the distal "
                    "girth minimum. Tag the hand with the arm if the character has "
                    "one: this is a tagging job, not a fitting one."
                    % (side, 100.0 * HAND_REACH_FACTOR, side))
            # ... and an elbow apexes BACKWARD.
            elbow_point, elbow_prebend = prebend_joint(
                arm, Vector(shoulder["point"]), Vector(elbow["point"]),
                wrist_point, forward, "elbow", side,
                station_fraction=elbow.get("fraction"))
            prebends.append(elbow_prebend)
            points["shoulder.%s" % side] = Vector(shoulder["point"])
            points["elbow.%s" % side] = elbow_point
            points["wrist.%s" % side] = wrist_point
            detail["shoulder.%s" % side] = shoulder
            elbow = dict(elbow)
            elbow["prebend"] = elbow_prebend
            if elbow_prebend.get("nudged"):
                elbow["point"] = elbow_point
            detail["elbow.%s" % side] = elbow
            detail["wrist.%s" % side] = wrist_detail
            # The clavicle runs from beside the spine out to the shoulder: its
            # root is the chest's own cross-section, a little way off the
            # midplane.
            chest_z = points["shoulder.%s" % side].z
            chest = slab_centre(torso, chest_z - half * 2.0, chest_z + half * 2.0)
            points["clavicle.%s" % side] = Vector((
                midplane + sign * (torso_high.x - torso_low.x) * 0.12,
                chest.y,
                min(chest_z, torso_high.z - (torso_high.z - torso_low.z) * 0.04)))
            detail["clavicle.%s" % side] = {
                "how": "chest cross-section centroid, 12% off the midplane"}

    fallbacks = sorted(role for role, info in detail.items()
                       if str(info.get("how", "")).endswith("fallback"))
    if fallbacks:
        warnings.append(
            "These joints had no landmark in the geometry and fell back to a fraction "
            "of the limb: %s. That is the old guess, and it is reported as one."
            % ", ".join(fallbacks))
    moved = [entry for entry in prebends if entry.get("nudged")]
    backwards = [entry for entry in moved if (entry.get("before_mm") or 0.0) < 0.0]
    if moved:
        warnings.append(
            "Anatomical pre-bend: %s. %s%s"
            % (", ".join("%s %+.1f -> %+.1f mm (%s)"
                         % (entry["joint"], entry.get("before_mm") or 0.0,
                            entry.get("after_mm") or 0.0, entry["direction"])
                         for entry in moved),
               ("%d of them apexed the WRONG way and would have bent backwards under "
                "IK. " % len(backwards)) if backwards else "",
               "A rest pose carries a pre-bend or the IK solver has no plane to choose; "
               "the nudge is %.0f%% of each limb's own span and it runs before the "
               "mirror, so both sides get it identically."
               % (100.0 * PREBEND_FRACTION)))
    clamped = [entry for entry in moved if entry.get("clamped_by_flesh")]
    if clamped:
        warnings.append(
            "The pre-bend did not fully fit inside the flesh at %s, so it was clamped "
            "to stay inside the limb's own cross-section rather than pushing the joint "
            "out of the body." % ", ".join(entry["joint"] for entry in clamped))
    flexed = [entry for entry in stances if entry.get("flexed")]
    if flexed:
        warnings.append(
            "Flexed rest stance: %s. The ankle did not move; the hip dropped and the "
            "knee came forward, so the chain rests at %.2f of its own length instead of "
            "against the solver's limit. The angle is derived from that fraction and "
            "each limb's own bones, and the stance runs before the mirror, so both "
            "sides get it identically."
            % (", ".join("%s %.4f -> %.4f of chain (%.1f deg, hip down %.1f mm, knee "
                         "forward %.1f mm)"
                         % (entry["joint"], entry.get("extension_frac_before") or 0.0,
                            entry.get("extension_frac_after") or 0.0,
                            entry.get("knee_flex_deg") or 0.0,
                            entry.get("hip_drop_mm") or 0.0,
                            entry.get("knee_offline_mm") or 0.0)
                         for entry in flexed), stance_span))
    outside = [entry for entry in flexed if entry.get("inside_flesh") is False]
    if outside:
        warnings.append(
            "The stance takes %s past the limb's own cross-section (%s). That is the "
            "price of flexing a rest pose on a mesh sculpted with straight legs: the "
            "joint is where a standing character's knee is, and the flesh is where the "
            "sculpt left it. rig_check's centering gate measures the gap; sculpt the "
            "character with its knees already bent, or lower stance_span, to close it."
            % (", ".join(entry["joint"] for entry in outside),
               ", ".join("%s %.1f mm forward against %.1f mm of section"
                         % (entry["joint"], entry.get("knee_offline_mm") or 0.0,
                            entry.get("section_reach_mm") or 0.0)
                         for entry in outside)))
    return {"points": points, "detail": detail, "limbs": limbs,
            "midplane": midplane, "character_left": character_left,
            "sides": tuple(sides), "tags": tags, "all_tags": all_tags,
            "torso_tag": torso_tag, "head_tag": head_tag,
            "facing": facing, "forward": forward, "prebend": prebends,
            "stance": stances, "stance_span": stance_span,
            "feet": feet, "hands": hands, "warnings": warnings}


# ---------------------------------------------------------------------------
# step 4: the X-mirror
# ---------------------------------------------------------------------------

def mirror_edit_bones(armature, source="L", midplane_local=0.0):
    """Reflect every ``.L`` bone onto its ``.R`` twin. Exact, by construction.

    The armature must already be in Edit Mode.  This is Blender's
    ``Armature > Symmetrize`` written out: head and tail reflected about the
    local ``x = midplane`` plane, roll negated (a mirrored twist is the opposite
    twist), and unsided bones — the spine, the neck, the head — **snapped onto
    the plane**, because a spine 2 mm off the midline is the same defect as an
    asymmetric limb wearing a smaller number.

    Returns what it did, including the worst asymmetry **left over**, which is
    0.0 whenever this ran.
    """
    bones = armature.data.edit_bones

    def mirror(vector):
        out = Vector(vector)
        out.x = 2.0 * midplane_local - out.x
        return out

    def depth(bone):
        steps = 0
        parent = bone.parent
        while parent is not None and steps < 64:
            steps += 1
            parent = parent.parent
        return steps

    # How far off the midplane a bone with no side in its name may be and still
    # be treated as a centre bone. Beyond that it is something the template put
    # out to one side on purpose, and flattening it would be this function
    # inventing anatomy rather than mirroring it.
    xs = [abs(bone.head.x - midplane_local) for bone in bones] or [0.0]
    centre_limit = max(sorted(xs)[-1] * 0.05, 1e-4)

    mirrored = []
    centred = []
    unpaired = []
    off_centre = []
    # Parents first: a connected child's head *is* its parent's tail, so a child
    # mirrored before its parent would be dragged back out of place by it.
    for bone in sorted(list(bones), key=depth):
        _base, side = _side_of(bone.name)
        if side is None:
            moved = max(abs(bone.head.x - midplane_local),
                        abs(bone.tail.x - midplane_local))
            if moved > centre_limit:
                off_centre.append({"bone": bone.name,
                                   "off_midplane_mm": round(moved * M_TO_MM, 3)})
                continue
            if moved > 1e-9:
                centred.append({"bone": bone.name, "moved_mm": round(moved * M_TO_MM, 3)})
            bone.head.x = midplane_local
            bone.tail.x = midplane_local
            continue
        if side != source.upper():
            continue
        twin_name = _twin_name(bone.name)
        twin = bones.get(twin_name) if twin_name else None
        if twin is None:
            unpaired.append(bone.name)
            continue
        twin.head = mirror(bone.head)
        twin.tail = mirror(bone.tail)
        try:
            twin.roll = -bone.roll
        except AttributeError:  # pragma: no cover - every build has roll
            pass
        try:
            twin.use_connect = bone.use_connect
        except (AttributeError, RuntimeError):
            pass
        mirrored.append(twin.name)

    worst = 0.0
    for name in mirrored:
        left = bones.get(_twin_name(name) or "")
        right = bones.get(name)
        if left is None or right is None:
            continue
        worst = max(worst,
                    (mirror(left.head) - right.head).length,
                    (mirror(left.tail) - right.tail).length)
    return {
        "source_side": source.upper(),
        "mirrored_bones": sorted(mirrored),
        "mirrored": len(mirrored),
        "centred_bones": centred,
        "off_midplane_unsided": off_centre,
        "unpaired": sorted(unpaired),
        "midplane_mm": round(midplane_local * M_TO_MM, 3),
        "residual_asymmetry_mm": round(worst * M_TO_MM, 6),
        "says": ("%d bone(s) were reflected from the %s side and %d centre bone(s) "
                 "snapped onto the midplane; the left/right asymmetry that leaves is "
                 "%.4f mm." % (len(mirrored), source.upper(), len(centred),
                               worst * M_TO_MM)),
    }


# ---------------------------------------------------------------------------
# step 5 / the self-check: is the skeleton where the flesh says it should be
# ---------------------------------------------------------------------------

def _deform_groups(rig, mesh):
    """``{vertex group index: bone name}`` for every deform bone with a group."""
    out = {}
    for bone in rig.data.bones:
        if not bone.use_deform:
            continue
        group = mesh.vertex_groups.get(bone.name)
        if group is not None:
            out[group.index] = bone.name
    return out


def _weight_table(mesh, groups):
    """``{bone: {vertex index: weight}}`` plus ``{vertex: total}``, normalised."""
    per_bone = {name: {} for name in groups.values()}
    totals = {}
    for vertex in mesh.data.vertices:
        entries = [(groups[e.group], float(e.weight)) for e in vertex.groups
                   if e.group in groups and e.weight > 0.0]
        if not entries:
            continue
        total = sum(w for _n, w in entries)
        if total <= 0.0:
            continue
        totals[vertex.index] = total
        for name, weight in entries:
            per_bone[name][vertex.index] = weight / total
    return per_bone, totals


def _bone_graph_distance(rig):
    """Hops between every pair of bones along the armature's own tree."""
    names = [bone.name for bone in rig.data.bones]
    neighbours = {name: set() for name in names}
    for bone in rig.data.bones:
        if bone.parent is not None:
            neighbours[bone.name].add(bone.parent.name)
            neighbours[bone.parent.name].add(bone.name)
    distances = {}
    for start in names:
        seen = {start: 0}
        frontier = [start]
        while frontier:
            nxt = []
            for name in frontier:
                for other in neighbours[name]:
                    if other not in seen:
                        seen[other] = seen[name] + 1
                        nxt.append(other)
            frontier = nxt
        distances[start] = seen
    return distances


def influence_overlap(rig, mesh, floor=0.02, max_pairs=400, max_outliers=12):
    """The bone-to-bone **influence overlap matrix** — what one move does to another.

    For every pair of bones that share any influence over the same vertices:

    * ``vertices`` — how many vertices both bones move;
    * ``mass`` — ``sum(min(w_a, w_b))`` over those vertices, in vertex units
      (1.0 means one whole vertex is owned jointly).  A count alone would rate a
      thousand vertices at weight 0.001 as a bigger overlap than fifty at 0.5;
      the mass is the honest number and the count is reported next to it;
    * ``share_pct`` — that mass as a percentage of the *smaller* bone's total
      influence, which is what says "half of what this bone does, that one does
      too";
    * ``gap_mm`` — how far apart the two bones actually are, as the shortest
      distance between the two segments;
    * ``hops`` — how far apart they are in the armature's tree, reported but not
      trusted: Rigify's ``DEF-`` bones are re-parented into their own hierarchy
      and some of them are not connected to each other at all, so a *geometric*
      neighbour can be a tree stranger.

    Touching bones (``gap`` under 2% of the rig's size) are the **blend band**
    between a joint's two halves: it is supposed to be there, and a limb with
    none of it creases like a drinking straw.  A pair that shares influence
    while sitting far apart is the defect the owner asked about — a hand vertex
    that a thigh bone moves — and every one of those is named, with the worst
    vertex, its distance to the far bone, and the two bones' own names.
    """
    groups = _deform_groups(rig, mesh)
    if not groups:
        return {"pairs": [], "outliers": [], "bones": 0,
                "says": "%r has no vertex group named after a deform bone of %r, so "
                        "there is no influence to overlap." % (mesh.name, rig.name)}
    per_bone, _totals = _weight_table(mesh, groups)
    distances = _bone_graph_distance(rig)
    matrix = mesh.matrix_world
    verts = mesh.data.vertices

    pair_mass = {}
    pair_count = {}
    pair_worst = {}
    for vertex in verts:
        entries = [(groups[e.group], float(e.weight)) for e in vertex.groups
                   if e.group in groups and e.weight > 0.0]
        if len(entries) < 2:
            continue
        total = sum(w for _n, w in entries) or 1.0
        entries = [(name, weight / total) for name, weight in entries
                   if weight / total >= floor]
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                a, wa = entries[i]
                b, wb = entries[j]
                key = (a, b) if a <= b else (b, a)
                shared = min(wa, wb)
                pair_mass[key] = pair_mass.get(key, 0.0) + shared
                pair_count[key] = pair_count.get(key, 0) + 1
                if shared > pair_worst.get(key, (0.0, None))[0]:
                    pair_worst[key] = (shared, vertex.index)

    bone_mass = {name: sum(table.values()) for name, table in per_bone.items()}
    span = max(max(rig.dimensions), 1e-6)
    touching = span * 0.02
    nearby = span * 0.12
    rows = []
    for (a, b), mass in pair_mass.items():
        hops = distances.get(a, {}).get(b)
        smaller = min(bone_mass.get(a, 0.0), bone_mass.get(b, 0.0)) or 1.0
        gap = _bone_gap(rig, a, b)
        rows.append({
            "bones": [a, b],
            "vertices": pair_count[(a, b)],
            "mass": round(mass, 4),
            "share_pct": round(100.0 * mass / smaller, 2),
            "gap_mm": round(gap * M_TO_MM, 1),
            "hops": hops,
            "kind": ("blend band" if gap <= touching
                     else ("near" if gap <= nearby else "stray")),
        })
    rows.sort(key=lambda row: (-row["mass"], row["bones"]))

    outliers = []
    for row in rows:
        if row["kind"] != "stray":
            continue
        a, b = row["bones"]
        _shared, vertex_index = pair_worst[(a, b)]
        point = matrix @ verts[vertex_index].co
        far = max((a, b), key=lambda name: _distance_to_bone(rig, name, point))
        near = a if far == b else b
        outliers.append({
            "bones": [a, b],
            "gap_mm": row["gap_mm"],
            "hops": row["hops"],
            "vertices": row["vertices"],
            "mass": row["mass"],
            "share_pct": row["share_pct"],
            "worst_vertex": vertex_index,
            "distance_to_%s_mm" % far: round(
                _distance_to_bone(rig, far, point) * M_TO_MM, 1),
            "says": ("%d vertices are moved by both %s and %s, two bones %.0f mm apart: "
                     "the worst of them sits %.0f mm from %s while %s also claims it."
                     % (row["vertices"], a, b, row["gap_mm"],
                        _distance_to_bone(rig, far, point) * M_TO_MM, far, near)),
        })
        if len(outliers) >= max_outliers:
            break

    stray_mass = sum(row["mass"] for row in rows if row["kind"] == "stray")
    verdict = _band(stray_mass, OVERLAP_THRESHOLDS["stray_mass"])
    if outliers:
        says = ("%d bone pair(s) share influence while sitting far apart (%.2f vertices "
                "of stray mass). Worst: %s."
                % (len(outliers), stray_mass, outliers[0]["says"]))
    else:
        says = ("Every pair of bones that shares influence is a pair that touches — the "
                "blend bands at the joints, which is what they are for. No stray "
                "influence.")
    return {
        "bones": len(per_bone),
        "pairs": rows[:max_pairs],
        "pairs_total": len(rows),
        "outliers": outliers,
        "stray_mass": round(stray_mass, 4),
        "floor": floor,
        "verdict": verdict,
        "thresholds": OVERLAP_THRESHOLDS,
        "says": says,
    }


def _segment(rig, name):
    bone = rig.data.bones.get(name)
    if bone is None:
        return None
    matrix = rig.matrix_world
    return matrix @ bone.head_local, matrix @ bone.tail_local


def _bone_gap(rig, first, second):
    """Shortest distance between two bones, sampled along them.

    Sampled rather than solved: eleven points down each segment is well inside
    a millimetre on any bone a character has, and it cannot go wrong on the
    degenerate cases (a zero-length bone, two parallel bones) that a closed-form
    segment-segment solver has to special-case.
    """
    a = _segment(rig, first)
    b = _segment(rig, second)
    if a is None or b is None:
        return float("inf")
    best = float("inf")
    for step in range(11):
        point = a[0].lerp(a[1], step / 10.0)
        best = min(best, _point_segment(point, b[0], b[1]))
    for step in range(11):
        point = b[0].lerp(b[1], step / 10.0)
        best = min(best, _point_segment(point, a[0], a[1]))
    return best


def _point_segment(point, head, tail):
    span = tail - head
    length = span.length
    if length < 1e-9:
        return (point - head).length
    t = max(0.0, min(1.0, (point - head).dot(span) / (length * length)))
    return (point - (head + span * t)).length


def _distance_to_bone(rig, name, point):
    bone = rig.data.bones.get(name)
    if bone is None:
        return float("inf")
    matrix = rig.matrix_world
    head = matrix @ bone.head_local
    tail = matrix @ bone.tail_local
    span = tail - head
    length = span.length
    if length < 1e-9:
        return (point - head).length
    t = max(0.0, min(1.0, (point - head).dot(span) / (length * length)))
    return (point - (head + span * t)).length


def _edge_neighbours(mesh):
    out = {}
    for edge in mesh.data.edges:
        a, b = edge.vertices
        out.setdefault(a, []).append(b)
        out.setdefault(b, []).append(a)
    return out


#: Where along each bone the cross-section is taken.  The ends are **reported**
#: and the middle is **gated**: a bone's head and tail are joints, where the
#: flesh belongs to two bones and to the body part beyond them (an ankle's
#: section is half shin and half foot), so "is the bone in the middle of the
#: flesh" is only a well-posed question along the shaft.
CENTERING_STATIONS = (0.0, 0.35, 0.5, 0.65, 1.0)


def rest_flex_chords(rig):
    """Per long bone, the **rest flex its own chain carries** — as a lookup.

    The chord is the straight line between a limb chain's two ends — hip to
    ankle, shoulder to wrist — taken off the rig's rest skeleton.  A chain that
    rests flexed (:func:`stance_flex`, :func:`prebend_joint`) holds its middle
    bones off that line on purpose, by an amount that is therefore *computable*
    rather than a threshold somebody chose.

    The flex is read from the chain's **three joints** (hip, knee, ankle) and
    never from the bone being judged, which is the whole point: a bone dragged
    off its limb does not get to enlarge its own allowance by moving.  Each
    entry is ``(chord start, chord axis, joint points, joint arc lengths, arc
    before this bone, this bone's length)``.

    Empty for a rig with no recognisable limb chain, which makes the caller fall
    back to the plain "how far from the flesh's centre" question.
    """
    out = {}
    if rig is None or getattr(rig, "data", None) is None:
        return out
    matrix = rig.matrix_world
    for stems in CENTERING_FLEX_CHAINS:
        grouped = {}
        for bone in rig.data.bones:
            if not bone.use_deform:
                continue
            text = bone.name
            for prefix in ("DEF-", "ORG-", "MCH-"):
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break
            base, side = _side_of(text)
            lowered = base.lower()
            for index, stem in enumerate(stems):
                if lowered == stem or lowered.startswith(stem + "."):
                    grouped.setdefault(side, {}).setdefault(index, []).append(bone)
                    break
        for by_stem in grouped.values():
            if len(by_stem) < len(stems):
                continue
            ordered = []
            joints = []
            for index in range(len(stems)):
                segment = sorted(by_stem[index], key=lambda bone: bone.name)
                if not joints:
                    joints.append(matrix @ segment[0].head_local)
                joints.append(matrix @ segment[-1].tail_local)
                ordered.extend(segment)
            span = joints[-1] - joints[0]
            if span.length < 1e-9:
                continue
            axis = span / span.length
            arcs = [0.0]
            for index in range(1, len(joints)):
                arcs.append(arcs[-1] + (joints[index] - joints[index - 1]).length)
            travelled = 0.0
            for bone in ordered:
                length = ((matrix @ bone.tail_local)
                          - (matrix @ bone.head_local)).length
                out.setdefault(bone.name,
                               (joints[0], axis, joints, arcs, travelled, length))
                travelled += length
    return out


def _flex_allowance(chord, fraction, bone_axis):
    """How far off the flesh's centreline this station is *entitled* to be.

    The station's place in the chain is its **arc length**, and the point the
    chain's own joints put there is the one that earns the allowance.  Returned
    negated — the flesh sits on the chord and the bone off it, so the offset the
    gate measures (bone to flesh) points back towards the chord — and flattened
    into the plane that offset lives in.
    """
    if chord is None:
        return Vector((0.0, 0.0, 0.0))
    start, axis, joints, arcs, before, length = chord
    travelled = before + fraction * length
    ideal = joints[-1]
    for index in range(1, len(joints)):
        if travelled <= arcs[index] or index == len(joints) - 1:
            segment = arcs[index] - arcs[index - 1]
            step = 0.0 if segment < 1e-9 else (travelled - arcs[index - 1]) / segment
            step = max(0.0, min(1.0, step))
            ideal = joints[index - 1] + (joints[index] - joints[index - 1]) * step
            break
    delta = ideal - start
    allowance = -(delta - axis * delta.dot(axis))
    sideways = CENTERING_FLEX_FORBIDDEN_AXIS
    allowance = allowance - sideways * allowance.dot(sideways)
    return allowance - bone_axis * allowance.dot(bone_axis)


def _excess_offset(offset, allowance):
    """The part of ``offset`` the chain's own rest flex does **not** explain.

    Everything perpendicular to the allowance is kept in full — that is the
    "bone painted onto one wall of the limb" defect this gate was built for, and
    a stance cannot excuse it — and along the allowance, up to its own length is
    spent before anything counts.
    """
    length = allowance.length
    if length < 1e-9:
        return offset.copy()
    direction = allowance / length
    spent = max(0.0, min(length, offset.dot(direction)))
    return offset - direction * spent


def bone_centering(rig, mesh, weight_floor=0.2, stations=CENTERING_STATIONS,
                   min_vertices=8):
    """Is each bone actually **inside** the flesh it drives, on its centreline?

    The inverse of the landmark math, which is what makes it a real check rather
    than a restatement: for each station along a deform bone, the mesh is cut
    perpendicular to the bone and the **cross-section's own centroid** is
    compared with the bone.  The section is found by growing outward along the
    mesh's edges from the vertices this bone already owns, so the answer does
    not depend on the weights being right — a bone painted onto one wall of a
    limb still gets the whole limb's cross-section, and the offset shows up.

    Reported in millimetres **and** as a percentage of the section's own radius,
    because 8 mm off centre is nothing on a thigh and catastrophic on a finger.

    **The rest flex is allowed for, and it is arithmetic rather than a slacker
    threshold.**  A limb chain that rests flexed — the anatomical pre-bend, and
    the flexed stance :func:`stance_flex` authors — holds its middle bones off
    the straight line between its own two ends on purpose, while a character
    sculpted standing straight has its flesh's centreline *on* that line.  The
    gap between the two is the stance, exactly, and :func:`rest_flex_chords`
    computes it from the rig's own skeleton.  So each station spends that much
    offset **along that direction** for free and is judged on what is left:
    ``excess_mm``.  Everything across the flex direction still counts in full,
    which is the defect this gate exists for — a bone painted onto one wall of
    its limb is off-centre whatever the stance is doing.  The raw
    ``offset_mm`` is reported beside it, always, so the number the owner asked
    for by name never moved.
    """
    groups = _deform_groups(rig, mesh)
    if not groups:
        return {"bones": [], "worst": None,
                "says": "%r has no deform vertex groups, so nothing could be centred "
                        "or off-centre." % mesh.name}
    matrix = mesh.matrix_world
    coords = [matrix @ v.co for v in mesh.data.vertices]
    neighbours = _edge_neighbours(mesh)
    owners = {}
    for vertex in mesh.data.vertices:
        for entry in vertex.groups:
            if entry.group in groups and entry.weight >= weight_floor:
                owners.setdefault(groups[entry.group], []).append(vertex.index)

    rows = []
    rig_matrix = rig.matrix_world
    chords = rest_flex_chords(rig)
    for bone in rig.data.bones:
        if not bone.use_deform:
            continue
        seed = owners.get(bone.name) or []
        if len(seed) < min_vertices:
            continue
        head = rig_matrix @ bone.head_local
        tail = rig_matrix @ bone.tail_local
        axis = tail - head
        length = axis.length
        if length < 1e-6:
            continue
        axis = axis / length
        seed_set = set(seed)
        seed_radius = sum(((coords[i] - head) - axis * (coords[i] - head).dot(axis)).length
                          for i in seed) / float(len(seed))
        reach = max(seed_radius * 2.5, length * 0.35)
        entries = []
        for fraction in stations:
            station = head + axis * (length * fraction)
            section = _section_at(coords, neighbours, seed_set, station, axis, reach)
            if len(section) < MIN_SECTION_POINTS:
                continue
            centre = _centroid(section)
            gap = _angular_gap(section, centre, axis)
            if gap > MAX_SECTION_GAP_DEG:
                continue
            delta = (centre - station)
            offset_vector = delta - axis * delta.dot(axis)
            offset = offset_vector.length
            allowance = _flex_allowance(chords.get(bone.name), fraction, axis)
            excess = _excess_offset(offset_vector, allowance).length
            radius = sum(((point - centre) - axis * (point - centre).dot(axis)).length
                         for point in section) / float(len(section))
            entries.append({
                "fraction": round(fraction, 3),
                "section_points": len(section),
                "section_gap_deg": round(gap, 1),
                "offset_mm": round(offset * M_TO_MM, 2),
                "rest_flex_mm": round(allowance.length * M_TO_MM, 2),
                "excess_mm": round(excess * M_TO_MM, 2),
                "radius_mm": round(radius * M_TO_MM, 2),
                "offset_pct_of_radius": (round(100.0 * offset / radius, 1)
                                         if radius > 1e-9 else None),
                "excess_pct_of_radius": (round(100.0 * excess / radius, 1)
                                         if radius > 1e-9 else None),
            })
        if not entries:
            continue
        head_entry = next((entry for entry in entries if entry["fraction"] <= 0.0),
                          entries[0])
        # The verdict comes off the **shaft**, not the ends. A joint's head is
        # shared with its parent and is deliberately displaced a little to give
        # the IK solver a plane to bend in (a knee really does sit forward of
        # the line from hip to ankle), and a tail's section is half the next
        # body part, so gating on either would fail every correct rig. Both are
        # still measured and reported — the head offset is the number the owner
        # asked for by name.
        shaft = [entry for entry in entries
                 if 0.0 < entry["fraction"] < 1.0] or entries
        # Two "worst" stations, deliberately: the raw one is the number this
        # gate has always printed, and the excess one is the number it now
        # judges, once the chain's own rest flex has been paid for.
        worst = max(shaft, key=lambda e: e["offset_pct_of_radius"] or 0.0)
        worst_excess = max(shaft, key=lambda e: e["excess_pct_of_radius"] or 0.0)
        verdict = _band(worst_excess["excess_pct_of_radius"],
                        CENTERING_THRESHOLDS["offset_pct_of_radius"])
        rows.append({
            "bone": bone.name,
            "limb": bool(LIMB_BONE_RE.search(bone.name)),
            "gated": bool(CENTERED_BONE_RE.search(bone.name)),
            "head_offset_mm": head_entry["offset_mm"],
            "head_offset_pct_of_radius": head_entry["offset_pct_of_radius"],
            "worst_offset_mm": worst["offset_mm"],
            "worst_offset_pct_of_radius": worst["offset_pct_of_radius"],
            "worst_at_fraction": worst["fraction"],
            "rest_flex_mm": worst_excess["rest_flex_mm"],
            "worst_excess_mm": worst_excess["excess_mm"],
            "worst_excess_pct_of_radius": worst_excess["excess_pct_of_radius"],
            "worst_excess_at_fraction": worst_excess["fraction"],
            "verdict": verdict,
            "stations": entries,
        })
    rows.sort(key=lambda row: -(row["worst_excess_pct_of_radius"] or 0.0))
    judged = [row for row in rows if row["gated"]] or rows
    verdict = _worst([row["verdict"] for row in judged]) if judged else "unmeasured"
    worst_row = judged[0] if judged else None
    if worst_row is None:
        says = "No bone had enough skinned geometry around it to be centred or not."
    elif verdict == "ok":
        says = ("Every measured bone sits on its limb's centreline: worst %s, %.1f mm "
                "off (%.0f%% of that section's radius), of which %.1f mm is the rest "
                "flex its own chain carries — %.1f mm left unaccounted for."
                % (worst_row["bone"], worst_row["worst_offset_mm"],
                   worst_row["worst_offset_pct_of_radius"] or 0.0,
                   worst_row["rest_flex_mm"] or 0.0,
                   worst_row["worst_excess_mm"] or 0.0))
    else:
        says = ("%s is %.1f mm off the centre of its own cross-section at %.0f%% along "
                "the bone, and %.1f mm of that (%.0f%% of the section radius) is more "
                "than the %.1f mm its chain's own rest flex explains — the bone is not "
                "inside the middle of the flesh it drives."
                % (worst_row["bone"], worst_row["worst_offset_mm"],
                   100.0 * worst_row["worst_excess_at_fraction"],
                   worst_row["worst_excess_mm"] or 0.0,
                   worst_row["worst_excess_pct_of_radius"] or 0.0,
                   worst_row["rest_flex_mm"] or 0.0))
    return {
        "bones": rows,
        "measured": len(rows),
        "worst": worst_row["bone"] if worst_row else None,
        "worst_offset_mm": worst_row["worst_offset_mm"] if worst_row else None,
        "worst_offset_pct_of_radius": (worst_row["worst_offset_pct_of_radius"]
                                       if worst_row else None),
        "worst_excess_mm": worst_row["worst_excess_mm"] if worst_row else None,
        "worst_excess_pct_of_radius": (worst_row["worst_excess_pct_of_radius"]
                                       if worst_row else None),
        "rest_flex_mm": worst_row["rest_flex_mm"] if worst_row else None,
        "verdict": verdict,
        "thresholds": CENTERING_THRESHOLDS,
        "gated_bones": [row["bone"] for row in rows if row["gated"]],
        "measured_at": ("the bone's shaft (35/50/65% along it); the head and the tail "
                        "are reported per bone but not gated, because a joint's flesh "
                        "belongs to two bones and the head is deliberately offset to "
                        "define the IK plane. "
                        "Only the long bones (thigh, shin, upper arm, forearm) are "
                        "gated: a hand, a toe, a clavicle or a foot runs through flesh "
                        "with no centreline of its own. "
                        "The verdict is taken on the EXCESS offset: the chain's own "
                        "rest flex (the pre-bend, and the flexed stance) is subtracted "
                        "along its own direction first, because a mesh sculpted with "
                        "straight legs and a rig that rests on bent ones differ by "
                        "exactly that much and neither of them is wrong."),
        "threshold_tier": ("heuristic (proxy tier): a third of the section's radius is "
                           "the slack a correct rig uses — a knee sits forward of the "
                           "hip-to-ankle line by 3% of the limb's length to give Rigify "
                           "a pole plane, which measured 29% of the forearm's radius on "
                           "the test character — and two thirds of it reads as off "
                           "centre in a render. The millimetres are reported next to "
                           "the percentage either way."),
        "says": says,
    }


def _angular_gap(points, centre, axis):
    """The widest angle between neighbouring crossings, around the bone. Degrees."""
    up = Vector((0.0, 0.0, 1.0))
    if abs(axis.dot(up)) > 0.95:
        up = Vector((0.0, 1.0, 0.0))
    right = axis.cross(up).normalized()
    other = axis.cross(right).normalized()
    angles = []
    for point in points:
        delta = point - centre
        delta = delta - axis * delta.dot(axis)
        if delta.length < 1e-9:
            continue
        angles.append(math.atan2(delta.dot(other), delta.dot(right)))
    if len(angles) < 3:
        return 360.0
    angles.sort()
    gaps = [angles[i + 1] - angles[i] for i in range(len(angles) - 1)]
    gaps.append(angles[0] + 2.0 * math.pi - angles[-1])
    return math.degrees(max(gaps))


def _section_at(coords, neighbours, seed_set, station, axis, reach):
    """The mesh's true cross-section at ``station``: where its **edges** cross the plane.

    Not "the vertices near the plane".  A game mesh's edge loops are tens of
    millimetres apart, so a slab of vertices lands between two loops as often as
    on one, and when it clips a loop obliquely it returns *half* a ring whose
    centroid sits well off the axis — measured on a perfectly placed forearm:
    37 mm, 93% of the section radius, from eight vertices of a sixteen-vertex
    ring.  Every edge that crosses the plane contributes exactly one point, so a
    tube gives a full ring however the loops happen to fall.

    The search is grown along the mesh's own edges from the vertices this bone
    already owns, inside a ball of ``reach``, so the section is *this* limb's and
    not the torso's or the other leg's — and, because the growth only needs a
    foothold, a bone weighted onto one wall of a limb still measures the whole
    cross-section.  That is what makes this a check on the bone rather than a
    restatement of its weights.
    """
    region = set()
    frontier = [index for index in seed_set
                if (coords[index] - station).length <= reach]
    region.update(frontier)
    while frontier:
        nxt = []
        for index in frontier:
            for other in neighbours.get(index, ()):
                if other in region or (coords[other] - station).length > reach:
                    continue
                region.add(other)
                nxt.append(other)
        frontier = nxt
    if len(region) < 3:
        return []
    points = []
    seen = set()
    for index in region:
        first = (coords[index] - station).dot(axis)
        for other in neighbours.get(index, ()):
            if other not in region:
                continue
            key = (index, other) if index < other else (other, index)
            if key in seen:
                continue
            seen.add(key)
            second = (coords[other] - station).dot(axis)
            if (first > 0.0) == (second > 0.0):
                continue
            span = first - second
            if abs(span) < 1e-12:
                continue
            points.append(coords[index].lerp(coords[other], first / span))
    return points


def bone_asymmetry(rig, midplane=None):
    """The L/R table: how far each ``.R`` bone is from the mirror of its ``.L``.

    A human who mirrors gets 0.0 on every row.  The live character measured 6 to
    24 mm on every limb, which is the fingerprint of two sides fitted
    *independently* — and that is exactly what this table shows, per bone, in
    millimetres, on every ``rig_check`` run.
    """
    matrix = rig.matrix_world
    bones = {bone.name: bone for bone in rig.data.bones}
    midpoints = []
    for name, bone in bones.items():
        _base, side = _side_of(name)
        if side != "L":
            continue
        twin = bones.get(_twin_name(name) or "")
        if twin is not None:
            midpoints.append(0.5 * ((matrix @ bone.head_local).x
                                    + (matrix @ twin.head_local).x))
    if midplane is None:
        # The rig's **own** midplane: the median of every pair's midpoint. A
        # character modelled 40 mm off the world origin is not an asymmetric
        # character, and measuring its two sides against x = 0 would report a
        # translation as a defect on every single bone.
        midpoints.sort()
        midplane = midpoints[len(midpoints) // 2] if midpoints else 0.0
    rows = []
    for name, bone in sorted(bones.items()):
        base, side = _side_of(name)
        if side != "L":
            continue
        twin = bones.get(_twin_name(name) or "")
        if twin is None:
            continue
        left_head = matrix @ bone.head_local
        left_tail = matrix @ bone.tail_local
        right_head = matrix @ twin.head_local
        right_tail = matrix @ twin.tail_local

        def mirror(vector):
            out = Vector(vector)
            out.x = 2.0 * midplane - out.x
            return out

        head_error = (mirror(left_head) - right_head).length
        tail_error = (mirror(left_tail) - right_tail).length
        worst = max(head_error, tail_error)
        rows.append({
            "bone": base,
            "deform": bool(bone.use_deform),
            "limb": bool(LIMB_BONE_RE.search(base)),
            "head_mm": round(head_error * M_TO_MM, 3),
            "tail_mm": round(tail_error * M_TO_MM, 3),
            "asymmetry_mm": round(worst * M_TO_MM, 3),
            "verdict": _band(worst * M_TO_MM, ASYMMETRY_THRESHOLDS["asymmetry_mm"]),
        })
    rows.sort(key=lambda row: -row["asymmetry_mm"])
    worst_row = rows[0] if rows else None
    verdict = _worst([row["verdict"] for row in rows]) if rows else "unmeasured"
    if worst_row is None:
        says = "This rig has no .L/.R bone pairs to compare."
    elif verdict == "ok":
        says = ("Left and right are mirror images: the worst pair (%s) differs by "
                "%.3f mm. A rigger who places one side and presses Symmetrize gets "
                "this number." % (worst_row["bone"], worst_row["asymmetry_mm"]))
    else:
        says = ("Left and right do not match: %s is %.1f mm off its mirror image, over "
                "%d pair(s) measured. An X-mirrored rig measures 0.0, so this rig's two "
                "sides were fitted independently."
                % (worst_row["bone"], worst_row["asymmetry_mm"], len(rows)))
    return {
        "pairs": rows,
        "measured": len(rows),
        "midplane_mm": round(midplane * M_TO_MM, 3),
        "worst": worst_row["bone"] if worst_row else None,
        "worst_asymmetry_mm": worst_row["asymmetry_mm"] if worst_row else None,
        "verdict": verdict,
        "thresholds": ASYMMETRY_THRESHOLDS,
        "says": says,
    }


def side_naming(rig, mesh, orientation=None):
    """Do the ``.L`` bones drive the character's **left**?

    The check the live project needed and nobody had: with the mesh facing
    ``-Y``, the character's left is ``+X``, so a bone called ``.L`` belongs at
    ``x > 0``.  ``DEF-shin.L`` at ``x = -191 mm`` while the left leg's geometry
    centres at ``x = +182 mm`` is a **373 mm** side swap that automatic weights
    will happily hide for ever, because they bind by proximity and the flesh
    next to the mis-named bone is perfectly happy to follow it.  Retargeting,
    X-mirror tooling and Godot's humanoid mapping are not.
    """
    orientation = orientation or detect_orientation(mesh)
    left_sign = _left_sign(orientation["faces"]) if orientation.get("confident") else 1.0
    midplane = (orientation.get("midplane_mm") or 0.0) / M_TO_MM
    matrix = rig.matrix_world
    mesh_matrix = mesh.matrix_world
    coords = [mesh_matrix @ v.co for v in mesh.data.vertices]

    rows = []
    for bone in sorted(rig.data.bones, key=lambda b: b.name):
        base, side = _side_of(bone.name)
        if side is None or not bone.use_deform:
            continue
        head = matrix @ bone.head_local
        tail = matrix @ bone.tail_local
        centre = (head + tail) * 0.5
        want = left_sign if side == "L" else -left_sign
        offset = (centre.x - midplane)
        correct = (offset * want) >= 0.0
        # Where the geometry of the side this bone claims actually is, at this
        # bone's own height: the number that makes the swap a distance, not a
        # sign. The band is half the bone's own length, never its vertical
        # extent — a clavicle or a foot is horizontal, and a 1 mm slab of a
        # character finds nothing to average.
        half = max((tail - head).length * 0.5, 1e-3)
        wanted = [p for p in coords
                  if abs(p.z - centre.z) <= half and (p.x - midplane) * want > 0.0]
        geometry_x = (sum(p.x for p in wanted) / len(wanted)) if wanted else None
        rows.append({
            "bone": bone.name,
            "side": side,
            "bone_x_mm": round(centre.x * M_TO_MM, 1),
            "expected_side": "+X" if want > 0 else "-X",
            "geometry_x_mm": None if geometry_x is None else round(geometry_x * M_TO_MM, 1),
            "distance_mm": (None if geometry_x is None
                            else round(abs(centre.x - geometry_x) * M_TO_MM, 1)),
            "correct": bool(correct),
        })
    wrong = [row for row in rows if not row["correct"]]
    verdict = "ok" if not wrong else "fail"
    if not rows:
        verdict = "unmeasured"
        says = "This rig has no sided deform bones, so there is no side naming to check."
    elif not wrong:
        says = ("Every one of the %d sided deform bones is on the side its name claims "
                "(the character faces %s, so its left is %s)."
                % (len(rows), orientation["faces"],
                   "+X" if left_sign > 0 else "-X"))
    else:
        worst = max(wrong, key=lambda row: row["distance_mm"] or 0.0)
        says = ("SIDE NAMES ARE MIRRORED: %d of %d sided deform bones are on the wrong "
                "half of the body. %s sits at x = %+.0f mm while the geometry it should "
                "be driving centres at x = %+.0f mm — %.0f mm away. Automatic weights "
                "hide this (they bind by proximity); X-mirror tooling, retargeting and "
                "Godot's humanoid mapping do not."
                % (len(wrong), len(rows), worst["bone"], worst["bone_x_mm"],
                   worst["geometry_x_mm"] or 0.0, worst["distance_mm"] or 0.0))
    return {
        "faces": orientation["faces"],
        "character_left": "+X" if left_sign > 0 else "-X",
        "orientation_confident": bool(orientation.get("confident")),
        "bones": rows,
        "wrong": [row["bone"] for row in wrong],
        "worst_distance_mm": (max((row["distance_mm"] or 0.0) for row in wrong)
                              if wrong else 0.0),
        "verdict": verdict,
        "says": says,
    }


# ---------------------------------------------------------------------------
# the artifacts: renders a human looks at
# ---------------------------------------------------------------------------

#: The ghosted body's opacity in the skeleton echo: enough to read the silhouette
#: and the limb volumes, faint enough that a bone inside it is unmistakable.
GHOST_ALPHA = 0.22

SKELETON_COLOR = (0.88, 0.13, 0.13)
ECHO_BACKGROUND = (0.13, 0.14, 0.16)

#: Blender's own weight-paint ramp, which is the one every artist can already
#: read: blue is nothing, red is everything.
WEIGHT_RAMP = ((0.0, (0.0, 0.0, 1.0)), (0.25, (0.0, 1.0, 1.0)),
               (0.5, (0.0, 1.0, 0.0)), (0.75, (1.0, 1.0, 0.0)),
               (1.0, (1.0, 0.0, 0.0)))


def _weight_color(weight):
    value = max(0.0, min(1.0, float(weight)))
    for index in range(len(WEIGHT_RAMP) - 1):
        low, low_color = WEIGHT_RAMP[index]
        high, high_color = WEIGHT_RAMP[index + 1]
        if value <= high:
            span = max(high - low, 1e-9)
            blend = (value - low) / span
            return tuple(low_color[i] + (high_color[i] - low_color[i]) * blend
                         for i in range(3))
    return WEIGHT_RAMP[-1][1]


def _render_pass(targets, path, view, resolution, bounds, transparent=True,
                 color_type="SINGLE", single_color=None, flat=False):
    """One Workbench render of exactly ``targets``, framed on shared ``bounds``.

    Shared bounds, always: two passes of the same character have to be the same
    picture twice or compositing them means nothing.
    """
    scene = get_scene()
    restore = []
    camera_object = None
    camera_data = None
    try:
        refresh_view_layer()
        _preview_snapshot(restore, scene, ("camera",))
        _preview_snapshot(restore, scene.render, (
            "engine", "filepath", "resolution_x", "resolution_y",
            "resolution_percentage", "film_transparent", "use_overwrite",
            "use_file_extension", "use_stamp", "use_border"))
        _preview_snapshot(restore, scene.render.image_settings,
                          ("file_format", "color_mode", "color_depth"))
        _preview_snapshot(restore, scene.display, ("render_aa",))
        _preview_snapshot(restore, scene.display.shading, (
            "light", "color_type", "single_color", "studio_light",
            "background_type", "background_color", "show_shadows",
            "show_specular_highlight", "show_cavity", "cavity_type",
            "show_object_outline", "show_xray"))
        view_settings = getattr(scene, "view_settings", None)
        if view_settings is not None:
            _preview_snapshot(restore, view_settings,
                              ("view_transform", "look", "exposure", "gamma"))

        chosen = {obj.name for obj in targets}
        for obj in bpy.data.objects:
            if obj.type not in ("MESH", "CURVE", "SURFACE", "META", "FONT", "VOLUME"):
                continue
            restore.append((obj, "hide_render", obj.hide_render))
            obj.hide_render = obj.name not in chosen

        scene.render.engine = "BLENDER_WORKBENCH"
        _preview_configure_workbench(scene)
        shading = scene.display.shading
        for name, value in (("color_type", color_type),
                            ("show_cavity", not flat),
                            ("show_shadows", not flat),
                            ("show_specular_highlight", not flat)):
            try:
                setattr(shading, name, value)
            except Exception:  # noqa: BLE001
                pass
        if flat:
            try:
                shading.light = "FLAT"
            except Exception:  # noqa: BLE001
                pass
        if single_color is not None:
            try:
                shading.single_color = single_color
            except Exception:  # noqa: BLE001
                pass

        render = scene.render
        render.filepath = path
        render.resolution_x = resolution
        render.resolution_y = resolution
        render.resolution_percentage = 100
        render.film_transparent = bool(transparent)
        render.use_overwrite = True
        render.use_file_extension = True
        render.use_border = False
        try:
            render.use_stamp = False
        except (AttributeError, TypeError):
            pass
        render.image_settings.file_format = "PNG"
        render.image_settings.color_mode = "RGBA" if transparent else "RGB"
        if view_settings is not None:
            for name, value in (("view_transform", "Standard"), ("look", "None"),
                                ("exposure", 0.0), ("gamma", 1.0)):
                try:
                    setattr(view_settings, name, value)
                except (AttributeError, TypeError, ValueError):
                    pass

        camera_data = bpy.data.cameras.new("Forge Landmark Camera")
        camera_object = bpy.data.objects.new("Forge Landmark Camera", camera_data)
        scene.collection.objects.link(camera_object)
        _preview_frame(camera_object, PREVIEW_VIEWS[view], bounds[0], bounds[1],
                       margin=PREVIEW_MARGIN)
        scene.camera = camera_object
        refresh_view_layer()
        try:
            status = bpy.ops.render.render(write_still=True)
        except RuntimeError as exc:
            raise ForgeError("Blender could not render the %s pass: %s" % (view, exc))
        if "FINISHED" not in status:
            raise ForgeError("The %s render returned %s instead of finishing."
                             % (view, ", ".join(sorted(status)) or "nothing"))
    finally:
        if camera_object is not None:
            try:
                bpy.data.objects.remove(camera_object, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
        if camera_data is not None:
            try:
                if camera_data.users == 0:
                    bpy.data.cameras.remove(camera_data)
            except (ReferenceError, RuntimeError):
                pass
        _preview_restore(restore)
        try:
            refresh_view_layer()
        except Exception:  # noqa: BLE001
            pass
    if not os.path.exists(path):
        raise ForgeError("The render reported success but wrote nothing to %r." % path)
    return path


def _read_pixels(path):
    image = bpy.data.images.load(path)
    try:
        width, height = image.size
        buffer = [0.0] * (width * height * 4)
        image.pixels.foreach_get(buffer)
    finally:
        try:
            bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):
            pass
    return buffer, width, height


def _write_pixels(path, buffer, width, height):
    image = bpy.data.images.new("Forge Composite", width=width, height=height,
                                alpha=False, float_buffer=False)
    try:
        image.pixels.foreach_set(buffer)
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    finally:
        try:
            bpy.data.images.remove(image)
        except (ReferenceError, RuntimeError):
            pass
    return path


def _composite_ghost(body_path, overlay_path, out_path, ghost=GHOST_ALPHA,
                     background=ECHO_BACKGROUND):
    """Ghosted body, opaque overlay, one picture.

    Blender's transparent film is **premultiplied**, so the body's colour is
    already scaled by its own coverage and the mix is a plain add over the
    background rather than a lerp.  Getting that wrong is what makes a
    composited ghost look like a fog bank.
    """
    body, width, height = _read_pixels(body_path)
    overlay, o_width, o_height = _read_pixels(overlay_path)
    if (width, height) != (o_width, o_height):
        raise ForgeError("The two render passes came out different sizes (%dx%d vs "
                         "%dx%d); they cannot be composited."
                         % (width, height, o_width, o_height))
    out = [0.0] * len(body)
    for index in range(0, len(body), 4):
        body_alpha = body[index + 3] * ghost
        overlay_alpha = overlay[index + 3]
        for channel in range(3):
            value = (background[channel] * (1.0 - body_alpha)
                     + body[index + channel] * ghost)
            value = value * (1.0 - overlay_alpha) + overlay[index + channel]
            out[index + channel] = min(1.0, max(0.0, value))
        out[index + 3] = 1.0
    return _write_pixels(out_path, out, width, height)


#: How big a joint marker is, as a multiple of the prism's own half-width at
#: that bone.  Big enough to read as a ball rather than a lump in the shaft,
#: small enough that two adjacent joints do not merge into one blob.
JOINT_MARKER_SCALE = 2.1


def _joint_ball(bm, centre, radius, segments=8, rings=4):
    """A small UV sphere at ``centre``, welded into ``bm``.

    Built by hand rather than with ``bmesh.ops.create_uvsphere`` because that
    operator writes into the *current* mesh with its own transform matrix and
    the surrounding code is already placing vertices in world space; two
    coordinate conventions in one loop is how a marker ends up somewhere else.
    """
    poles = (bm.verts.new(centre + Vector((0.0, 0.0, radius))),
             bm.verts.new(centre - Vector((0.0, 0.0, radius))))
    grid = []
    for ring in range(1, rings):
        phi = math.pi * ring / float(rings)
        z = math.cos(phi) * radius
        r = math.sin(phi) * radius
        row = []
        for step in range(segments):
            theta = 2.0 * math.pi * step / segments
            row.append(bm.verts.new(centre + Vector((r * math.cos(theta),
                                                     r * math.sin(theta), z))))
        grid.append(row)
    for index in range(len(grid) - 1):
        for step in range(segments):
            a = grid[index][step]
            b = grid[index][(step + 1) % segments]
            c = grid[index + 1][(step + 1) % segments]
            d = grid[index + 1][step]
            try:
                bm.faces.new((a, b, c, d))
            except ValueError:
                continue
    for row, pole in ((grid[0], poles[0]), (grid[-1], poles[1])):
        for step in range(segments):
            a = row[step]
            b = row[(step + 1) % segments]
            try:
                bm.faces.new((pole, b, a) if pole is poles[0] else (pole, a, b))
            except ValueError:
                continue


def _bone_sticks(armature, name="Forge Skeleton Echo", deform_only=True,
                 thickness=0.35, joint_markers=True):
    """A mesh of square prisms, one per bone: what an armature looks like to a render.

    Armatures do not appear in a render at all, so the skeleton has to *become*
    geometry to be photographed.  Prisms rather than octahedra: the picture is
    read for where a bone is, not for how pretty its widget is.

    **Plus a ball at every joint**, which is the owner's own note on the first
    echo they looked at: *"I couldn't see where the bones articulate."*  A chain
    of prisms drawn end to end is a continuous red worm — the thing a rigger
    actually needs to find in that picture is the **hinge**, and a stick figure
    without dots does not have one.  Each bone's head and tail get a sphere
    sized off that bone's own prism, so a finger's joints stay finger-sized;
    coincident joints (a connected chain's tail and its child's head) are welded
    by the ``round``ed key below rather than drawn twice into a lumpy double
    ball.  ``joint_markers=False`` gets the old stick-only picture back.
    """
    bm = bmesh.new()
    matrix = armature.matrix_world
    drawn = []
    joints = {}
    bones = [b for b in armature.data.bones
             if (b.use_deform or not deform_only)]
    if not bones:
        bones = list(armature.data.bones)
    lengths = [(matrix @ b.tail_local - matrix @ b.head_local).length for b in bones]
    median = sorted(lengths)[len(lengths) // 2] if lengths else 0.1
    for bone in bones:
        head = matrix @ bone.head_local
        tail = matrix @ bone.tail_local
        direction = tail - head
        length = direction.length
        if length < 1e-6:
            continue
        direction = direction / length
        up = Vector((0.0, 0.0, 1.0))
        if abs(direction.dot(up)) > 0.95:
            up = Vector((0.0, 1.0, 0.0))
        side = direction.cross(up).normalized()
        other = direction.cross(side).normalized()
        radius = max(min(length * thickness * 0.25, median * 0.12), median * 0.02)
        corners = []
        for end in (head, tail):
            for sx, sy in ((1, 1), (1, -1), (-1, -1), (-1, 1)):
                corners.append(bm.verts.new(end + side * (radius * sx)
                                            + other * (radius * sy)))
        quads = ((0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1), (1, 5, 6, 2),
                 (2, 6, 7, 3), (3, 7, 4, 0))
        for quad in quads:
            try:
                bm.faces.new([corners[i] for i in quad])
            except ValueError:
                continue
        drawn.append(bone.name)
        if joint_markers:
            for end in (head, tail):
                key = (round(end.x, 5), round(end.y, 5), round(end.z, 5))
                # The biggest bone meeting at a joint sets the ball's size, so a
                # wrist between a forearm and a hand reads as the forearm's
                # joint rather than shrinking to the hand's.
                joints[key] = (end, max(joints.get(key, (None, 0.0))[1],
                                        radius * JOINT_MARKER_SCALE))
    for centre, radius in joints.values():
        _joint_ball(bm, centre, radius)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    get_scene().collection.objects.link(obj)
    refresh_view_layer()
    return obj, drawn, len(joints)


def _world_box(objects):
    lows = []
    highs = []
    for obj in objects:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        lows.append(Vector((min(c.x for c in corners), min(c.y for c in corners),
                            min(c.z for c in corners))))
        highs.append(Vector((max(c.x for c in corners), max(c.y for c in corners),
                             max(c.z for c in corners))))
    low = Vector((min(v.x for v in lows), min(v.y for v in lows), min(v.z for v in lows)))
    high = Vector((max(v.x for v in highs), max(v.y for v in highs),
                   max(v.z for v in highs)))
    return low, high


def render_skeleton_echo(armature, mesh, directory, views=("FRONT", "SIDE"),
                         resolution=768, deform_only=True, prefix="skeleton",
                         joint_markers=True):
    """The echo-back: the placed skeleton drawn over the ghosted body.

    The rig's equivalent of the floor plan's approval SVG.  A human rigger looks
    at the skeleton inside the mesh before they bind anything, because that is
    the moment a mis-placed or mis-named bone is free to fix; this produces the
    same picture, front and side, as a file the report names.  The numbers in
    ``rig_check``'s ``centering`` block measure the same thing — the picture is
    for the artist, the number is for the gate, and they must agree.

    Every joint gets a **ball** as well as its bones getting prisms
    (``joint_markers``, default on).  Sticks alone draw a chain as one
    continuous red worm, and the question this picture exists to answer — *does
    the knee sit where the flesh creases* — is a question about the **hinge**,
    which a worm does not have.
    """
    directory = resolve_path(directory, make_parents=True)
    if os.path.isfile(directory):
        raise ForgeError("%r is a file; the skeleton echo writes into a folder." % directory)
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    sticks = None
    out = []
    started = time.monotonic()
    try:
        with object_mode():
            sticks, drawn, marked = _bone_sticks(armature, deform_only=deform_only,
                                                 joint_markers=joint_markers)
            bounds = _world_box([mesh, sticks])
            for view in views:
                key = str(view).upper()
                if key not in PREVIEW_VIEWS:
                    raise ForgeError("Unknown view %r for the skeleton echo. Known: %s."
                                     % (view, ", ".join(sorted(PREVIEW_VIEWS))))
                body = os.path.join(directory, "_%s_%s_body.png" % (prefix, key.lower()))
                bones = os.path.join(directory, "_%s_%s_bones.png" % (prefix, key.lower()))
                final = os.path.join(directory, "%s_%s.png" % (prefix, key.lower()))
                _render_pass([mesh], body, key, resolution, bounds, transparent=True)
                _render_pass([sticks], bones, key, resolution, bounds, transparent=True,
                             single_color=SKELETON_COLOR, flat=True)
                _composite_ghost(body, bones, final)
                for temp in (body, bones):
                    try:
                        os.remove(temp)
                    except OSError:
                        pass
                out.append({"view": key.lower(), "path": final,
                            "bytes": os.path.getsize(final)})
    finally:
        if sticks is not None:
            data = sticks.data
            try:
                bpy.data.objects.remove(sticks, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass
            try:
                if data is not None and data.users == 0:
                    bpy.data.meshes.remove(data)
            except (ReferenceError, RuntimeError):
                pass
        refresh_view_layer()
    return {
        "images": out,
        "bones_drawn": len(drawn),
        "joints_marked": marked,
        "ghost_alpha": GHOST_ALPHA,
        "resolution": resolution,
        "says": ("The skeleton is drawn in red over the body at %d%% opacity, front and "
                 "side, with a ball at each of its %d joints so the articulation points "
                 "are visible rather than a continuous stick (%s). Look at it the way a "
                 "rigger looks at a rig before binding: every bone inside its limb, on "
                 "the centreline, the left side on the left, and each joint where the "
                 "flesh actually creases."
                 % (int(GHOST_ALPHA * 100), marked,
                    ", ".join(entry["path"] for entry in out))),
        "seconds": round(time.monotonic() - started, 3),
    }


def render_weight_maps(rig, mesh, directory, bones=None, views=("FRONT",),
                       resolution=640, max_bones=8, weight_floor=0.001,
                       prefix="weights"):
    """Per-bone weight maps, blue to red, as pictures — the map, looked at.

    ``weight_report`` counts vertices and ``influence_overlap`` names pairs, but
    a weight map is a *shape*, and the artefacts that matter — a stripe of the
    thigh caught in the hand's group, a hard edge where a smooth falloff belongs
    — are shapes.  So they get rendered, in the ramp every Blender artist
    already reads, and the report names the files.
    """
    directory = resolve_path(directory, make_parents=True)
    if not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    groups = _deform_groups(rig, mesh)
    if not groups:
        raise ForgeError(
            "%r has no vertex group named after a deform bone of %r, so there are no "
            "weight maps to render. Skin it first (rigforge_generate_rig)."
            % (mesh.name, rig.name))
    per_bone, _totals = _weight_table(mesh, groups)
    ranked = sorted(((name, sum(table.values())) for name, table in per_bone.items()),
                    key=lambda row: -row[1])
    if bones:
        wanted = []
        known = set(per_bone)
        for name in bones:
            if name not in known:
                raise ForgeError("%r is not a deform bone with a vertex group on %r. "
                                 "It has: %s." % (name, mesh.name,
                                                  ", ".join(sorted(known)[:12])))
            wanted.append(name)
    else:
        wanted = [name for name, mass in ranked if mass > 0.0][:max_bones]
    if not wanted:
        raise ForgeError("Every deform group on %r is empty: there is no weight to "
                         "paint a map of." % mesh.name)

    count = len(mesh.data.vertices)
    attribute_name = "forge_weight_view"
    out = []
    created = None
    started = time.monotonic()
    try:
        with object_mode():
            colors = mesh.data.color_attributes
            existing = colors.get(attribute_name)
            if existing is not None:
                colors.remove(existing)
            created = colors.new(name=attribute_name, type="FLOAT_COLOR", domain="POINT")
            for index, attr in enumerate(colors):
                if attr.name == attribute_name:
                    for field in ("active_color_index", "render_color_index"):
                        try:
                            setattr(colors, field, index)
                        except (AttributeError, TypeError, ValueError):
                            pass
            bounds = _world_box([mesh])
            for bone in wanted:
                table = per_bone.get(bone, {})
                flat = []
                for vertex in range(count):
                    weight = table.get(vertex, 0.0)
                    if weight < weight_floor:
                        weight = 0.0
                    color = _weight_color(weight)
                    flat.extend((color[0], color[1], color[2], 1.0))
                created.data.foreach_set("color", flat)
                mesh.data.update()
                for view in views:
                    key = str(view).upper()
                    if key not in PREVIEW_VIEWS:
                        raise ForgeError("Unknown view %r for a weight map." % view)
                    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", bone)
                    path = os.path.join(directory, "%s_%s_%s.png"
                                        % (prefix, safe, key.lower()))
                    _render_pass([mesh], path, key, resolution, bounds,
                                 transparent=False, color_type="VERTEX", flat=True)
                    out.append({
                        "bone": bone, "view": key.lower(), "path": path,
                        "vertices": len(table),
                        "mass": round(sum(table.values()), 3),
                        "bytes": os.path.getsize(path),
                    })
    finally:
        try:
            with object_mode():
                attribute = mesh.data.color_attributes.get(attribute_name)
                if attribute is not None:
                    mesh.data.color_attributes.remove(attribute)
                mesh.data.update()
        except (ReferenceError, RuntimeError, AttributeError):
            pass
        refresh_view_layer()
    return {
        "images": out,
        "bones": wanted,
        "ramp": "blue 0.0 -> cyan -> green -> yellow -> red 1.0 (Blender's weight ramp)",
        "resolution": resolution,
        "says": ("%d weight map(s) rendered for %s. Blue is no influence, red is all of "
                 "it: look for a map that reaches somewhere the bone cannot, and for a "
                 "hard edge where a limb should fade."
                 % (len(out), ", ".join(wanted))),
        "seconds": round(time.monotonic() - started, 3),
    }


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def prepare_for_rigging(obj, params, warnings):
    """Steps 1-2 of the human workflow, on their own: orient, then symmetrize.

    Shared by ``rigforge_landmarks`` and ``rigforge_metarig`` so the default
    rigging path and the report say the same thing about the same mesh.
    """
    orient_mode = get_choice(params, "orient",
                             {"FIX": "fix", "REPORT": "report", "STRICT": "strict",
                              "SKIP": "skip"}, "fix")
    symmetry = params.get("symmetry", True)
    if isinstance(symmetry, str):
        symmetry = symmetry.strip().lower() not in ("false", "no", "off", "0")
    symmetry = bool(symmetry)
    keep = get_choice(params, "symmetry_keep", {"+X": "+X", "-X": "-X", "X": "+X"}, "+X")
    tolerance = get_float(params, "symmetry_tolerance_mm", SYMMETRY_TOLERANCE_MM,
                          minimum=0.0, maximum=1000.0)

    orientation = detect_orientation(obj)
    if orient_mode == "skip":
        orientation["action"] = "skipped"
        orientation["says"] = (
            "The orientation gate was skipped by request; everything below assumes "
            "%r already faces -Y with its left at +X." % obj.name)
        if not orientation["matches_convention"]:
            warnings.append(
                "orient='skip' was passed but %r measures as facing %s. Every side name "
                "below is only as right as that assumption."
                % (obj.name, orientation["faces"]))
    else:
        orientation = orient_to_convention(
            obj, report=orientation, allow_rotate=(orient_mode == "fix"),
            strict=(orient_mode == "strict"), warnings=warnings)
        if orientation.get("action") == "rotated":
            warnings.append(orientation["says"])

    left_sign = _left_sign(orientation["faces"]) if orientation.get("confident") else 1.0
    symmetry_report = measure_symmetry(obj)
    midplane = symmetry_report["midplane"]

    # Sides are re-derived from the geometry **before** symmetrizing, because
    # symmetrizing copies one half's vertex groups onto the other and the
    # evidence of a swap — which half a tag was painted on — is gone the moment
    # it runs.
    retag = retag_sides_from_geometry(obj, midplane=midplane, character_left=left_sign)
    if retag["swapped_tags"]:
        warnings.append(retag["says"])
    symmetrized = None
    measured = symmetry_report["measured_mm"]
    # Symmetrizing rewrites half the mesh, and on a mesh that has already been
    # unwrapped and baked that half's UVs — and therefore its normal map — go
    # with it. A rigger symmetrizes the *sculpt*, long before UVs exist. So an
    # unwrapped mesh is measured and left alone: the bones are still authored on
    # one side and mirrored (which is what a rigger does with a slightly
    # lopsided model), and the residual is quoted as the error that leaves.
    unwrapped = bool(getattr(obj.data, "uv_layers", None) and len(obj.data.uv_layers))
    if not symmetry:
        warnings.append(
            "symmetry=false: %r was left exactly as it is (%.2f mm of asymmetry) and "
            "each side will be authored from its own geometry. That is the right call "
            "for a character that is *meant* to be asymmetric and the wrong one for a "
            "generated mesh that came out lopsided." % (obj.name, measured or 0.0))
    elif unwrapped and measured is not None and measured > tolerance:
        symmetry_report["not_symmetrized"] = "the mesh is already unwrapped"
        warnings.append(
            "%r is %.2f mm out of X-symmetry, and it was NOT symmetrized because it is "
            "already unwrapped: rewriting half the mesh would mirror that half's UVs and "
            "orphan the normal map baked against them. The rig is still authored on the "
            "character's left and mirrored, so its two sides match each other exactly — "
            "but the right side's bones sit up to %.1f mm from the right side's flesh. "
            "Fix the symmetry on the sculpt, before retopo; or pass symmetry=false to "
            "fit each side to its own geometry instead."
            % (obj.name, measured, measured))
    elif measured is not None and measured > tolerance:
        symmetrized = symmetrize_mesh(obj, midplane, keep=keep)
        after = symmetrized["residual_after"]
        symmetry_report["removed_mm"] = measured
        symmetry_report["after_mm"] = after["measured_mm"]
        midplane = after["midplane"]
        if after["measured_mm"] is not None and after["measured_mm"] > tolerance:
            raise ForgeError(
                "Refusing to rig %r: it is %.2f mm out of X-symmetry and symmetrizing "
                "it only got that to %.2f mm (tolerance %.2f mm). A mesh that cannot be "
                "made symmetric cannot be rigged one side and mirrored, which is the "
                "whole method. Fix the mesh, raise symmetry_tolerance_mm, or pass "
                "symmetry=false to rig it exactly as it is."
                % (obj.name, measured, after["measured_mm"] or -1.0, tolerance))
        warnings.append(
            "%r was %.2f mm out of X-symmetry, so it was symmetrized about its own "
            "midplane (x = %.1f mm) before a bone was placed; the residual is now "
            "%.3f mm." % (obj.name, measured, symmetry_report["midplane_mm"],
                          after["measured_mm"] or 0.0))
    if symmetrized is not None:
        # ... and again afterwards, because the copy carries the source half's
        # group names onto the other side of the body.
        retag["after_symmetrize"] = retag_sides_from_geometry(
            obj, midplane=midplane, character_left=left_sign)
    return {
        "orientation": orientation,
        "symmetry": symmetry_report,
        "symmetrized": symmetrized,
        "retag": retag,
        "midplane": midplane,
        "character_left": left_sign,
        "symmetry_requested": symmetry,
    }


def _measured_axis_hints(obj):
    """Limb directions a previous ``rigforge_autotag`` measured, or ``(None, None)``.

    Imported at call time rather than at module scope on purpose: auto-tagging
    reuses this module's midplane, facing gate and principal-axis solver, so the
    dependency runs that way round and a top-level import here would close the
    cycle.  The lookup is a courtesy — a mesh that has never been auto-tagged
    simply gets the anatomical defaults, which is the behaviour this module
    shipped with.
    """
    try:
        from . import rigforge_autotag
    except ImportError:
        return None, None
    try:
        return rigforge_autotag.stored_axis_hints(obj)
    except Exception:  # noqa: BLE001 - a stale hint must never cost a fit
        return None, None


@command("rigforge_landmarks")
def cmd_rigforge_landmarks(params):
    """The human rigger's workflow, steps 1-3, as a report (and optionally applied).

    ``rigforge_landmarks {"object"?, "action"?: "report"|"prepare",
    "orient"?: "fix"|"report"|"skip", "symmetry"?: bool, "symmetry_keep"?: "+X"|"-X",
    "symmetry_tolerance_mm"?, "stations"?, "stance_span"?,
    "rest_stance"?: "flexed"|"straight"}``

    ``report`` measures and changes nothing.  ``prepare`` runs the gate for real:
    rotates the mesh onto the convention, symmetrizes it about its measured
    midplane, re-derives the sided tags from the geometry — and then measures the
    landmarks the metarig fit would use, so they can be read before any bone
    exists.  ``rigforge_metarig`` calls exactly this.
    """
    obj = resolve_object(params, mesh_only=True)
    started = time.monotonic()
    warnings = []
    action = get_choice(params, "action", {"REPORT": "report", "PREPARE": "prepare",
                                           "APPLY": "prepare"}, "report")
    stations = get_int(params, "stations", STATIONS, minimum=9, maximum=129)
    stance_span = get_stance_span(params)

    if action == "report":
        orientation = detect_orientation(obj)
        symmetry = measure_symmetry(obj)
        prepared = {"orientation": orientation, "symmetry": symmetry,
                    "symmetrized": None, "retag": None,
                    "midplane": symmetry["midplane"],
                    "character_left": (_left_sign(orientation["faces"])
                                       if orientation["confident"] else 1.0),
                    "symmetry_requested": None}
    else:
        prepared = prepare_for_rigging(obj, params, warnings)

    landmarks = None
    error = None
    sides = ("L", "R") if prepared["symmetry_requested"] is False else ("L",)
    axis_hints, hint_note = _measured_axis_hints(obj)
    if hint_note:
        warnings.append(hint_note)
    try:
        landmarks = biped_landmarks(obj, midplane=prepared["midplane"],
                                    character_left=prepared["character_left"],
                                    warnings=warnings, sides=sides,
                                    axis_hints=axis_hints,
                                    facing=prepared["orientation"]["faces"],
                                    stance_span=stance_span)
    except ForgeError as exc:
        error = str(exc)

    points = {}
    if landmarks is not None:
        for role, point in sorted(landmarks["points"].items()):
            info = dict(landmarks["detail"].get(role) or {})
            info.pop("point", None)
            info["mm"] = [round(v * M_TO_MM, 2) for v in point]
            points[role] = info

    return {
        "object": obj.name,
        "action": action,
        "convention": CONVENTION,
        "orientation": prepared["orientation"],
        "symmetry": prepared["symmetry"],
        "symmetrized": prepared["symmetrized"],
        "side_tags": prepared["retag"],
        "midplane_mm": round(prepared["midplane"] * M_TO_MM, 3),
        "character_left": "+X" if prepared["character_left"] > 0 else "-X",
        "landmarks": points,
        "limbs": ({name: limb.as_dict() for name, limb in landmarks["limbs"].items()}
                  if landmarks else {}),
        "prebend": (landmarks.get("prebend") if landmarks else []),
        "stance": (landmarks.get("stance") if landmarks else []),
        "stance_span": (landmarks.get("stance_span") if landmarks
                        else stance_span),
        "landmark_error": error,
        "stations": stations,
        "says": _landmark_sentence(obj, prepared, points, error,
                                   landmarks.get("prebend") if landmarks else None,
                                   landmarks.get("stance") if landmarks else None),
        "warnings": warnings,
        "seconds": round(time.monotonic() - started, 3),
    }


def _landmark_sentence(obj, prepared, points, error, prebend=None, stance=None):
    orientation = prepared["orientation"]
    lines = ["%s faces %s (%s is its left)."
             % (obj.name, orientation["faces"],
                "+X" if prepared["character_left"] > 0 else "-X")]
    symmetry = prepared["symmetry"]
    if symmetry.get("measured_mm") is not None:
        lines.append("Mesh asymmetry %.2f mm%s."
                     % (symmetry["measured_mm"],
                        " (symmetrized to %.3f mm)" % symmetry["after_mm"]
                        if symmetry.get("after_mm") is not None else ""))
    if error:
        lines.append("No landmarks: %s" % error)
    else:
        lines.append("%d landmark(s) taken from cross-section centroids, "
                     "character-left only; the right side is their mirror."
                     % len(points))
    if prebend:
        moved = [entry for entry in prebend if entry.get("nudged")]
        if moved:
            lines.append("Pre-bent %d joint(s) so IK has an unambiguous plane: %s."
                         % (len(moved),
                            ", ".join("%s %+.1f mm %s"
                                      % (entry["joint"], entry.get("nudge_mm") or 0.0,
                                         entry.get("direction") or "")
                                      for entry in moved)))
        else:
            lines.append("Every limb already apexed the right way by more than %.0f%% "
                         "of its span, so no pre-bend was needed."
                         % (100.0 * PREBEND_FRACTION))
    if stance:
        flexed = [entry for entry in stance if entry.get("flexed")]
        if flexed:
            lines.append("Flexed the rest stance on %d leg(s) — ankle planted, hip "
                         "down, knee forward: %s."
                         % (len(flexed),
                            ", ".join("%s %.4f -> %.4f of chain (%.1f deg)"
                                      % (entry["joint"],
                                         entry.get("extension_frac_before") or 0.0,
                                         entry.get("extension_frac_after") or 0.0,
                                         entry.get("knee_flex_deg") or 0.0)
                                      for entry in flexed)))
        else:
            lines.append("Every leg already stood inside %.2f of its own chain, so the "
                         "stance left it alone." % STANCE_SPAN_FRACTION)
    return " ".join(lines)


@command("rigforge_echo_skeleton")
def cmd_rigforge_echo_skeleton(params):
    """Draw the placed skeleton over the ghosted mesh, front and side.

    ``rigforge_echo_skeleton {"rig"|"metarig"?, "mesh"?, "dir"?, "views"?,
    "resolution"?, "deform_only"?, "joint_markers"?}``

    ``joint_markers`` (default true) puts a ball at every bone head and tail, so
    the picture shows where the skeleton **articulates** rather than one
    continuous red stick.
    """
    started = time.monotonic()
    name = params.get("rig") or params.get("metarig") or params.get("armature")
    armature = None
    if isinstance(name, str) and name.strip():
        armature = bpy.data.objects.get(name.strip())
        if armature is None or armature.type != "ARMATURE":
            raise ForgeError("%r is not an armature in this scene." % name)
    else:
        for obj in bpy.data.objects:
            if obj.type == "ARMATURE":
                armature = obj
                break
        if armature is None:
            raise ForgeError("There is no armature in this scene to echo back. Build "
                             "one with rigforge_metarig first.")
    mesh_name = params.get("mesh")
    mesh = None
    if isinstance(mesh_name, str) and mesh_name.strip():
        mesh = bpy.data.objects.get(mesh_name.strip())
        if mesh is None or mesh.type != "MESH":
            raise ForgeError("%r is not a mesh in this scene." % mesh_name)
    else:
        stored = armature.get("forge_rig_mesh")
        if isinstance(stored, str):
            mesh = bpy.data.objects.get(stored)
        if mesh is None:
            for child in bpy.data.objects:
                if child.type == "MESH" and child.find_armature() is armature:
                    mesh = child
                    break
        if mesh is None:
            raise ForgeError("Could not tell which mesh %r belongs to; name it with "
                             "'mesh'." % armature.name)

    directory = params.get("dir") or params.get("path")
    if not isinstance(directory, str) or not directory.strip():
        raise ForgeError("Give 'dir': the folder to write the echo images into.")
    views = params.get("views") or ("FRONT", "SIDE")
    if isinstance(views, str):
        views = [views]
    resolution = get_int(params, "resolution", 768, minimum=PREVIEW_MIN_RESOLUTION,
                         maximum=PREVIEW_MAX_RESOLUTION)
    deform_only = get_bool(params, "deform_only", True)
    result = render_skeleton_echo(
        armature, mesh, directory.strip(), views=views, resolution=resolution,
        deform_only=deform_only,
        joint_markers=get_bool(params, "joint_markers", True))
    result.update({"armature": armature.name, "mesh": mesh.name,
                   "seconds": round(time.monotonic() - started, 3)})
    return result


@command("rigforge_weight_maps")
def cmd_rigforge_weight_maps(params):
    """Render per-bone weight maps, and the influence-overlap matrix beside them.

    ``rigforge_weight_maps {"rig"?, "mesh"?, "dir", "bones"?, "views"?,
    "resolution"?, "max_bones"?, "overlap"?}``
    """
    started = time.monotonic()
    from . import rigcheck

    rig = rigcheck._resolve_rig(params)
    mesh = rigcheck._mesh_for(rig, params)
    directory = params.get("dir") or params.get("path")
    if not isinstance(directory, str) or not directory.strip():
        raise ForgeError("Give 'dir': the folder to write the weight maps into.")
    bones = params.get("bones")
    if isinstance(bones, str):
        bones = [bones]
    views = params.get("views") or ("FRONT",)
    if isinstance(views, str):
        views = [views]
    resolution = get_int(params, "resolution", 640, minimum=PREVIEW_MIN_RESOLUTION,
                         maximum=PREVIEW_MAX_RESOLUTION)
    max_bones = get_int(params, "max_bones", 8, minimum=1, maximum=64)
    want_overlap = get_bool(params, "overlap", True)

    result = render_weight_maps(rig, mesh, directory.strip(), bones=bones, views=views,
                                resolution=resolution, max_bones=max_bones)
    result.update({
        "rig": rig.name,
        "mesh": mesh.name,
        "overlap": influence_overlap(rig, mesh) if want_overlap else None,
        "seconds": round(time.monotonic() - started, 3),
    })
    return result
