"""Magmoo v5 -- the LIVING GOO: translucent RED lava serpent that rests PAUSED MID-MOTION (mound + tail grounded, the
neck arch + head hanging in the air, droplets hanging beside it) and comes to life through its movement (cartoon slime:
anticipation squash, launch, a FLOATY hang, splat, settle).

v5 (design/review-log.md 2026-09-26 "Cross-unit feedback round", artist verbatim, binding; v4 was "a lot better"):
  LEAP     floatier: longer air time + a flat-topped hop profile (1 - |2 tau - 1|^WALK_FLOAT: the arc dwells round its
           apex, launch and splat stay snappy); longer cycle so the loop, the gather and the chase keep their beats.
  REST     = the bind pose "paused in motion" per design/reference/magmoo-v5-rest-pose-reference.png: the mound + curled
           tail lie on the floor (they carry feet_origin), the mid keeps its combined ARCH lifted clear of the crater
           (REST_GAP_CROWN), the head rides the arch in its nose-down flight attitude thrown out ahead (REST_GAP_HEAD),
           three droplets HANG in the air beside the arch, two sit on the floor by the mound. Visible gaps kept (v4).
  EYES     a solid RED GOO EYE fills each carved socket: one lens-shaped piece (the dish below, a shallow dome
           EYE_ORB_BULGE proud of the goo on top), no pupil, no white; own region 'eye_goo'. The ball's sockets too.
  COLOUR   the body goo shifts from orange to the reference RED; accents (hot patches, flame tips, cores) unchanged.
  FLOOR    v4 caveat fixed: the bulge keys + bridge strands (set after the floor solver) are re-settled per frame.

v4 (design/review-log.md 2026-09-26 "Magmoo v4 feedback" + "Magmoo movement identity", artist verbatim, binding):
  COLOUR   bright glossy ORANGE lava per design/reference/magmoo-v4-color-reference.png, still a bit see-through
           (palettes/magmoo: per-region alpha kept, re-tuned for the lighter colour).
  EYES     carved EMPTY sockets: a spherical dish sunk into the head goo, no eyeball, no white, no pupil, no glow; the
           socket interior is a darker shade of the body red ('eye' region). Placed high on the head sides, further
           back toward the dorsal flames (the dragon placement of magmoo-v4-eye-annotation.png).
  MOUND    the splash mound (crown + finger splats) exists only while grounded: the 'flight' shape key maps the mound
           surface onto a smooth tube segment continuing the tail forward (a fold-free relaxed projection), so in the
           air the body is one clean serpent (mid -> smooth segment -> tail).
  REST     = the bind pose: the three goo segments LYING ON THE FLOOR, visibly separate (head flat on the floor as
           sculpted, the mid a hump with both ends on the floor, the mound + curled tail), measured surface gaps
           REST_GAP_*; the droplets sit on the floor beside the pieces, never inside a gap.
  IDLE     from the segmented rest he cycles his forms: gathers into the goo BALL (with small bounces), releases back
           into segments, then the pieces rise, merge and fly the vertical-S in place, settle and separate again.
  WALK     bounding leaps, "goops chasing each other": the rear pieces chase up to the head (gaps close, surfaces
           bulge on contact), anticipation squash, LAUNCH -- the body follows the head through the air along a
           vertical-S arc (a delayed hop per body point + an up/down travelling wave), lands head first with a splat,
           the pieces run apart into segments at the next spot (bridges neck + snap) and settle. In place.
  BALL     the goo-ball round trip from the segmented rest, now with small bounces during the hold.
  GOO      stretchy liquid deformation on top of the bones: bone stretch with volume-preserving thinning, squash shape
           keys per piece, bulge keys at the joins, goo BRIDGES (two tapered half-strands per join on their own bones)
           that reach, connect, neck and snap with a recoil, driven by the measured surface gap frame by frame.
  SHEDDING a launch splat left on the floor that absorbs, drips that fall off the flying body and splat + absorb.
  Recorded, NOT built: death = collapse into an inert puddle; spawn = rise from one (deferred wave).

    blender --background source-copies/newunit-magmoo.blend --factory-startup --python improve/magmoo_build.py -- \
        [--stop improved]                (geometry + regions + palette + shape keys only: no rig -- fast look loop)
        [--outroot <dir>]                (write every output under <dir> instead of the project: the determinism re-run)
        [--skins default,obsidian]       (extra palette skins -> rigged/magmoo__<skin>.blend)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)

Earlier rulings kept (review-log 2026-09-26 v2/v3): the sketch identity (teardrop head with flame licks, curved bean
mid with drips, splash mound with a jagged crown and finger splats, a long tail with a finger-splat tip, droplets),
translucent lava, the goo ball with only the eyes and ears showing.

Pipeline:
  1. HEAD = the artist's sculpt (newunit-magmoo.blend 'Icosphere') mapped to game axes; v4 sockets carved into its
     remeshed surface (finish hook), kept dense through decimation.
  2. MID + TAIL + DROPLETS + BALL modelled as SDFs (magmoo_sdf.py), marching tetrahedra; bridges + shed drips too.
  3. ONE FINISH for all: voxel remesh (VOXEL) -> collapse decimation to the same triangle density per area.
  4. assembly (the COMBINED serpent: pieces plugged goo-into-goo, v3 geometry) -> v4 REST layout: a rigid transform
     per piece (combined -> rest), gaps measured by BVH surface distance and solved by bisection.
  5. regions (iso-contour cuts) per piece; one merged skinned mesh (glTF: one primitive, one morph-target set).
  6. shape keys: flight (mound -> smooth segment), sq_head / sq_mid / sq_tail (squash; negative = stretch),
     bulge_crown / bulge_head (torn ends bulge on contact).
  7. rig: root + FLAT deform bones (every bone parented to root: bone stretch / thinning never shears a child):
     head.0-1 | body.0-4 | tail.0-10 | drop.0-4 | ball | bridge.{crown,head}.{a,b} | shed.0-2 | splat.
  8. clips (procedural): idle / walk / ball; the Key's shape-key channels ride each clip's action (KEY slot).
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast
import numpy as np
from mathutils import Matrix, Vector, Quaternion
from mathutils.bvhtree import BVHTree

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K        # noqa: E402  (read-only use)
import palettes as PAL    # noqa: E402  (read-only use)
import magmoo_sdf as SD   # noqa: E402

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; lengths in source units = the head sculpt's own units, natural scale)
UNIT = "magmoo"
TRI_BUDGET = [30000, 50000]           # declared hero-tier window (review-log: "30-50k hero tier stands")
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest cell ceilings -- REPORT ONLY (scale policy 2026-09-25)
VOXEL = 0.03                          # "surface finish": the ONE voxel remesh size every piece goes through
SDF_STEP = 0.03                       # SDF sampling step for the modelled pieces
TRI_DENSITY = 305.0                   # "mesh detail": triangles per square unit (v4 330 -> 305: room for bridges + shed)
# ---- head = the artist's sculpt (head-local = the source game-framed: front -Y, floor 0, source units)
HEAD_SCALE = 1.0                      # "head size"
HEAD_SOCKET = (0.0, 0.25, 1.05)       # where the mid plugs into the back of the head (head-local)
HEAD_MIDPT = (0.0, -1.30, 0.85)       # head bone joint (head-local)
HEAD_NOSE_Z = 0.75                    # snout axis height (head-local)
HEAD_PITCH_EXTRA_DEG = -8.0           # "head droop" in the combined serpent (flight): nose-down beyond the mid tangent
# v4 eyes = carved empty sockets, dragon placement (high on the head sides, back toward the dorsal flames)
EYE_Y = -1.25                         # "eye position" along the head (head-local y; v3 -2.0; the rounded front is -3.2)
EYE_UP_DEG = 36.0                     # "eye height": each eye's axis, degrees from straight up toward its side
EYE_OPEN_R = 0.24                     # "eye size": socket opening radius on the goo surface
EYE_DEPTH = 0.20                      # "eye depth": how far the socket floor sinks below the goo surface
EYE_RIM_SOFT = 3                      # "rim softness": smoothing passes on the socket lip (clamped: may only sink)
EYE_SHADE_T = 0.10                    # the darker socket colour starts this far below the goo surface (deepened with the
                                      #   v5.1 eye-fit fix: at 0.03 the socket lip painted dark and ringed the seated orb)
EYE_PROTECT_R = 0.28                  # decimation keeps remesh density within this radius of each eye (crisp sockets)
# v5 red goo eye: one solid lens per socket (the carved dish below, a shallow dome on top), no pupil
EYE_ORB_BULGE = 0.012                 # "eye bulge": the goo eye's dome rises this far above the goo surface at its centre
                                      #   (artist 2026-09-26: "the eyes don't fit" - lowered from 0.035 so the orb seats
                                      #   into the socket instead of standing proud)
EYE_ORB_INSET = 0.006                 # the eye's back sits this far inside the carved dish (clear of the socket surface)
EYE_ORB_FIT = "rim"                   # v5.2 "eye seating": 'rim' = the eye's top is the pre-socket goo surface itself (+ a
                                      #   bulge fading to 0 at the traced rim): flush with the lip all round; 'axis' = the
                                      #   v5 sphere dome on the cutter axis at the mean rim height -- the real opening is
                                      #   tilted ~24 deg from that axis on the sloped head side, so the dish showed above
                                      #   the orb (artist: "titled wrong"); kept only for the before numbers
EYE_CORE_FRAC = 0.45                  # "eye core size" (artist 2026-09-26: "needs something inside to show it reads more
                                      #   as an eye then just a red circle"): the inner molten core disc, as a fraction
                                      #   of the socket opening radius; its own hotter region 'eye_core'
EYE_ORB_DENSITY = 1100.0              # "eye detail": triangles per square unit of the head's goo eyes
BALL_EYE_ORB_DENSITY = 420.0          # ... of the ball's goo eyes (the ball is a coarser mesh)
# ---- mid piece (the curved bean), knots rear (plugged into the crown) -> front (plugged into the head)
MID_PTS = [(0, 0.0, 1.50), (0, -0.20, 2.55), (0, -0.85, 3.35), (0, -1.80, 3.75), (0, -2.75, 3.62), (0, -3.35, 3.30)]
MID_R = [0.50, 0.58, 0.65, 0.67, 0.61, 0.52]                  # "bean thickness" at those knots
MID_PLUG_HEAD = 0.35                  # the mid's front end stops this short of the head socket (its round cap reaches in)
MID_TUFTS = (3, 0.30, 0.34, 0.11)     # "torn-end tufts": per end (count, ring radius, length, root radius)
MID_DRIPS = [(0.42, 0.44, 0.14), (0.60, 0.32, 0.12), (0.78, 0.24, 0.10)]   # "drips": (arc fraction, hang length, bulb r)
# ---- tail piece: splash mound + crown + finger splats + the long tail (+ its tip splat)
MOUND_BASE = ((0.0, 0.10, 0.20), (1.35, 1.40, 0.95))          # "mound size": base ellipsoid (centre, radii)
MOUND_CONE_R = (1.08, 0.64)           # volcano cone radius, base -> crown
CROWN_Z = 1.78                        # "crown height"
CROWN_LICKS = 7                       # "crown points" count (the sketch's zigzag rim)
CROWN_LICK = (0.50, 0.13, 0.62)       # crown point (length, root radius, rim radius)
CRATER_R = 0.44                       # the bowl the mid plugs into
MOUND_FINGERS = [(58, 1.30, 0.33), (-58, 1.30, 0.33), (108, 1.50, 0.31), (-108, 1.50, 0.31),
                 (152, 1.05, 0.27), (-152, 1.05, 0.27)]        # "finger splats": (azimuth deg from +Y back, length, r)
FINGER_TIP_R = 0.14                   # finger ends stay round (drip bulbs), not pointed
TAIL_LEN = 6.6                        # "tail length" (spine arc length from inside the mound)
TAIL_START_Y = 0.55                   # tail spine start (inside the mound)
TAIL_R0, TAIL_RTIP = 0.56, 0.17       # "tail thickness" at the base / at the tip
TAIL_TAPER = 0.9                      # taper curve exponent (1 = linear)
TAIL_FLAT = 0.78                      # tail cross-section height / width (goo slumped on the floor)
TAIL_BEND_DEG = 14.0                  # "rest S-curve": lateral heading swing of the resting tail
TAIL_CURL_DEG = 105.0                 # v4 "rest curl": the resting tail curls round to one side (shorter footprint)
TAIL_STRAIGHT = 1.8                   # the first stretch of tail stays straight (the flight segment continues it)
TAIL_TIP_FINGERS = [(-34, 0.60), (0, 0.72), (34, 0.60)]       # "tail-tip splat": (heading deg off the tail, length)
# ---- droplets v5: beside the pieces (never in a gap): (radius, piece, side, fraction along it, hang)
#      hang None = on the floor; a number = HANGING in the air beside the piece at that fraction of its height band
DROPLETS = [(0.21, "tail", 1.0, 0.30, None), (0.17, "tail", -1.0, 0.55, None),
            (0.16, "mid", 1.0, 0.30, 0.18), (0.13, "mid", -1.0, 0.52, 0.10), (0.12, "mid", 1.0, 0.80, 0.30)]
DROP_TIP = 1.4                        # "droplet point": teardrop tip length / droplet radius (tips point up)
DROP_CLEAR = 0.22                     # rest droplets sit this far off the piece's flank
# ---- v5 REST layout (the bind pose): paused in motion -- mound + tail grounded, arch + head in the air, visibly apart
REST_GAP_HEAD = 1.10                  # "visible gap" head <-> mid: minimum surface distance at rest (units)
REST_GAP_CROWN = 1.10                 # "visible gap" mid <-> mound: minimum surface distance at rest (units)
REST_MID_FWD = 0.70                   # "arch lean": the arch lifts off the crown along its join axis blended this much forward
                                      # (0.35 -> arch top 6.6, head underside 2.0: too tall vs the reference; 0.85 -> head
                                      # nearly on the floor 0.21; 0.70 -> arch top 5.35, head in the air ~1.1)
REST_HEAD_FWD = 0.0                   # "head throw": the head leaves the arch along its front tangent blended this much level
REST_HEAD_PITCH_DEG = -8.0            # "head look": extra nose-down (+) of the head at rest beyond its flight attitude
                                      # (-8: 22 -> 14 deg nose-down, "looking slightly down / forward")
REST_BOB = 0.05                       # "hover bob": the airborne pieces + hanging droplets bob this much while resting
# ---- v4 flight segment (the mound -> smooth body segment shape key)
CAP_FRONT_AHEAD = 0.95                # the smooth segment's front tip sits this far ahead of the tail root (units)
CAP_RELAX_ITERS = 900                 # relaxation passes of the mound -> segment map (fold-free projection)
CAP_OVERLAP = 0.50                    # in the air the mid's rear end plugs this deep into the segment's front
# ---- v4 goo shape keys
SQUASH_AMT = 0.36                     # "squash": sq_* at weight 1 lowers a piece's height by this fraction (volume kept)
BULGE_AMT = 0.16                      # "contact bulge": torn-end surfaces swell this far along their normals at weight 1
BULGE_R = 0.95                        # ... over this radius round the facing points
# ---- v4 goo bridges (per join two tapered half-strands) + shedding
BR_ROOT_R = 0.24                      # "bridge thickness" at the root (on the piece surface)
BR_TIP_R = 0.05                       # ... at the tip (the neck in the middle of the strand)
BR_REACH_GAP = 0.85                   # "reach": approaching pieces throw strands out once their surfaces are this close
BR_SNAP_GAP = 0.90                    # "snap": a stretching strand snaps once the surfaces are this far apart (< the rest gap)
BR_RECOIL_FRAMES = 7                  # snapped halves recoil into their piece over this many frames (spring)
BR_NECK = 0.55                        # thinning exponent: thickness ~ (rest length / length) ** BR_NECK
HIDE_SCALE = 0.01                     # hidden parts (ball, bridges, shed) ride at this scale inside their host piece
SHED_DRIP_R = 0.13                    # "shed drip size"
SPLAT_R = (0.80, 0.07)                # "launch splat": puddle radius, thickness
# ---- rig
MID_BONES, TAIL_BONES = 5, 10         # tail bones along the long tail (+ tail.0 = the mound)
TAIL_ROOT_BLEND = (0.0, 0.60)         # the mound rides tail.0 alone; the mound -> tail blend runs over this arc span
STRETCH_CLAMP = (0.55, 1.8)           # bone stretch limits (thinning = 1 / sqrt(stretch): volume kept)
# ---- clips
BREATH = 0.07                         # "resting wobble": squash keys breathe this much while the pieces rest
IDLE_FRAMES = 192                     # idle loop (24 fps -> 8 s): rest -> ball (+ bounces) -> rest -> S-rise -> rest
IDLE_T = {"ball": (0.05, 0.17, 0.33, 0.45), "rise": (0.50, 0.60, 0.80, 0.90)}   # (start, formed, release, done)
IDLE_HOVER = 2.3                      # "hover height" of the S-rise (body centreline, units)
IDLE_WAVE = (0.85, 7.5, 2.0)          # vertical-S wave: (amplitude units, wavelength units, waves travelled in the hold)
IDLE_RISE_LAG = 0.0045                # cycle fraction per unit of body: the pieces rise/settle one after another
WALK_FRAMES = 64                      # leap cycle (24 fps -> 2.67 s; v4 56 -> room for the floatier hang)
WALK_GATHER = {"head": (0.03, 0.17), "mid": (0.02, 0.15), "tail": (0.05, 0.19)}   # v5: from the paused rest the arch
                                      # + head drop back into the one serpent while the mound + tail chase forward under
                                      # them (start, arrive): the combined serpent, goo-into-goo, before the crouch
WALK_CROUCH = (0.10, 0.215)           # anticipation squash (start, peak = just before the launch)
WALK_SQUASH = 0.85                    # anticipation squash key weight at the peak
WALK_LAUNCH = 0.225                   # the head launches (cycle fraction)
WALK_LAG = 0.0055                     # cycle fraction per unit of body: each body point launches this much later
WALK_AIR = 0.36                       # "hang time": air time of each body point (cycle fraction; v4 0.30 of 56 frames)
WALK_FLOAT = 3.0                      # "floatiness": hop profile 1 - |2 tau - 1|^this (2 = the v4 parabola; higher =
                                      # flatter apex dwell, snappier launch + landing)
WALK_MOVE_END = 0.78                  # the last body point stops moving here
WALK_HOP = 3.8                        # "leap height": apex of each body point's hop (units)
WALK_DLS = 8.0                        # "leap length": how far every body point travels in the leap (units)
WALK_WAVE = (1.35, 12.0, 0.9)          # vertical-S undulation in the air: (amplitude, wavelength, waves travelled)
WALK_LAND = 0.80                      # landing splat squash key weight
WALK_SEP = {"head": (0.64, 0.82), "mid": (0.68, 0.87), "tail": (0.72, 0.93)}   # run apart into segments (start, end)
WALK_SEP_HEAD = 1.4                   # the head runs this much further ahead than the leap (the gaps open both ways)
WALK_SPRING = (2.1, 0.42)             # "bounce" of every arrival: spring (Hz, damping ratio 0.42 -> 23 % overshoot)
BALL_FRAMES = 132                     # ball clip (24 fps -> 5.5 s round trip)
BALL_TIMING = (0.05, 0.27, 0.73, 0.95)  # converge start, ball formed, release start, back to segments
BALL_SIZE = 1.0                       # "ball size": x the radius holding the pieces' total goo volume
BALL_SLUMP = 0.10                     # goo sag: the ball sits this x its radius into the floor (flat contact)
BALL_EYE_SCALE = 1.25                 # ball eye sockets vs the head's
BALL_EYE_EL_DEG = 22.0                # eye height on the ball front (deg above the ball centre; v4 higher, dragon-like)
BALL_EAR = (0.95, 0.30, 30.0, 56.0, 36.0)  # "ears": flame lick (length, root radius, azimuth off the front,
                                      #  elevation deg, lean back deg) -- the head's dorsal flame licks
BALL_TRI_DENSITY = 60.0               # ball mesh detail (triangles per square unit)
BALL_ABSORB = 0.01                    # absorbed pieces shrink to this scale at the ball centre
BALL_CURL_DEG = 200.0                 # the tail curls (tip heading) as it is sucked in
BALL_SWIRL_DEG = 70.0                 # ... and the whole bottom piece swirls round the ball axis
BALL_BOUNCES = 2                      # "ball bounce": small hops during the hold
BALL_BOUNCE_H = 0.60                  # hop height (units)
BALL_BOUNCE_SQ = 0.17                 # squash at take-off / landing (vertical scale 1 - this)
BALL_ENV = {"drops": (0.00, 0.55), "mid": (0.05, 0.75), "tail": (0.12, 1.00), "head_move": (0.00, 0.55),
            "head_absorb": (0.50, 1.00), "grow": (0.06, 1.00)}   # per-piece windows inside the converge (fractions);
                                      # the release mirrors them (last in, first out)
GLOW_PULSE = {"idle": 0.30, "walk": 0.22, "ball": 0.30}

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
STOP = argv[argv.index("--stop") + 1] if "--stop" in argv else None
OUTROOT = argv[argv.index("--outroot") + 1] if "--outroot" in argv else ROOT
SKINS = argv[argv.index("--skins") + 1].split(",") if "--skins" in argv else ["default"]
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
OUT_IMPROVED = os.path.join(OUTROOT, "improved", UNIT + ".blend")
OUT_RIGGED = os.path.join(OUTROOT, "rigged", UNIT + ".blend")
OUT_GLB = os.path.join(OUTROOT, "rigged", UNIT + ".glb")
for d_ in (os.path.dirname(OUT_IMPROVED), os.path.dirname(OUT_RIGGED)):
    os.makedirs(d_, exist_ok=True)
PIECES = ["head", "mid", "tail"]
report = {"unit": UNIT, "version": "v5 living goo (paused rest, floaty leap, red goo eyes, red body)", "source": bpy.data.filepath, "tier": "hero", "tri_budget": TRI_BUDGET,
          "yaw_fix_deg": 0.0, "overrides": OVERRIDES, "pieces": {}}
scene = bpy.context.scene
TAU = 2 * math.pi
UP = np.array([0.0, 0.0, 1.0])
X_AX = np.array([1.0, 0.0, 0.0])


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def sstep(e0, e1, x):
    """scalar smootherstep (C2 at both ends)."""
    u = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return u * u * u * (u * (u * 6.0 - 15.0) + 10.0)


def unit_rows(A):
    A = np.asarray(A, float)
    return A / np.maximum(np.linalg.norm(A, axis=1), 1e-12)[:, None]


def unit(v):
    v = np.asarray(v, float)
    return v / max(np.linalg.norm(v), 1e-12)


def mesh_arrays(me, M=None):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    if M is not None:
        M = np.array(M); co = co @ M[:3, :3].T + M[:3, 3]
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count(me):
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    return int((lt - 2).sum())


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V, float).tolist(), [], [list(map(int, f)) for f in F])
    me.update()
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    return ob


def evaluated_arrays(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me_l = bpy.data.meshes.new_from_object(ob.evaluated_get(dg))
    V, F = mesh_arrays(me_l)
    bpy.data.meshes.remove(me_l)
    return V, F


def islands(V, F):
    n = len(V)
    par = np.arange(n)

    def find(a):
        while par[a] != a:
            par[a] = par[par[a]]; a = par[a]
        return a
    for f in F:
        r0 = find(f[0])
        for v in f[1:]:
            r1 = find(v)
            if r1 != r0:
                par[r1] = r0
    return np.array([find(i) for i in range(n)])


def keep_main_island(V, F, min_frac):
    roots = islands(V, F)
    lab, inv, cnt = np.unique(roots, return_inverse=True, return_counts=True)
    keep_lab = cnt >= min_frac * len(V)
    keep_v = keep_lab[inv]
    remap = -np.ones(len(V), dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum())}


def area_of(V, F):
    V = np.asarray(V)
    a = 0.0
    for n in sorted({len(f) for f in F}):
        T = np.asarray([f for f in F if len(f) == n])
        for j in range(1, n - 1):
            a += 0.5 * np.linalg.norm(np.cross(V[T[:, j]] - V[T[:, 0]], V[T[:, j + 1]] - V[T[:, 0]]), axis=1).sum()
    return float(a)


def finish(name, V, F, voxel=None, density=None, min_frac=0.02, hook=None, protect=None):
    """THE shared finish: voxel remesh -> [hook: sculpt the remeshed surface] -> collapse decimation to density x area
    -> main shell. protect = [(centre, radius)]: vertices inside keep the remesh density (Decimate vertex group,
    inverted); the target grows by the protected triangles so the rest of the piece keeps the shared density."""
    voxel = voxel or VOXEL
    density = density or TRI_DENSITY
    t = time.time()
    tmp = new_obj(name + "_rm", V, F)
    rm = tmp.modifiers.new("vox", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = voxel; rm.use_smooth_shade = False
    RV, RF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    RV, RF, specks = keep_main_island(RV, RF, min_frac)
    hook_rep = None
    if hook is not None:
        RV, hook_rep = hook(np.asarray(RV), RF)
    area = area_of(RV, RF)
    rtris = sum(len(f) - 2 for f in RF)
    protect = protect() if callable(protect) else (protect or [])
    prot_v = np.zeros(len(RV), bool)
    for c_, r_ in protect:
        prot_v |= np.linalg.norm(np.asarray(RV) - np.asarray(c_), axis=1) < r_
    prot_f = [f for f in RF if prot_v[f].all()]
    prot_area = area_of(RV, prot_f) if prot_f else 0.0
    n_prot = sum(len(f) - 2 for f in prot_f)
    target = int(round((area - prot_area) * density)) + n_prot
    tmp = new_obj(name + "_dec", RV, RF)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / rtris)
    if n_prot:
        vg = tmp.vertex_groups.new(name="protect")
        vg.add([int(i) for i in np.nonzero(prot_v)[0]], 1.0, "REPLACE")
        dm.vertex_group = "protect"; dm.invert_vertex_group = True; dm.vertex_group_factor = 1.0
    LV, LF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    LV, LF, specks2 = keep_main_island(LV, LF, min_frac)
    out = {"voxel": voxel, "remesh_tris": rtris, "area": round(area, 4), "target_tris": target,
           "tris": sum(len(f) - 2 for f in LF), "specks": [specks, specks2], "seconds": round(time.time() - t, 1)}
    if protect:
        out["protected"] = {"zones": len(protect), "remesh_tris_kept": n_prot, "area": round(prot_area, 4)}
    if hook_rep is not None:
        out["sculpt"] = hook_rep
    return np.asarray(LV), [list(f) for f in LF], out


def mt_audit(V, T, G_):
    e = np.sort(np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), 1)
    _, c = np.unique(e, axis=0, return_counts=True)
    out = {"mt_verts": len(V), "mt_tris": len(T), "open_edges": int((c == 1).sum()), "nonmanifold_edges": int((c > 2).sum()),
           "grid": G_.n.tolist(), "nan": int(np.isnan(G_.F).sum())}
    assert out["open_edges"] == 0 and out["nan"] == 0, out
    return out


def lick_cones(root, ctrl, tip, r0, n=10, rtip=0.014, sharp=1.5):
    pts = SD.bezier(root, ctrl, tip, n)
    u = np.linspace(0.0, 1.0, n)
    rr = rtip + (r0 - rtip) * (1 - u) ** sharp
    return [(pts[i], pts[i + 1], rr[i], rr[i + 1]) for i in range(n - 1)]


def add_cones(G, cones, k, fmap=None):
    for a, b, r1, r2 in cones:
        lo_ = np.minimum(a, b) - max(r1, r2); hi_ = np.maximum(a, b) + max(r1, r2)
        if fmap is None:
            G.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(P, a, b, r1, r2), lo_, hi_, k)
        else:
            G.apply(lambda P, a=a, b=b, r1=r1, r2=r2: SD.sd_round_cone(fmap(P), a, b, r1, r2), fmap(lo_[None], inv=True)[0],
                    fmap(hi_[None], inv=True)[0], k)


def bvh_of(V, F):
    return BVHTree.FromPolygons(np.asarray(V).tolist(), [list(map(int, f)) for f in F])


def ray_hit(bvh, origin, direction):
    h = bvh.ray_cast(Vector(origin), Vector(direction))[0]
    return None if h is None else np.array(h)


def vertex_normals(V, F):
    V = np.asarray(V); T = np.asarray([f for f in F if len(f) == 3])
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    N = np.zeros_like(V)
    for k in range(3):
        np.add.at(N, T[:, k], n)
    return N / np.maximum(np.linalg.norm(N, axis=1), 1e-12)[:, None]


def edges_of(F):
    E = set()
    for f in F:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            E.add((min(a, b), max(a, b)))
    return np.array(sorted(E))


def smooth_copy(V, E, iters, lam=0.5):
    n = len(V)
    deg = np.bincount(E.ravel(), minlength=n).astype(float)
    X = np.array(V, float, copy=True)
    one = X.ndim == 1
    if one:
        X = X[:, None]
    for _ in range(iters):
        S = np.zeros_like(X)
        for k in range(X.shape[1]):
            S[:, k] = np.bincount(E[:, 0], X[E[:, 1], k], minlength=n) + np.bincount(E[:, 1], X[E[:, 0], k], minlength=n)
        X = X + lam * (S / np.maximum(deg, 1)[:, None] - X)
    return X[:, 0] if one else X


def signed_dist(bvh, P):
    """signed distance of points to a closed outward-wound surface (+ outside / above the goo, - sunk into it)."""
    out = np.empty(len(P)); loc_ = np.empty((len(P), 3))
    for i, p in enumerate(np.asarray(P)):
        loc, nrm, _, d = bvh.find_nearest(Vector(p))
        out[i] = d if (Vector(p) - loc).dot(nrm) >= 0 else -d
        loc_[i] = loc
    return out, loc_


def surface_gap(VA, bB, step=3):
    """min distance from (every step-th vertex of) A to surface B, + the closest pair; 0 when they touch or overlap."""
    best, pa, pb = 1e9, None, None
    for p in np.asarray(VA)[::step]:
        loc, _, _, d = bB.find_nearest(Vector(p))
        if d < best:
            best, pa, pb = d, p, np.array(loc)
    return float(best), pa, pb


# ---- v4 eye sockets: a spherical dish carved into the goo (no pupil pit, no eyeball)
def socket_geom(p, dvec, a, D):
    """p = the eye point on the goo surface, dvec = outward socket axis. Cutter sphere through the rim circle
    (radius a) with its lowest point D below p: Rs = (a^2 + D^2) / 2D."""
    dvec = unit(dvec)
    Rs = (a * a + D * D) / (2.0 * D)
    cs = p + dvec * (Rs - D)
    return {"p": np.asarray(p, float), "n": dvec, "a": a, "D": D, "Rs": Rs, "cs": cs}


def carve_sockets(RV, RF, socks):
    """project every remeshed vertex inside a cutter sphere onto it: the surface only ever moves INTO the goo. Then
    EYE_RIM_SOFT smoothing passes on the lip ring, clamped back onto the original surface wherever a pass would lift a
    vertex above it."""
    V = np.array(RV, float, copy=True)
    rep = []
    for S in socks:
        d = V - S["cs"]
        r = np.linalg.norm(d, axis=1)
        m = r < S["Rs"]
        V[m] = S["cs"] + d[m] / r[m, None] * S["Rs"]
        rep.append({"dish_verts": int(m.sum())})
    E = edges_of(RF)
    ring = np.zeros(len(V), bool)
    for S in socks:
        d0 = np.asarray(RV) - S["p"]
        h = d0 @ S["n"]
        rho = np.linalg.norm(d0 - np.outer(h, S["n"]), axis=1)
        ring |= (np.abs(rho - S["a"]) < 0.07) & (np.abs(h) < 0.10)
    bvh0 = bvh_of(RV, RF)
    lifted = 0
    for _ in range(EYE_RIM_SOFT):
        Vs = smooth_copy(V, E, 1)
        V[ring] = Vs[ring]
        sd, loc = signed_dist(bvh0, V[ring])
        up = sd > 0
        idx = np.nonzero(ring)[0][up]
        V[idx] = loc[up]
        lifted += int(up.sum())
    return V, {"sockets": rep, "rim_ring_verts": int(ring.sum()), "rim_passes": EYE_RIM_SOFT,
               "rim_lift_clamps": lifted}


def rigid(R=None, t=None):
    M = np.eye(4)
    if R is not None:
        M[:3, :3] = R
    if t is not None:
        M[:3, 3] = t
    return M


def apply_m(M, P):
    P = np.asarray(P, float)
    return P @ M[:3, :3].T + M[:3, 3]


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0, 0], [0, c, -s], [0, s, c]])


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


# =========================================================================== 1. HEAD = the artist's sculpt
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
src = bpy.data.objects["Icosphere"]
SV_local, SF = mesh_arrays(src.data)
report["source_mesh"] = {"object": src.name, "verts": len(SV_local), "tris": tri_count(src.data),
                         "object_rotation_deg": [round(math.degrees(a), 3) for a in src.rotation_euler],
                         "role": "the HEAD (the sketch's rounded teardrop with flame licks trailing off the back)",
                         "colour_note": "no material on the mesh: goo colours are authored in palettes/magmoo"}
# sculpt frame -> game frame: local (x, y, z) -> (-x, -z, -y): rounded end (+Z) to the front (-Y), dorsal flames up
MAP = np.array([[-1.0, 0, 0], [0, 0, -1.0], [0, -1.0, 0]])
assert abs(np.linalg.det(MAP) - 1.0) < 1e-9
HV0 = SV_local @ MAP.T
lo, hi = HV0.min(0), HV0.max(0)
HV0 = (HV0 - np.array([(lo[0] + hi[0]) / 2, 0.0, lo[2]])) * HEAD_SCALE
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)
hs = HEAD_SCALE
H_SOCKET = np.array(HEAD_SOCKET) * hs
H_MID = np.array(HEAD_MIDPT) * hs
H_EYES, H_EYE_N, SOCKETS, HEAD_ORIG = [], [], [], {}


def head_sculpt(RV, RF):
    """v4: find the eyes on the remeshed head (rays from the snout axis at EYE_Y, EYE_UP_DEG off straight up), carve
    the empty sockets, keep the pre-socket surface for the inset proof + the region field."""
    HEAD_ORIG["V"], HEAD_ORIG["F"] = np.array(RV, float, copy=True), [list(f) for f in RF]
    b0 = bvh_of(RV, RF)
    for sgn in (1.0, -1.0):
        a = math.radians(EYE_UP_DEG)
        dvec = np.array([sgn * math.sin(a), 0.0, math.cos(a)])
        core = np.array([0.0, EYE_Y * hs, HEAD_NOSE_Z * hs])
        p = ray_hit(b0, core + dvec * 20.0, -dvec)
        assert p is not None, "eye ray missed the head"
        H_EYES.append(p); H_EYE_N.append(dvec)
        SOCKETS.append(socket_geom(p, dvec, EYE_OPEN_R * hs, EYE_DEPTH * hs))
    return carve_sockets(RV, RF, SOCKETS)


hV, hF, hfin = finish("head", HV0, SF, hook=head_sculpt,
                      protect=lambda: [(S["p"], EYE_PROTECT_R * hs) for S in SOCKETS])   # zones known after the hook
report["pieces"]["head"] = {"finish": hfin, "source_tris": report["source_mesh"]["tris"],
                            "head_local_bbox": [hV.min(0).round(4).tolist(), hV.max(0).round(4).tolist()]}
HEAD_LOCAL_V = hV.copy()                                   # head-local (sculpt frame, floor 0): the REST head frame
hb = bvh_of(hV, hF)
H_SNOUT = ray_hit(hb, (0.0, -50.0, HEAD_NOSE_Z * hs), (0, 1.0, 0))
assert H_SNOUT is not None
print("HEAD", json.dumps({**hfin, "snout": H_SNOUT.round(4).tolist(), "eyes": [e.round(4).tolist() for e in H_EYES]}))
# eye inset proof (head-local): signed height of the finished head's vertices vs the PRE-socket goo surface
H_SINK, _ = signed_dist(bvh_of(HEAD_ORIG["V"], HEAD_ORIG["F"]), hV)
_zone = np.zeros(len(hV), bool)
for S in SOCKETS:
    _zone |= np.linalg.norm(hV - S["p"], axis=1) < EYE_OPEN_R * hs * 1.6
_socket_v = _zone & (H_SINK < -EYE_SHADE_T)
report["eye_inset_proof"] = {
    "rule": "signed height of the finished head's vertices above the pre-socket (remeshed) goo surface: + = sticks "
            "out, - = sunk in. v4 sockets are empty carved dishes: no eyeball, no pupil pit.",
    "eye_zone_radius": round(EYE_OPEN_R * hs * 1.6, 4), "eye_zone_verts": int(_zone.sum()),
    "eye_zone_max_height": round(float(H_SINK[_zone].max()), 5),
    "socket_verts": int(_socket_v.sum()),
    "eye_zone_deepest": round(float(H_SINK[_zone].min()), 5),
    "rest_of_head_decimation_noise": {"max_height": round(float(H_SINK[~_zone].max()), 5),
                                      "p99_abs": round(float(np.percentile(np.abs(H_SINK[~_zone]), 99)), 5)},
    "placement": {"eye_y_head_local": EYE_Y, "eye_up_deg": EYE_UP_DEG, "v3_was": {"eye_y": -2.0, "eye_up_deg": 40.0}},
    "sockets": [{"eye_point_head_local": S["p"].round(4).tolist(), "axis": S["n"].round(4).tolist(), "opening_r": S["a"],
                 "depth": S["D"], "cutter_r": round(S["Rs"], 4)} for S in SOCKETS]}
print("EYE_INSET", json.dumps(report["eye_inset_proof"]))


# ---- v5 red goo eyes: one solid lens per carved socket (no pupil, no white)
def eye_orb(S, sdf_fn, density, scale=1.0):
    """the goo eye filling socket S: intersection of the dish sphere (shrunk EYE_ORB_INSET: the eye's back hugs the
    carved dish) and a shallow dome through the socket rim whose top stands EYE_ORB_BULGE proud of the goo surface.
    sdf_fn(P) = signed distance to the PRE-socket goo surface (+ outside): gives the rim height (where the dish meets
    the goo, measured round the opening). -> (V, F, finish, geometry report)"""
    n_ = S["n"]; p_ = S["p"]; a_ = S["a"]
    e1 = unit(np.cross(n_, X_AX) if abs(n_[0]) < 0.9 else np.cross(n_, UP)); e2 = np.cross(n_, e1)
    h_rim = []
    for k in range(12):
        q = p_ + (math.cos(TAU * k / 12) * e1 + math.sin(TAU * k / 12) * e2) * a_
        lo_h, hi_h = -0.4 * scale, 0.4 * scale                     # bisection along the socket axis onto the surface
        for _ in range(30):
            mh = 0.5 * (lo_h + hi_h)
            if float(sdf_fn((q + n_ * mh)[None])[0]) > 0:
                hi_h = mh
            else:
                lo_h = mh
        h_rim.append(0.5 * (lo_h + hi_h))
    h_rim_spread = float(np.ptp(h_rim))
    h_rim = float(np.mean(h_rim))
    Ri = S["Rs"] - EYE_ORB_INSET * scale
    # the socket's real OPENING: trace, per azimuth, where the cutter sphere (the carved dish) leaves the pre-socket goo
    # -- walking the dish's meridian from its bottom (-n, inside the goo) up to its top (+n, outside)
    RIM = []
    for k in range(24):
        e = math.cos(TAU * k / 24) * e1 + math.sin(TAU * k / 24) * e2
        lo_t, hi_t = 0.0, math.pi
        for _ in range(40):
            th = 0.5 * (lo_t + hi_t)
            if float(sdf_fn((S["cs"] + S["Rs"] * (-math.cos(th) * n_ + math.sin(th) * e))[None])[0]) > 0:
                hi_t = th
            else:
                lo_t = th
        th = 0.5 * (lo_t + hi_t)
        RIM.append(S["cs"] + S["Rs"] * (-math.cos(th) * n_ + math.sin(th) * e))
    RIM = np.array(RIM)
    c_r = RIM.mean(0)
    n_r = np.linalg.svd(RIM - c_r)[2][2]
    n_r = n_r if n_r @ n_ > 0 else -n_r
    a_r = float(np.linalg.norm((RIM - c_r) - np.outer((RIM - c_r) @ n_r, n_r), axis=1).mean())
    plane_res = (RIM - c_r) @ n_r
    lo_s, hi_s = -0.4 * scale, 0.4 * scale                          # the goo surface above the opening's centre
    for _ in range(40):
        ms = 0.5 * (lo_s + hi_s)
        if float(sdf_fn((c_r + n_r * ms)[None])[0]) > 0:
            hi_s = ms
        else:
            lo_s = ms
    s_surf = 0.5 * (lo_s + hi_s)
    if EYE_ORB_FIT == "rim":
        # the opening is a SADDLE (the rim leaves the plane by up to ~0.07 on the curved head side): no sphere cap fits
        # it. The eye's top IS the pre-socket goo surface lifted by a bulge that is EYE_ORB_BULGE over the opening's
        # centre and 0 at the rim -> flush with the lip all the way round, following the head's own curvature
        def top_sdf(P):
            d = P - c_r
            rho = np.linalg.norm(d - np.outer(d @ n_r, n_r), axis=1)
            return sdf_fn(P) - EYE_ORB_BULGE * scale * np.clip(1.0 - (rho / a_r) ** 2, 0.0, 1.0)
        Rd = None
        ax, top_pt = n_r, c_r + n_r * (s_surf + EYE_ORB_BULGE * scale)
    else:
        h_top = EYE_ORB_BULGE * scale
        dh = h_top - h_rim
        Rd = (a_ * a_ + dh * dh) / (2.0 * dh)
        dome_c = p_ + n_ * (h_top - Rd)
        top_sdf = lambda P: SD.sd_sphere(P, dome_c, Rd)
        ax, top_pt = n_, p_ + n_ * h_top
    lo_ = S["cs"] - Ri - 0.04 * scale; hi_ = S["cs"] + Ri + 0.04 * scale
    G_ = SD.Grid(lo_, hi_, 0.007 * scale, band=0.03 * scale)
    G_.apply(lambda P: np.maximum(SD.sd_sphere(P, S["cs"], Ri), top_sdf(P)), lo_, hi_, 0.0)
    V_, T_ = SD.polygonise(G_)
    V2, F2, fin = finish("eye_orb", V_, T_, voxel=0.009 * scale, density=density)
    hgt = (V2 - p_) @ n_
    bo = bvh_of(V2, F2)
    rim_gap = [float(bo.find_nearest(Vector(q))[3]) for q in RIM]
    fit = {"mode": EYE_ORB_FIT,
           "opening_tilt_vs_cutter_axis_deg": round(math.degrees(math.acos(min(1.0, float(n_r @ n_)))), 3),
           "dome_axis_vs_opening_deg": round(math.degrees(math.acos(min(1.0, float(ax @ n_r)))), 3),
           "opening_centre_offset_from_cutter_axis": round(float(np.linalg.norm((c_r - p_) - ((c_r - p_) @ n_) * n_)), 5),
           "rim_height_spread_along_cutter_axis": round(h_rim_spread, 5), "opening_radius": round(a_r, 5),
           "rim_plane_residual_max": round(float(np.abs(plane_res).max()), 5),
           "rim_to_orb_gap": {"max": round(max(rim_gap), 5), "mean": round(float(np.mean(rim_gap)), 5)},
           "dome_top_above_goo": round(float((top_pt - (c_r + n_r * s_surf)) @ n_r), 5)}
    return V2, F2, fin, {"rim_height": round(h_rim, 5), "dome_radius": None if Rd is None else round(Rd, 4),
                         "back_radius": round(Ri, 4), "thickness": round(float(np.ptp(hgt)), 4),
                         "max_above_goo_surface": round(float(hgt.max()), 5), "tris": fin["tris"], "fit": fit,
                         "_rim": RIM, "_n_r": n_r}


def dish_exposure(S, orb, headV, sink, scale=1.0, pre_bvh=None):
    """how much carved dish shows round the goo eye: every dish vertex of the finished head (sunk > 0.01 below the
    pre-socket goo, inside 1.3 x the opening) casts a ray OUT of the socket; exposed = the ray escapes without hitting
    the eye. Directions: the opening normal (straight into the socket), the cutter axis, and the two views the renders
    use (the head front-on, 30 deg up; the socket seen from 40 deg above its opening)."""
    bo = bvh_of(orb[0], orb[1])
    bh = bvh_of(headV, hF)
    near = np.linalg.norm(headV - S["p"], axis=1) < S["a"] * 1.3
    n_r = orb[3]["_n_r"]
    dirs = {"opening_normal": n_r, "cutter_axis": S["n"], "front_30up": unit(np.array([0.0, -1.0, 0.577])),
            "above_opening_40deg": unit(n_r * math.cos(math.radians(40)) + UP * math.sin(math.radians(40)))}
    # VISIBLE carved area, ray-traced: a 64 x 64 grid of parallel view rays over the socket (1.4 x the opening) per
    # direction; each ray's first hit is the goo eye, carved goo (sunk below the pre-socket surface: > 0.01 = any carved
    # goo incl. the smoothed lip, > EYE_SHADE_T = the dark socket colour) or plain goo. Reported: carved hits / (carved +
    # eye hits) = the share of the eye's visible footprint where dish shows instead of eye.
    bpre = bvh_of(HEAD_ORIG["V"], HEAD_ORIG["F"]) if pre_bvh is None else pre_bvh
    out = {}
    for k, d in dirs.items():
        d = unit(d)
        e1 = unit(np.cross(d, X_AX) if abs(d[0]) < 0.9 else np.cross(d, UP)); e2 = np.cross(d, e1)
        g = np.linspace(-1.4, 1.4, 64) * S["a"]
        cnt = {"eye": 0, "lip": 0, "dark": 0}
        for u in g:
            for w in g:
                o_ = S["p"] + d * 1.5 + e1 * u + e2 * w
                ho = bo.ray_cast(Vector(o_), Vector(-d), 4.0)
                hh = bh.ray_cast(Vector(o_), Vector(-d), 4.0)
                if ho[0] is not None and (hh[0] is None or ho[3] <= hh[3] + 1e-6):
                    cnt["eye"] += 1
                elif hh[0] is not None:
                    loc, nrm, _, dd = bpre.find_nearest(hh[0])
                    sd_ = dd if (hh[0] - loc).dot(nrm) >= 0 else -dd
                    if sd_ < -0.01 * scale:
                        cnt["lip"] += 1
                        if sd_ < -EYE_SHADE_T * scale:
                            cnt["dark"] += 1
        out[k] = {"eye_px": cnt["eye"], "carved_px": cnt["lip"], "dark_socket_px": cnt["dark"],
                  "carved_share": round(cnt["lip"] / max(cnt["lip"] + cnt["eye"], 1), 4),
                  "dark_share": round(cnt["dark"] / max(cnt["dark"] + cnt["eye"], 1), 4)}
    return out


_bvh_pre = bvh_of(HEAD_ORIG["V"], HEAD_ORIG["F"])
EYE_ORBS = [eye_orb(S, lambda P: signed_dist(_bvh_pre, P)[0], EYE_ORB_DENSITY) for S in SOCKETS]   # head-local
_orb_sd = [signed_dist(bvh_of(hV, hF), o_[0])[0] for o_ in EYE_ORBS]      # vs the finished (carved) head surface
report["eye_goo"] = {
    "rule": "v5 (artist: 'put a red goo eye no pupil just one solid piece where its carved out'): one lens per socket = "
            "the carved dish sphere (shrunk EYE_ORB_INSET) intersected with a shallow dome through the socket rim; its "
            "own region 'eye_goo' (solid deep red, no glow, near-opaque); no pupil, no white. Heights along the socket "
            "axis from the pre-socket goo surface point.",
    "orbs": [{**{k: v for k, v in o_[3].items() if not k.startswith("_")}, "verts": len(o_[0]),
              "signed_dist_to_carved_head": {"min": round(float(sd_.min()), 5), "max": round(float(sd_.max()), 5),
                                             "frac_inside_goo": round(float((sd_ < 0).mean()), 4)},
              "dish_exposure": dish_exposure(S, o_, hV, H_SINK, pre_bvh=_bvh_pre)}
             for o_, sd_, S in zip(EYE_ORBS, _orb_sd, SOCKETS)],
    "bulge": EYE_ORB_BULGE, "inset": EYE_ORB_INSET, "fit": EYE_ORB_FIT}
print("EYE_GOO", json.dumps(report["eye_goo"]))

# =========================================================================== 2a. MID piece (SDF bean arch)
t_mid = time.time()
MP = np.array(MID_PTS, float)
mid_poly = SD.resample(SD.catmull(MP, 12), 48)
mid_s = SD.arclen(mid_poly)
mid_r = np.interp(mid_s / mid_s[-1], SD.arclen(MP) / SD.arclen(MP)[-1], MID_R)
U2 = unit(mid_poly[1] - mid_poly[0])                 # crown join axis: from the mound up into the mid
U1 = unit(mid_poly[-1] - mid_poly[-2])               # head join axis: from the mid forward into the head
lo_m = mid_poly.min(0) - 1.3; hi_m = mid_poly.max(0) + 1.3
G = SD.Grid(lo_m, hi_m, SDF_STEP, band=0.09)
add_cones(G, [(mid_poly[i], mid_poly[i + 1], mid_r[i], mid_r[i + 1]) for i in range(len(mid_poly) - 1)], 0.04)
n_t, ring_r, t_len, t_r0 = MID_TUFTS
for end_p, axis_, r_end in ((mid_poly[0], -U2, mid_r[0]), (mid_poly[-1], U1, mid_r[-1])):
    e1 = unit(np.cross(axis_, [1.0, 0, 0]) if abs(axis_[0]) < 0.9 else np.cross(axis_, UP)); e2 = np.cross(axis_, e1)
    for j in range(n_t):
        ang = TAU * (j + 0.25) / n_t
        radial = math.cos(ang) * e1 + math.sin(ang) * e2
        root = end_p + axis_ * (0.55 * r_end) + radial * ring_r * r_end / 0.55 * 0.9
        tip = root + axis_ * t_len + radial * 0.18 * t_len
        add_cones(G, lick_cones(root, (root + tip) / 2 + radial * 0.06, tip, t_r0, n=8, rtip=0.02), 0.06)
for frac, hang, bulb in MID_DRIPS:
    i = int(round(frac * (len(mid_poly) - 1)))
    top = mid_poly[i] - UP * (mid_r[i] * 0.55)
    bot = mid_poly[i] - UP * (mid_r[i] + hang)
    G.apply(lambda P, a=top, b=bot, r2=bulb: SD.sd_round_cone(P, a, b, 0.12, r2), np.minimum(top, bot) - 0.3,
            np.maximum(top, bot) + 0.3, 0.14)
mV0, mT0 = SD.polygonise(G)
mt_mid = mt_audit(mV0, mT0, G)
mV, mF, mfin = finish("mid", mV0, mT0)
report["pieces"]["mid"] = {"finish": mfin, "sdf": {"step": SDF_STEP, **mt_mid, "seconds": round(time.time() - t_mid, 1)},
                           "spine_arc_length": round(float(mid_s[-1]), 4)}
print("MID", json.dumps(mfin))

# =========================================================================== 2b. TAIL piece (SDF splash mound + tail)
t_tl = time.time()
C_JOIN = mid_poly[0].copy()                             # the crown join (the mid's rear end centre, in the crater)


def tail_spine(n=200):
    s = np.linspace(0.0, TAIL_LEN, n)
    ds = s[1] - s[0]
    ramp = smoothstep(TAIL_STRAIGHT, TAIL_STRAIGHT + 1.5, s)
    psi = np.radians(TAIL_BEND_DEG) * np.sin(TAU * s / TAIL_LEN * 0.8 + 0.3) * ramp + \
        np.radians(TAIL_CURL_DEG) * smoothstep(TAIL_STRAIGHT, TAIL_LEN, s) ** 1.2
    psi = psi - psi[0]
    d = np.stack([np.sin(psi), np.cos(psi), np.zeros_like(psi)], 1)
    P = np.vstack([[0, 0, 0], np.cumsum(d[:-1] * ds, 0)]) + np.array([C_JOIN[0], TAIL_START_Y, 0.0])
    r = TAIL_RTIP + (TAIL_R0 - TAIL_RTIP) * (1 - s / TAIL_LEN) ** TAIL_TAPER
    return s, P, d, r


ts, tP, tD, tr_ = tail_spine()


def unflat(P, inv=False):
    Q = np.array(P, float, copy=True)
    Q[:, 2] = Q[:, 2] * TAIL_FLAT if inv else Q[:, 2] / TAIL_FLAT
    return Q


spine_u = tP.copy(); spine_u[:, 2] = tr_                # unflattened space: every ring's bottom on the floor
TAIL_SPINE = tP.copy(); TAIL_SPINE[:, 2] = tr_ * TAIL_FLAT
tip_end = TAIL_SPINE[-1]; tip_dir = tD[-1]
reach = max(l for _, l in TAIL_TIP_FINGERS) + 1.0
lo_t = np.minimum(TAIL_SPINE.min(0), [MOUND_BASE[0][0] - 3.2, MOUND_BASE[0][1] - 3.2, 0]) - np.array([reach, 0.5, 0.05])
hi_t = np.maximum(TAIL_SPINE.max(0), [MOUND_BASE[0][0] + 3.2, MOUND_BASE[0][1] + 3.2, CROWN_Z + 0.8]) + np.array([reach, reach, 0.2])
Gt = SD.Grid(lo_t, hi_t, SDF_STEP, band=0.16)
mc_, mr_ = np.array(MOUND_BASE[0]), np.array(MOUND_BASE[1])
Gt.apply(lambda P: SD.sd_ellipsoid(P, mc_, mr_), mc_ - mr_, mc_ + mr_, 0.0)
crown_c = np.array([C_JOIN[0], C_JOIN[1], CROWN_Z])
cone_a = np.array([mc_[0], mc_[1], 0.55])
Gt.apply(lambda P: SD.sd_round_cone(P, cone_a, crown_c, MOUND_CONE_R[0], MOUND_CONE_R[1]), cone_a - 1.2, crown_c + 1.2, 0.36)
idx = np.linspace(0, len(ts) - 1, 56).round().astype(int)
add_cones(Gt, [(spine_u[idx[i]], spine_u[idx[i + 1]], tr_[idx[i]], tr_[idx[i + 1]]) for i in range(len(idx) - 1)], 0.05,
          fmap=unflat)
for hd, ln in TAIL_TIP_FINGERS:
    a = math.radians(hd)
    dd = np.array([tip_dir[0] * math.cos(a) - tip_dir[1] * math.sin(a), tip_dir[0] * math.sin(a) + tip_dir[1] * math.cos(a), 0])
    root = spine_u[-1] - tip_dir * 0.1
    tip = root + dd * ln; tip[2] = FINGER_TIP_R * 0.75
    add_cones(Gt, lick_cones(root, (root + tip) / 2, tip, TAIL_RTIP * 0.95, n=7, rtip=FINGER_TIP_R * 0.75, sharp=1.0),
              0.08, fmap=unflat)
for az, ln, r0 in MOUND_FINGERS:
    a = math.radians(az)
    dd = np.array([math.sin(a), math.cos(a), 0.0])
    root = mc_ + dd * 0.75 + UP * 0.25
    tip = mc_ + dd * (0.75 + ln) + UP * 0.02
    ctrl = (root + tip) / 2 + UP * 0.02
    add_cones(Gt, lick_cones(root, ctrl, tip, r0, n=8, rtip=FINGER_TIP_R, sharp=1.0), 0.18)
lick_len, lick_r, rim_r = CROWN_LICK
for j in range(CROWN_LICKS):
    ang = TAU * (j + 0.5) / CROWN_LICKS
    radial = np.array([math.sin(ang), math.cos(ang), 0.0])
    ln = lick_len * (1.0 + 0.22 * math.sin(3.7 * j + 0.9))
    root = crown_c + radial * rim_r - UP * 0.10
    tip = root + UP * ln + radial * 0.28 * ln
    add_cones(Gt, lick_cones(root, root + UP * 0.6 * ln + radial * 0.05, tip, lick_r, n=8, rtip=0.02), 0.07)
crater_c = crown_c + UP * (CRATER_R * 0.55)
Gt.apply(lambda P: SD.sd_sphere(P, crater_c, CRATER_R), crater_c - CRATER_R, crater_c + CRATER_R, 0.08, mode="subtract")
Gt.floor(0.0)
tV0, tT0 = SD.polygonise(Gt)
mt_tail = mt_audit(tV0, tT0, Gt)
lV, lF, lfin = finish("tail", tV0, tT0)
lV[:, 2] = np.maximum(lV[:, 2], 0.0)                   # voxel rounding under the flat floor cut -> back onto the floor
report["pieces"]["tail"] = {"finish": lfin, "sdf": {"step": SDF_STEP, **mt_tail, "seconds": round(time.time() - t_tl, 1)},
                            "tail_spine_length": TAIL_LEN, "rest_curl_deg": TAIL_CURL_DEG}
print("TAIL", json.dumps(lfin))

# =========================================================================== 3. assembly: the COMBINED serpent
MF = mid_poly[-1].copy()                                 # the mid's front end centre
A1 = MF + U1 * MID_PLUG_HEAD                             # the head socket lands here
ax_local = unit(np.array([0.0, H_SNOUT[1], HEAD_NOSE_Z * hs]) - H_SOCKET)     # head axis socket -> snout (YZ plane)
cur = math.atan2(-ax_local[2], -ax_local[1])
tgt = math.atan2(-U1[2], -U1[1]) + math.radians(HEAD_PITCH_EXTRA_DEG)
HEAD_PITCH = tgt - cur
Rh = np.array(Matrix.Rotation(HEAD_PITCH, 3, "X"))
HEAD_XF = rigid(Rh, A1 - Rh @ H_SOCKET)                  # head-local -> combined
hV = apply_m(HEAD_XF, hV)
SNOUT = apply_m(HEAD_XF, H_SNOUT[None])[0]; HMID = apply_m(HEAD_XF, H_MID[None])[0]
EYES = [apply_m(HEAD_XF, e[None])[0] for e in H_EYES]; EYE_N = [Rh @ n_ for n_ in H_EYE_N]
report["assembly"] = {"head_pitch_deg": round(math.degrees(HEAD_PITCH), 3),
                      "head_axis_nose_down_deg": round(math.degrees(tgt), 3),
                      "mid_front_tangent": U1.round(4).tolist(), "mid_rear_tangent": U2.round(4).tolist()}
PIECE_V = {"head": hV, "mid": mV, "tail": lV}
PIECE_F = {"head": hF, "mid": mF, "tail": lF}
BVH = {k: bvh_of(PIECE_V[k], PIECE_F[k]) for k in PIECES}
END_ANCHOR = {
    ("crown", "tail"): ray_hit(BVH["tail"], C_JOIN + U2 * 20.0, -U2),      # the crater floor
    ("crown", "mid"): ray_hit(BVH["mid"], C_JOIN - U2 * 20.0, U2),         # the mid's rear cap bottom
    ("head", "mid"): ray_hit(BVH["mid"], MF + U1 * 20.0, -U1),             # the mid's front cap tip
    ("head", "head"): ray_hit(BVH["head"], A1 - U1 * 20.0, U1)}            # the back of the head on the join axis
assert all(v is not None for v in END_ANCHOR.values()), END_ANCHOR
END_DIR = {("crown", "tail"): U2, ("crown", "mid"): -U2, ("head", "mid"): U1, ("head", "head"): -U1}
report["assembly"]["combined_join_overlap"] = {
    "rule": "combined serpent (the flight state): each join is goo-into-goo -- negative = overlap depth along the join "
            "axis between the two torn-end anchors",
    "crown": round(float((END_ANCHOR[("crown", "mid")] - END_ANCHOR[("crown", "tail")]) @ U2), 4),
    "head": round(float((END_ANCHOR[("head", "head")] - END_ANCHOR[("head", "mid")]) @ U1), 4)}


def mesh_volume(V, F):
    V = np.asarray(V)
    vol = 0.0
    for f in F:
        for j in range(1, len(f) - 1):
            vol += float(V[f[0]] @ np.cross(V[f[j]], V[f[j + 1]]))
    return vol / 6.0


# ---- droplets (SDF teardrops, tips up), modelled at the origin (rest spots are set by the rest layout)
DROP_G = []
for i, (r, _, _, _, _) in enumerate(DROPLETS):
    p = np.zeros(3)
    Gd = SD.Grid(p - r * 1.6, p + r * (1.6 + DROP_TIP), 0.012, band=0.04)
    Gd.apply(lambda P, c=p, r=r: SD.sd_sphere(P, c, r), p - r, p + r, 0.0)
    tipp = p + UP * r * DROP_TIP
    Gd.apply(lambda P, a=p + UP * r * 0.2, b=tipp, r1=r * 0.72: SD.sd_round_cone(P, a, b, r1, r * 0.08), p - r, tipp + r, r * 0.35)
    dV0, dT0 = SD.polygonise(Gd)
    dV, dF_, dfin = finish("drop%d" % i, dV0, dT0, voxel=0.012)
    DROP_G.append((dV, dF_, dfin))
DROP_BOT = [float(-g_[0][:, 2].min()) for g_ in DROP_G]        # droplet bottoms below their centres (teardrops)


# =========================================================================== 4. v4 REST layout (the bind pose)
def gap_at(VA, bB, step=4):
    return surface_gap(VA, bB, step)[0]


def solve_offset(VA, FB_bvh, target, direction, lo_o, hi_o, iters=34):
    """smallest offset o (VA + o * direction) whose surface gap to B reaches target; bisection on the monotone
    predicate gap >= target between lo_o (touching / overlapping) and hi_o (clear)."""
    assert gap_at(VA + hi_o * direction, FB_bvh) >= target, "hi offset not clear"
    for _ in range(iters):
        mid_o = 0.5 * (lo_o + hi_o)
        if gap_at(VA + mid_o * direction, FB_bvh) >= target:
            hi_o = mid_o
        else:
            lo_o = mid_o
    return hi_o


FWD = np.array([0.0, -1.0, 0.0])
HEAD_INV = np.linalg.inv(HEAD_XF)                         # combined -> head-local (the sculpt frame, floor 0)
# v5 PAUSED REST (design/reference/magmoo-v5-rest-pose-reference.png): tail stays as modelled (the mound + the curled
# tail on the floor: the grounded pieces)
S_REST = {"tail": np.eye(4)}
# mid: keeps its combined ARCH (rising off the crown, curving forward and down) and lifts clear of the crater along
# the crown join axis leaned REST_MID_FWD forward -> the neck arch hangs in the air, paused just after leaving the mound
D_MID = unit(U2 * (1.0 - REST_MID_FWD) + FWD * REST_MID_FWD)
mid_o = solve_offset(mV, BVH["tail"], REST_GAP_CROWN, D_MID, 0.0, 20.0)   # offset 0: the rear end in the crater
M_mid = rigid(t=D_MID * mid_o)
S_REST["mid"] = M_mid
mid_rest_V = apply_m(M_mid, mV)
# head: rides the arch in its combined (flight) attitude -- nose down, looking down / forward -- tilted a further
# REST_HEAD_PITCH_DEG about its socket, then thrown ahead along the arch's front tangent until the gap opens
A1_r = A1 + D_MID * mid_o
Rp_ = rot_x(math.radians(REST_HEAD_PITCH_DEG))
M_head = rigid(Rp_, A1_r - Rp_ @ A1_r) @ rigid(t=D_MID * mid_o)
D_HEAD = unit(U1 * (1.0 - REST_HEAD_FWD) + FWD * REST_HEAD_FWD)
bvh_mid_rest = bvh_of(mid_rest_V, mF)
head_o = solve_offset(apply_m(M_head, hV), bvh_mid_rest, REST_GAP_HEAD, D_HEAD, 0.0, 12.0)
M_head[:3, 3] += D_HEAD * head_o
S_REST["head"] = M_head
head_rest_V = apply_m(M_head, hV)
tail_rest_V = lV.copy()
# droplets: beside their piece at a fraction along its length, clear of its flank; on the floor, or HANGING in the
# air beside the arch (hang = height inside the piece's local height band)
REST_V = {"head": head_rest_V, "mid": mid_rest_V, "tail": tail_rest_V}
DROP_REST = []
for i, (r, pc, side, frac, hang) in enumerate(DROPLETS):
    Vp = REST_V[pc]
    if pc == "tail":                                   # along the mound, not the long tail
        Vp = Vp[Vp[:, 1] < MOUND_BASE[0][1] + MOUND_BASE[1][1]]
    y0 = Vp[:, 1].min() + frac * np.ptp(Vp[:, 1])
    band = Vp[np.abs(Vp[:, 1] - y0) < 0.45]
    xs = side * band[:, 0]
    x0 = side * (float(xs.max()) + DROP_CLEAR + r)
    z0 = DROP_BOT[i] if hang is None else float(band[:, 2].min() + hang * np.ptp(band[:, 2]))
    DROP_REST.append(np.array([x0, y0, max(z0, DROP_BOT[i])]))
# recentre the whole rest layout (contract feet_origin: bbox centre XY at 0, floor at 0)
ALLR = np.vstack([head_rest_V, mid_rest_V, tail_rest_V] + [DROP_G[i][0] + DROP_REST[i] for i in range(len(DROPLETS))])
lo_a, hi_a = ALLR.min(0), ALLR.max(0)
CENTER = np.array([(lo_a[0] + hi_a[0]) / 2, (lo_a[1] + hi_a[1]) / 2, lo_a[2]])
T_C = rigid(t=-CENTER)
for pc in PIECES:
    S_REST[pc] = T_C @ S_REST[pc]
DROP_REST = [p - CENTER for p in DROP_REST]
REST_V = {pc: apply_m(S_REST[pc], PIECE_V[pc]) for pc in PIECES}
REST_BVH = {pc: bvh_of(REST_V[pc], PIECE_F[pc]) for pc in PIECES}
gaps = {}
for a_, b_ in (("head", "mid"), ("mid", "tail"), ("head", "tail")):
    g_, pa_, pb_ = surface_gap(REST_V[a_], REST_BVH[b_], step=1)
    gaps["%s|%s" % (a_, b_)] = {"min_surface_gap": round(g_, 4), "closest_a": pa_.round(4).tolist(),
                                "closest_b": pb_.round(4).tolist()}
REST_CLOSEST = {"head": (np.array(gaps["head|mid"]["closest_b"]), np.array(gaps["head|mid"]["closest_a"])),
                "crown": (np.array(gaps["mid|tail"]["closest_a"]), np.array(gaps["mid|tail"]["closest_b"]))}
# REST_CLOSEST[join] = (point on the mid, point on the other piece)
drop_rep = []
for i, p in enumerate(DROP_REST):
    r = DROPLETS[i][0]
    dmin = {pc: round(float(REST_BVH[pc].find_nearest(Vector(p))[3]) - r, 4) for pc in PIECES}
    drop_rep.append({"rest": p.round(4).tolist(), "radius": r, "piece": DROPLETS[i][1],
                     "clearance_to_pieces": dmin})


def in_gap_corridor(p, r):
    """does a droplet sit between two neighbouring pieces (inside the y span of a gap and the x span of the pieces)?"""
    out = []
    for a_, b_ in (("head", "mid"), ("mid", "tail")):
        ya, yb = REST_V[a_][:, 1].max(), REST_V[b_][:, 1].min()
        xs = np.concatenate([REST_V[a_][:, 0], REST_V[b_][:, 0]])
        if min(ya, yb) - r < p[1] < max(ya, yb) + r and xs.min() - r < p[0] < xs.max() + r:
            out.append("%s|%s" % (a_, b_))
    return out


for d_, p in zip(drop_rep, DROP_REST):
    d_["in_a_gap_corridor"] = in_gap_corridor(p, d_["radius"])


def chain_points(poly, n):
    s = SD.arclen(poly)
    t = np.linspace(0.0, s[-1], n + 1)
    return np.stack([np.interp(t, s, poly[:, k]) for k in range(3)], 1)


MID_J = chain_points(mid_poly, MID_BONES)                 # rear (crown) -> front (combined frame)
TAIL_J = chain_points(TAIL_SPINE, TAIL_BONES)              # tail root (inside the mound) -> tip
JC = {"head": np.array([SNOUT, HMID, A1]),
      "mid": np.array([MID_J[i] for i in range(MID_BONES, -1, -1)]),          # MF ... C_JOIN
      "tail": np.array([C_JOIN] + list(TAIL_J))}                              # C_JOIN, T0, J1 ... J10
JR = {pc: apply_m(S_REST[pc], JC[pc]) for pc in PIECES}                      # the REST (bind) joints
_pz = {pc: (float(REST_V[pc][:, 2].min()), float(REST_V[pc][:, 2].max())) for pc in PIECES}
_hl = apply_m(HEAD_INV, hV)                                     # the head lying in its sculpt frame (the v4 rest)
report["rest_layout"] = {
    "rule": "v5 bind pose = PAUSED IN MOTION (magmoo-v5-rest-pose-reference.png): the mound + curled tail lie on the "
            "floor as modelled; the mid keeps its combined arch and is lifted off the crater along the crown join axis "
            "leaned REST_MID_FWD forward; the head rides in its combined (nose-down flight) attitude + "
            "REST_HEAD_PITCH_DEG and is thrown ahead along the arch's front tangent. Surface gaps solved by bisection "
            "(BVH nearest-surface distance, every vertex) to REST_GAP_*. Droplets beside the pieces: on the floor by "
            "the mound, HANGING in the air beside the arch.",
    "targets": {"head|mid": REST_GAP_HEAD, "mid|tail": REST_GAP_CROWN},
    "measured": gaps, "mid_lift": {"direction": D_MID.round(4).tolist(), "offset": round(mid_o, 4)},
    "head_throw": {"direction": D_HEAD.round(4).tolist(), "offset": round(head_o, 4),
                   "extra_pitch_deg": REST_HEAD_PITCH_DEG,
                   "nose_down_deg": round(math.degrees(tgt) + REST_HEAD_PITCH_DEG, 3)},
    "piece_z_range": {pc: [round(a, 4), round(b, 4)] for pc, (a, b) in _pz.items()},
    "grounded_pieces": [pc for pc in PIECES if _pz[pc][0] < 0.01],
    "airborne_pieces": {pc: round(_pz[pc][0], 4) for pc in PIECES if _pz[pc][0] >= 0.01},
    "head_height": {"top": round(_pz["head"][1], 4), "underside_clearance": round(_pz["head"][0], 4),
                    "v4_head_top_lying": round(float(_hl[:, 2].max() - _hl[:, 2].min()), 4)},
    "droplets": drop_rep,
    "droplets_hanging": [{"i": i, "bottom_z": round(float(DROP_REST[i][2] - DROP_BOT[i]), 4)}
                         for i in range(len(DROPLETS)) if DROPLETS[i][4] is not None],
    "droplets_in_a_gap_corridor": int(sum(1 for d_ in drop_rep if d_["in_a_gap_corridor"])),
    "centre_shift": CENTER.round(5).tolist()}
assert report["rest_layout"]["grounded_pieces"] == ["tail"] and _pz["tail"][0] > -1e-6, report["rest_layout"]
print("REST", json.dumps(report["rest_layout"]))

# =========================================================================== 4b. BALL piece: the one big goo ball
t_ball = time.time()
VOL = {pc: mesh_volume(PIECE_V[pc], PIECE_F[pc]) for pc in PIECES}
VOL["droplets"] = sum(mesh_volume(dV, dF_) for dV, dF_, _ in DROP_G)
R_B = BALL_SIZE * (3.0 * sum(VOL.values()) / (4.0 * math.pi)) ** (1.0 / 3.0)
BALL_RAD3 = np.array([1.0 + 0.4 * BALL_SLUMP, 1.0 + 0.4 * BALL_SLUMP, 1.0 - 0.7 * BALL_SLUMP]) * R_B   # sagging goo
BALL_C = np.array([0.0, 0.0, BALL_RAD3[2] * (1.0 - BALL_SLUMP)])  # clip frame: the floor contact centre = origin


def ball_base_sdf(P):
    return SD.sd_ellipsoid(P, BALL_C, BALL_RAD3)


_eye_sep = float(np.linalg.norm(H_EYES[0] - H_EYES[1])) * BALL_EYE_SCALE
_el = math.radians(BALL_EYE_EL_DEG)
BALL_EYE_AZ = math.asin(min(0.95, _eye_sep / (2.0 * R_B * math.cos(_el))))
BALL_SOCKETS = []
for sgn in (1.0, -1.0):
    dvec = np.array([sgn * math.sin(BALL_EYE_AZ) * math.cos(_el), -math.cos(BALL_EYE_AZ) * math.cos(_el), math.sin(_el)])
    BALL_SOCKETS.append(socket_geom(BALL_C + dvec / math.sqrt(float(((dvec / BALL_RAD3) ** 2).sum())), dvec,
                                    EYE_OPEN_R * BALL_EYE_SCALE, EYE_DEPTH * BALL_EYE_SCALE))
ear_len, ear_r, ear_az, ear_el, ear_lean = BALL_EAR
Gb = SD.Grid(BALL_C - (R_B * 1.05 + ear_len + 0.4), BALL_C + (R_B * 1.05 + ear_len + 0.4), SDF_STEP, band=0.12)
Gb.apply(ball_base_sdf, BALL_C - BALL_RAD3, BALL_C + BALL_RAD3, 0.0)
BALL_EAR_TIPS = []
for sgn in (1.0, -1.0):
    a_, e_ = math.radians(ear_az), math.radians(ear_el)
    radial = np.array([sgn * math.sin(a_) * math.cos(e_), -math.cos(a_) * math.cos(e_), math.sin(e_)])
    back = unit(np.array([0.0, 1.0, 0.0]) - radial[1] * radial)
    d_ear = unit(radial * math.cos(math.radians(ear_lean)) + back * math.sin(math.radians(ear_lean)))
    root = BALL_C + radial * (R_B - 0.12)
    tip = root + d_ear * (ear_len + 0.12) + back * 0.18 * ear_len
    ctrl = root + radial * 0.62 * (ear_len + 0.12)
    add_cones(Gb, lick_cones(root, ctrl, tip, ear_r, n=10, rtip=0.02, sharp=1.2), 0.16)
    BALL_EAR_TIPS.append(tip)
for S in BALL_SOCKETS:
    Gb.apply(lambda P, c=S["cs"], r=S["Rs"]: SD.sd_sphere(P, c, r), S["cs"] - S["Rs"], S["cs"] + S["Rs"], 0.03,
             mode="subtract")
Gb.floor(0.0)
bV0, bT0 = SD.polygonise(Gb)
mt_ball = mt_audit(bV0, bT0, Gb)
bV, bF, bfin = finish("ball", bV0, bT0, density=BALL_TRI_DENSITY,
                      protect=[(S["p"], EYE_PROTECT_R * BALL_EYE_SCALE) for S in BALL_SOCKETS])
bV[:, 2] = np.maximum(bV[:, 2], 0.0)
PIECE_V["ball"], PIECE_F["ball"] = bV, bF
B_SINK = ball_base_sdf(bV)
BALL_EYE_ORBS = [eye_orb(S, ball_base_sdf, BALL_EYE_ORB_DENSITY, BALL_EYE_SCALE) for S in BALL_SOCKETS]   # ball frame
report["eye_goo"]["ball_orbs"] = [{**{k: v for k, v in o_[3].items() if not k.startswith("_")}, "verts": len(o_[0])}
                                  for o_ in BALL_EYE_ORBS]
MOUND_C_REST = apply_m(S_REST["tail"], np.array(MOUND_BASE[0])[None])[0]
BALL_BIND_POS = 0.5 * (JR["tail"][0] + JR["tail"][1])    # hidden: rides tiny on the mound bone (inside the goo)
report["ball"] = {"volumes": {k: round(v, 4) for k, v in VOL.items()}, "total_volume": round(sum(VOL.values()), 4),
                  "radius": round(R_B, 4), "diameter": round(2 * R_B, 4),
                  "centre_in_clip": BALL_C.round(4).tolist(), "slump": BALL_SLUMP,
                  "eye_azimuth_deg": round(math.degrees(BALL_EYE_AZ), 3), "eye_elevation_deg": BALL_EYE_EL_DEG,
                  "bind": {"position": BALL_BIND_POS.round(4).tolist(), "scale": HIDE_SCALE},
                  "finish": bfin, "sdf": {"step": SDF_STEP, **mt_ball, "seconds": round(time.time() - t_ball, 1)}}
print("BALL", json.dumps({k: report["ball"][k] for k in ("radius", "total_volume", "eye_azimuth_deg")}), json.dumps(bfin))


# =========================================================================== 4c. v4 bridges + shed geometry
def strand_geom(r_root, r_tip, length=1.0):
    """a tapered goo strand half along +Y: round cone from the root sphere (at the origin) to the tip at y = length."""
    G_ = SD.Grid(np.array([-r_root, -r_root, -r_root]) - 0.05, np.array([r_root, length + r_tip, r_root]) + 0.05, 0.012,
                 band=0.04)
    G_.apply(lambda P: SD.sd_round_cone(P, np.zeros(3), np.array([0.0, length, 0.0]), r_root, r_tip),
             np.array([-r_root, -r_root, -r_root]), np.array([r_root, length + r_tip, r_root]), 0.0)
    V_, T_ = SD.polygonise(G_)
    return finish("strand", V_, T_, voxel=0.02, density=TRI_DENSITY * 0.75)


def drip_geom(r):
    p = np.zeros(3)
    G_ = SD.Grid(p - r * 1.6, p + r * (1.6 + DROP_TIP), 0.012, band=0.04)
    G_.apply(lambda P: SD.sd_sphere(P, p, r), p - r, p + r, 0.0)
    tipp = p + UP * r * DROP_TIP
    G_.apply(lambda P: SD.sd_round_cone(P, p + UP * r * 0.2, tipp, r * 0.72, r * 0.08), p - r, tipp + r, r * 0.35)
    V_, T_ = SD.polygonise(G_)
    return finish("drip", V_, T_, voxel=0.012)


def splat_geom(R, h):
    """a flat lava puddle: a squashed disc with five short finger lobes, bottom on z = 0."""
    G_ = SD.Grid(np.array([-R * 1.6, -R * 1.6, -0.05]), np.array([R * 1.6, R * 1.6, h * 3 + 0.1]), 0.02, band=0.06)
    G_.apply(lambda P: SD.sd_ellipsoid(P, np.array([0, 0, 0.0]), np.array([R, R, h * 2.0])),
             np.array([-R, -R, -h * 2]), np.array([R, R, h * 2]), 0.0)
    for j in range(5):
        a = TAU * (j + 0.3) / 5
        d = np.array([math.cos(a), math.sin(a), 0.0])
        G_.apply(lambda P, d=d: SD.sd_round_cone(P, d * R * 0.6, d * R * (1.25 + 0.12 * (j % 2)), h * 1.4, h * 0.9),
                 -np.full(3, R * 1.5), np.full(3, R * 1.5), 0.08)
    G_.floor(0.0)
    V_, T_ = SD.polygonise(G_)
    return finish("splat", V_, T_, voxel=0.03, density=TRI_DENSITY * 0.3)


STRAND = strand_geom(BR_ROOT_R, BR_TIP_R)
DRIP = drip_geom(SHED_DRIP_R)
SPLAT = splat_geom(*SPLAT_R)
SPLAT[0][:, 2] = np.maximum(SPLAT[0][:, 2], 0.0)
DRIP_BOT = float(-DRIP[0][:, 2].min())
report["goo_extras"] = {"strand_tris": STRAND[2]["tris"], "drip_tris": DRIP[2]["tris"], "splat_tris": SPLAT[2]["tris"]}
print("EXTRAS", json.dumps(report["goo_extras"]))

# =========================================================================== 5. regions (iso-contour cuts per piece)
REG = ["goo_cool", "goo", "goo_hot", "flame", "core", "eye", "eye_goo",
       "eye_core"]                    # v5: + eye_goo (the solid red goo eyes); v5.3: + eye_core (the molten inner disc)
R_ = {n: i for i, n in enumerate(REG)}
CUT_SNAP = 0.15
TIP_SMOOTH_R = 0.42                   # tip field: smoothing reach of the reference copy (units)
RIDGE_T = 0.050                       # "hot ridge": outward displacement off the smoothed copy above this = goo_hot
TIP_T = 0.080                         # "hot tip size": ... above this = flame
UNDER_NZ = -0.45                      # "cool underside": smoothed normal z below this = goo_cool
MOTTLE_CELL = 1.60                    # "flow patch size" of the hot mottling
MOTTLE_T = 0.78                       # "flow patch amount" (field > this = goo_hot; higher = fewer patches)
HEAT_UP = 0.45                        # "hot tops": hot patches surface on upward-facing goo (field += this x normal z)
END_W = 0.75                          # "molten end size": zone radius round each torn (join) end
END_T, END_T2 = 0.30, 0.55            # torn end: flame ring from this facing x closeness, white-hot core inside this


def mottle(V):
    k = TAU / MOTTLE_CELL
    W = V + 0.22 * MOTTLE_CELL * np.stack([np.sin(k * 0.61 * V[:, 1] + 0.7), np.sin(k * 0.57 * V[:, 2] + 1.9),
                                           np.sin(k * 0.53 * V[:, 0] + 2.6)], 1)
    return (np.sin(k * (0.83 * W[:, 1] + 0.31 * W[:, 2]) + 1.1) + np.sin(k * (0.67 * W[:, 2] - 0.45 * W[:, 0]) + 2.3) +
            np.sin(k * (0.59 * W[:, 0] + 0.71 * W[:, 1] - 0.37 * W[:, 2]) + 0.4)) / 3.0


# head fields in the COMBINED frame (regions are geometry-intrinsic; the eye sink field comes from the head-local proof)
PDATA = {}
SHAPED = PIECES + ["ball"]
for pc in SHAPED:
    t = time.time()
    V = PIECE_V[pc]; F = PIECE_F[pc]
    E = edges_of(F)
    N = vertex_normals(V, F)
    mean_edge = float(np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1).mean())
    tip_iters = int(round((TIP_SMOOTH_R / mean_edge) ** 2))
    Vs = smooth_copy(V, E, tip_iters)
    tipf = np.einsum("ij,ij->i", V - Vs, N)
    if pc == "ball":
        ear_d = np.min([K.seg_dist(V, BALL_C + unit(tp - BALL_C) * (R_B - 0.3), tp) for tp in BALL_EAR_TIPS], 0)
        tipf = np.where(ear_d < BALL_EAR[1] + 0.35, tipf, np.minimum(tipf, 0.0))
    nz = smooth_copy(N[:, 2], E, 12) if pc != "head" else \
        smooth_copy(vertex_normals(HEAD_LOCAL_V, F)[:, 2], E, 12)      # head undersides judged lying on the floor (rest)
    mot = smooth_copy(mottle(V), E, 4) + HEAT_UP * nz
    endf = np.full(len(V), -1.0)
    for (jn, side), anchor in END_ANCHOR.items():
        if side != pc:
            continue
        fac = N @ END_DIR[(jn, side)]
        clo = 1.0 - np.linalg.norm(V - anchor, axis=1) / END_W
        endf = np.maximum(endf, np.minimum(fac, clo))
    endf = smooth_copy(endf, E, 3)
    fields = {"tip": tipf, "nz": nz, "mot": mot, "end": endf}
    if pc == "head":
        fields["sink"] = np.asarray(H_SINK, float)
    elif pc == "ball":
        fields["sink"] = np.asarray(B_SINK, float)
    PDATA[pc] = {"V": V, "F": F, "fields": fields, "tip_iters": tip_iters, "mean_edge": round(mean_edge, 4)}


def cut_mesh(V, F, FLD, cuts):
    """iso-contour cuts: every colour edge becomes a mesh edge (no per-face sawtooth). -> V2, F2, vertex fields, log"""
    bm = bmesh.new()
    for p in V:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in F:
        bm.faces.new([bm.verts[i] for i in f])
    bm.verts.index_update()
    LAY = {k: bm.verts.layers.float.new(k) for k in FLD}
    for v in bm.verts:
        for k, arr in FLD.items():
            v[LAY[k]] = float(arr[v.index])

    def iso_cut(key, tau):
        L = LAY[key]
        eps = 1e-6 * max(1.0, abs(tau))
        side = lambda v: 0 if abs(v[L] - tau) <= eps else (1 if v[L] > tau else -1)
        for e in bm.edges:
            a, b = e.verts
            if side(a) * side(b) < 0:
                tt = (tau - a[L]) / (b[L] - a[L])
                if tt < CUT_SNAP:
                    a[L] = tau
                elif tt > 1 - CUT_SNAP:
                    b[L] = tau
        cuts_ = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0]
        for e in cuts_:
            a, b = e.verts
            tt = (tau - a[L]) / (b[L] - a[L])
            vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
            _, nv = bmesh.utils.edge_split(e, a, tt)
            for k in LAY:
                nv[LAY[k]] = vals[k]
            nv[L] = tau
        pairs = []
        for f in bm.faces:
            vs = list(f.verts)
            sides = [side(v) for v in vs]
            if 1 in sides and -1 in sides:
                cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
                if len(cv) == 2:
                    pairs.append(cv)
        for cv in pairs:
            bmesh.ops.connect_verts(bm, verts=cv)
        big = [f for f in bm.faces if len(f.verts) > 3]
        if big:
            bmesh.ops.triangulate(bm, faces=big)
        return {"field": key, "tau": tau, "edge_splits": len(cuts_), "face_connects": len(pairs)}
    log = [iso_cut(k, tau) for k, tau in cuts]
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
    bm.verts.index_update(); bm.faces.index_update()
    V2 = np.array([v.co[:] for v in bm.verts])
    F2 = [[v.index for v in f.verts] for f in bm.faces]
    VF = {k: np.array([v[LAY[k]] for v in bm.verts]) for k in LAY}
    bm.free()
    return V2, F2, VF, {"cuts": log}


def cut_piece(pc):
    D = PDATA[pc]
    cuts = [("nz", UNDER_NZ), ("mot", MOTTLE_T), ("tip", RIDGE_T), ("tip", TIP_T), ("end", END_T), ("end", END_T2)]
    eyes = "sink" in D["fields"]
    if eyes:                                           # v4: the socket interior edge is a cut line too
        cuts += [("sink", -EYE_SHADE_T)]
    V2, F2, VF, rep_ = cut_mesh(D["V"], D["F"], D["fields"], cuts)
    FV = {k: np.array([np.mean(VF[k][f]) for f in F2]) for k in VF}
    rid = np.full(len(F2), R_["goo"], dtype=np.int32)
    rid[FV["nz"] < UNDER_NZ] = R_["goo_cool"]
    rid[FV["mot"] > MOTTLE_T] = R_["goo_hot"]
    rid[FV["tip"] > RIDGE_T] = R_["goo_hot"]
    rid[FV["tip"] > TIP_T] = R_["flame"]
    rid[FV["end"] > END_T] = R_["flame"]
    rid[FV["end"] > END_T2] = R_["core"]
    if eyes:
        zone = np.zeros(len(F2), bool)
        socks = SOCKETS if pc == "head" else BALL_SOCKETS
        Fc = np.array([np.mean(V2[f], 0) for f in F2])
        Fc_l = apply_m(HEAD_INV, Fc) if pc == "head" else Fc
        for S in socks:
            zone |= np.linalg.norm(Fc_l - S["p"], axis=1) < S["a"] * 1.35
        rid[zone & (FV["sink"] < -EYE_SHADE_T)] = R_["eye"]
        rep_["eye_faces"] = int((rid == R_["eye"]).sum())
    return V2, F2, rid, rep_, VF


t_cut = time.time()
CUT = {}
for pc in SHAPED:
    V2, F2, rid, cutrep, VF = cut_piece(pc)
    CUT[pc] = {"V": V2, "F": F2, "rid": rid, "VF": VF}
    report["pieces"].setdefault(pc, {}).update({"regions_cut": cutrep, "tip_field_passes": PDATA[pc]["tip_iters"],
                                                "mean_edge": PDATA[pc]["mean_edge"]})
report["cut_seconds"] = round(time.time() - t_cut, 1)


# =========================================================================== 6a. v4 flight segment: mound -> smooth segment
def spine_s_of(V):
    """arc parameter of each vertex's nearest tail-spine sample (combined frame)."""
    d2 = ((np.asarray(V)[:, None, :] - TAIL_SPINE[None, ::2, :]) ** 2).sum(2)
    return ts[::2][np.argmin(d2, 1)], np.sqrt(d2.min(1))


def mound_exit():
    inside = SD.sd_ellipsoid(TAIL_SPINE, np.array(MOUND_BASE[0]), np.array(MOUND_BASE[1])) < 0
    i_exit = int(np.argmin(inside)) if not inside.all() else len(inside) - 1
    return float(ts[i_exit])


S_EXIT = mound_exit()
S_J = S_EXIT + 0.35                                         # the fixed tube starts here (a clean round cross-section)
TV, TF = CUT["tail"]["V"], CUT["tail"]["F"]
TS_NEAR, _ = spine_s_of(TV)
FREE = TS_NEAR < S_J                                        # the mound + crown + fingers + the tube root inside the blend
P_J = np.array([np.interp(S_J, ts, spine_u[:, k]) for k in range(3)])
R_C = float(np.interp(S_J, ts, tr_))
T0_U = spine_u[0]
A_AX = unit(P_J - T0_U)                                      # capsule axis (points back toward the tail)
CAP_TIP_U = T0_U - A_AX * CAP_FRONT_AHEAD
C_DOME = CAP_TIP_U + A_AX * R_C
CAP_L = float((P_J - C_DOME) @ A_AX)


def cap_project(Q):
    """closest point on the flight segment (unflat space): a round capsule from the front dome to the junction."""
    Q = np.asarray(Q, float)
    lam = (Q - C_DOME) @ A_AX
    lam_c = np.clip(lam, 0.0, CAP_L - 0.01)
    foot = C_DOME + np.outer(lam_c, A_AX)
    rad = Q - foot
    rad_n = np.linalg.norm(rad, axis=1)
    fallback = np.tile(UP, (len(Q), 1))
    rdir = np.where(rad_n[:, None] > 1e-6, rad / np.maximum(rad_n, 1e-12)[:, None], fallback)
    dome = lam < 0.0
    out = foot + rdir * R_C
    if dome.any():
        dd = Q[dome] - C_DOME
        dn = np.linalg.norm(dd, axis=1)
        dd = np.where(dn[:, None] > 1e-6, dd / np.maximum(dn, 1e-12)[:, None], -A_AX)
        out[dome] = C_DOME + dd * R_C
    return out


def cap_normal(Q):
    Q = np.asarray(Q, float)
    lam = (Q - C_DOME) @ A_AX
    foot = C_DOME + np.outer(np.clip(lam, 0.0, CAP_L), A_AX)
    return unit_rows(Q - foot)


t_fl = time.time()
XU = unflat(TV)
E_T = edges_of(TF)
nT = len(TV)
deg_T = np.bincount(E_T.ravel(), minlength=nT).astype(float)
XU[FREE] = cap_project(XU[FREE])
fi = np.nonzero(FREE)[0]
for it in range(CAP_RELAX_ITERS):
    S_ = np.zeros_like(XU)
    for k in range(3):
        S_[:, k] = np.bincount(E_T[:, 0], XU[E_T[:, 1], k], minlength=nT) + np.bincount(E_T[:, 1], XU[E_T[:, 0], k], minlength=nT)
    avg = S_ / np.maximum(deg_T, 1)[:, None]
    XU[fi] = XU[fi] + 0.6 * (avg[fi] - XU[fi])
    XU[fi] = cap_project(XU[fi])
TF_A = np.array(TF)
touch = FREE[TF_A].any(1)
fc_ = XU[TF_A].mean(1)
fn_ = np.cross(XU[TF_A[:, 1]] - XU[TF_A[:, 0]], XU[TF_A[:, 2]] - XU[TF_A[:, 0]])
area_ = 0.5 * np.linalg.norm(fn_, axis=1)
flipped = touch & (np.einsum("ij,ij->i", fn_, cap_normal(fc_)) < 0) & (area_ > 1e-9)
FLIGHT_V = unflat(XU, inv=True)
FLIGHT_DELTA = FLIGHT_V - TV
FLIGHT_DELTA[~FREE] = 0.0
e_len = np.linalg.norm(XU[E_T[:, 0]] - XU[E_T[:, 1]], axis=1)
fe = FREE[E_T].any(1)
report["flight_segment"] = {
    "rule": "shape key 'flight': every mound vertex (nearest tail-spine arc < mound exit + 0.35) maps onto a smooth round "
            "segment continuing the tail forward (unflattened space: the tube's own cross-section), seeded by closest-"
            "point projection then relaxed (uniform Laplacian, re-projected every pass, the tube boundary fixed) -> a "
            "fold-free map of the mound disc onto the segment",
    "free_verts": int(FREE.sum()), "fixed_verts": int((~FREE).sum()), "relax_passes": CAP_RELAX_ITERS,
    "segment": {"radius": round(R_C, 4), "length_to_junction": round(CAP_L, 4), "front_ahead_of_tail_root": CAP_FRONT_AHEAD,
                "junction_arc": round(S_J, 4), "mound_exit_arc": round(S_EXIT, 4)},
    "faces_touching_free": int(touch.sum()), "flipped_faces": int(flipped.sum()),
    "flipped_area_frac": round(float(area_[flipped].sum() / max(area_[touch].sum(), 1e-9)), 6),
    "degenerate_faces": int((touch & (area_ <= 1e-9)).sum()),
    "free_edge_len_p05_p50": [round(float(np.percentile(e_len[fe], 5)), 5), round(float(np.percentile(e_len[fe], 50)), 5)],
    "seconds": round(time.time() - t_fl, 1)}
print("FLIGHT", json.dumps(report["flight_segment"]))
CAP_TIP_COMB = unflat(CAP_TIP_U[None], inv=True)[0]          # the segment's front tip (combined = tail rest frame - T_C)
CAP_AXIS_COMB = unit(unflat(P_J[None], inv=True)[0] - unflat(C_DOME[None], inv=True)[0])

# =========================================================================== 6b. one merged mesh in the REST layout
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
amul = nt.nodes.new("ShaderNodeMath"); amul.operation = "MULTIPLY"; amul.location = (-300, -80); amul.name = "alpha_factor"
nt.links.new(vc.outputs["Alpha"], amul.inputs[0]); nt.links.new(amul.outputs[0], bsdf.inputs["Alpha"])
mat.surface_render_method = "DITHERED"                 # Eevee: order-independent see-through
mat.use_backface_culling = True                        # glTF doubleSided false
mat.use_transparent_shadow = True
pal_default = PAL.load(UNIT, "default")
PAL.apply_material(mat, pal_default)


def apply_alpha(mat_, pal):
    mat_.node_tree.nodes["alpha_factor"].inputs[1].default_value = float(pal["material"].get("alpha", 1.0))


def paint_alpha(me, pal):
    names, rid, _ = PAL.read_regions(me)
    a_reg = np.array([float(pal["regions"][n].get("alpha", 1.0)) for n in names], np.float32)
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    ca = me.color_attributes["Col"]
    cd = np.empty(len(me.loops) * 4, dtype=np.float32); ca.data.foreach_get("color", cd)
    cd = cd.reshape(-1, 4); cd[:, 3] = np.repeat(a_reg[rid], lt)
    ca.data.foreach_set("color", cd.ravel())
    return {n: float(a) for n, a in zip(names, a_reg)}


def repaint(me, pal):
    counts = PAL.paint(me, pal)
    paint_alpha(me, pal)
    return counts


apply_alpha(mat, pal_default)


def glow_tiers(pal):
    """emission tiers grade heat across the goo: cool < body < hot < flame < core; v4 the eye socket is DARKER than the
    body: no glow above the coolest goo and a darker base colour than the body goo."""
    tier = {n: float(v.get("emission_scale", 1.0)) if "emission" in v else 0.0 for n, v in pal["regions"].items()}
    grade = ["goo_cool", "goo", "goo_hot", "flame", "core"]
    lum = lambda n: float(np.dot(PAL.srgb_to_linear(pal["regions"][n]["rgb"]), [0.2126, 0.7152, 0.0722]))
    ok_grade = all(tier[a] < tier[b] for a, b in zip(grade, grade[1:]))
    ok_eye = tier["eye"] < tier["goo_cool"] and lum("eye") < lum("goo")
    # v5.1 goo eye (artist 2026-09-26: "give me some inner glow to them"): a SOLID near-opaque piece that glows from
    # within - tier ABOVE the body goo but BELOW the hot accents, so the eye reads lit without competing with goo_hot.
    # v5.3 (artist: "needs something inside"): eye_core, the molten inner disc, hotter than the eye but still under
    # the goo_hot accents: goo < eye_goo < eye_core < goo_hot.
    a_eg = float(pal["regions"]["eye_goo"].get("alpha", 1.0))
    ok_eg = tier["goo"] < tier["eye_goo"] < tier["eye_core"] < tier["goo_hot"] and a_eg >= 0.9
    return {"skin": pal["skin"], "emission_scale_tiers": tier, "grade": grade, "grade_pass": ok_grade,
            "eye_dark_socket_pass": ok_eye, "eye_vs_goo_luminance": [round(lum("eye"), 4), round(lum("goo"), 4)],
            "eye_goo_solid_pass": ok_eg, "eye_goo_alpha": a_eg, "eye_goo_luminance": round(lum("eye_goo"), 4),
            "pass": ok_grade and ok_eye and ok_eg}


report["glow_tiers"] = {"default": glow_tiers(pal_default)}
assert report["glow_tiers"]["default"]["pass"], report["glow_tiers"]["default"]


def jitter(V, F):
    FC = np.array([np.mean(V[f], 0) for f in F])
    j = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
    return 0.95 + 0.10 * j


ISL = []          # (name, V_rest, F, rid, shade, bone or None)


def add_island(name, V, F, rid, shade=None, bone=None):
    ISL.append((name, np.asarray(V, float), [list(map(int, f)) for f in F], np.asarray(rid, np.int32),
                jitter(np.asarray(V, float), F) if shade is None else np.asarray(shade, float), bone))


for pc in PIECES:
    Vr = apply_m(S_REST[pc], CUT[pc]["V"])
    add_island(pc, Vr, CUT[pc]["F"], CUT[pc]["rid"])
for i, (dV, dF_, _) in enumerate(DROP_G):
    add_island("drop.%d" % i, dV + DROP_REST[i], dF_, np.full(len(dF_), R_["core"]), np.ones(len(dF_)), "drop.%d" % i)
BALL_FULL_V = CUT["ball"]["V"]
add_island("ball", BALL_BIND_POS + BALL_FULL_V * HIDE_SCALE, CUT["ball"]["F"], CUT["ball"]["rid"], bone="ball")
# v5 goo eyes: the head's ride the head (skinned like the socket goo round them), the ball's ride the ball bone.
# v5.3: an inner molten CORE disc (eye_core, hotter) inside each eye so it reads as an eye, not a flat red circle -
# faces whose centroid lies within EYE_CORE_FRAC of the opening radius of the socket axis.
def eye_rid(oV, oF, S):
    C = np.array([np.asarray(oV)[list(f)].mean(0) for f in oF])
    d = C - np.asarray(S["p"], float)[None]
    ax = unit(np.asarray(S["n"], float))
    r = np.linalg.norm(d - np.outer(d @ ax, ax), axis=1)
    return np.where(r < EYE_CORE_FRAC * float(S["a"]), R_["eye_core"], R_["eye_goo"])


EYE_ORB_ISL = ["eye_orb.%d" % i for i in range(len(EYE_ORBS))]
for nm, (oV, oF, _, _), S in zip(EYE_ORB_ISL, EYE_ORBS, SOCKETS):
    add_island(nm, apply_m(S_REST["head"], apply_m(HEAD_XF, oV)), oF, eye_rid(oV, oF, S), np.ones(len(oF)))
BALL_EYE_ISL = ["ball_eye.%d" % i for i in range(len(BALL_EYE_ORBS))]
for nm, (oV, oF, _, _), S in zip(BALL_EYE_ISL, BALL_EYE_ORBS, BALL_SOCKETS):
    add_island(nm, BALL_BIND_POS + oV * HIDE_SCALE, oF, eye_rid(oV, oF, S), np.ones(len(oF)), "ball")


def link_frame(d):
    """columns (side, along, up): side = along x UP (no roll about the travel direction)."""
    a = unit(d)
    s = np.cross(a, UP)
    if np.linalg.norm(s) < 1e-5:
        s = X_AX.copy()
    s = unit(s)
    return np.column_stack([s, a, np.cross(s, a)])


# bridges: per join two half-strands, bind = tiny at the rest facing points, pointing at the partner
BRIDGES = {}
for jn in ("crown", "head"):
    p_mid, p_oth = REST_CLOSEST[jn]
    for side_, p_, q_ in (("a", p_mid, p_oth), ("b", p_oth, p_mid)):
        bn = "bridge.%s.%s" % (jn, side_)
        Fr = link_frame(q_ - p_)
        BRIDGES[bn] = {"join": jn, "side": side_, "anchor": p_, "dir": unit(q_ - p_), "frame": Fr}
        add_island(bn, p_ + (STRAND[0] * HIDE_SCALE) @ Fr.T, STRAND[1], np.full(len(STRAND[1]), R_["flame"]),
                   np.ones(len(STRAND[1])), bn)
# shed drips: hidden inside the mid; the splat: hidden inside the head
SHED_BIND = [0.5 * (JR["mid"][4 - i] + JR["mid"][3 - i]) for i in range(3)]    # on the body.1-3 bone lines
for i, p_ in enumerate(SHED_BIND):
    add_island("shed.%d" % i, p_ + DRIP[0] * HIDE_SCALE, DRIP[1], np.full(len(DRIP[1]), R_["flame"]), np.ones(len(DRIP[1])),
               "shed.%d" % i)
SPLAT_BIND = 0.5 * (JR["head"][1] + JR["head"][2])        # on the head.0 bone line
add_island("splat", SPLAT_BIND + SPLAT[0] * HIDE_SCALE, SPLAT[1], np.full(len(SPLAT[1]), R_["goo_hot"]),
           np.ones(len(SPLAT[1])), "splat")
RANGE, FRANGE, VALL, FALL, RID, SHD = {}, {}, [], [], [], []
nv_ = nf_ = 0
for name, V, F, rid, shd, _ in ISL:
    RANGE[name] = (nv_, nv_ + len(V)); FRANGE[name] = (nf_, nf_ + len(F))
    VALL.append(V); FALL += [[i + nv_ for i in f] for f in F]; RID.append(rid); SHD.append(shd)
    nv_ += len(V); nf_ += len(F)
VALL = np.vstack(VALL); RID = np.concatenate(RID); SHD = np.concatenate(SHD)
# the hidden parts ride at HIDE_SCALE round their bone heads; they are UV-unwrapped at FULL size (a 1 % island would
# have ~zero UV area and fail uv_health), then shrunk back to their bind size (UVs live on the loops, unaffected)
HIDDEN = {"ball": BALL_BIND_POS, **{n: BALL_BIND_POS for n in BALL_EYE_ISL}, **{n: BRIDGES[n]["anchor"] for n in BRIDGES},
          **{"shed.%d" % i: SHED_BIND[i] for i in range(3)}, "splat": SPLAT_BIND}
VALL_UV = VALL.copy()
for n_, c_ in HIDDEN.items():
    a_, b_ = RANGE[n_]
    VALL_UV[a_:b_] = c_ + (VALL[a_:b_] - c_) / HIDE_SCALE
ob = new_obj(UNIT, VALL_UV, FALL)
me = ob.data
t_uv = time.time()
bpy.context.view_layer.objects.active = ob
for o in scene.objects:
    o.select_set(o is ob)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.003, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.003)
bpy.ops.object.mode_set(mode="OBJECT")
me.vertices.foreach_set("co", VALL.ravel())
me.update()
report["uv_seconds"] = round(time.time() - t_uv, 1)
PAL.store_regions(me, REG, RID, SHD)
region_counts0 = repaint(me, pal_default)
me.materials.append(mat)
me.shade_flat()
me["conquest_islands"] = json.dumps({k: list(v) for k, v in RANGE.items()})
ALL_OBS = [ob]
report["islands"] = {k: {"verts": v[1] - v[0], "faces": FRANGE[k][1] - FRANGE[k][0]} for k, v in RANGE.items()}


def sl(name):
    a, b = RANGE[name]
    return slice(a, b)


# =========================================================================== 6c. shape keys (glTF morph targets)
t_sk = time.time()
REST_PIECE_V = {pc: VALL[sl(pc)] for pc in PIECES}


def squash_offsets(V, pivot_xy, w, z0=0.0):
    """z -> z0 + (z - z0) (1 - A w); xy spread about the pivot by 1 / sqrt(1 - A w) (volume kept). z0 = the piece's
    own underside (v5: the arch + head rest in the air -- they squash onto their own bottoms; the grounded tail's
    z0 = 0 keeps its floor contact on the floor)."""
    a = SQUASH_AMT * np.asarray(w, float)
    k = 1.0 / np.sqrt(1.0 - a) - 1.0
    D = np.zeros_like(V)
    D[:, 2] = -(V[:, 2] - z0) * a
    D[:, :2] = (V[:, :2] - pivot_xy) * k[:, None]
    return D


KEYS = {}
Vh = REST_PIECE_V["head"]
SQ_PIV = {"head": (Vh[:, :2].mean(0), float(Vh[:, 2].min()))}
D = np.zeros_like(VALL); D[sl("head")] = squash_offsets(Vh, SQ_PIV["head"][0], np.ones(len(Vh)), SQ_PIV["head"][1])
for nm in EYE_ORB_ISL:                                   # the goo eyes squash with the head (same affine map)
    D[sl(nm)] = squash_offsets(VALL[sl(nm)], SQ_PIV["head"][0], np.ones(len(VALL[sl(nm)])), SQ_PIV["head"][1])
KEYS["sq_head"] = D
Vm = REST_PIECE_V["mid"]
D = np.zeros_like(VALL); D[sl("mid")] = squash_offsets(Vm, Vm[:, :2].mean(0), np.ones(len(Vm)), float(Vm[:, 2].min()))
KEYS["sq_mid"] = D
Vt = REST_PIECE_V["tail"]
FREE_R = FREE                                                # tail island vertex order == CUT["tail"] order
spine_rest = apply_m(S_REST["tail"], TAIL_SPINE)
d2_ = ((Vt[:, None, :2] - spine_rest[None, ::2, :2]) ** 2).sum(2)
piv_t = np.where(FREE_R[:, None], apply_m(S_REST["tail"], np.array(MOUND_BASE[0])[None])[0][:2],
                 spine_rest[::2][np.argmin(d2_, 1), :2])
D = np.zeros_like(VALL); D[sl("tail")] = squash_offsets(Vt, piv_t, np.where(FREE_R, 1.0, 0.55)); KEYS["sq_tail"] = D
D = np.zeros_like(VALL); D[sl("tail")] = FLIGHT_DELTA @ S_REST["tail"][:3, :3].T; KEYS["flight"] = D
BULGE_INFO = {}
for jn, (pc_a, pc_b) in (("crown", ("mid", "tail")), ("head", ("mid", "head"))):
    D = np.zeros_like(VALL)
    p_mid, p_oth = REST_CLOSEST[jn]
    for pc, p_, q_ in ((pc_a, p_mid, p_oth), (pc_b, p_oth, p_mid)):
        Vp = REST_PIECE_V[pc]
        Np = vertex_normals(Vp, [f for f in CUT[pc]["F"]])
        dd = np.linalg.norm(Vp - p_, axis=1)
        fall = np.clip(1.0 - (dd / BULGE_R) ** 2, 0.0, 1.0) ** 2
        toward = unit(q_ - p_)
        D[sl(pc)] = (Np * 0.75 + toward * 0.25) * (BULGE_AMT * fall)[:, None]
        D[sl(pc), 2] = np.maximum(D[sl(pc), 2], -Vp[:, 2])          # a bulge never pushes goo through the floor
        BULGE_INFO["%s:%s" % (jn, pc)] = {"verts": int((fall > 0).sum()), "max_offset": round(float((BULGE_AMT * fall).max()), 4)}
    KEYS["bulge_" + jn] = D
# the goo eyes follow the head surface round them: nearest head vertex (rest) -> its weights + bulge delta
from mathutils.kdtree import KDTree  # noqa: E402
_kd = KDTree(len(REST_PIECE_V["head"]))
for i_, v_ in enumerate(REST_PIECE_V["head"]):
    _kd.insert(Vector(v_), i_)
_kd.balance()
ORB_NEAR = {nm: np.array([_kd.find(Vector(v_))[1] for v_ in VALL[sl(nm)]]) for nm in EYE_ORB_ISL}
for nm in EYE_ORB_ISL:
    KEYS["bulge_head"][sl(nm)] = KEYS["bulge_head"][sl("head")][ORB_NEAR[nm]]
# tuck_mid: the drips hanging under the mid pull up flush into its underside (the mid lies straight in the air line)
Vmc = CUT["mid"]["V"]
d2m = ((Vmc[:, None, :] - mid_poly[None, :, :]) ** 2).sum(2)
jm = np.argmin(d2m, 1)
dm = np.sqrt(d2m[np.arange(len(Vmc)), jm])
fr_m = mid_s[jm] / mid_s[-1]
DRIPV = (dm > mid_r[jm] + 0.03) & (Vmc[:, 2] - mid_poly[jm, 2] < -0.3 * mid_r[jm]) & (fr_m > 0.08) & (fr_m < 0.92)
tuck_t = mid_poly[jm] + unit_rows(Vmc - mid_poly[jm]) * (mid_r[jm] * 0.97)[:, None]
D = np.zeros_like(VALL)
D[sl("mid")] = np.where(DRIPV[:, None], (tuck_t - Vmc) @ S_REST["mid"][:3, :3].T, 0.0)
KEYS["tuck_mid"] = D
KEY_ORDER = ["flight", "tuck_mid", "sq_head", "sq_mid", "sq_tail", "bulge_crown", "bulge_head"]
ob.shape_key_add(name="Basis", from_mix=False)
for kn in KEY_ORDER:
    kb = ob.shape_key_add(name=kn, from_mix=False)
    kb.data.foreach_set("co", (VALL + KEYS[kn]).ravel())
    kb.slider_min = -1.0 if kn.startswith("sq_") else 0.0
    kb.slider_max = 1.0
    kb.value = 0.0
me.shape_keys.use_relative = True
KEY = me.shape_keys
report["shape_keys"] = {"order": KEY_ORDER, "squash_amount": SQUASH_AMT, "bulge": {"amount": BULGE_AMT, "radius": BULGE_R,
                        **BULGE_INFO}, "max_offset": {k: round(float(np.linalg.norm(KEYS[k], axis=1).max()), 4) for k in KEY_ORDER},
                        "moved_verts": {k: int((np.linalg.norm(KEYS[k], axis=1) > 1e-7).sum()) for k in KEY_ORDER},
                        "seconds": round(time.time() - t_sk, 1)}
print("KEYS", json.dumps(report["shape_keys"]))

# =========================================================================== facing landmark + props, UVs
EYES_REST = [apply_m(S_REST["head"], e[None])[0] for e in EYES]
SNOUT_REST = apply_m(S_REST["head"], SNOUT[None])[0]
anchor = (EYES_REST[0] + EYES_REST[1]) / 2
landmark = SNOUT_REST
dvec = landmark - anchor
report["facing"] = {"rule": "midpoint of the two eye sockets -> the snout tip (the head's rounded front on its axis)",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 3)}
ob["conquest_unit"] = UNIT
ob["conquest_tier"] = "hero"
ob["conquest_tri_budget"] = TRI_BUDGET
ob["conquest_max_height"] = CELL_MAX_H
ob["conquest_max_footprint"] = CELL_MAX_FP
ob["conquest_yaw_fix_deg"] = 0.0
ob["conquest_front_anchor"] = anchor.tolist()
ob["conquest_front_landmark"] = landmark.tolist()
ob["conquest_facing_rule"] = report["facing"]["rule"]
ob["conquest_source"] = "newunit-magmoo.blend (the head, v4 carved sockets) + fresh SDF mid/tail/droplets/ball/bridges/shed"
ob["conquest_scale_policy"] = "natural proportions, source units; game scales at import (cell fit report-only)"
ob["conquest_emission_channel"] = "Glow colour attribute (2nd colour set, glTF COLOR_1) -> Emission Color; Col -> Base Color"
ob["conquest_alpha_channel"] = "Col alpha (glTF COLOR_0.a, per-region palette alpha) x material alpha factor " \
                               "(baseColorFactor[3]); alphaMode BLEND, single-sided"
ob["conquest_rest"] = "v5: the bind pose is paused in motion - mound + tail on the floor, the arch + head in the air " \
                      "(visibly apart), droplets hanging beside the arch"
lo_a, hi_a = VALL.min(0), VALL.max(0)
report["measure"] = {
    "bbox": [lo_a.round(4).tolist(), hi_a.round(4).tolist()],
    "length_y": round(float(hi_a[1] - lo_a[1]), 4), "width_x": round(float(hi_a[0] - lo_a[0]), 4),
    "height_z": round(float(hi_a[2] - lo_a[2]), 4),
    "units": "source units (the head sculpt's own scale, natural proportions)",
    "v3_combined_rest_was": {"length_y": 14.579, "width_x": 4.5874, "height_z": 4.7328, "cell_fit_scale": 0.13032},
    "v4_segmented_rest_was": {"length_y": 19.6078, "width_x": 6.2386, "height_z": 2.6436, "cell_fit_scale": 0.0969}}
fp = max(report["measure"]["length_y"], report["measure"]["width_x"])
k_fit = min(CELL_MAX_H / report["measure"]["height_z"], CELL_MAX_FP / fp)
report["measure"]["export_cell_fit_report_only"] = {
    "scale": round(k_fit, 5), "bound_by": "footprint" if CELL_MAX_FP / fp < CELL_MAX_H / report["measure"]["height_z"] else "height",
    "length_m": round(report["measure"]["length_y"] * k_fit, 4), "height_m": round(report["measure"]["height_z"] * k_fit, 4)}
report["tris"] = {"total": tri_count(me), **{k: sum(len(f) - 2 for f in ISL[i][2]) for i, k in enumerate(RANGE)}}
report["region_faces"] = region_counts0
print("MEASURE", json.dumps(report["measure"]), json.dumps({"tris": report["tris"]["total"]}))


def geometry_digest():
    h = hashlib.sha256()
    for o in sorted(ALL_OBS, key=lambda o_: o_.name):
        me_ = o.data
        co = np.empty(len(me_.vertices) * 3); me_.vertices.foreach_get("co", co)
        lv = np.empty(len(me_.loops), dtype=np.int64); me_.loops.foreach_get("vertex_index", lv)
        h.update(np.round(co, 6).astype(np.float32).tobytes()); h.update(lv.tobytes())
        for nm in ("Col", "Glow"):
            cd = np.empty(len(me_.loops) * 4, dtype=np.float32); me_.color_attributes[nm].data.foreach_get("color", cd)
            h.update(np.round(cd, 5).tobytes())
        uv = np.empty(len(me_.loops) * 2); me_.uv_layers.active.data.foreach_get("uv", uv)
        h.update(np.round(uv, 6).astype(np.float32).tobytes())
        if me_.shape_keys:
            for kb in me_.shape_keys.key_blocks:
                kc = np.empty(len(me_.vertices) * 3); kb.data.foreach_get("co", kc)
                h.update(kb.name.encode()); h.update(np.round(kc, 6).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


report["digest_geometry_colour_uv"] = geometry_digest()
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"]}
report["seconds_to_improved"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True)
json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, report["digest_geometry_colour_uv"], round(time.time() - T0, 1))
sys.stdout.flush()
if STOP == "improved":
    os._exit(0)
# =========================================================================== 7. rig (root + FLAT deform bones)
rep = {"unit": UNIT, "version": report["version"], "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
CHAIN =[("head.0", "head", 2, 1), ("head.1", "head", 1, 0)] + \
    [("body.%d" % i, "mid", MID_BONES - i, MID_BONES - 1 - i) for i in range(MID_BONES)] + \
    [("tail.%d" % i, "tail", i, i + 1) for i in range(TAIL_BONES + 1)]
CHAIN_BONES = [c[0] for c in CHAIN]
SEG_OF = {pc: [c[0] for c in CHAIN if c[1] == pc] for pc in PIECES}
DROP_BONES = ["drop.%d" % i for i in range(len(DROPLETS))]
BRIDGE_BONES = sorted(BRIDGES)
SHED_BONES = ["shed.%d" % i for i in range(3)]
FREE_BONES = DROP_BONES + ["ball"] + BRIDGE_BONES + SHED_BONES + ["splat"]
DEFORM = CHAIN_BONES + FREE_BONES
BR_HOST = {"bridge.crown.a": "body.0", "bridge.crown.b": "tail.0", "bridge.head.a": "body.%d" % (MID_BONES - 1),
           "bridge.head.b": "head.0"}
HIDE_HOST = {**BR_HOST, **{n: "body.2" for n in SHED_BONES}, "splat": "head.0", "ball": "tail.0"}

arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.5); eb.use_deform = False
FREE_HEAD = {**{"drop.%d" % i: DROP_REST[i] for i in range(len(DROPLETS))}, "ball": BALL_BIND_POS,
             **{n: BRIDGES[n]["anchor"] for n in BRIDGE_BONES},
             **{"shed.%d" % i: SHED_BIND[i] for i in range(3)}, "splat": SPLAT_BIND}
for n in DEFORM:
    e = arm_data.edit_bones.new(n)
    if n in FREE_HEAD:
        h_ = FREE_HEAD[n]
        t_ = h_ + (BRIDGES[n]["dir"] * 0.3 if n in BRIDGES else UP * 0.3)
    else:
        _, pc, a, b = next(c for c in CHAIN if c[0] == n)
        h_, t_ = JR[pc][a], JR[pc][b]
    e.head = Vector(h_); e.tail = Vector(t_)
    e.align_roll(Vector((0, 0, 1.0)) if abs((Vector(t_) - Vector(h_)).normalized().z) < 0.9 else Vector((0, 1.0, 0)))
    e.use_deform = True
    e.parent = arm_data.edit_bones["root"]
    e.use_connect = False
bpy.ops.object.mode_set(mode="OBJECT")
REST = {b.name: np.array(b.matrix_local) for b in arm_data.bones}
REST_INV = {n: np.linalg.inv(M) for n, M in REST.items()}
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local],
                 "length": round(b.length, 4)} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["segment_chains"] = {**SEG_OF, "droplets": DROP_BONES, "ball": ["ball"], "bridges": BRIDGE_BONES,
                         "shed": SHED_BONES + ["splat"]}
rep["hierarchy"] = "v4 FLAT: every deform bone is parented straight to 'root' (bone stretch + volume thinning is keyed " \
                   "as bone-local scale; a chain hierarchy would turn a parent's non-uniform scale into child shear, " \
                   "which glTF TRS cannot carry). Each piece is weighted only to its own bones (verified below)."


def chain_weights(V, E, pts, root_blend=None, first_only=None):
    """ordered bones along the piece (pts = bone heads + the last tail): vertex -> arc param by nearest point on the
    polyline (extended past both ends), 6 graph-smoothing passes, then hat weights between consecutive bone midpoints
    (<= 2 influences). first_only = vertex mask forced onto the first bone (the mound); root_blend=(a, b): the first
    bone owns arc params up to a past its tail end, the blend into the next bone runs from there to b."""
    nb = len(pts) - 1
    seg_len = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    cum = np.concatenate([[0], np.cumsum(seg_len)])
    best_d = np.full(len(V), np.inf); s = np.zeros(len(V))
    for i in range(nb):
        a, b = pts[i], pts[i + 1]
        ab = b - a
        tt = (V - a) @ ab / (ab @ ab)
        tt = np.clip(tt, -np.inf if i == 0 else 0.0, np.inf if i == nb - 1 else 1.0)
        d = np.linalg.norm(V - (a + tt[:, None] * ab), axis=1)
        m = d < best_d
        best_d[m] = d[m]; s[m] = cum[i] + tt[m] * seg_len[i]
    n = len(V)
    if first_only is not None:
        s[first_only] = 0.0
    deg = np.bincount(E.ravel(), minlength=n).astype(float)
    for _ in range(6):
        sm = (np.bincount(E[:, 0], s[E[:, 1]], minlength=n) + np.bincount(E[:, 1], s[E[:, 0]], minlength=n)) / np.maximum(deg, 1)
        s = 0.5 * s + 0.5 * sm
    mids = 0.5 * (cum[:-1] + cum[1:])
    active = list(range(nb))
    if root_blend is not None:
        active = [0] + [k for k in range(1, nb) if mids[k] > cum[1] + root_blend[1]]
        mids = np.array([cum[1] + root_blend[0]] + [mids[k] for k in active[1:]])
        assert np.all(np.diff(mids) > 0), mids
    Wa = np.zeros((n, len(active)))
    k = np.clip(np.searchsorted(mids, s) - 1, 0, len(active) - 2)
    u = np.clip((s - mids[k]) / (mids[k + 1] - mids[k]), 0.0, 1.0)
    Wa[np.arange(n), k] = 1 - u
    Wa[np.arange(n), k + 1] += u
    W = np.zeros((n, nb))
    W[:, active] = Wa
    return W


PIECE_PTS = {"head": JR["head"][[2, 1, 0]], "mid": JR["mid"][::-1], "tail": JR["tail"]}
VG = {n: ob.vertex_groups.new(name=n) for n in DEFORM}
WREP, SKIN = {}, {}
for pc in PIECES:
    a0, b0 = RANGE[pc]
    V = VALL[a0:b0]
    E = edges_of(CUT[pc]["F"])
    if pc == "tail":
        W = chain_weights(V, E, PIECE_PTS[pc], root_blend=(S_J, S_J + TAIL_ROOT_BLEND[1]), first_only=FREE_R)
    else:
        W = chain_weights(V, E, PIECE_PTS[pc])
    bones = SEG_OF[pc]
    for j, bn in enumerate(bones):
        idx = np.nonzero(W[:, j] > 1e-6)[0]
        for w_ in np.unique(np.round(W[idx, j], 6)):
            sel = idx[np.round(W[idx, j], 6) == w_]
            VG[bn].add([int(i) + a0 for i in sel], float(w_), "REPLACE")
    order = np.argsort(-W, axis=1)[:, :2]
    SKIN[pc] = {"b0": order[:, 0], "b1": order[:, 1], "w0": W[np.arange(len(V)), order[:, 0]],
                "w1": W[np.arange(len(V)), order[:, 1]], "bones": bones}
    WREP[pc] = {"bones": bones, "per_bone_dominant": {b: int((np.argmax(W, 1) == j).sum()) for j, b in enumerate(bones)}}
    if pc == "head":                                   # v5 goo eyes: the weights of the nearest head goo vertex
        for nm in EYE_ORB_ISL:
            a1 = RANGE[nm][0]
            Wo = W[ORB_NEAR[nm]]
            for j, bn in enumerate(bones):
                idx = np.nonzero(Wo[:, j] > 1e-6)[0]
                for w_ in np.unique(np.round(Wo[idx, j], 6)):
                    sel = idx[np.round(Wo[idx, j], 6) == w_]
                    VG[bn].add([int(i) + a1 for i in sel], float(w_), "REPLACE")
        WREP["eye_goo"] = {"rule": "each goo-eye vertex copies the head-chain weights of its nearest rest head vertex",
                           "verts": int(sum(RANGE[nm][1] - RANGE[nm][0] for nm in EYE_ORB_ISL))}
WREP["tail"]["mound_rigid"] = {"mound_verts_forced_tail0": int(FREE_R.sum()), "root_blend_from_arc": round(S_J, 4)}
for name, V, F, rid, shd, bone in ISL:
    if bone is not None:
        a0, b0 = RANGE[name]
        VG[bone].add(list(range(a0, b0)), 1.0, "REPLACE")
ob.parent = rig; ob.matrix_parent_inverse = Matrix.Identity(4)
ob.modifiers.new("Armature", "ARMATURE").object = rig


def weight_audit():
    deform = {b.name for b in arm_data.bones if b.use_deform}
    gi = {g.index: g.name for g in ob.vertex_groups}
    sums, infl = np.zeros(len(me.vertices)), np.zeros(len(me.vertices), int)
    used = {k: set() for k in RANGE}
    owner = np.empty(len(me.vertices), dtype=object)
    for k, (a, b) in RANGE.items():
        owner[a:b] = k
    for v in me.vertices:
        ws = [(gi[g.group], g.weight) for g in v.groups if gi[g.group] in deform and g.weight > 0]
        sums[v.index] = sum(w for _, w in ws); infl[v.index] = len(ws)
        used[owner[v.index]] |= {n for n, _ in ws}
    return {"weight_sum_min": round(float(sums.min()), 6), "weight_sum_max": round(float(sums.max()), 6),
            "unweighted": int((infl == 0).sum()), "max_influences": int(infl.max())}, used


WREP["audit"], USED = weight_audit()
for pc in PIECES:
    WREP[pc]["only_own_chain"] = USED[pc] <= set(SEG_OF[pc])
    assert WREP[pc]["only_own_chain"], (pc, USED[pc])
rep["weights"] = WREP

# =========================================================================== 8. the pose system
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
REST_DIR = {n: JR[pc][b] - JR[pc][a] for n, pc, a, b in CHAIN}
REST_FR = {n: link_frame(REST_DIR[n]) for n in CHAIN_BONES}


def arc_rot(a, b):
    """the shortest-arc rotation taking direction a onto b. v4: link frames built from (d x UP) flip their side vector
    when a link passes vertical (the steep mound bone does in every leap) -- a 180-degree roll that folded the flight
    segment back over the tail; the shortest arc is continuous everywhere except an exact reversal."""
    a = unit(a); b = unit(b)
    v = np.cross(a, b); c = float(a @ b)
    if c < -0.999999:
        ax = unit(UP - (UP @ a) * a) if abs(a[2]) < 0.9 else unit(np.cross(a, X_AX))   # a reversal yaws
        return 2.0 * np.outer(ax, ax) - np.eye(3)
    V = np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
    return np.eye(3) + V + V @ V / (1.0 + c)
R0 = {n: REST[n][:3, :3] for n in DEFORM}
CHAIN_BY_PC = {pc: [c for c in CHAIN if c[1] == pc] for pc in PIECES}


def spring_step(tau, f=None, z=None):
    """unit step response of a damped spring (zero start velocity), tau in seconds; 0 before the step."""
    if tau <= 0:
        return 0.0
    f = WALK_SPRING[0] if f is None else f
    z = WALK_SPRING[1] if z is None else z
    wn = TAU * f / math.sqrt(1 - z * z)
    wd = wn * math.sqrt(1 - z * z)
    return 1.0 - math.exp(-z * wn * tau) * (math.cos(wd * tau) + z / math.sqrt(1 - z * z) * math.sin(wd * tau))


def spring_between(t, a, b, cyc_s, f=None, z=None):
    """0 before a; a spring step from a whose tail is faded to exactly 1 at b (loops close)."""
    if t <= a:
        return 0.0
    if t >= b:
        return 1.0
    s = spring_step((t - a) * cyc_s, f, z)
    fade = sstep(a + 0.7 * (b - a), b, t)
    return s * (1.0 - fade) + fade


def kabsch(A, B):
    ca, cb = A.mean(0), B.mean(0)
    H = (A - ca).T @ (B - cb)
    U_, S_, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U_.T))
    return Vt.T @ np.diag([1.0, 1.0, d]) @ U_.T, ca, cb


def blend_pts(A, B, w):
    """rigid (Kabsch) interpolation of a joint set A -> B plus the residual bend, w may overshoot [0, 1] (springs)."""
    if abs(w) < 1e-9:
        return A.copy()
    if abs(w - 1.0) < 1e-9:
        return B.copy()
    R, ca, cb = kabsch(A, B)
    Eres = B - ((A - ca) @ R.T + cb)
    q = Quaternion(Matrix(R.tolist()).to_quaternion())
    Rw = np.array(Quaternion((1, 0, 0, 0)).slerp(q, min(max(w, 0.0), 1.0)).to_matrix())   # overshoot: rotation clamps,
    return (A - ca) @ Rw.T + ca + w * (cb - ca) + w * Eres                               # translation + bend extrapolate


def shift(J, dv):
    return {pc: J[pc] + dv for pc in J}


def tail_root_joint(T0p, J1p):
    """the tail's crown joint rides with the tail root: tail.0 turns exactly like tail.1 (the flight segment continues
    the tube straight)."""
    Q0 = arc_rot(JR["tail"][2] - JR["tail"][1], J1p - T0p)
    return T0p + Q0 @ (JR["tail"][0] - JR["tail"][1])


# the AIR LINE: the combined serpent laid straight along +Y behind the snout (offsets c along the body)
LINK_LEN = {pc: np.linalg.norm(np.diff(JC[pc], axis=0), axis=1) for pc in PIECES}
C_HEAD = np.concatenate([[0.0], np.cumsum(LINK_LEN["head"])])                 # SNOUT, HMID, A1
C_MID = C_HEAD[-1] + float(np.linalg.norm(A1 - MF)) + np.concatenate([[0.0], np.cumsum(LINK_LEN["mid"])])   # MF..CJ
C_T0 = C_MID[-1] + MID_R[0] + CAP_FRONT_AHEAD - CAP_OVERLAP
C_TAIL = C_T0 + np.concatenate([[0.0], np.cumsum(LINK_LEN["tail"][1:])])     # T0, J1..J10
L_AIR = float(C_TAIL[-1])
C_CAP = C_T0 - CAP_FRONT_AHEAD                                                # the flight segment's front tip
# the line's floor heights (where each body point touches down): v5 the head's = its joints lying on the floor in its
# sculpt frame (the rest head is in the air now), the mid's = its radius, the tail's = its rest (grounded) joints
Z_LINE = {"head": apply_m(HEAD_INV, JC["head"])[:, 2] - float(_hl[:, 2].min()),
          "mid": np.full(len(JC["mid"]), MID_R[0] * 1.05), "tail": JR["tail"][1:, 2].copy()}
AIR_C = {"head": C_HEAD, "mid": C_MID, "tail": C_TAIL}
Y_SNOUT = float(JR["head"][0][1])
rep["air_line"] = {"rule": "the flight state = the combined serpent laid along one line: head (snout first), the mid's "
                           "front plugged MID_PLUG_HEAD behind the head socket, the flight segment's front taking the "
                           "mid's rear end CAP_OVERLAP deep, then the tail", "length": round(L_AIR, 4),
                   "offsets": {"snout": 0.0, "head_socket": round(C_HEAD[-1], 4), "mid_front": round(C_MID[0], 4),
                               "mid_rear": round(C_MID[-1], 4), "segment_front": round(C_CAP, 4),
                               "tail_root": round(C_T0, 4), "tail_tip": round(L_AIR, 4)}}


def air_joints(fn):
    """fn(c, z_line) -> world point of the air line at body offset c. -> joint dict (tail crown joint derived)."""
    J = {}
    for pc in ("head", "mid"):
        J[pc] = np.array([fn(c, z) for c, z in zip(AIR_C[pc], Z_LINE[pc])])
    Tt = np.array([fn(c, z) for c, z in zip(AIR_C["tail"], Z_LINE["tail"])])
    J["tail"] = np.vstack([tail_root_joint(Tt[0], Tt[1])[None], Tt])
    return J


def chain_mats(J, U=None, bsq=None):
    """bone world matrices from joint positions: link rotation (no roll), stretch along the bone with volume-keeping
    thinning, + bsq[piece]: squash ACROSS the body in the bone frame (bone Z = up: flatter + wider) for pieces that are
    not in their rest shape (a bind-space squash key would push a straightened piece through the floor)."""
    M = {}
    for n, pc, a, b in CHAIN:
        h = J[pc][a]; d = J[pc][b] - h
        Q = arc_rot(REST_DIR[n], d)
        sy = float(np.clip(np.linalg.norm(d) / np.linalg.norm(REST_DIR[n]), *STRETCH_CLAMP))
        sx = 1.0 / math.sqrt(sy)
        q_ = SQUASH_AMT * float(np.clip((bsq or {}).get(pc, 0.0), -1.0, 1.0))
        Mn = np.eye(4); Mn[:3, :3] = Q @ R0[n] @ np.diag([sx / math.sqrt(1.0 - q_), sy, sx * (1.0 - q_)]); Mn[:3, 3] = h
        if U is not None and pc in U:
            Mn = U[pc] @ Mn
        M[n] = Mn
    return M


def free_mat(n, pos, s=(1.0, 1.0, 1.0), Q=None):
    Mn = np.eye(4)
    Mn[:3, :3] = (np.eye(3) if Q is None else Q) @ R0[n] @ np.diag(s)
    Mn[:3, 3] = pos
    return Mn


def hidden_mat(n, M):
    host = HIDE_HOST[n]
    return M[host] @ REST_INV[host] @ REST[n]


def all_mats(P):
    """P: pose dict -> world (armature) matrices of every deform bone."""
    M = chain_mats(P["J"], P.get("U"), P.get("bsq"))
    for i, n in enumerate(DROP_BONES):
        p, s = P["drops"][i]
        M[n] = free_mat(n, p, (s, s, s))
    M["ball"] = P["ball"] if P.get("ball") is not None else hidden_mat("ball", M)
    for n in BRIDGE_BONES:
        br = P.get("bridges", {}).get(n)
        if br is None:
            M[n] = hidden_mat(n, M)
        else:
            p, d, L, th = br
            Q = arc_rot(BRIDGES[n]["dir"], d)
            M[n] = free_mat(n, p, (th / HIDE_SCALE, L / HIDE_SCALE, th / HIDE_SCALE), Q)
    for n in SHED_BONES + ["splat"]:
        sh = P.get("shed", {}).get(n)
        if sh is None:
            M[n] = hidden_mat(n, M)
        else:
            p, sxz, sv = sh
            M[n] = free_mat(n, p, (sxz / HIDE_SCALE, sv / HIDE_SCALE, sxz / HIDE_SCALE))
    return M


# ---- my own skinning (the gap measurement between pieces, frame by frame, without the depsgraph)
KEYV = {k: KEYS[k] for k in KEY_ORDER}


def skin_piece(pc, M, keys, step=1):
    a0, b0 = RANGE[pc]
    V = VALL[a0:b0][::step].copy()
    for k, w in keys.items():
        if abs(w) > 1e-9:
            V = V + w * KEYV[k][a0:b0][::step]
    S = SKIN[pc]
    bones = S["bones"]
    SK = np.stack([M[b] @ REST_INV[b] for b in bones])          # (nb, 4, 4)
    Vh = np.hstack([V, np.ones((len(V), 1))])
    b0_, b1_ = S["b0"][::step], S["b1"][::step]
    w0, w1 = S["w0"][::step], S["w1"][::step]
    P0 = np.einsum("nij,nj->ni", SK[b0_], Vh)[:, :3]
    P1 = np.einsum("nij,nj->ni", SK[b1_], Vh)[:, :3]
    return P0 * w0[:, None] + P1 * w1[:, None]


FACES_PC = {pc: np.array(CUT[pc]["F"]) for pc in PIECES}


def piece_gap(Pa, pb_full, pc_b):
    bb = BVHTree.FromPolygons(pb_full.tolist(), FACES_PC[pc_b].tolist())
    return surface_gap(Pa, bb, step=1)


FLOOR_LIFTS = []


def settle_floor(P):
    """contact: nothing goes through the floor. Each piece (skinned here, every vertex, with its shape keys) is lifted by
    its penetration; droplets / shed drips sit on their scaled bottoms."""
    M = chain_mats(P["J"], P.get("U"), P.get("bsq"))
    keys = {k: P["keys"].get(k, 0.0) for k in KEY_ORDER}
    lifts = {}
    for pc in PIECES:
        z = float(skin_piece(pc, M, keys)[:, 2].min())
        if z < 0.0:
            lifts[pc] = -z
            if P.get("U") is not None and pc in P["U"]:
                P["U"][pc] = rigid(t=UP * -z) @ P["U"][pc]
            else:
                P["J"][pc] = P["J"][pc] + UP * -z
    for i, (p, s) in enumerate(P["drops"]):
        r = DROP_BOT[i] * s
        if p[2] < r:
            P["drops"][i] = (np.array([p[0], p[1], r]), s)
    for n, v in list(P.get("shed", {}).items()):
        if n.startswith("shed") and v[0][2] < DRIP_BOT * v[2]:
            P["shed"][n] = (np.array([v[0][0], v[0][1], DRIP_BOT * v[2]]), v[1], v[2])
    FLOOR_LIFTS.append(max(lifts.values()) if lifts else 0.0)
    return P


# ---- breathing at rest (every clip): tiny squash wobble per piece, periodic in the clip
def breath(t, pc, cycles):
    ph = {"head": 0.0, "mid": 0.33, "tail": 0.66}[pc]
    return BREATH * math.sin(TAU * (cycles * t + ph))


AIRBORNE = [pc for pc in PIECES if pc not in report["rest_layout"]["grounded_pieces"]]


def bob(t, cycles, ph):
    return UP * REST_BOB * math.sin(TAU * (cycles * t + ph))


def rest_pose(t, cycles=1, off=None):
    J = {pc: JR[pc].copy() for pc in PIECES}
    for pc in AIRBORNE:                                  # v5: the paused pieces hover, bobbing a hair
        J[pc] = J[pc] + bob(t, cycles, {"head": 0.15, "mid": 0.0}[pc])
    if off is not None:
        J = shift(J, off)
    keys = {"sq_" + pc: breath(t, pc, cycles) for pc in PIECES}
    drops = [(DROP_REST[i] + (off if off is not None else 0.0) + (bob(t, cycles, 0.3 + 0.17 * i) if DROPLETS[i][4] is not None
                                                                   else 0.0),
              1.0 + 0.04 * math.sin(TAU * (cycles * t + 0.2 * i))) for i in range(len(DROPLETS))]
    return {"J": J, "keys": keys, "drops": drops, "glow": 1.0}


# ---- the goo ball (idle beat + the ball clip)
BALL_EYE_MID = np.mean([S["p"] for S in BALL_SOCKETS], 0)
EYE_N_REST = [S_REST["head"][:3, :3] @ n_ for n_ in EYE_N]
HEAD_EYE_MID = (EYES_REST[0] + EYES_REST[1]) / 2


def align_frame(b, f):
    b = unit(b); f = unit(f - (f @ b) * b)
    return np.column_stack([b, f, np.cross(b, f)])


R_HEAD_TO_BALL = align_frame(np.mean([S["n"] for S in BALL_SOCKETS], 0), np.array([0.0, -1.0, 0.0])) @ \
    align_frame(unit(EYE_N_REST[0] + EYE_N_REST[1]), unit(SNOUT_REST - JR["head"][2])).T
BALL_DROP_DIRS = [np.array([math.cos(a) * math.cos(0.45), math.sin(a) * math.cos(0.45), math.sin(0.45)])
                  for a in np.linspace(0.3, TAU + 0.3, len(DROPLETS), endpoint=False)]
HEAD_REST_VERTS = VALL[sl("head")]


def scale_about(c, s):
    M = np.eye(4); M[:3, :3] *= s; M[:3, 3] = np.asarray(c) * (1.0 - s)
    return M


def ball_env(key, t, T4):
    a, b, c, d = T4
    u0, u1 = BALL_ENV[key]
    return sstep(a + (b - a) * u0, a + (b - a) * u1, t) * (1.0 - sstep(d - (d - c) * u1, d - (d - c) * u0, t))


def bounce_profile(ph):
    """small hops during the hold: -> (lift, vertical squash; + = squashed, - = stretched). ph in [0, 1]."""
    lift, sq = 0.0, 0.0
    for k in range(BALL_BOUNCES):
        a = (k + 0.18) / BALL_BOUNCES; b = (k + 0.82) / BALL_BOUNCES
        u = (ph - a) / (b - a)
        pre = (ph - (a - 0.10 / BALL_BOUNCES)) / (0.10 / BALL_BOUNCES)
        if 0.0 <= pre < 1.0:
            sq += BALL_BOUNCE_SQ * math.sin(math.pi * pre)            # crouch before the hop
        if 0.0 <= u <= 1.0:
            lift += BALL_BOUNCE_H * 4.0 * u * (1.0 - u)
            sq -= 0.6 * BALL_BOUNCE_SQ * math.sin(math.pi * u) * (1.0 - u)   # stretched on the way up
        land = (ph - b) / (0.16 / BALL_BOUNCES)
        if 0.0 <= land < 1.0:
            sq += BALL_BOUNCE_SQ * 1.3 * math.exp(-3.0 * land) * math.cos(TAU * 0.9 * land) * (1 - land)   # splat + wobble
    return lift, sq


BALL_TRACE = []


def curl_tail(J, deg):
    """cumulative rotation of the tail links about Z (the tail sucked round into the ball)."""
    ang = 0.0
    out = [J[0], J[1]]
    for i in range(2, len(J)):
        ang += math.radians(deg) / (len(J) - 2)
        out.append(out[-1] + rot_z(ang) @ (J[i] - J[i - 1]))
    return np.array(out)


def ball_pose(t, T4, cycles=1):
    P = rest_pose(t, cycles)
    e = {k: ball_env(k, t, T4) for k in BALL_ENV}
    grow = e["grow"]
    rad = max(HIDE_SCALE, grow)
    a_, b_, c_, d_ = T4
    ph = min(1.0, max(0.0, (t - b_) / (c_ - b_)))
    lift, sq = bounce_profile(ph)
    lift *= grow; sq *= grow
    et, em = e["tail"], e["mid"]
    J = P["J"]
    J["tail"] = curl_tail(JR["tail"], BALL_CURL_DEG * et)
    U = {"tail": scale_about(BALL_C, 1.0 - (1.0 - BALL_ABSORB) * et) @ rigid(rot_z(math.radians(BALL_SWIRL_DEG) * et),
                                                                              BALL_C - rot_z(math.radians(BALL_SWIRL_DEG) * et) @ BALL_C),
         "mid": scale_about(BALL_C, 1.0 - (1.0 - BALL_ABSORB) * em)}
    m, ab = e["head_move"], e["head_absorb"]
    q_ = Quaternion(Matrix(R_HEAD_TO_BALL.tolist()).to_quaternion())
    Rm = np.array(Quaternion((1, 0, 0, 0)).slerp(q_, m).to_matrix())
    Et = BALL_EYE_MID * rad + UP * lift
    Ep = HEAD_EYE_MID + (Et - HEAD_EYE_MID) * m
    GA = np.eye(4); GA[:3, :3] = Rm * (1.0 + (BALL_EYE_SCALE - 1.0) * m); GA[:3, 3] = Ep - GA[:3, :3] @ HEAD_EYE_MID
    cen = BALL_C * max(rad, 0.35) + UP * lift
    GB = rigid(t=(cen - Ep) * ab) @ scale_about(Ep, 1.0 - (1.0 - BALL_ABSORB) * ab)
    G_head = GB @ GA
    zmin = float(apply_m(G_head, HEAD_REST_VERTS[::7])[:, 2].min())
    if zmin < 0.0:
        G_head = rigid(t=np.array([0.0, 0.0, -zmin])) @ G_head
    U["head"] = G_head
    P["U"] = U
    carried = apply_m(U["tail"], BALL_BIND_POS[None])[0]
    pos = carried * (1.0 - float(smoothstep(0.0, 0.15, grow))) + UP * lift
    s_b = rad / HIDE_SCALE
    P["ball"] = free_mat("ball", pos, (s_b * (1.0 + sq / 2.0), s_b * (1.0 - sq), s_b * (1.0 + sq / 2.0)))
    drops = []
    for i in range(len(DROPLETS)):
        w = e["drops"]
        tgt = (BALL_C + BALL_DROP_DIRS[i] * R_B * 1.02) * rad + UP * lift
        melt = float(smoothstep(0.6, 1.0, w))
        p = DROP_REST[i] + (tgt - DROP_REST[i]) * float(smoothstep(0.0, 0.75, w))
        p = p + (BALL_C * rad + UP * lift - p) * melt
        drops.append((p, (1.0 - (1.0 - BALL_ABSORB) * melt) * P["drops"][i][1]))
    P["drops"] = drops
    fade = 1.0 - max(em, et, m)
    P["keys"] = {k: v * fade for k, v in P["keys"].items()}
    P["glow"] = 1.0 + GLOW_PULSE["ball"] * grow + 1.5 * max(0.0, sq)
    P["bridge_ok"] = max(em, et, ab, grow) < 0.2          # absorbing pieces: no strands between shrunken goo
    BALL_TRACE.append({"t": t, "e": e, "rad": rad, "lift": lift, "sq": sq, "head_zmin": zmin})
    return P


def split_squash(P, pc, total, s_pc):
    """a piece in its rest shape squashes with its bind-space key; a straightened piece (the air line) squashes across
    its bones instead; blended by how far the piece is into the air line."""
    w = float(np.clip(s_pc, 0.0, 1.0))
    P["keys"]["sq_" + pc] = total * (1.0 - w)
    P.setdefault("bsq", {})[pc] = total * w


# ---- the flying S (idle beat): pieces rise one after another, merge into the serpent, undulate, settle, separate
def idle_rise_pose(t, T4, cycles):
    P = rest_pose(t, cycles)
    a, b, c, d = T4
    Y0 = -L_AIR / 2.0

    def rise(cb):
        lag = IDLE_RISE_LAG * cb
        return sstep(a + lag, b + lag, t) * (1.0 - sstep(c + lag, d + lag, t))

    hold = min(1.0, max(0.0, (t - a) / (d - a)))

    def fn(cb, z0):
        r = rise(cb)
        wave = IDLE_WAVE[0] * math.sin(TAU * (cb / IDLE_WAVE[1] - IDLE_WAVE[2] * hold)) * r
        arch = 0.35 * math.sin(math.pi * min(1.0, max(0.0, cb / L_AIR))) * r
        return np.array([0.0, Y0 + cb, z0 + (IDLE_HOVER - z0) * r + wave + arch])
    J_air = air_joints(fn)
    J_air["head"][0] = J_air["head"][0] + UP * 0.35 * rise(0.0)            # nose up while flying
    anchor_c = {"head": 0.0, "mid": C_MID[0], "tail": C_CAP}
    s = {}
    for pc in PIECES:
        lag = IDLE_RISE_LAG * anchor_c[pc]
        up_ = sstep(a + lag - 0.015, a + lag + 0.05, t)
        down_ = spring_between(t, d + lag - 0.02, d + lag + 0.05, IDLE_FRAMES / K.FPS)
        s[pc] = up_ * (1.0 - down_) if t < d + lag - 0.02 else (1.0 - down_)
        P["J"][pc] = blend_pts(P["J"][pc], J_air[pc], s[pc])       # from the (bobbing) paused rest
    P["keys"]["flight"] = float(np.clip(s["tail"], 0.0, 1.0))
    for pc in PIECES:
        lag = IDLE_RISE_LAG * anchor_c[pc]
        land = t - (d + lag - 0.035)
        splat = WALK_LAND * 0.8 * math.exp(-land * 30.0) * math.sin(min(math.pi, land * 60.0)) if land > 0 else 0.0
        split_squash(P, pc, P["keys"]["sq_" + pc] * (1.0 - min(1.0, max(0.0, s[pc]))) + splat, s[pc])
    P["keys"]["tuck_mid"] = float(np.clip(s["mid"], 0.0, 1.0))
    # droplets: float up under the serpent, then back down to their spots
    for i in range(len(DROPLETS)):
        r = sstep(a + 0.02 + 0.01 * i, b + 0.02 * i, t) * (1.0 - sstep(c + 0.01 * i, d + 0.02 * i, t))
        p0, s0 = P["drops"][i]
        tgt = np.array([p0[0] * 0.6, Y0 + (i + 0.5) / len(DROPLETS) * L_AIR, IDLE_HOVER - 1.1 + 0.25 * math.sin(TAU * (3 * t + 0.3 * i))])
        P["drops"][i] = (p0 + (tgt - p0) * r, s0)
    P["glow"] = 1.0 + GLOW_PULSE["idle"] * max(s.values())
    P["_s"] = s
    return P


def idle_pose(t):
    tb, tr = IDLE_T["ball"], IDLE_T["rise"]
    if tb[0] <= t <= tb[3]:
        return ball_pose(t, tb, cycles=2)
    if tr[0] - 0.03 <= t < 1.0:
        return idle_rise_pose(t, tr, cycles=2)
    return rest_pose(t, cycles=2)


# ---- the WALK: bounding leaps, goops chasing each other (world frame; the in-place shift comes last)
WALK_D = WALK_DLS + WALK_SEP_HEAD                         # per-cycle travel: rest -> the same rest D ahead
WALK_TMOVE = WALK_MOVE_END - WALK_LAUNCH - WALK_LAG * L_AIR
CYC_W = WALK_FRAMES / K.FPS
REST_Y_EXT = {pc: (float(REST_V[pc][:, 1].min()), float(REST_V[pc][:, 1].max())) for pc in PIECES}
# v5 gather: from the paused rest the pieces pull together into the COMBINED serpent (the v3 assembly: goo-into-goo at
# both joins) slid forward so the snout keeps its rest y -- the arch + head drop back onto the mound while the mound +
# tail chase forward under them (anticipation); the launch leaves from there
GATHER_K = float(JC["head"][0][1] - CENTER[1] - JR["head"][0][1])
JG = {pc: JC[pc] - CENTER + FWD * GATHER_K for pc in PIECES}
WALK_TRACE = []


def hop(tau):
    """v5 floaty hop profile, 0 at take-off and touch-down, 1 at the apex: 1 - |2 tau - 1|^WALK_FLOAT."""
    return 1.0 - abs(2.0 * tau - 1.0) ** WALK_FLOAT if 0.0 < tau < 1.0 else 0.0


def walk_disp(x):
    return 1.0 - (1.0 - x) ** 2.4                         # explosive launch, sliding stop


def walk_point(cb, z0, t):
    tl = WALK_LAUNCH + WALK_LAG * cb
    x = min(1.0, max(0.0, (t - tl) / WALK_TMOVE))
    u = WALK_DLS * walk_disp(x)
    tau = (t - tl) / WALK_AIR
    h = WALK_HOP * hop(tau)
    env = math.sin(math.pi * tau) if 0.0 < tau < 1.0 else 0.0
    ph = (t - WALK_LAUNCH) / (WALK_MOVE_END - WALK_LAUNCH)
    wave = WALK_WAVE[0] * math.sin(TAU * (cb / WALK_WAVE[1] - WALK_WAVE[2] * ph)) * env
    return np.array([0.0, Y_SNOUT + cb - u, z0 + h + wave]), tau


def walk_pose(t):
    P = rest_pose(t, cycles=1)
    J_air = air_joints(lambda cb, z0: walk_point(cb, z0, t)[0])
    anchor_c = {"head": 0.0, "mid": C_MID[0], "tail": C_CAP}
    s, ground = {}, {}
    for pc in PIECES:
        tl = WALK_LAUNCH + WALK_LAG * anchor_c[pc]
        up_ = sstep(tl - 0.012, tl + 0.05, t)                # the rest shape lifts straight into the arc
        a_s, b_s = WALK_SEP[pc]
        down_ = spring_between(t, a_s, b_s, CYC_W)
        s[pc] = up_ * (1.0 - down_) if t < a_s else 1.0 - down_
        if t < a_s:                                        # before landing: paused rest -> gathered serpent
            ground[pc] = blend_pts(JR[pc], JG[pc], spring_between(t, *WALK_GATHER[pc], CYC_W))
        else:                                              # after: the same paused rest one leap ahead
            ground[pc] = JR[pc] + FWD * WALK_D
        P["J"][pc] = blend_pts(ground[pc], J_air[pc], s[pc])
    P["keys"]["flight"] = float(np.clip(s["tail"], 0.0, 1.0))
    # squash: anticipation crouch -> launch stretch -> landing splat (per piece, at its own launch / landing)
    for pc in PIECES:
        tl = WALK_LAUNCH + WALK_LAG * anchor_c[pc]
        crouch = WALK_SQUASH * sstep(tl - 0.11, tl - 0.012, t) * (1.0 - sstep(tl - 0.012, tl + 0.01, t))
        stretch = -0.45 * math.sin(math.pi * min(1.0, max(0.0, (t - tl + 0.005) / 0.05)))
        land_t = tl + WALK_AIR
        lu = (t - land_t) * CYC_W
        splat = WALK_LAND * math.exp(-lu * 5.5) * math.sin(min(math.pi, lu * 30.0)) if lu > 0 else 0.0
        a_s, b_s = WALK_SEP[pc]
        arrive = (t - b_s + 0.07) * CYC_W
        settle = 0.35 * math.exp(-arrive * 7.0) * math.sin(min(TAU, arrive * 26.0)) if arrive > 0 else 0.0
        bw = 1.0 - sstep(0.02, 0.09, t) * (1.0 - sstep(0.90, 0.99, t))       # resting wobble only at the loop ends
        total = P["keys"]["sq_" + pc] * bw + crouch + (stretch if abs(t - tl) < 0.06 else 0.0) + splat + settle
        split_squash(P, pc, total, s[pc])
    P["keys"]["tuck_mid"] = float(np.clip(s["mid"], 0.0, 1.0))
    # droplets hop along after the landing, one after another
    for i in range(len(DROPLETS)):
        t0 = 0.58 + 0.05 * i
        u = min(1.0, max(0.0, (t - t0) / 0.16))
        p = DROP_REST[i] + FWD * WALK_D * sstep(0.0, 1.0, u)
        p = p + UP * 1.3 * 4.0 * u * (1.0 - u)
        P["drops"][i] = (p, P["drops"][i][1] * (1.0 + 0.25 * math.sin(math.pi * u)))
    # shedding: the launch splat left under the head's take-off, drips falling off the flying body
    shed = {}
    su = t - WALK_LAUNCH
    if 0.0 < su < 0.55:
        grow_ = spring_step(su * CYC_W, 3.0, 0.5)
        absorb = 1.0 - sstep(0.18, 0.55, su)
        sxz = max(0.02, grow_ * absorb)
        shed["splat"] = (np.array([0.0, Y_SNOUT + 0.6, 0.0]), sxz, max(0.02, absorb))
    g_ = 34.0                                              # units / s^2 (cartoon gravity)
    for i, cb in enumerate((C_MID[1], C_MID[4], C_TAIL[3])):
        td = WALK_LAUNCH + WALK_LAG * cb + (0.48 + 0.08 * i) * WALK_AIR          # torn off just past the apex
        dt = (t - td) * CYC_W
        if dt <= 0.0:
            continue
        p0, _ = walk_point(cb, 0.35, td)
        p1, _ = walk_point(cb, 0.35, td + 0.004)
        v = (p1 - p0) / (0.004 * CYC_W)
        p0 = p0 - UP * 0.55
        z = p0[2] + v[2] * dt - 0.5 * g_ * dt * dt
        r = SHED_DRIP_R
        if z > r:
            pos = p0 + np.array([0.0, v[1] * dt, 0.0]); pos[2] = z
            k = min(1.0, dt * 14.0)
            shed["shed.%d" % i] = (pos, 0.2 + 0.8 * k, (0.2 + 0.8 * k) * 1.25)
        else:
            disc = v[2] * v[2] + 2.0 * g_ * (p0[2] - r)
            dt_hit = (v[2] + math.sqrt(max(disc, 0.0))) / g_
            pos = p0 + np.array([0.0, v[1] * dt_hit, 0.0]); pos[2] = 0.0
            after = dt - dt_hit
            flat = min(1.0, after * 20.0)
            fade = max(0.0, 1.0 - after / 0.55)
            if fade > 0.0:
                shed["shed.%d" % i] = (pos + UP * r * 0.2 * (1 - flat), (1.0 + 0.9 * flat) * fade, (1.0 - 0.7 * flat) * fade)
    P["shed"] = shed
    P["glow"] = 1.0 + GLOW_PULSE["walk"] * max(0.0, min(1.0, max(s.values())))
    # in place: the game moves the unit D per cycle toward -Y
    dv = -FWD * WALK_D * t
    P["J"] = shift(P["J"], dv)
    P["drops"] = [(p + dv, s_) for p, s_ in P["drops"]]
    P["shed"] = {k: (v[0] + dv, v[1], v[2]) for k, v in P["shed"].items()}
    P["_s"] = s
    WALK_TRACE.append({"t": t, "s": dict(s)})
    return P


def ball_clip_pose(t):
    return ball_pose(t, BALL_TIMING, cycles=1)


# =========================================================================== 9. authoring (two passes: gaps -> bridges)
JOINS = {"crown": ("mid", "tail"), "head": ("mid", "head")}


def pose_basis(M):
    out = {}
    for n in DEFORM:
        basis = REST_INV[n] @ M[n]                          # parent = root at rest identity
        loc, q, sc = Matrix(basis.tolist()).decompose()
        out[n] = (loc, q, sc)
    return out


def bridge_tracks(frames_data, frames, oks):
    """frames_data[f] = {join: (gap, p_mid, p_other)} -> per frame {bridge bone: (p, d, L, th) or None}, bulge values,
    and the event list. Runs the state machine twice round the loop (the first lap only settles the wrap state)."""
    tracks = [dict() for _ in range(frames + 1)]
    bulge = {jn: np.zeros(frames + 1) for jn in JOINS}
    events = []
    EPS = 0.03
    for jn in JOINS:
        g = np.array([frames_data[f][jn][0] for f in range(frames + 1)])
        pa = np.array([frames_data[f][jn][1] for f in range(frames + 1)])
        pb = np.array([frames_data[f][jn][2] for f in range(frames + 1)])
        ker = np.array([1, 2, 3, 2, 1], float); ker /= ker.sum()
        n_ = frames
        pa_s = np.array([sum(ker[k] * pa[(f + k - 2) % n_] for k in range(5)) for f in range(frames + 1)])
        pb_s = np.array([sum(ker[k] * pb[(f + k - 2) % n_] for k in range(5)) for f in range(frames + 1)])
        state, since, L_snap = ("merged" if g[0] <= EPS else "open"), 99, 0.0
        imp = np.zeros(frames + 1)
        for lap in range(2):
            for f in range(frames):
                prev = state
                gp = g[(f - 1) % frames]
                if not oks[f]:
                    state = "merged"
                    continue
                if g[f] <= EPS:
                    state = "merged"
                    if prev in ("open", "reach", "recoil") and lap == 1:
                        events.append({"join": jn, "event": "contact", "frame": f + 1})
                        imp[f] += 1.0
                elif prev in ("merged", "stretch"):
                    if g[f] < BR_SNAP_GAP:
                        state = "stretch"
                    else:
                        state, since = "recoil", 0
                        L_snap = float(np.linalg.norm(pa_s[f] - pb_s[f])) / 2.0
                        if lap == 1:
                            events.append({"join": jn, "event": "snap", "frame": f + 1, "gap": round(float(g[f]), 4)})
                            imp[f] += 0.6
                elif prev == "recoil" and since < BR_RECOIL_FRAMES:
                    state = "recoil"; since += 1
                elif g[f] < BR_REACH_GAP and g[f] < gp - 1e-4:
                    state = "reach"
                else:
                    state = "open"
                if lap == 0:
                    continue
                d = pb_s[f] - pa_s[f]
                dist = float(np.linalg.norm(d))
                dn = unit(d) if dist > 1e-6 else UP
                a_, b_ = "bridge.%s.a" % jn, "bridge.%s.b" % jn
                if state == "stretch":
                    th = float(np.clip((0.45 / (0.45 + g[f])) ** BR_NECK, 0.3, 1.0))
                    L = dist / 2.0 + 0.12
                    tracks[f][a_] = (pa_s[f], dn, L, th); tracks[f][b_] = (pb_s[f], -dn, L, th)
                    bulge[jn][f] += 0.45 * min(1.0, g[f] / BR_SNAP_GAP)
                elif state == "recoil":
                    k = since / K.FPS
                    rr = max(0.0, 1.0 - spring_step(k, 5.0, 0.35))
                    L = max(0.04, L_snap * rr)
                    th = 0.8 + 0.4 * math.sin(math.pi * min(1.0, since / BR_RECOIL_FRAMES))
                    if since < BR_RECOIL_FRAMES:
                        tracks[f][a_] = (pa_s[f], dn, L, th); tracks[f][b_] = (pb_s[f], -dn, L, th)
                elif state == "reach":
                    r = float(np.clip(1.0 - g[f] / BR_REACH_GAP, 0.0, 1.0)) ** 0.7
                    L = (dist / 2.0 + 0.1) * r
                    if L > 0.03:
                        tracks[f][a_] = (pa_s[f], dn, L, 0.75); tracks[f][b_] = (pb_s[f], -dn, L, 0.75)
        # contact / snap impulses -> bulge wobble (loops round)
        for f in range(frames):
            if imp[f] > 0:
                for k in range(0, 18):
                    ff = (f + k) % frames
                    bulge[jn][ff] += imp[f] * math.exp(-k / 5.0) * math.cos(TAU * k / 11.0)
        bulge[jn][frames] = bulge[jn][0]
        for f in range(frames + 1):
            bulge[jn][f] = float(np.clip(bulge[jn][f], -0.35, 1.0))
    for tr_ in tracks:                                    # strand roots never dip their sphere under the floor
        for bn, (p, d, L, th) in list(tr_.items()):
            tr_[bn] = (np.array([p[0], p[1], max(p[2], BR_ROOT_R * th)]), d, L, th)
    tracks[frames] = tracks[0]
    return tracks, bulge, events


sock = bsdf.inputs["Emission Strength"]
base_es = sock.default_value


def key_slot_assign(act):
    K.assign_action(KEY, act)


def author(name, frames, pose_fn):
    t_a = time.time()
    FLOOR_LIFTS.clear()
    poses = [settle_floor(pose_fn(((f - 1) / frames) % 1.0)) for f in range(1, frames + 2)]   # last frame = t 0 = first
    lifts = list(FLOOR_LIFTS)
    # pass 1: surface gaps per join per frame (own skinning, bridges excluded)
    fdata = []
    for f, P in enumerate(poses):
        M = all_mats(P)
        keys = {k: P["keys"].get(k, 0.0) for k in KEY_ORDER}
        row = {}
        full = {pc: skin_piece(pc, M, keys) for pc in PIECES}
        for jn, (pa, pb) in JOINS.items():
            g, xa, xb = piece_gap(full[pa][::3], full[pb], pb)
            row[jn] = (g, xa if xa is not None else full[pa][0], xb if xb is not None else full[pb][0])
        fdata.append(row)
    tracks, bulge, events = bridge_tracks(fdata, frames, [P.get("bridge_ok", True) for P in poses])
    # v5 floor fix (the v4 caveat: min z -0.023 idle / -0.011 walk): the bulge keys and the bridge strands are only
    # known after pass 1, past the floor solver -- re-settle every frame with its bulge weights, and lift any strand
    # whose posed goo dips under the floor
    relift = {"pieces_frames": 0, "pieces_max": 0.0, "strand_frames": 0, "strand_max": 0.0}
    for f, P in enumerate(poses):
        for jn in JOINS:
            P["keys"]["bulge_" + jn] = float(np.clip(bulge[jn][f], -1.0, 1.0))
        n0 = len(FLOOR_LIFTS)
        settle_floor(P)
        lift_p = FLOOR_LIFTS[n0]
        del FLOOR_LIFTS[n0:]
        if lift_p > 0:
            relift["pieces_frames"] += 1; relift["pieces_max"] = max(relift["pieces_max"], lift_p)
        tr_f = tracks[f] if f < frames else tracks[0]
        if tr_f:
            P["bridges"] = tr_f
            M = all_mats(P)
            for bn, (p, d, L, th) in list(tr_f.items()):
                Vb = VALL[sl(bn)]
                z = float(((M[bn] @ REST_INV[bn])[:3, :3] @ Vb.T).T[:, 2].min() + (M[bn] @ REST_INV[bn])[2, 3])
                if z < 0.0:
                    tr_f[bn] = (p + UP * -z, d, L, th)
                    relift["strand_frames"] += 1; relift["strand_max"] = max(relift["strand_max"], -z)
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    for f in range(1, frames + 2):
        P = poses[f - 1]
        P["bridges"] = tracks[f - 1]
        M = all_mats(P)
        B = pose_basis(M)
        for n in DEFORM:
            loc, q, sc = B[n]
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pb = pose[n]
            pb.location = loc; pb.rotation_quaternion = q; pb.scale = sc
            pb.keyframe_insert("location", frame=f, group=n)
            pb.keyframe_insert("rotation_quaternion", frame=f, group=n)
            pb.keyframe_insert("scale", frame=f, group=n)
        pr = pose["root"]
        pr.location = (0, 0, 0); pr.rotation_quaternion = (1, 0, 0, 0); pr.scale = (1, 1, 1)
        for ch in ("location", "rotation_quaternion", "scale"):
            pr.keyframe_insert(ch, frame=f, group="root")
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, frames + 1
    act.use_cyclic = True
    # shape keys ride the SAME action (KEY slot)
    key_slot_assign(act)
    kvals = {k: [] for k in KEY_ORDER}
    for f in range(1, frames + 2):
        P = poses[f - 1]
        for k in KEY_ORDER:
            v = P["keys"].get(k, 0.0)
            if k.startswith("bulge_"):
                v = bulge[k[6:]][f - 1]
            v = float(np.clip(v, -1.0, 1.0))
            kvals[k].append(v)
            KEY.key_blocks[k].value = v
            KEY.key_blocks[k].keyframe_insert("value", frame=f)
    KEY.animation_data.action = None
    for k in KEY_ORDER:
        KEY.key_blocks[k].value = 0.0
    out = {"frames": frames, "author_seconds": round(time.time() - t_a, 1),
           "floor_contact_lift": {"frames_lifted": int(sum(1 for x in lifts if x > 0)), "max": round(max(lifts), 4)},
           "floor_relift_after_bulge_and_strands": {k: (round(v, 5) if isinstance(v, float) else v) for k, v in relift.items()}}
    glow = [P["glow"] for P in poses]
    try:
        K.assign_action(nt, act)
        for f, g in enumerate(glow, start=1):
            sock.default_value = base_es * g
            sock.keyframe_insert("default_value", frame=f)
        out["glow"] = {"socket": "Principled BSDF.Emission Strength", "base": base_es,
                       "range": [round(base_es * min(glow), 4), round(base_es * max(glow), 4)]}
    except Exception as exc:  # noqa: BLE001
        out["glow"] = {"error": repr(exc)}
    nt.animation_data.action = None
    sock.default_value = base_es
    out["slots"] = [s_.identifier for s_ in act.slots]
    out["shape_key_range"] = {k: [round(min(v), 4), round(max(v), 4)] for k, v in kvals.items()}
    out["bridge_events"] = events
    out["gap_track"] = {jn: [round(float(fdata[f][jn][0]), 4) for f in range(frames + 1)] for jn in JOINS}
    out["bridge_visible_frames"] = {b: int(sum(1 for f in range(frames) if b in tracks[f])) for b in BRIDGE_BONES}
    return act, out, poses


def eval_mesh():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


def bind_clip(act):
    K.assign_action(rig, act)
    K.assign_action(KEY, act)


def unbind():
    rig.animation_data.action = None
    if KEY.animation_data:
        KEY.animation_data.action = None
    for k in KEY_ORDER:
        KEY.key_blocks[k].value = 0.0


LIVE_ISL = PIECES + DROP_BONES


def measure(act, frames):
    bind_clip(act)
    first = last = None
    minz, root_off = 1e9, 0.0
    ext_lo, ext_hi = np.full(3, 1e9), np.full(3, -1e9)
    per = {k: {"minz": [], "maxz": [], "cy": []} for k in PIECES + ["all"]}
    for f in range(1, frames + 2):
        scene.frame_set(f)
        C = eval_mesh()
        if f == 1:
            first = C
        if f == frames + 1:
            last = C
        minz = min(minz, float(C[:, 2].min()))
        ext_lo = np.minimum(ext_lo, C.min(0)); ext_hi = np.maximum(ext_hi, C.max(0))
        root_off = max(root_off, (rig.matrix_world @ pose["root"].head).length)
        for k in PIECES:
            X = C[sl(k)]
            per[k]["minz"].append(float(X[:, 2].min())); per[k]["maxz"].append(float(X[:, 2].max()))
            per[k]["cy"].append(float(X[:, 1].mean()))
    unbind()
    seam = float(np.linalg.norm(first - last, axis=1).max()) * 1000
    return {"frames": frames + 1, "cycle_frames": frames, "cycle_s": round(frames / K.FPS, 4),
            "seam_first_last_max_mm": round(seam, 6), "min_z": round(minz, 6), "root_offset_max": round(root_off, 9),
            "extent": {"width": round(float(ext_hi[0] - ext_lo[0]), 4), "length": round(float(ext_hi[1] - ext_lo[1]), 4),
                       "height": round(float(ext_hi[2] - ext_lo[2]), 4)}}, per


t_anim = time.time()
act_idle, idle_x, idle_poses = author("idle", IDLE_FRAMES, idle_pose)
act_walk, walk_x, walk_poses = author("walk", WALK_FRAMES, walk_pose)
act_ball, ball_x, ball_poses = author("ball", BALL_FRAMES, ball_clip_pose)
unbind()
m_idle, per_idle = measure(act_idle, IDLE_FRAMES)
m_walk, per_walk = measure(act_walk, WALK_FRAMES)
m_ball, per_ball = measure(act_ball, BALL_FRAMES)

# ---- walk: the launch-cycle numbers
fr_w = np.arange(1, WALK_FRAMES + 2)
tw = (fr_w - 1) / WALK_FRAMES
hmin = np.array(per_walk["head"]["minz"]); hmax = np.array(per_walk["head"]["maxz"])
allmin = np.min([per_walk[k]["minz"] for k in PIECES], 0)
tmin = np.array(per_walk["tail"]["minz"])
air_tail = tmin > 0.05                                  # the grounded mound + tail leave the floor only in the leap
air_all = allmin > 0.05
head_floor = hmin < 0.05                                # v5: the head touches the floor only at the landing splat
crouch_lo = int(round(WALK_CROUCH[0] * WALK_FRAMES)) + 1
crouch_f = crouch_lo - 1 + int(np.argmin(hmax[crouch_lo - 1: int(WALK_LAUNCH * WALK_FRAMES) + 2])) + 1
rest_head_h = float(hmax[crouch_lo - 1])                # the gathered head top as the crouch starts
AIR_S = WALK_AIR * CYC_W


def dwell_s(frac, p, air_s):
    """seconds of one body point's hop spent at or above frac x the apex (profile 1 - |2 tau - 1|^p)."""
    return (1.0 - frac) ** (1.0 / p) * air_s


V4_WAS = {"cycle_frames": 56, "per_body_point_air_s": 0.70, "hop_profile": "parabola (p = 2)",
          "apex_dwell_s": {"ge_90pct": round(dwell_s(0.9, 2.0, 0.70), 4), "ge_75pct": round(dwell_s(0.75, 2.0, 0.70), 4)},
          "launch_vertical_speed_u_per_s": round(4.0 * WALK_HOP / 0.70, 3),
          "measured": {"source": "v4 rigged blend probed 2026-09-26 (rig + Key slots bound, every frame)",
                       "whole_body_off_floor_frames": 13, "tail_off_floor_frames": 15,
                       "head_min_z_ge_90pct_of_max_frames": 7, "head_min_z_ge_75pct_of_max_frames": 10,
                       "head_max_clearance": 2.7816}}
world_cy = np.array(per_walk["head"]["cy"]) - WALK_D * tw                     # local = world + D t (toward +Y)
final_cy = float(REST_PIECE_V["head"][:, 1].mean()) - WALK_D
after = tw > WALK_SEP["head"][0]
over = float(np.max(final_cy - world_cy[after]))                                    # past the final spot (toward -Y)
sep_travel = WALK_SEP_HEAD
wk = walk_x["shape_key_range"]
m_walk["launch_cycle"] = {
    "anticipation_squash": {"key_weight_peak": WALK_SQUASH, "height_drop_pct_by_key": round(100 * SQUASH_AMT * WALK_SQUASH, 2),
                            "head_top_gathered": round(rest_head_h, 4), "head_top_at_crouch": round(float(hmax[crouch_f - 1]), 4),
                            "head_top_drop_pct_measured": round(100 * (1 - float(hmax[crouch_f - 1]) / rest_head_h), 2),
                            "crouch_frame": crouch_f},
    "launch_frame_head": 1 + int(round(WALK_LAUNCH * WALK_FRAMES)),
    "air_time": {"per_body_point_s": round(AIR_S, 4), "per_body_point_frames": round(WALK_AIR * WALK_FRAMES, 2),
                 "hop_profile": "1 - |2 tau - 1|^%.2f" % WALK_FLOAT,
                 "apex_dwell_s": {"ge_90pct": round(dwell_s(0.9, WALK_FLOAT, AIR_S), 4),
                                  "ge_75pct": round(dwell_s(0.75, WALK_FLOAT, AIR_S), 4)},
                 "launch_vertical_speed_u_per_s": round(2.0 * WALK_FLOAT * WALK_HOP / AIR_S, 3),
                 "measured": {"whole_body_off_floor_frames": int(air_all.sum()), "tail_off_floor_frames": int(air_tail.sum()),
                              "head_min_z_ge_90pct_of_max_frames": int((hmin >= 0.9 * hmin.max()).sum()),
                              "head_min_z_ge_75pct_of_max_frames": int((hmin >= 0.75 * hmin.max()).sum()),
                              "head_on_floor_frames": [int(x) for x in fr_w[head_floor]]},
                 "v4_was": V4_WAS},
    "arc_height": {"hop_apex_per_point": WALK_HOP, "vertical_S_wave_amp": WALK_WAVE[0],
                   "head_max_clearance": round(float(hmin.max()), 4), "head_top_max": round(float(hmax.max()), 4),
                   "body_top_max": round(float(np.max([per_walk[k]["maxz"] for k in PIECES])), 4)},
    "leap": {"travel_per_body_point": WALK_DLS, "cycle_travel_D": round(WALK_D, 4),
             "lag_head_to_tail_frames": round(WALK_LAG * L_AIR * WALK_FRAMES, 2),
             "implied_speed_units_per_s": round(WALK_D / CYC_W, 4),
             "implied_speed_m_per_s_at_cell_fit": round(WALK_D / CYC_W * report["measure"]["export_cell_fit_report_only"]["scale"], 4)},
    "landing": {"splat_key_weight": WALK_LAND, "splat_height_drop_pct": round(100 * SQUASH_AMT * WALK_LAND, 2),
                "head_overshoot_past_final_spot_units": round(over, 4),
                "head_overshoot_pct_of_run_apart": round(100 * over / sep_travel, 2),
                "spring_hz_damping": WALK_SPRING,
                "spring_theory_overshoot_pct": round(100 * math.exp(-WALK_SPRING[1] * math.pi / math.sqrt(1 - WALK_SPRING[1] ** 2)), 2)},
    "shape_key_ranges": wk}
m_walk["motion"] = {"gather": WALK_GATHER, "gather_rule": "paused rest -> the combined serpent slid %.4f forward "
                    "(snout keeps its rest y): arch + head drop onto the mound, the mound + tail chase forward" % GATHER_K,
                    "crouch": WALK_CROUCH, "launch": WALK_LAUNCH, "lag_per_unit": WALK_LAG, "air": WALK_AIR,
                    "float": WALK_FLOAT,
                    "move_end": WALK_MOVE_END, "hop": WALK_HOP, "wave": WALK_WAVE, "separate": WALK_SEP,
                    "head_runs_ahead": WALK_SEP_HEAD, "in_place": "root at the origin; the unit's rest spot advances "
                    "D per cycle toward -Y (the game glides it)", **{k: v for k, v in walk_x.items() if k not in ("gap_track",)}}
m_walk["min_z_grounded_frames"] = {"first": round(float(allmin[0]), 5), "last": round(float(allmin[-1]), 5)}
# ---- idle + ball: beats and the frames the renders use
fr_i = lambda t: 1 + int(round(t * IDLE_FRAMES))
IDLE_BALL_HOLD = fr_i(0.5 * (IDLE_T["ball"][1] + IDLE_T["ball"][2]))
IDLE_FLY = fr_i(0.5 * (IDLE_T["rise"][1] + IDLE_T["rise"][2]))
BALL_HOLD_FRAME = 1 + int(round(0.5 * (BALL_TIMING[1] + BALL_TIMING[2]) * BALL_FRAMES))
bt = [b for b in BALL_TRACE if abs(b["t"] - (BALL_HOLD_FRAME - 1) / BALL_FRAMES) < 1e-9]
m_idle["beats"] = {"ball": {"frames": [fr_i(x) for x in IDLE_T["ball"]], "hold_frame": IDLE_BALL_HOLD},
                   "flying_S": {"frames": [fr_i(x) for x in IDLE_T["rise"]], "hover_frame": IDLE_FLY,
                                "hover": IDLE_HOVER, "wave": IDLE_WAVE, "rise_lag_per_unit": IDLE_RISE_LAG},
                   "loop_choice": "one idle loop stages both beats (artist: 'he idles between turning into a ball and "
                                  "the flying s shape'): rest -> ball (+ bounces) -> rest -> flying S -> rest; 8 s. The "
                                  "'ball' clip keeps the ball round trip on its own for states that want only that beat."}
m_idle.update({k: v for k, v in idle_x.items() if k != "gap_track"})
m_ball["timing_frames"] = {"converge_start": 1 + int(round(BALL_TIMING[0] * BALL_FRAMES)),
                           "ball_formed": 1 + int(round(BALL_TIMING[1] * BALL_FRAMES)),
                           "release_start": 1 + int(round(BALL_TIMING[2] * BALL_FRAMES)),
                           "segments_again": 1 + int(round(BALL_TIMING[3] * BALL_FRAMES)), "hold_frame": BALL_HOLD_FRAME}
lifts = [b["lift"] for b in BALL_TRACE[-(BALL_FRAMES + 1):]]
sqs = [b["sq"] for b in BALL_TRACE[-(BALL_FRAMES + 1):]]
m_ball["bounce"] = {"bounces": BALL_BOUNCES, "hop_height": BALL_BOUNCE_H, "max_lift_measured": round(max(lifts), 4),
                    "squash_max": round(max(sqs), 4), "stretch_max": round(-min(sqs), 4),
                    "bounce_apex_frames": [int(i) + 1 for i in np.nonzero((np.diff(np.sign(np.diff(lifts))) < 0))[0] + 1]}
m_ball.update({k: v for k, v in ball_x.items() if k != "gap_track"})
m_ball["form"] = report["ball"]
rep["idle"] = {"status": "v5 PROPOSED (v4 beats kept -- 'he idles between turning into a ball and the flying s shape' -- "
                         "restaged from the v5 paused rest: 'we want some still flying as if he was paused in motion')",
               **m_idle}
rep["walk"] = {"status": "v5 PROPOSED (v4 leap kept -- launch, goops chasing, vertical S, splat -- made floatier: 'can we "
                         "make him floatier while jumping'; launches from and lands back into the paused rest)", **m_walk}
rep["ball"] = {"status": "v5 PROPOSED (v4 ball + bounces, from the v5 paused rest; red goo eyes in the ball's sockets)",
               **m_ball}
rep["gap_tracks"] = {"idle": idle_x["gap_track"], "walk": walk_x["gap_track"], "ball": ball_x["gap_track"]}
rep["transition_events"] = {"idle": idle_x["bridge_events"], "walk": walk_x["bridge_events"], "ball": ball_x["bridge_events"]}
rep["anim_seconds"] = round(time.time() - t_anim, 1)
rep["rest_layout"] = report["rest_layout"]
rep["flight_segment"] = report["flight_segment"]
unbind()
scene.frame_set(1)
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
scene.frame_start, scene.frame_end = 1, IDLE_FRAMES + 1
rig["conquest_rig"] = "magmoo v5 flat rig: root + head.0-1 | body.0-%d | tail.0-%d | drop.0-%d | ball | bridge x4 | " \
                      "shed.0-2 | splat (every deform bone a child of root)" % (MID_BONES - 1, TAIL_BONES, len(DROPLETS) - 1)
rig["conquest_idle_ball_hold_frame"] = IDLE_BALL_HOLD
rig["conquest_idle_fly_frame"] = IDLE_FLY
rig["conquest_ball_hold_frame"] = BALL_HOLD_FRAME
ob["conquest_clips"] = ["idle", "walk", "ball"]
ob["conquest_clip_status"] = "v5: idle = paused rest -> ball (+bounces) -> rest -> flying vertical S -> rest; walk = " \
                             "floaty bounding leap (gather, launch, goops chasing, splat, back into the paused rest); " \
                             "ball = goo-ball round trip with bounces; all PROPOSED; no attack/hit/death"
ob["conquest_shape_keys"] = "flight (mound -> smooth segment), sq_head/sq_mid/sq_tail (squash; negative = stretch), " \
                            "bulge_crown/bulge_head (torn ends bulge on contact); glTF morph targets, weights keyed per clip"


def full_digest():
    h = hashlib.sha256(geometry_digest().encode())
    for v in me.vertices:
        for g in v.groups:
            h.update(np.array([v.index, g.group, round(g.weight, 6)], np.float64).tobytes())
    for b in arm_data.bones:
        h.update(np.round(np.array(b.matrix_local), 6).astype(np.float32).tobytes())
    for act in (act_idle, act_walk, act_ball):
        for fc in K.action_fcurves(act):
            h.update(fc.data_path.encode()); h.update(bytes([fc.array_index]))
            h.update(np.round(np.array([k.co[:] for k in fc.keyframe_points]), 5).astype(np.float32).tobytes())
    return h.hexdigest()[:16]


rep["digest_full"] = full_digest()
rep["digest_geometry_colour_uv"] = report["digest_geometry_colour_uv"]
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True)

# =========================================================================== glb (identity scale, natural units)
for o in scene.objects:
    o.select_set(o is rig or o is ob)
bpy.context.view_layer.objects.active = rig
K.assign_action(rig, act_idle)
# the shape-key channels export per clip only from the Key's NLA (probed 2026-09-26, Blender 5.0 ACTIONS mode: a KEY
# slot that is not bound or stacked is skipped): one strip per clip on the Key, for the export only
kad = KEY.animation_data or KEY.animation_data_create()
kad.action = None
for act in (act_idle, act_walk, act_ball):
    tr = kad.nla_tracks.new(); tr.name = act.name
    st = tr.strips.new(act.name, 1, act)
    st.action_slot = next(s for s in act.slots if s.target_id_type == "KEY")
t = time.time()
import export_glb as _EG; _EG.add_glow_attr([o for o in scene.objects if o.select_get() and o.type == "MESH"])
bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                          export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                          export_skins=True, export_def_bones=False, export_morph=True, export_morph_animation=True,
                          export_attributes=True)
for tr in list(kad.nla_tracks):
    kad.nla_tracks.remove(tr)
rig.animation_data.action = None


def glb_carries(path):
    import struct
    data = open(path, "rb").read()
    L = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L])
    prims = [p for m in js.get("meshes", []) for p in m["primitives"]]
    attrs = {}
    for p in prims:
        for k, i in p["attributes"].items():
            if k.startswith("COLOR"):
                attrs.setdefault(k, set()).add(js["accessors"][i]["type"])
    nodes = js.get("nodes", [])
    anims = []
    for a in js.get("animations", []):
        w = [c for c in a["channels"] if c["target"].get("path") == "weights"]
        wk = []
        for c in w:
            acc = js["accessors"][a["samplers"][c["sampler"]]["output"]]
            wk.append({"node": nodes[c["target"]["node"]].get("name"), "values": acc["count"]})
        anims.append({"name": a.get("name"), "channels": len(a["channels"]), "weights_channels": wk})
    return {"materials": [{"name": m.get("name"), "alphaMode": m.get("alphaMode", "OPAQUE"),
                           "baseColorFactor": m.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1]),
                           "doubleSided": m.get("doubleSided", False)} for m in js.get("materials", [])],
            "colour_sets": {k: sorted(v) for k, v in attrs.items()},
            "morph_targets_per_primitive": [len(p.get("targets", [])) for p in prims],
            "morph_target_names": [m.get("extras", {}).get("targetNames") for m in js.get("meshes", [])],
            "animations": anims, "meshes": len(js.get("meshes", [])), "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0,
            "extensionsUsed": js.get("extensionsUsed", [])}


rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "sha256_16": hashlib.sha256(open(OUT_GLB, "rb").read()).hexdigest()[:16],
              "seconds": round(time.time() - t, 1), "carries": glb_carries(OUT_GLB),
              "structure": "armature identity + ONE skinned mesh (all islands) with %d morph targets, natural scale; "
                           "report-only cell fit %.5f" % (len(KEY_ORDER), k_fit)}
car = rep["glb"]["carries"]
rep["glb"]["morph_gate"] = {"targets": car["morph_target_names"], "every_clip_has_weights": all(a["weights_channels"] for a in car["animations"]),
                            "pass": bool(car["morph_targets_per_primitive"]) and all(n == len(KEY_ORDER) for n in car["morph_targets_per_primitive"])
                            and all(a["weights_channels"] for a in car["animations"]) and len(car["animations"]) == 3}


# =========================================================================== skins (palette swap, geometry untouched)
def region_sample():
    out = {}
    for nm in ("Col", "Glow"):
        names, rid, _ = PAL.read_regions(me)
        ls = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_start", ls)
        cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes[nm].data.foreach_get("color", cd)
        cd = cd.reshape(-1, 4)[ls, :3]
        out[nm] = {names[r]: [round(float(x), 4) for x in cd[rid == r].mean(0)] for r in np.unique(rid)}
    return out


def vpos_sha():
    return hashlib.sha256(np.round(np.array([v.co[:] for v in me.vertices]), 6).astype(np.float32).tobytes()).hexdigest()[:16]


geo0 = geometry_digest()
vpos0 = vpos_sha()
rep["skins"] = {"default": {"file": OUT_RIGGED, "palette": PAL.table(pal_default), "glow_tiers": report["glow_tiers"]["default"],
                            "region_faces": region_counts0, "region_mean_linear": region_sample(), "vertex_positions_sha": vpos0}}
for skin in SKINS:
    if skin == "default":
        continue
    pal = PAL.load(UNIT, skin)
    gt = glow_tiers(pal)
    assert gt["pass"], (skin, gt)
    counts = repaint(me, pal)
    PAL.apply_material(mat, pal); apply_alpha(mat, pal)
    out = OUT_RIGGED[:-6] + "__" + skin + ".blend"
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    rep["skins"][skin] = {"file": out, "palette": PAL.table(pal), "palette_files": pal["files"], "glow_tiers": gt,
                          "region_faces": counts, "region_mean_linear": region_sample(),
                          "geometry_colour_uv_digest": geometry_digest(), "vertex_positions_sha": vpos_sha()}
repaint(me, pal_default)
PAL.apply_material(mat, pal_default); apply_alpha(mat, pal_default)
rep["skins"]["repaint_proof"] = {
    "rule": "a skin is a pure palette swap: region face counts identical, vertex positions identical, colours differ only "
            "by the palette table; the default digest is restored after the swap",
    "default_digest_before": geo0, "default_digest_after_restore": geometry_digest()}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({"bones": rep["bone_count"], "digest_full": rep["digest_full"], "seconds": rep["seconds"]}))
print("IDLE", json.dumps({k: v for k, v in rep["idle"].items() if k not in ("bridge_events",)})[:3000])
print("WALK", json.dumps(rep["walk"]["launch_cycle"]), json.dumps({k: rep["walk"][k] for k in ("seam_first_last_max_mm", "min_z")}))
print("BALLCLIP", json.dumps({k: rep["ball"][k] for k in ("seam_first_last_max_mm", "min_z", "bounce", "timing_frames")}))
print("EVENTS", json.dumps(rep["transition_events"]))
print("GLB", json.dumps({k: rep["glb"][k] for k in ("bytes", "sha256_16", "morph_gate")}))
sys.stdout.flush()
os._exit(0)
