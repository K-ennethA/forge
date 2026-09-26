"""Supaoctto v3 (the octopus superhero) through the shared pipeline, one headless run.

    blender --background source-copies/newunit-supaoctto.blend --factory-startup --python improve/supaoctto_build.py -- \
        [--preview <out.blend>]          (geometry + webs + UV + regions + palette only: no bake, no rig -- fast look loop)
        [--digest-only <out.json>]       (the whole pipeline, saves NOTHING but the digest json: the two-run determinism probe)
        [--set NAME=value ...]           (exploration override of a tunable constant; the committed build uses none)
        [--tag NAME]                     (alternative build: every output is named supaoctto__NAME.*, no glb -- the
                                          starfish-mask renders use  --set MASK_STYLE='starfish' --tag starfish)

v3 (artist 2026-09-26, verbatim, binding): "the circle mouth should be more of smirk line when open like a confident hero,
and there should be nothing visible when its closed so a smirk opening when open and nothing when closed" / "would a belt
like this help supacctto design" / "can we also try a version of the mask more like this, star fish inspire to give the
aquatic hero vibe better". v3 on the approved v2 base:
  - the siphon dimple is GONE. The mouth is a slit cut along an asymmetric smirk line (the character's left corner turned
    up); at rest the two lips are exact duplicate vertices (the surface is watertight and unchanged: nothing visible) and a
    dark pocket sits BEHIND them inside the mantle. The morph target 'smirk' parts the lips into a thin dark gap. The dark
    'mouth' region is painted ONLY on the pocket faces, which are enclosed (invisible) while the lips are closed.
  - a BELT (design/reference/supaoctto-v3-belt-annotation.png): a band round the waist whose bottom edge dips at the front
    centre, and two side plates; raised plates fused into the body remesh (snug, no gap), its own palette region.
  - MASK_STYLE: "w" (the approved W-visor, committed default) or "starfish" (star arms radiating round the same lenses).

Reads (never writes): source-copies/newunit-supaoctto.blend (opened). Render meshes: Body (torso + 2 arm tentacles + 2 leg
tentacles, one shell, NEGATIVE z scale), Cape (4 cape tentacles joined at a shoulder yoke, a Y-mirror modifier makes the
two halves), Head (the mantle), Mask-Goggles, Icosphere (the mouth). 'Mask' is hidden in the view layer and is left out.
v2: Mask-Goggles and Icosphere are read ONLY as placement landmarks (goggle centre height -> visor, mouth centre -> siphon)
and never reach the outputs.

Outputs:
    improved/supaoctto.blend + .json        regular-tier retopo + built water webs, visor, neck, UVs, baked normal/AO, palette
    improved/textures/supaoctto_{normal,ao}.png
    rigged/supaoctto.blend + .json          + biped/tentacle rig, clips idle + walk (stride) + float
    rigged/supaoctto.glb                    identity-scale export (natural scale)

Artist v1 (design/review-log.md, verbatim): "the cape is tentancles ... in between the cape tentacles we want them connected
by water. he walks upright on two legs".
Artist v2 (2026-09-26, verbatim, binding): "lets have supacctoo be all blue - the darker one that exists on the tentacles,
it should all be the color / the color for the water is good but it should connect closer to the bottom of the full
length of the tentacle cape / for the googles lets do an orange on the outer permiter of the google and the inner a
complimenting color / can we also make the goggles look more like this or like the glasses look so hes more
superhero-esque / lets get rid of the mouth piece and just give a small circle hole in its place / can we also smooth out
the textures and align things to be btter placed, and give the head a thin neck so it connects better to the body"
Addendum: "chest emblem should be something more appropriate for an octopus superhero than just a circle, and he walks
with confidence like a stride / an animation can have him float up cross his arms, the cape flares a little bit like if
theres wind and then floats back down"

Pipeline:
  1. every render mesh to world space (evaluated), crumbs dropped, YAW_FIX_DEG = 180 applied to the DATA.
  2. v2 SCULPT PREP: Taubin smoothing of body/head/cape (SMOOTH_ITERS; "smooth out"), the cape's mirror plane moved onto
     the body midline (CAPE_CENTRE; v1 sat 0.25 to +X), the head raised NECK_LIFT and joined by a new thin NECK tube,
     the new angular W-VISOR plate built on the head (CDT of the W outline in a (u = arc, z) head frame, ray-projected onto
     the mantle, orange frame raised above two inset lenses), the mouth piece dropped (a siphon dimple is cut into the
     remeshed head in its place). Then v1's FIXES: the shorter leg tip stretched to the floor, the cape hem lifted.
  3. ANATOMY by geodesic ring tracing (unchanged from v1).
  4. WATER WEBS between neighbouring cape tentacles, v2 to WEB_V_EDGE of the tentacle length (v1 0.62).
  5. LOW: body shell (Body + Head + visor + neck) and cape shell remeshed + collapse-decimated SEPARATELY; the siphon
     dimple is pressed into the body remesh; the remeshed shells are the bake/cavity HIGH. Detail zones (visor, siphon,
     emblem) are subdivided so the colour cuts are crisp.
  6. regions: part Voronoi + SDF iso-contour cuts (W-visor outline, lenses, siphon disc, octopus sigil) -> palette.
  7. Smart UV + pack; bake normal + AO from the remeshed shells (web texels reset to flat).
  8. RIG: v1's + 4-bone web mid chains; each web mid bone inherits the mean drape of its two side tentacles at the same
     depth (web_follow) so the 93%-deep webs do not lag the cape and stretch their hem. Planted legs solve their IK so the
     lowest skinned tip vertex (the smoothed tip is rounded) touches z = 0: no floor dip.
  9. CLIPS: idle (v1), walk -> confident STRIDE, NEW float (rise, arms cross, cape flares, descend). Closed form, keyed
     every frame, envelopes zero at both ends -> exact seams.
 10. identity-scale glb export.
"""
import bpy, bmesh, sys, os, math, json, time, hashlib, ast, heapq, tempfile
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils.geometry import delaunay_2d_cdt

T0 = time.time()
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
import rigkit as K      # noqa: E402  (read-only use)
import palettes as PAL  # noqa: E402  (read-only use)

# =========================================================================== TUNABLE CONSTANTS
# (artist-facing names in the comments; a parameter tweak is a one-line edit + rerun). Lengths are sculpt units, final
# frame: front -Y, floor (the leg-tentacle tips at rest) z = 0. The sculpt is ~20.6 units tall; natural scale.
UNIT = "supaoctto"
CHAR_ID = "supaoctto"                 # roster id: not yet in Conquest (ship path deferred by the artist)
VERSION = "v3"
# ---- v3 knobs first (the artist's v3 asks) -------------------------------------------------------------------------------
MASK_STYLE = "w"                      # "mask style": "w" = the approved W-visor (COMMITTED DEFAULT) | "starfish" (the v3 variant)
# starfish mask, (u, dz) round the visor centre like the W (u = arc round the mantle, dz up): a five-armed star (one arm
# up the forehead, two swept out like the W's wing tips, two down-and-out under the eyes) with its own swept lenses
STAR_C_DZ = 0.15                      # "starfish centre height" above the visor centre
STAR_BODY = (1.50, 0.90)              # "starfish body": half-width / half-height of the central body the arms grow from
STAR_ARMS = [(90.0, 2.20, 0.55, 0.16), (15.0, 2.60, 0.58, 0.16), (165.0, 2.60, 0.58, 0.16),
             (-54.0, 2.05, 0.55, 0.16), (-126.0, 2.05, 0.55, 0.16)]
                                      # "starfish arms": (angle deg from +u, reach from the centre, root half-width, tip radius)
STAR_SMOOTH = 0.30                    # "starfish fillets": smooth-union radius where the arms meet the body
STAR_LENS = [(0.18, -0.24), (0.86, -0.30), (1.40, 0.40), (0.72, 0.38), (0.18, 0.12)]
                                      # "starfish lenses": the character's-left eye (mirrored for the right), swept up the side arm
# smirk mouth (a slit + pocket, opened by the 'smirk' morph target)
MOUTH_DZ = 0.0                        # "mouth height": offset from the removed siphon's centre height (v2 placement)
MOUTH_LINE = [(-0.52, 0.02), (-0.24, -0.04), (0.06, -0.05), (0.33, 0.02), (0.56, 0.19)]
                                      # "smirk line": (u, dz) right corner -> left corner; the character's LEFT corner (+u) up
MOUTH_GAP_LOWER = 0.085               # "smirk opening": the lower lip drops this much at the widest point ...
MOUTH_GAP_UPPER = 0.025               # ... the upper lip lifts this much ...
MOUTH_GAP_PEAK = 0.60                 # ... the widest point this far along the line (0 = right corner, 1 = the upturned left)
MOUTH_SOFT = 0.26                     # "lip softness": the mantle within this distance of the line moves with the lips
MOUTH_DEPTH = 0.16                    # "mouth depth": the dark pocket behind the lips (inside the mantle)
MOUTH_BAND = 0.40                     # detail zone half-width round the smirk line (covers the lip falloff)
DETAIL_CUTS_MOUTH = 3                 # ... each low edge there split into 4 (a smooth line)
SMIRK_KEY = "smirk"                   # the morph target's name (glTF target name)
FLOAT_SMIRK_T = (0.35, 0.40, 0.57, 0.62)   # "float smirk": opens after the arms cross, holds over the hover, shuts before they part
IDLE_SMIRK_T = None                   # "idle smirk beat": None = mouth shut all idle; (open0, open1, close0, close1) stages one
WALK_SMIRK_T = None                   # "walk smirk": None = shut
# belt (design/reference/supaoctto-v3-belt-annotation.png: side plates + a band whose bottom edge dips at the front centre)
BELT = True                           # "belt on/off"
BELT_TOP_FRAC = 0.395                 # "belt height": band top edge (hip -> neck fraction)
BELT_BOT_FRAC = 0.325                 # band bottom edge at the sides
BELT_DIP = 0.30                       # "belt centre dip": the bottom edge dips this much at the front centre ...
BELT_DIP_W = 0.62                     # ... over this half-width (arc units round the waist)
BELT_T = 0.12                         # "belt thickness" above the body surface (band)
PLATE_T = 0.17                        # "side plate thickness"
BELT_EMBED = 0.10                     # depth into the body (fused by the remesh: snug, no gap)
PLATE_Z = (0.205, 0.475)              # "side plate height": bottom / top (hip -> neck fraction)
PLATE_TH = (36.0, 86.0)               # "side plate span": inner / outer edge, degrees round the waist from the front centre
PLATE_FLARE = 7.0                     # the plate's top outer corner flares this many degrees further out
PLATE_TAPER = 7.0                     # the plate's inner edge moves this many degrees outward at the bottom
PLATE_ROUND = (0.30, 0.14)            # corner rounding bottom / top (arc units)
BELT_REGION_PAD = 0.05                # the belt colour region reaches this far past the outline (covers the plate rim wall)
BELT_IN_WEB_CLEARANCE = False        # the approved v2 webs keep their shape: the belt is NOT an obstacle to the web solve
                                      # (True pushed web.C 0.87 -> 1.55 and faceted it; measured with False: belt-to-cape/web
                                      # min gap 1.03 over every sampled idle/walk/float frame -- the webs never reach the belt)
# ---------------------------------------------------------------------------------------------------------------------------
YAW_FIX_DEG = 180.0                   # "facing fix": applied to the mesh DATA (goggles + mouth were at +Y)
VOXEL = 0.06                          # "retopo resolution" (voxel remesh size before decimation)
LOW_TRIS_BODY = 9500                  # "body detail" (decimation target, body shell)            v1 6200
LOW_TRIS_CAPE = 3600                  # "cape detail" (decimation target, cape tentacles + yoke) v1 2600
TRI_BUDGET = [3000, 24000]            # declared tier: regular unit; v2 window 22000, v3 +2000 for the belt + smirk detail
CRUMB_FRAC = 0.01                     # sculpt crumbs smaller than this fraction of their part are dropped
# v2 polish ("smooth out the textures and align things")
SMOOTH_ITERS = {"head": 150, "body": 30, "cape": 20}   # "surface smoothing": Taubin passes per sculpt part (0 = v1 clay lumps)
SMOOTH_LAMBDA, SMOOTH_MU = 0.5, -0.53 # Taubin pair (shrink-free)
LOW_RELAX_ITERS, LOW_RELAX_K = 4, 0.5 # "even facets": relax the decimated triangles over the high surface (0 = v1)
LOW_RELAX_SPIKE = 0.30                # ... vertices further than this x their mean edge from the neighbour centroid stay put
LOW_RELAX_FLOOR_KEEP = 1.2            # ... as do vertices this close above the shell's lowest point (the foot tips)
FACE_JITTER = 0.0                    # "texture mottling": per-face value jitter                  v1 0.08
CAVITY_K = 0.25                       # "cavity shading" depth                                    v1 0.40
CAPE_CENTRE = True                    # "centre the cape": its mirror plane onto the body midline (v1 sat 0.25 to +X)
# v2 neck ("give the head a thin neck")
NECK_LIFT = 0.8                       # "neck length": the head (and its visor) rise this much ...
NECK_R = 0.70                         # "neck thickness": ... joined to the shoulders by a neck tube of this radius
NECK_FLARE_BOT, NECK_FLARE_TOP = 0.55, 0.20   # extra radius flaring into the shoulders / the head
NECK_BURY_BODY, NECK_BURY_HEAD = 0.60, 0.90   # how far the tube runs into the torso / into the head
# v2 W-visor ("like the glasses look so hes more superhero-esque"); (u, dz): u = arc length round the head from the
# midline, dz = height above the visor centre (= the v1 goggles' centre height, raised with the head)
VISOR_TIP_U, VISOR_TIP_DZ = 2.90, 0.95      # "visor wing tips": the swept-up outer points
VISOR_DIP_DZ = 0.05                         # "visor top notch": the V dip at the top centre
VISOR_NOTCH_DZ = -0.40                      # "visor bridge notch": the small W notch at the bottom centre
VISOR_BOT_U, VISOR_BOT_DZ = 0.72, -0.85     # "visor lower points": the two bottom points of the W
VISOR_FRAME_W = 0.16                        # "orange frame width" (lens inset from the outline; the bridge is 2x)
VISOR_T_FRAME, VISOR_T_LENS = 0.18, 0.10    # plate height above the mantle: frame / lens (lens sits recessed)
VISOR_EMBED = 0.12                          # plate depth into the mantle (fused by the remesh: snug, no gap)
VISOR_DS = 0.045                            # outline sample spacing (CDT)
# v2 siphon ("a small circle hole") -- REMOVED in v3 (the smirk mouth replaces it; its centre height still places the mouth)
# v2 emblem ("something more appropriate for an octopus superhero than just a circle")
EMBLEM_Z_FRAC = 0.66                  # "chest emblem height" (hip -> neck)
EMBLEM_SIZE = 1.10                    # "chest emblem size" (sigil half-width, units)
EMB_DOME = ((0.0, 0.30), (0.36, 0.42))      # sigil mantle dome: centre, radii (emblem units)
EMB_STROKE = (0.105, 0.05)                  # sigil tentacle half-width root -> tip (emblem units)
EMB_TENTACLES = [                            # right-side tentacle curls (mirrored): points in emblem units
    [(0.24, -0.02), (0.50, -0.14), (0.76, -0.16), (0.92, -0.03), (0.90, 0.12), (0.77, 0.14), (0.72, 0.04)],
    [(0.15, -0.09), (0.31, -0.38), (0.47, -0.60), (0.65, -0.70), (0.79, -0.62), (0.77, -0.48), (0.66, -0.48)],
    [(0.05, -0.11), (0.08, -0.45), (0.13, -0.77), (0.26, -0.95), (0.41, -0.93), (0.44, -0.80), (0.35, -0.75)]]
DETAIL_CUTS = 1                       # detail zones (mask): each low edge split into DETAIL_CUTS + 1
DETAIL_CUTS_EMBLEM = 3                # ... the chest sigil (thin curling strokes need the finest triangles)
DETAIL_BAND = 0.30                    # ... on the faces within this distance of a colour boundary
VISOR_REGION_PAD = 0.05               # the orange frame region reaches this far past the outline (covers the plate's rim wall)
LEG_EQUALIZE = True                   # "level the feet": stretch the shorter leg tentacle's tip to the other's floor level
LEG_EQ_SPAN = 2.0                     # ... the stretch acts on this much of the leg above its tip
CAPE_TIP_CLEAR = 0.40                 # "cape hem height": the lowest cape tip hangs this far above the floor at rest
CAPE_LIFT_SPAN = 4.0                  # ... the lift eases in over this much of the cape's bottom
TRACE_DS = 0.4                        # anatomy trace ring width
TRACE_MERGE_K = 1.3                   # a ring spreading this much past the last three has left the tentacle
TRACE_MIN_RINGS = 8                   # (tip clubs/paddles grow fast: the merge test starts after this many rings)
ARM_ROOT_EXT = 0.8                    # "shoulder pivot": the arm chain starts this far inside the armpit ring
LEG_ROOT_EXT = 0.6                    # "hip pivot": the leg chain starts this far above the crotch ring
# water webs between the cape tentacles
WEB_V_ROOT = 0.0                      # "web top": starts this fraction down the tentacles (0 = at the yoke)
WEB_V_EDGE = 0.93                     # "web depth": reaches this fraction down the tentacles at the tentacles  v1 0.62
WEB_SCALLOP = 0.05                    # ... and this much less at the middle of each gap (the hem)              v1 0.16
WEB_BILLOW = 0.10                     # "web billow": outward bulge, x the gap width
WEB_THICK = 0.06                      # sheet thickness (closed thin shell: renders from both sides in any engine)
WEB_CLEAR = 0.15                      # minimum clearance from the body surface (the web is pushed back until clear)
WEB_NU, WEB_NW = 8, 18                # web grid: across the gap, down the web                                  v1 8, 12
WEB_RIM_W = 0.92                      # "water edge": web rows past this fraction are the bright rim (v1 0.88 of 0.62)
# colour regions
ARM_TIP_FRAC = 0.80                   # "hand tip": the arm tentacle past this fraction of its length
LEG_TIP_FRAC = 0.72                   # "foot tip": the leg tentacle past this fraction of its length
CAPE_TIP_FRAC = 0.88                  # "cape tip": the cape tentacle past this fraction of its length
UNDER_T = 0.35                        # "cape lining": cape surface facing the body more than this (normal . inward)
CUT_SNAP = 0.18                       # iso-cut: crossings this close to a vertex snap to it (no sliver triangles)
BAKE_CAGE = 0.08                      # bake cage extrusion
BAKE_RES = (1024, 512)                # normal, AO texture sizes
CELL_MAX_H, CELL_MAX_FP = 1.8, 1.9    # Conquest regular-unit ceilings -- REPORT ONLY (scale policy 2026-09-25)
# rig + weights
SPINE_FRACS = (0.0, 0.35, 0.70, 1.0)  # pelvis / spine / chest joints, hip -> neck
ARM_BONES, CAPE_BONES, WEB_BONES = 4, 5, 4                                                              # v1 web 3
LEG_KNEE_FRAC, LEG_ANKLE_FRAC = 0.45, 0.80
NECK_RAMP = (0.60, 0.60)              # "neck bend": head weight ramps from (body top - a) to (head bottom + b)
ROOT_BLEND = {"arm": 1.0, "leg": 0.9, "cape": 1.2}    # parent -> chain blend length at each chain's root
# clips (24 fps; every periodic term has an integer number of cycles per clip, so the loop seam is exact)
IDLE_N = 96                           # idle: 4 s loop (unchanged from v1)
IDLE_REACH_K = 0.985                  # "idle knee soften": stance reach (x rest reach) -- both tips planted on z = 0
IDLE_BREATH = 0.05                    # breathing dip (2 per loop)
IDLE_SWAY_DEG, IDLE_BREATH_DEG = 1.2, 0.8
IDLE_HEAD_YAW_DEG, IDLE_HEAD_NOD_DEG = 4.0, 1.5
IDLE_ARM_DEG = (3.0, 9.0)             # "arm tentacle drift": per-bone amplitude root -> tip (travelling curl wave)
IDLE_ARM_LAG = 0.7
IDLE_CAPE_DEG = (0.8, 2.4)            # "cape sway": per-bone amplitude root -> tip (a cape: well under the arms)
IDLE_CAPE_LAG = 0.5
IDLE_CAPE_BIAS = 0.8                  # "cape breeze": fore/aft sway biased backward by this x the amplitude
IDLE_WEB_DEG = (1.5, 4.5)             # "web ripple": mid-chain amplitude root -> edge, 3 ripples per loop (v1 2-6 over 3 bones: same 12 deg summed over the 4 v2 bones)
IDLE_WEB_LAG = 0.9
# walk v2 = the confident STRIDE ("he walks with confidence like a stride")
WALK_N = 30                           # 1.25 s per stride (2 steps) -> 96 steps/min                v1 28 (102.9/min)
WALK_STEP_FRAC = 0.34                 # "step size": stance-foot half-travel / leg reach            v1 0.26
WALK_REACH_K = 0.98                   # stance leg reach (x rest reach): straighter, prouder legs  v1 0.975
WALK_LIFT_FRAC = 0.13                 # "foot lift" (x leg reach)                                  v1 0.12
WALK_FOOT_CURL_DEG = 25.0             # swing-phase tentacle-tip curl
WALK_PELVIS_YAW_DEG, WALK_PELVIS_ROLL_DEG = 6.0, 2.5                                              # v1 5.0, 2.5
WALK_CHEST_COUNTER_DEG = 10.0         # shoulders counter-twist the pelvis                          v1 7.0
WALK_LEAN_DEG = 2.0                   # spine leans in from the hips ...                            v1 3.0
WALK_CHEST_UP_DEG = 4.0               # "chest up": ... while the chest lifts back (proud posture)  v1 0 (chest leaned 1.5)
WALK_HEAD_UP_DEG = 2.0                # "chin up": head world pitch                                 v1 level
WALK_ARM_SWING_DEG = 22.0             # "arm swing": counter-swing at the shoulder ...              v1 12.0
WALK_ARM_FOLLOW_DEG = 8.0             # ... and the lagging follow-through wave in each further bone v1 5.0
WALK_ARM_LAG = 0.6
WALK_CAPE_TRAIL_DEG = 3.0             # "cape trail": backward drape per bone while striding       v1 2.0
WALK_CAPE_DEG = (0.8, 2.2)            # cape flutter per bone root -> tip (2 per stride)
WALK_CAPE_LAG = 0.7
WALK_WEB_BILLOW_DEG = 0.0             # extra web belly while striding (v1 3.0; v2 webs inherit the cape trail via web_follow, so none)
WALK_WEB_DEG = (1.5, 3.75)            # web ripple per bone (v1 2-5 over 3 bones, same sum)
WALK_WEB_LAG = 0.9
# float (NEW, "float up cross his arms, the cape flares a little bit like if theres wind and then floats back down")
FLOAT_N = 120                         # 5 s loop
FLOAT_T = (0.08, 0.32, 0.64, 0.90)    # rise starts / top reached / descent starts / landed (loop fraction)
FLOAT_RISE_FRAC = 0.15                # "float height" x body height
FLOAT_CROUCH = 0.22                   # anticipation dip before lift-off + landing absorb (pelvis, units)
FLOAT_BOB = 0.12                      # hover bob while up (2 bobs)
FLOAT_CROSS_T = (0.12, 0.33, 0.62, 0.84)   # arms: start crossing / crossed / start uncrossing / hanging again
FLOAT_BONE_STAGGER = 0.03             # arm bones lead root-first crossing, tip-first uncrossing
FLOAT_CROSS_Z_FRAC = 0.46             # "crossed arms height" (hip -> neck)
FLOAT_ARM_GAP = 0.30                  # forearm clearance in front of the torso surface
FLOAT_STACK_DZ, FLOAT_STACK_DY = 0.50, 0.60   # the top forearm (left) rides above / ahead of the other
FLOAT_CHEST_UP_DEG, FLOAT_HEAD_UP_DEG = 5.0, 4.0
FLOAT_HEAD_SWAY_DEG = 3.0             # slow look-around while hovering
FLOAT_LEG_TRAIL = 0.5                 # dangling leg tips drift back this much
FLOAT_LEG_EXTEND = 0.995              # ... and straighten to this x reach
FLOAT_LEG_POINT_DEG = 20.0            # ... tips pointed down
FLOAT_CAPE_T = (0.14, 0.40, 0.60, 0.90)    # cape wind: builds / full / eases / calm
FLOAT_CAPE_FLARE_DEG = (1.5, 5.0)     # "cape flare": backward lift per bone root -> tip
FLOAT_CAPE_SPREAD_DEG = (0.8, 2.5)    # ... outward spread per bone (inner chains half)
FLOAT_CAPE_FLUTTER_DEG = (1.0, 3.5)   # ... wind flutter per bone (5 per loop, travelling)
FLOAT_CAPE_LAG = 0.7
FLOAT_CAPE_PHASE = 0.35               # gust phase step between neighbouring cape tentacles (small = coherent wind, webs stretch less)
FLOAT_WEB_BILLOW_DEG = 1.0            # "membranes ripple": extra web belly per bone on top of the followed cape flare ...
FLOAT_WEB_DEG = (2.0, 5.0)            # ... and ripple (8 per loop)
FLOAT_WEB_LAG = 0.9

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PREVIEW = argv[argv.index("--preview") + 1] if "--preview" in argv else None
DIGEST_ONLY = argv[argv.index("--digest-only") + 1] if "--digest-only" in argv else None
OVERRIDES = {}
for i_, a_ in enumerate(argv):
    if a_ == "--set":
        k_, v_ = argv[i_ + 1].split("=", 1)
        assert k_ in globals() and k_.isupper(), "unknown constant " + k_
        globals()[k_] = OVERRIDES[k_] = ast.literal_eval(v_)
TAG = argv[argv.index("--tag") + 1] if "--tag" in argv else None
assert MASK_STYLE in ("w", "starfish"), MASK_STYLE
OUT_NAME = UNIT + ("__" + TAG if TAG else "")          # an alternative (tagged) build never overwrites the default's files
OUT_IMPROVED = os.path.join(ROOT, "improved", OUT_NAME + ".blend")
OUT_RIGGED = os.path.join(ROOT, "rigged", OUT_NAME + ".blend")
OUT_GLB = os.path.join(ROOT, "rigged", OUT_NAME + ".glb")
TEX_DIR = os.path.join(ROOT, "improved", "textures")
report = {"unit": UNIT, "version": VERSION, "conquest_character_id": CHAR_ID, "source": bpy.data.filepath, "tier": "regular",
          "tri_budget": TRI_BUDGET, "yaw_fix_deg": YAW_FIX_DEG, "overrides": OVERRIDES, "tag": TAG, "mask_style": MASK_STYLE}
scene = bpy.context.scene
DIG = {}                                # the determinism digest: every array a consumer receives
TAU = 2.0 * math.pi


def sha(a):
    return hashlib.sha256(np.ascontiguousarray(np.round(np.asarray(a, float), 6)).astype(np.float32).tobytes()).hexdigest()[:16]


def smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ss5(e0, e1, x):
    """smootherstep (zero 1st and 2nd derivative at both ends), scalar."""
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * t * (t * (6 * t - 15) + 10)


def bump(a, b, x):
    """sin^2 window on [a, b]: 0 with zero slope at both ends, 1 at the middle."""
    if x <= a or x >= b:
        return 0.0
    return math.sin(math.pi * (x - a) / (b - a)) ** 2


def mesh_arrays(me, M=None):
    n = len(me.vertices)
    co = np.empty(n * 3); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    if M is not None:
        M = np.array(M); co = co @ M[:3, :3].T + M[:3, 3]
    lt = np.empty(len(me.polygons), dtype=np.int64); me.polygons.foreach_get("loop_total", lt)
    lv = np.empty(len(me.loops), dtype=np.int64); me.loops.foreach_get("vertex_index", lv)
    ls = np.concatenate([[0], np.cumsum(lt)[:-1]])
    return co, [lv[a:a + b].tolist() for a, b in zip(ls, lt)]


def tri_count_F(F):
    return int(sum(len(f) - 2 for f in F))


def new_obj(name, V, F):
    me = bpy.data.meshes.new(name)
    me.from_pydata(np.asarray(V).tolist(), [], F)
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
    roots = np.array([find(i) for i in range(n)])
    return np.unique(roots, return_inverse=True, return_counts=True)


def keep_islands(V, F, min_frac):
    """Drop connected pieces smaller than min_frac of the vertices (sculpt crumbs / remesh specks)."""
    lab, inv, cnt = islands(V, F)
    keep_lab = cnt >= min_frac * len(V)
    keep_v = keep_lab[inv]
    remap = -np.ones(len(V), dtype=np.int64); remap[keep_v] = np.arange(int(keep_v.sum()))
    F2 = [[int(remap[v]) for v in f] for f in F if keep_v[f[0]]]
    return V[keep_v], F2, {"pieces": int(len(cnt)), "kept": int(keep_lab.sum()), "dropped_verts": int((~keep_v).sum())}


def decimate(V, F, target):
    tmp = new_obj("dec_src", V, F)
    dm = tmp.modifiers.new("dec", "DECIMATE"); dm.decimate_type = "COLLAPSE"; dm.use_collapse_triangulate = True
    dm.ratio = min(1.0, target / tri_count_F(F))
    V2, F2 = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    return V2, F2


def join(parts):
    Vs, Fs, o_ = [], [], 0
    for V, F in parts:
        Vs.append(V); Fs += [[i + o_ for i in f] for f in F]; o_ += len(V)
    return np.vstack(Vs), Fs


def rotm(axis, deg):
    return K._rot(axis, math.radians(deg))


def Rx(d):
    return rotm((1, 0, 0), d)


def Ry(d):
    return rotm((0, 1, 0), d)


def Rz(d):
    return rotm((0, 0, 1), d)


def edges_of(F):
    E = set()
    for f in F:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            E.add((a, b) if a < b else (b, a))
    return np.array(sorted(E), dtype=np.int64)


def taubin(V, F, iters, lam, mu):
    """Shrink-free Laplacian smoothing (umbrella operator, lambda/mu pairs)."""
    if iters <= 0:
        return V
    E = edges_of(F)
    n = len(V)
    deg = np.maximum(np.bincount(E.ravel(), minlength=n).astype(float), 1.0)

    def lap(X):
        s_ = np.zeros_like(X)
        for k in range(3):
            s_[:, k] = np.bincount(E[:, 0], X[E[:, 1], k], minlength=n) + np.bincount(E[:, 1], X[E[:, 0], k], minlength=n)
        return s_ / deg[:, None] - X
    X = V.copy()
    for _ in range(iters):
        X = X + lam * lap(X)
        X = X + mu * lap(X)
    return X


def poly_sdf(P, poly):
    """Signed distance of 2D points to a closed polygon (negative inside)."""
    P = np.asarray(P, float); Q = np.asarray(poly, float)
    d = np.full(len(P), np.inf); inside = np.zeros(len(P), bool)
    for i in range(len(Q)):
        a, b = Q[i], Q[(i + 1) % len(Q)]
        ab = b - a; ap = P - a
        t = np.clip((ap @ ab) / (ab @ ab), 0.0, 1.0)
        d = np.minimum(d, np.linalg.norm(ap - t[:, None] * ab, axis=1))
        dy = b[1] - a[1]
        cond = (a[1] > P[:, 1]) != (b[1] > P[:, 1])
        xint = a[0] + (P[:, 1] - a[1]) * (b[0] - a[0]) / (dy if abs(dy) > 1e-12 else 1e-12)
        inside ^= cond & (P[:, 0] < xint)
    return np.where(inside, -d, d)


def poly_area(Q):
    Q = np.asarray(Q, float)
    return 0.5 * float(np.sum(Q[:, 0] * np.roll(Q[:, 1], -1) - np.roll(Q[:, 0], -1) * Q[:, 1]))


def ccw(Q):
    return np.asarray(Q, float) if poly_area(Q) > 0 else np.asarray(Q, float)[::-1].copy()


def inset_convex(poly, w):
    """Inset a CCW convex polygon by w (every edge moved inward, corners re-intersected)."""
    Q = ccw(poly); n = len(Q); lines = []
    for i in range(n):
        a, b = Q[i], Q[(i + 1) % n]
        e = (b - a) / np.linalg.norm(b - a)
        lines.append((a + np.array([-e[1], e[0]]) * w, e))
    out = []
    for i in range(n):
        (p1, e1), (p2, e2) = lines[i - 1], lines[i]
        s_, _ = np.linalg.solve(np.array([e1, -e2]).T, p2 - p1)
        out.append(p1 + s_ * e1)
    return np.array(out)


def resample_closed(Q, ds):
    Q = np.asarray(Q, float); out = []
    for i in range(len(Q)):
        a, b = Q[i], Q[(i + 1) % len(Q)]
        n = max(1, int(math.ceil(np.linalg.norm(b - a) / ds)))
        out += [a + (b - a) * k / n for k in range(n)]
    return np.array(out)


def polyline_sd(P2, C):
    """Signed distance of 2D points to an open polyline C (CCW-left of the direction of travel = positive), the arc
    parameter of the nearest point (0 .. 1) and whether that point is interior (not clamped at an end)."""
    P2 = np.asarray(P2, float); C = np.asarray(C, float)
    A_, B_ = C[:-1], C[1:]
    AB = B_ - A_; L2 = (AB ** 2).sum(1)
    seg = np.sqrt(L2); cum = np.concatenate([[0.0], np.cumsum(seg)]); tot = float(cum[-1])
    best_d = np.full(len(P2), np.inf); best_s = np.zeros(len(P2)); best_sd = np.zeros(len(P2)); best_int = np.zeros(len(P2), bool)
    for k in range(len(A_)):
        ap = P2 - A_[k]
        tr = (ap @ AB[k]) / L2[k]
        t_ = np.clip(tr, 0.0, 1.0)
        q = A_[k] + t_[:, None] * AB[k]
        d = np.linalg.norm(P2 - q, axis=1)
        cr = AB[k, 0] * ap[:, 1] - AB[k, 1] * ap[:, 0]
        upd = d < best_d
        best_d = np.where(upd, d, best_d)
        best_sd = np.where(upd, np.where(cr >= 0, d, -d), best_sd)
        best_s = np.where(upd, (cum[k] + t_ * seg[k]) / tot, best_s)
        best_int = np.where(upd, ~(((k == 0) & (tr <= 0.0)) | ((k == len(A_) - 1) & (tr >= 1.0))), best_int)
    return best_sd, best_s, best_int


def catmull(P, n_per=24):
    P = np.asarray(P, float)
    Pp = np.vstack([2 * P[0] - P[1], P, 2 * P[-1] - P[-2]])
    out = []
    for i in range(1, len(Pp) - 2):
        p0, p1, p2, p3 = Pp[i - 1], Pp[i], Pp[i + 1], Pp[i + 2]
        for k in range(n_per):
            t = k / n_per
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(P[-1])
    return np.array(out)


# =========================================================================== 1. sculpt parts -> world, crumbs, yaw fix
if bpy.context.view_layer.objects.active and bpy.context.view_layer.objects.active.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
PART_OF = {"Body": "body", "Head": "head", "Mask-Goggles": "goggles", "Icosphere": "mouth", "Cape": "cape"}
RY = np.diag([-1.0, -1.0, 1.0]) if abs(YAW_FIX_DEG - 180.0) < 1e-9 else np.array(Matrix.Rotation(math.radians(YAW_FIX_DEG), 3, "Z"))
src_objs = sorted([o for o in scene.objects if o.type == "MESH" and not o.hide_render and o.visible_get()], key=lambda o: o.name)
left_out = sorted(o.name for o in scene.objects if o.type == "MESH" and o not in src_objs)
assert sorted(o.name for o in src_objs) == sorted(PART_OF), [o.name for o in src_objs]
dg0 = bpy.context.evaluated_depsgraph_get()
PARTS = {}
report["source_parts"] = {}
tris_source = 0
for o in src_objs:
    M = np.array(o.matrix_world)
    me_e = bpy.data.meshes.new_from_object(o.evaluated_get(dg0))
    V, F = mesh_arrays(me_e, M)
    bpy.data.meshes.remove(me_e)
    det = float(np.linalg.det(M[:3, :3]))
    if det < 0:                                  # negative scale: world-space winding is inverted -> flip back
        F = [f[::-1] for f in F]
    tris_source += tri_count_F(F)
    V, F, cr = keep_islands(V, F, CRUMB_FRAC)
    V = V @ RY.T
    PARTS[PART_OF[o.name]] = (V, F)
    report["source_parts"][o.name] = {"group": PART_OF[o.name], "tris_evaluated": tri_count_F(F), "det": round(det, 4),
                                      "winding_flipped": det < 0, "modifiers": [m.type for m in o.modifiers], "crumbs": cr}
report["source_left_out"] = {n: "hidden in the view layer (artist's unused alternative to Mask-Goggles)" for n in left_out}
report["tris_source"] = tris_source
for o in list(bpy.data.objects):             # the source objects never reach the outputs
    bpy.data.objects.remove(o, do_unlink=True)
for m in list(bpy.data.meshes):
    if m.users == 0:
        bpy.data.meshes.remove(m)

# =========================================================================== 2. v2 sculpt prep: smooth, centre, neck, visor
t = time.time()
prep = {}
GOG_LO, GOG_HI = PARTS["goggles"][0].min(0), PARTS["goggles"][0].max(0)
MOUTH_C = PARTS["mouth"][0].mean(0)
prep["removed_parts"] = {"Mask-Goggles": "replaced by the W-visor (its centre height places the visor)",
                         "Icosphere": "the mouth piece, removed (artist v2); its centre places the v3 smirk mouth"}
del PARTS["goggles"], PARTS["mouth"]
for g in ("body", "head", "cape"):
    V0, F0 = PARTS[g]
    V1 = taubin(V0, F0, SMOOTH_ITERS[g], SMOOTH_LAMBDA, SMOOTH_MU)
    dv = np.linalg.norm(V1 - V0, axis=1)
    PARTS[g] = (V1, F0)
    prep.setdefault("smoothing", {})[g] = {"iters": SMOOTH_ITERS[g], "mean_move": round(float(dv.mean()), 4),
                                          "p99_move": round(float(np.percentile(dv, 99)), 4), "max_move": round(float(dv.max()), 4)}
HV0 = PARTS["head"][0]
X_MID = 0.5 * (float(HV0[:, 0].min()) + float(HV0[:, 0].max()))       # the body midline (head centre)
if CAPE_CENTRE:
    CVc, CFc = PARTS["cape"]
    xc_c = 0.5 * (float(CVc[:, 0].min()) + float(CVc[:, 0].max()))
    CVc = CVc.copy(); CVc[:, 0] += X_MID - xc_c
    PARTS["cape"] = (CVc, CFc)
    prep["cape_centre"] = {"cape_mirror_x_before": round(xc_c, 4), "body_midline_x": round(X_MID, 4),
                           "shift_x": round(X_MID - xc_c, 4)}
# head lift + neck tube
BV_, BF_ = PARTS["body"]
Z_BODY_TOP = float(BV_[:, 2].max())
HV_, HF_ = PARTS["head"]
HV_ = HV_.copy(); HV_[:, 2] += NECK_LIFT
PARTS["head"] = (HV_, HF_)
Z_HEAD_BOT = float(HV_[:, 2].min())
GOG_LO = GOG_LO + np.array([0, 0, NECK_LIFT]); GOG_HI = GOG_HI + np.array([0, 0, NECK_LIFT])
MOUTH_C = MOUTH_C + np.array([0, 0, NECK_LIFT])


def mid_y(V, z, xw=1.0, dz=0.12):
    m = (np.abs(V[:, 2] - z) < dz) & (np.abs(V[:, 0] - X_MID) < xw)
    return 0.5 * (float(V[m, 1].min()) + float(V[m, 1].max()))


NB = np.array([X_MID, mid_y(BV_, Z_BODY_TOP - NECK_BURY_BODY), Z_BODY_TOP - NECK_BURY_BODY])
NT = np.array([X_MID, mid_y(HV_, Z_HEAD_BOT + NECK_BURY_HEAD), Z_HEAD_BOT + NECK_BURY_HEAD])
NRING, NSEG = 28, 24
nax = (NT - NB) / np.linalg.norm(NT - NB)
e1 = np.cross(nax, [0.0, 1.0, 0.0]); e1 /= np.linalg.norm(e1)
e2 = np.cross(nax, e1)
NV, NF = [], []
for j in range(NSEG + 1):
    s_ = j / NSEG
    r_ = NECK_R + NECK_FLARE_BOT * (1 - float(smoothstep(0.0, 0.45, s_))) ** 2 + NECK_FLARE_TOP * float(smoothstep(0.55, 1.0, s_)) ** 2
    c_ = NB + (NT - NB) * s_
    for i in range(NRING):
        a = TAU * i / NRING
        NV.append(c_ + r_ * (math.cos(a) * e1 + math.sin(a) * e2))
for j in range(NSEG):
    for i in range(NRING):
        a0, a1 = j * NRING + i, j * NRING + (i + 1) % NRING
        NF.append([a0, a1, a1 + NRING, a0 + NRING])
NV.append(NB); NV.append(NT)
cb, ct = len(NV) - 2, len(NV) - 1
for i in range(NRING):
    NF.append([cb, (i + 1) % NRING, i])
    NF.append([ct, NSEG * NRING + i, NSEG * NRING + (i + 1) % NRING])
NV = np.array(NV)
bmn = bmesh.new()
for p in NV:
    bmn.verts.new(p)
bmn.verts.ensure_lookup_table()
for f in NF:
    bmn.faces.new([bmn.verts[i] for i in f])
bmesh.ops.recalc_face_normals(bmn, faces=bmn.faces[:])
bmn.verts.index_update()
NF = [[v.index for v in f.verts] for f in bmn.faces]
bmn.free()
PARTS["neck"] = (NV, NF)
prep["neck"] = {"head_lift": NECK_LIFT, "radius": NECK_R, "flare": [NECK_FLARE_BOT, NECK_FLARE_TOP],
                "axis_bottom": NB.round(4).tolist(), "axis_top": NT.round(4).tolist(), "body_top_z": round(Z_BODY_TOP, 4),
                "head_bottom_z_after_lift": round(Z_HEAD_BOT, 4)}

# W-visor: a (u, z) frame round the mantle's vertical axis; u = arc length from the midline (+u toward the character's
# left = +X), outline + lenses built in 2D, CDT-triangulated, ray-projected onto the mantle, thickened into a closed plate
Z_VC = 0.5 * (float(GOG_LO[2]) + float(GOG_HI[2]))
bandh = np.abs(HV_[:, 2] - Z_VC) < 0.35
HXC = X_MID
HYC = 0.5 * (float(HV_[bandh, 1].min()) + float(HV_[bandh, 1].max()))
bvh_head = BVHTree.FromPolygons(HV_.tolist(), HF_)


def head_hit(th, z):
    d = np.array([math.sin(th), -math.cos(th), 0.0])
    o = np.array([HXC, HYC, z]) + d * 30.0
    loc, nrm, _, _ = bvh_head.ray_cast(Vector(o), Vector(-d))
    assert loc is not None, ("visor ray missed the mantle", th, z)
    n_ = np.array(nrm)
    if n_ @ d < 0:
        n_ = -n_
    return np.array(loc), n_


_p0, _ = head_hit(0.0, Z_VC)
R_REF = float(np.hypot(_p0[0] - HXC, _p0[1] - HYC))       # u = theta * R_REF (arc length at the visor centre height)
W_OUT = ccw([(VISOR_TIP_U, VISOR_TIP_DZ), (0.0, VISOR_DIP_DZ), (-VISOR_TIP_U, VISOR_TIP_DZ), (-VISOR_BOT_U, VISOR_BOT_DZ),
             (0.0, VISOR_NOTCH_DZ), (VISOR_BOT_U, VISOR_BOT_DZ)])
WING_R = [(VISOR_TIP_U, VISOR_TIP_DZ), (0.0, VISOR_DIP_DZ), (0.0, VISOR_NOTCH_DZ), (VISOR_BOT_U, VISOR_BOT_DZ)]
LENSES = [inset_convex(WING_R, VISOR_FRAME_W), inset_convex([(-u, z) for u, z in WING_R], VISOR_FRAME_W)]


def lens_sdf(P):
    return np.minimum(poly_sdf(P, LENSES[0]), poly_sdf(P, LENSES[1]))


def star_sdf(P):
    """v3 starfish mask: smooth union of an elliptical body and five tapered, round-tipped arms (u, dz frame)."""
    P = np.asarray(P, float)
    c = np.array([0.0, STAR_C_DZ])
    Q = P - c
    ax, az = STAR_BODY
    d = (np.hypot(Q[:, 0] / ax, Q[:, 1] / az) - 1.0) * min(ax, az)
    for ang, reach, w0, w1 in STAR_ARMS:
        e = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
        L_ = reach - w1                                   # the tip circle's centre (the arm ends exactly at 'reach')
        t_ = np.clip(Q @ e / L_, 0.0, 1.0)
        r_ = w0 + (w1 - w0) * t_
        da = np.linalg.norm(Q - t_[:, None] * (L_ * e), axis=1) - r_
        h_ = np.clip(0.5 + 0.5 * (da - d) / STAR_SMOOTH, 0.0, 1.0)            # polynomial smooth-min
        d = da * (1 - h_) + d * h_ - STAR_SMOOTH * h_ * (1 - h_)
    return d


def star_outline(n=720):
    """The starfish is star-shaped about its centre: one zero crossing per ray -> bisection per angle."""
    c = np.array([0.0, STAR_C_DZ]); out = []
    for k in range(n):
        a = TAU * k / n
        e = np.array([math.cos(a), math.sin(a)])
        lo_, hi_ = 0.0, 6.0
        for _ in range(40):
            m_ = 0.5 * (lo_ + hi_)
            if star_sdf((c + m_ * e)[None])[0] < 0:
                lo_ = m_
            else:
                hi_ = m_
        out.append(c + lo_ * e)
    return ccw(np.array(out))


if MASK_STYLE == "starfish":
    LENSES = [ccw(STAR_LENS), ccw([(-u, z) for u, z in STAR_LENS])]      # same treatment (recessed, glowing), own shape
    MASK_OUT = star_outline()
    mask_sdf = lambda P: poly_sdf(P, MASK_OUT)             # noqa: E731  (the polygon: the geometry and the colour agree)
else:
    MASK_OUT = W_OUT
    mask_sdf = lambda P: poly_sdf(P, W_OUT)                # noqa: E731
MASK_LO, MASK_HI = MASK_OUT.min(0), MASK_OUT.max(0)        # (u, dz) bbox: the face windows below use it
lens_pts = np.vstack([resample_closed(L_, 0.02) for L_ in LENSES])
LENS_MARGIN = float(-mask_sdf(lens_pts).max())            # every lens point this far inside the mask outline
assert LENS_MARGIN > VISOR_FRAME_W * 0.99, ("lens pokes out of the mask frame", LENS_MARGIN)
ob_ = resample_closed(MASK_OUT, VISOR_DS)
lb_ = [resample_closed(L_, VISOR_DS) for L_ in LENSES]
lo2d, hi2d = MASK_OUT.min(0), MASK_OUT.max(0)
gu, gz = np.meshgrid(np.arange(lo2d[0], hi2d[0], 0.07), np.arange(lo2d[1], hi2d[1], 0.07), indexing="ij")
G2 = np.stack([gu.ravel(), gz.ravel()], 1)
dout = mask_sdf(G2); dl_ = np.abs(lens_sdf(G2))
G2 = G2[(dout < -0.03) & (dl_ > 0.03)]
P2 = np.vstack([ob_] + lb_ + [G2])
n_ob = len(ob_)
cons_e, o_ = [], n_ob
for L_ in lb_:
    cons_e += [(o_ + i, o_ + (i + 1) % len(L_)) for i in range(len(L_))]
    o_ += len(L_)
cdt = delaunay_2d_cdt([Vector((float(p[0]), float(p[1]))) for p in P2], cons_e, [list(range(n_ob))], 1, 1e-7, True)
P2c = np.array([[v[0], v[1]] for v in cdt[0]])
F2c = [list(f) for f in cdt[2]]
for f in F2c:                                         # CDT keeps CCW (u right, z up = seen from the front camera)
    assert poly_area(P2c[f]) > 0
thick = VISOR_T_LENS + (VISOR_T_FRAME - VISOR_T_LENS) * smoothstep(-0.02, 0.03, lens_sdf(P2c))
S3, N3 = [], []
for (u_, dz_) in P2c:                                  # thickness along the horizontal radial: every plate vertex keeps
    p_, _ = head_hit(u_ / R_REF, Z_VC + dz_)           # its (u, z), so the colour SDF and the geometry agree exactly
    S3.append(p_); N3.append([math.sin(u_ / R_REF), -math.cos(u_ / R_REF), 0.0])
S3 = np.array(S3); N3 = np.array(N3)
FRONT = S3 + N3 * thick[:, None]
BACK = S3 - N3 * VISOR_EMBED
nv2 = len(P2c)
VV = np.vstack([FRONT, BACK])
VF = [list(f) for f in F2c] + [[i + nv2 for i in f[::-1]] for f in F2c]
bnd = {}
for f in F2c:                                         # boundary edges = edges used by one triangle
    for i in range(3):
        a, b = f[i], f[(i + 1) % 3]
        k_ = (min(a, b), max(a, b))
        bnd[k_] = None if k_ in bnd else (a, b)
for k_, ab in bnd.items():
    if ab is not None:
        a, b = ab
        VF.append([a, a + nv2, b + nv2, b])
bmv = bmesh.new()
for p in VV:
    bmv.verts.new(p)
bmv.verts.ensure_lookup_table()
for f in VF:
    bmv.faces.new([bmv.verts[i] for i in f])
bmesh.ops.recalc_face_normals(bmv, faces=bmv.faces[:])
visor_manifold = all(e.is_manifold for e in bmv.edges)
bmv.verts.index_update()
VF = [[v.index for v in f.verts] for f in bmv.faces]
bmv.free()
PARTS["visor"] = (VV, VF)
Z_MOUTH = float(MOUTH_C[2]) + MOUTH_DZ                 # v3: the removed siphon's centre height (the v2 mouth placement)
prep["visor"] = {"frame": "(u, z) round the mantle axis; u = theta x R_REF", "centre_z": round(Z_VC, 4),
                 "axis_xy": [round(HXC, 4), round(HYC, 4)], "R_REF": round(R_REF, 4), "mask_style": MASK_STYLE,
                 "outline_u_z": (W_OUT if MASK_STYLE == "w" else MASK_OUT[::8]).round(4).tolist(),
                 "lenses_u_z": [L_.round(4).tolist() for L_ in LENSES], "lens_inside_frame_min_margin": round(LENS_MARGIN, 4),
                 "outline_bbox_u_dz": [MASK_LO.round(3).tolist(), MASK_HI.round(3).tolist()],
                 "tip_angle_deg": round(math.degrees(float(np.abs(MASK_OUT[:, 0]).max()) / R_REF), 2), "cdt_tris_per_face": len(F2c),
                 "closed_manifold": visor_manifold, "thickness": {"frame": VISOR_T_FRAME, "lens": VISOR_T_LENS, "embed": VISOR_EMBED},
                 "reference": ("design/reference/supaoctto-v2-visor-reference.png (angular swept W)" if MASK_STYLE == "w" else
                               "design/reference/supaoctto-v3-starfish-mask-annotation.png (star arms round the eyes)")}
if MASK_STYLE == "starfish":
    prep["visor"]["starfish"] = {"centre_dz": STAR_C_DZ, "body": STAR_BODY, "arms_deg_reach_w0_tip": STAR_ARMS,
                                 "smooth": STAR_SMOOTH, "outline_points": int(len(MASK_OUT))}
prep["mouth_placement"] = {"centre_z": round(Z_MOUTH, 4), "rule": "the removed v2 siphon's centre height (the source mouth "
                                                                   "piece's centre) + MOUTH_DZ, on the midline"}
prep["seconds"] = round(time.time() - t, 1)
report["prep"] = prep

# =========================================================================== 2b. v1 fixes on the sculpt data
BV, BF = PARTS["body"]
BV = BV.copy()
leg_tip_i = {}
for sg in (1, -1):
    m = BV[:, 0] * sg > 0.3
    leg_tip_i[sg] = int(np.argmin(np.where(m, BV[:, 2], np.inf)))
zt = {sg: float(BV[leg_tip_i[sg], 2]) for sg in (1, -1)}
long_sg = 1 if zt[1] <= zt[-1] else -1
short_sg = -long_sg
need = zt[short_sg] - zt[long_sg]
fix = {"leg_tip_z_before": {"+X": round(zt[1], 4), "-X": round(zt[-1], 4)}, "difference": round(need, 4)}
if LEG_EQUALIZE and need > 1e-4:
    tip = BV[leg_tip_i[short_sg]].copy()
    z0 = tip[2] + LEG_EQ_SPAN
    side = BV[:, 0] * short_sg > 0.3
    sl = side & (np.abs(BV[:, 2] - z0) < 0.1)
    c0 = BV[sl].mean(0)
    a = (tip - c0) / np.linalg.norm(tip - c0)
    Dlen = float((tip - c0) @ a)
    db = 0.5 * Dlen
    delta = need / (-a[2])
    kk = delta / (Dlen - db / 2)
    region = side & (BV[:, 2] < z0 + 0.5)
    d = (BV - c0) @ a
    f = np.where(d <= 0, 0.0, np.where(d < db, d * d / (2 * db), d - db / 2))
    BV = BV + np.where(region[:, None], (kk * f)[:, None] * a[None, :], 0.0)
    fix.update({"stretched_leg": "+X" if short_sg > 0 else "-X", "axis": a.round(4).tolist(), "tip_moved": round(float(delta), 4),
                "stretch_factor_past_blend": round(1 + kk, 4), "span": LEG_EQ_SPAN,
                "leg_tip_z_after": round(float(BV[leg_tip_i[short_sg], 2]), 4),
                "rule": "vertices of that leg past a plane LEG_EQ_SPAN above the tip slide along the leg axis, C1 ramp"})
PARTS["body"] = (BV, BF)
floor_hi = float(BV[:, 2].min())
CV_, CF_ = PARTS["cape"]
CV_ = CV_.copy()
zmin_c = float(CV_[:, 2].min())
lift = (floor_hi + CAPE_TIP_CLEAR) - zmin_c
if lift > 0:
    CV_[:, 2] += lift * smoothstep(zmin_c + CAPE_LIFT_SPAN, zmin_c, CV_[:, 2])
PARTS["cape"] = (CV_, CF_)
fix["cape_bottom_lift"] = {"cape_min_z_before_rel_floor": round(zmin_c - floor_hi, 4), "lift": round(lift, 4),
                           "span": CAPE_LIFT_SPAN, "cape_min_z_after_rel_floor": round(float(CV_[:, 2].min()) - floor_hi, 4)}
report["sculpt_fixes"] = fix


# =========================================================================== 3. anatomy: geodesic ring tracing
def graph(V, F):
    E = edges_of(F)
    w = np.linalg.norm(V[E[:, 0]] - V[E[:, 1]], axis=1)
    src = np.concatenate([E[:, 0], E[:, 1]]); dst = np.concatenate([E[:, 1], E[:, 0]]); ww = np.concatenate([w, w])
    o = np.argsort(src, kind="stable")
    ptr = np.concatenate([[0], np.cumsum(np.bincount(src, minlength=len(V)))])
    return ptr.tolist(), dst[o].tolist(), ww[o].tolist()


def dijkstra(G, n, s0):
    ptr, nb, ww = G
    d = [math.inf] * n
    d[s0] = 0.0
    h = [(0.0, s0)]
    while h:
        dd, v = heapq.heappop(h)
        if dd > d[v]:
            continue
        for j in range(ptr[v], ptr[v + 1]):
            u = nb[j]; nd = dd + ww[j]
            if nd < d[u]:
                d[u] = nd; heapq.heappush(h, (nd, u))
    return np.array(d)


def trace(V, G, tip):
    d = dijkstra(G, len(V), tip)
    rings, k, stop = [], 0, "end"
    while True:
        m = (d >= k * TRACE_DS) & (d < (k + 1) * TRACE_DS)
        if not m.any():
            break
        c = V[m].mean(0); r = float(np.linalg.norm(V[m] - c, axis=1).max())
        if k >= TRACE_MIN_RINGS and r > TRACE_MERGE_K * float(np.median([q[1] for q in rings[-3:]])):
            stop = "merged at ring %d (spread %.3f)" % (k, r)
            break
        rings.append((c, r)); k += 1
    members = np.nonzero(d < k * TRACE_DS)[0]
    poly = np.array([V[tip]] + [c for c, _ in rings])[::-1]          # root -> tip
    return poly, members, {"rings": k, "stop": stop, "tip": V[tip].round(4).tolist(), "root": poly[0].round(4).tolist(),
                           "median_radius": round(float(np.median([q[1] for q in rings])), 4)}


def resample(poly, ext=0.0, step=0.05):
    P = np.array(poly, float)
    for _ in range(2):                         # light smoothing of the ring centroids (ends kept)
        P[1:-1] = (P[:-2] + P[1:-1] + P[2:]) / 3.0
    if ext > 0:
        d0 = P[1] - P[0]; d0 /= np.linalg.norm(d0)
        P = np.vstack([P[0] - d0 * ext, P])
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(s[-1])
    ss = np.linspace(0.0, L, max(2, int(math.ceil(L / step)) + 1))
    return np.stack([np.interp(ss, s, P[:, k]) for k in range(3)], 1), ss


def make_chain(name, kind, side, poly, members, ext, info):
    P, s = resample(poly, ext)
    return {"name": name, "kind": kind, "side": side, "P": P, "s": s, "L": float(s[-1]), "members": members, "info": info}


def chain_at(ch, s):
    s = np.clip(np.asarray(s, float), 0.0, ch["L"])
    return np.stack([np.interp(s, ch["s"], ch["P"][:, k]) for k in range(3)], -1)


t = time.time()
Gb = graph(BV, BF)
CHAINS = {}
BLAB = np.zeros(len(BV), dtype=np.int32)             # 0 torso, 2/3 arm L/R, 4/5 leg L/R
LAB_ID = {"arm.L": 2, "arm.R": 3, "leg.L": 4, "leg.R": 5}
for side, sg in (("L", 1.0), ("R", -1.0)):            # final frame: the character's left = +X
    m = BV[:, 0] * sg > 0.3
    hand = int(np.argmax(np.where(m, np.abs(BV[:, 0]), -1)))
    foot = int(np.argmin(np.where(m, BV[:, 2], np.inf)))
    for kind, tip, ext in (("arm", hand, ARM_ROOT_EXT), ("leg", foot, LEG_ROOT_EXT)):
        poly, mem, info = trace(BV, Gb, tip)
        nm = kind + "." + side
        CHAINS[nm] = make_chain(nm, kind, side, poly, mem, ext, info)
        BLAB[mem] = LAB_ID[nm]
Gc = graph(CV_, CF_)
CLAB = np.full(len(CV_), 20, dtype=np.int32)          # 20 = the shoulder yoke; 10 + c = cape chain c
CAPE_NAMES = ["cape_outer.R", "cape_inner.R", "cape_inner.L", "cape_outer.L"]     # -X .. +X
overlap = 0
for side, sg in (("L", 1.0), ("R", -1.0)):
    m = (CV_[:, 0] - X_MID) * sg > 0.3
    inner = int(np.argmin(np.where(m, CV_[:, 2], np.inf)))
    outer = int(np.argmax(np.where(m, np.abs(CV_[:, 0] - X_MID), -1)))
    for kind, tip in (("cape_inner", inner), ("cape_outer", outer)):
        poly, mem, info = trace(CV_, Gc, tip)
        nm = kind + "." + side
        CHAINS[nm] = make_chain(nm, "cape", side, poly, mem, 0.0, info)
        overlap += int((CLAB[mem] != 20).sum())
        CLAB[mem] = 10 + CAPE_NAMES.index(nm)
report["anatomy"] = {"method": "geodesic rings from each tentacle tip (Dijkstra on the sculpt's edge graph), ring width "
                               "TRACE_DS; stop when a ring's spread exceeds TRACE_MERGE_K x the median of the last 3",
                     "chains": {n: {**c["info"], "length": round(c["L"], 4), "members": int(len(c["members"]))} for n, c in CHAINS.items()},
                     "cape_member_overlap": overlap, "cape_yoke_vertices": int((CLAB == 20).sum()),
                     "body_torso_vertices": int((BLAB == 0).sum()), "seconds": round(time.time() - t, 1)}

# =========================================================================== 3b. v3 belt (new geometry, fused by the body remesh)
def grid_solid(FR, BK, periodic):
    """Closed solid between two (ni, nj, 3) grids (front surface, back surface); periodic in i or not."""
    ni, nj = FR.shape[:2]
    V = np.vstack([FR.reshape(-1, 3), BK.reshape(-1, 3)])
    ix = lambda l, i, j: l * ni * nj + (i % ni) * nj + j            # noqa: E731
    F = []
    for i in range(ni if periodic else ni - 1):
        for j in range(nj - 1):
            F.append([ix(0, i, j), ix(0, i + 1, j), ix(0, i + 1, j + 1), ix(0, i, j + 1)])
            F.append([ix(1, i, j + 1), ix(1, i + 1, j + 1), ix(1, i + 1, j), ix(1, i, j)])
    ring = [(i, 0) for i in range(ni)] if periodic else \
        [(i, 0) for i in range(ni - 1)] + [(ni - 1, j) for j in range(nj - 1)] + \
        [(i, nj - 1) for i in range(ni - 1, 0, -1)] + [(0, j) for j in range(nj - 1, 0, -1)]
    rings = [ring, [(i, nj - 1) for i in range(ni)]] if periodic else [ring]
    for rg in rings:
        for k in range(len(rg)):
            (i0, j0), (i1, j1) = rg[k], rg[(k + 1) % len(rg)]
            F.append([ix(0, i0, j0), ix(1, i0, j0), ix(1, i1, j1), ix(0, i1, j1)])
    bm_ = bmesh.new()
    for p in V:
        bm_.verts.new(p)
    bm_.verts.ensure_lookup_table()
    for f in F:
        bm_.faces.new([bm_.verts[i] for i in f])
    bmesh.ops.recalc_face_normals(bm_, faces=bm_.faces[:])
    ok_ = all(e.is_manifold for e in bm_.edges)
    bm_.verts.index_update()
    F = [[v.index for v in f.verts] for f in bm_.faces]
    bm_.free()
    return V, F, ok_


t = time.time()
Z_HIP0 = float(np.mean([CHAINS["leg.L"]["P"][0][2], CHAINS["leg.R"]["P"][0][2]]))
Z_NECK0 = 0.5 * ((Z_BODY_TOP - NECK_RAMP[0]) + (Z_HEAD_BOT + NECK_RAMP[1]))


def zf(f):
    return Z_HIP0 + f * (Z_NECK0 - Z_HIP0)


BZ_TOP, BZ_BOT = zf(BELT_TOP_FRAC), zf(BELT_BOT_FRAC)
PZ0, PZ1 = zf(PLATE_Z[0]), zf(PLATE_Z[1])
ZSTEP = 0.05
BZ_LO = min(BZ_BOT - BELT_DIP, PZ0) - 0.3
BZ_HI = max(BZ_TOP, PZ1) + 0.3
tor_ = (BLAB == 0) & (np.abs(BV[:, 0] - X_MID) < 1.0)
zs_ = np.arange(BZ_LO, BZ_HI + 1e-9, 0.1)
cys_ = []
for z_ in zs_:
    m_ = tor_ & (np.abs(BV[:, 2] - z_) < 0.12)
    cys_.append(0.5 * (float(BV[m_, 1].min()) + float(BV[m_, 1].max())))
AXB = np.polyfit(zs_, cys_, 1)                         # the waist axis: y mid-depth, a straight line over the belt span


def belt_cy(z):
    return AXB[0] * np.asarray(z, float) + AXB[1]


bvh_bsc = BVHTree.FromPolygons(BV.tolist(), BF)
NTH = 144
TH_G = np.arange(NTH) * TAU / NTH - math.pi            # 0 = the front (-Y), + toward the character's left (+X)
ZG = np.arange(BZ_LO, BZ_HI + 1e-9, ZSTEP)
RG = np.zeros((NTH, len(ZG)))
for i, th in enumerate(TH_G):
    d_ = Vector((math.sin(th), -math.cos(th), 0.0))
    for j, z_ in enumerate(ZG):
        hit = bvh_bsc.ray_cast(Vector((X_MID, float(belt_cy(z_)), float(z_))), d_)   # outward from the axis: the torso wall
        assert hit[0] is not None, ("belt ray missed the torso", th, z_)
        RG[i, j] = hit[3]
RG_RAW = RG.copy()
# robust: a ray that slips into a crease (the armpit) reads far too long; replace samples off their row's running
# median (7 around the waist) by that median before smoothing
RGm = np.median(np.stack([np.roll(RG, k, axis=0) for k in range(-3, 4)]), axis=0)
OUTL = np.abs(RG - RGm) > 0.15
RG = np.where(OUTL, RGm, RG)
for _ in range(24):                                    # the belt follows the waist, not the sculpt's lumps
    RGp = np.vstack([RG[-1:], RG, RG[:1]])
    RGs = RG.copy()
    RGs[:, 1:-1] = 0.25 * (RGp[:-2, 1:-1] + RGp[2:, 1:-1] + RG[:, :-2] + RG[:, 2:])
    RG = 0.5 * RG + 0.5 * RGs


def belt_r(th, z):
    th = np.asarray(th, float); z = np.asarray(z, float)
    fi = (np.mod(th + math.pi, TAU)) / TAU * NTH
    i0 = np.floor(fi).astype(int); a_ = fi - i0; i0 %= NTH; i1 = (i0 + 1) % NTH
    fj = np.clip((z - ZG[0]) / ZSTEP, 0.0, len(ZG) - 1.000001)
    j0 = np.floor(fj).astype(int); b_ = fj - j0; j1 = j0 + 1
    return RG[i0, j0] * (1 - a_) * (1 - b_) + RG[i1, j0] * a_ * (1 - b_) + RG[i0, j1] * (1 - a_) * b_ + RG[i1, j1] * a_ * b_


def belt_pt(th, z, off):
    th = np.asarray(th, float); z = np.asarray(z, float)
    r_ = belt_r(th, z) + off
    return np.stack([X_MID + r_ * np.sin(th), belt_cy(z) - r_ * np.cos(th), z], -1)


R_W = float(belt_r(TH_G, np.full(NTH, 0.5 * (BZ_TOP + BZ_BOT))).mean())      # u = theta x R_W round the waist
DEG_U = math.radians(1.0) * R_W


def band_zbot(u):
    return BZ_BOT - BELT_DIP * (1.0 - smoothstep(0.0, BELT_DIP_W, np.abs(np.asarray(u, float))))


def plate_rows(z, sg):
    """Right/left plate edge (u_in, u_out) at height z; sg = +1 the character's left plate (+X), -1 the right."""
    z = np.asarray(z, float)
    s_ = np.clip((z - PZ0) / (PZ1 - PZ0), 0.0, 1.0)
    u_in = (PLATE_TH[0] + PLATE_TAPER * (1 - s_)) * DEG_U
    u_out = (PLATE_TH[1] + PLATE_FLARE * s_ * s_) * DEG_U
    rb, rt = PLATE_ROUND
    db_ = np.clip(rb - (z - PZ0), 0.0, rb); dt_ = np.clip(rt - (PZ1 - z), 0.0, rt)
    sh = (rb - np.sqrt(np.maximum(rb * rb - db_ * db_, 0.0))) + (rt - np.sqrt(np.maximum(rt * rt - dt_ * dt_, 0.0)))
    u_in, u_out = u_in + sh, u_out - sh
    return (u_in, u_out) if sg > 0 else (-u_out, -u_in)


BELT_POLYS = {}
BELT_INFO = {}
if BELT:
    U_EXT = math.pi * R_W + 1.0                        # the band polygon runs past the back seam: no false edge there
    ub_ = np.linspace(-U_EXT, U_EXT, 400)
    BELT_POLYS["band"] = ccw(np.vstack([np.stack([ub_, np.full_like(ub_, BZ_TOP)], 1),
                                        np.stack([ub_[::-1], band_zbot(ub_[::-1])], 1)]))
    zp_ = PZ0 + (PZ1 - PZ0) * (0.5 - 0.5 * np.cos(np.linspace(0.0, math.pi, 64)))     # dense rows near the rounded ends
    for nm_, sg in (("plate.L", 1.0), ("plate.R", -1.0)):
        ui_, uo_ = plate_rows(zp_, sg)
        BELT_POLYS[nm_] = ccw(np.vstack([np.stack([ui_, zp_], 1), np.stack([uo_[::-1], zp_[::-1]], 1)]))
    parts_b = []
    NTB, NJB = 288, 7
    thb = np.arange(NTB) * TAU / NTB - math.pi
    ubb = thb * R_W
    zb_ = band_zbot(ubb)
    Jb = np.linspace(0.0, 1.0, NJB)
    ZZ = zb_[:, None] + (BZ_TOP - zb_)[:, None] * Jb[None, :]
    THb = np.repeat(thb[:, None], NJB, 1)
    Vb, Fb, okb = grid_solid(belt_pt(THb, ZZ, BELT_T), belt_pt(THb, ZZ, -BELT_EMBED), True)
    parts_b.append((Vb, Fb))
    NIP = 11
    xs_ = np.linspace(-1.0, 1.0, NIP)
    okp = []
    for nm_, sg in (("plate.L", 1.0), ("plate.R", -1.0)):
        ui_, uo_ = plate_rows(zp_, sg)
        UU_ = ui_[None, :] + (uo_ - ui_)[None, :] * (0.5 + 0.5 * xs_)[:, None]      # (NIP, rows)
        ZZp = np.repeat(zp_[None, :], NIP, 0)
        dome = PLATE_T * (1.0 - 0.3 * xs_ ** 2)[:, None]                           # a slight dome across the plate
        Vp, Fp, ok_ = grid_solid(belt_pt(UU_ / R_W, ZZp, dome), belt_pt(UU_ / R_W, ZZp, -BELT_EMBED), False)
        parts_b.append((Vp, Fp)); okp.append(ok_)
    PARTS["belt"] = join(parts_b)
    BELT_INFO = {"rule": "a band round the waist (bottom edge dipping BELT_DIP at the front centre) + two side plates; each a "
                         "closed solid BELT_T / PLATE_T proud of the waist, BELT_EMBED into it, fused by the body voxel remesh",
                 "waist_axis_y_fit": [round(float(AXB[0]), 5), round(float(AXB[1]), 4)], "R_W": round(R_W, 4),
                 "waist_radius_outliers_replaced": int(OUTL.sum()),
                 "waist_radius_change_after_outliers": {
                     "max": round(float(np.abs(RG - RG_RAW)[~OUTL].max()), 4),
                     "p99": round(float(np.percentile(np.abs(RG - RG_RAW)[~OUTL], 99)), 4),
                     "max_at_deg_z": [round(float(np.degrees(TH_G[np.unravel_index(np.argmax(np.where(OUTL, 0, np.abs(RG - RG_RAW))), RG.shape)[0]])), 1),
                                      round(float(ZG[np.unravel_index(np.argmax(np.where(OUTL, 0, np.abs(RG - RG_RAW))), RG.shape)[1]]), 3)]},
                 "band_z": {"top": round(BZ_TOP, 4), "bottom_sides": round(BZ_BOT, 4), "bottom_centre": round(BZ_BOT - BELT_DIP, 4)},
                 "plate_z": [round(PZ0, 4), round(PZ1, 4)], "plate_deg": {"inner": PLATE_TH[0], "outer": PLATE_TH[1],
                                                                         "flare": PLATE_FLARE, "taper": PLATE_TAPER},
                 "hip_z_pre": round(Z_HIP0, 4), "neck_z_pre": round(Z_NECK0, 4), "solids_closed_manifold": [okb] + okp,
                 "reference": "design/reference/supaoctto-v3-belt-annotation.png"}
BELT_INFO["seconds"] = round(time.time() - t, 1)
report["belt"] = BELT_INFO


def belt_sdf(P2):
    return np.min([poly_sdf(P2, Q_) for Q_ in BELT_POLYS.values()], axis=0)


# =========================================================================== 4. water webs (new geometry)
t = time.time()
BODY_HIGH_V, BODY_HIGH_F = join([PARTS["body"], PARTS["head"], PARTS["neck"]] + ([PARTS["belt"]] if BELT and BELT_IN_WEB_CLEARANCE else []))
bvh_body_hi = BVHTree.FromPolygons(BODY_HIGH_V.tolist(), BODY_HIGH_F)


def signed_dist(bvh, p):
    loc, nrm, _, d = bvh.find_nearest(Vector(p))
    return d if (Vector(p) - loc).dot(nrm) >= 0 else -d


WEBS = [("web.R", "cape_outer.R", "cape_inner.R"), ("web.C", "cape_inner.R", "cape_inner.L"), ("web.L", "cape_inner.L", "cape_outer.L")]
WEB = {}
web_rep = {}
for wn, an, bn in WEBS:
    A, B = CHAINS[an], CHAINS[bn]
    NU, NW = WEB_NU, WEB_NW
    us = np.linspace(0.0, 1.0, NU + 1); ws = np.linspace(0.0, 1.0, NW + 1)
    G = np.zeros((NU + 1, NW + 1, 3)); VVw = np.zeros((NU + 1, NW + 1)); GAP = np.zeros((NU + 1, NW + 1))
    for i, u in enumerate(us):
        vedge = WEB_V_EDGE - WEB_SCALLOP * math.sin(math.pi * u)
        v = WEB_V_ROOT + ws * (vedge - WEB_V_ROOT)
        pa = chain_at(A, v * A["L"]); pb = chain_at(B, v * B["L"])
        G[i] = (1 - u) * pa + u * pb
        VVw[i] = v
        GAP[i] = np.linalg.norm(pb - pa, axis=1)

    def grid_normals(G):
        du = np.gradient(G, axis=0); dw = np.gradient(G, axis=1)
        n = np.cross(du, dw)
        n /= np.maximum(np.linalg.norm(n, axis=2, keepdims=True), 1e-12)
        if n[..., 1].mean() < 0:              # orient toward +Y (behind the character: the web's outer face)
            n = -n
        return n
    N0 = grid_normals(G)
    UU, WW = np.meshgrid(us, ws, indexing="ij")
    G = G + (WEB_BILLOW * GAP * np.sin(np.pi * UU) * smoothstep(0.0, 0.35, WW))[..., None] * N0
    O = np.zeros((NU + 1, NW + 1))
    N1 = grid_normals(G)
    for it in range(8):
        P_ = G + O[..., None] * N1
        pushed = False
        for i in range(1, NU):
            for j in range(NW + 1):
                sd = signed_dist(bvh_body_hi, P_[i, j])
                if sd < WEB_CLEAR:
                    O[i, j] += WEB_CLEAR - sd; pushed = True
        for _ in range(2):
            Os = O.copy()
            Os[1:-1, 1:-1] = np.maximum(O[1:-1, 1:-1], 0.25 * (O[:-2, 1:-1] + O[2:, 1:-1] + O[1:-1, :-2] + O[1:-1, 2:]))
            O = Os
        if not pushed:
            break
    G = G + O[..., None] * N1
    minclear = min(signed_dist(bvh_body_hi, G[i, j]) for i in range(1, NU) for j in range(NW + 1))
    N2 = grid_normals(G)
    L0 = G + N2 * (WEB_THICK / 2); L1 = G - N2 * (WEB_THICK / 2)
    idx = lambda l, i, j: l * (NU + 1) * (NW + 1) + i * (NW + 1) + j
    Vw = np.vstack([L0.reshape(-1, 3), L1.reshape(-1, 3)])
    UVW = np.vstack([np.stack([UU.ravel(), WW.ravel(), VVw.ravel()], 1)] * 2)
    Fw = []
    for i in range(NU):
        for j in range(NW):
            Fw.append([idx(0, i, j), idx(0, i + 1, j), idx(0, i + 1, j + 1), idx(0, i, j + 1)])
            Fw.append([idx(1, i, j + 1), idx(1, i + 1, j + 1), idx(1, i + 1, j), idx(1, i, j)])
    border = [(i, 0) for i in range(NU)] + [(NU, j) for j in range(NW)] + [(i, NW) for i in range(NU, 0, -1)] + [(0, j) for j in range(NW, 0, -1)]
    for k in range(len(border)):
        (i0, j0), (i1, j1) = border[k], border[(k + 1) % len(border)]
        Fw.append([idx(0, i0, j0), idx(1, i0, j0), idx(1, i1, j1), idx(0, i1, j1)])
    bm = bmesh.new()
    for p in Vw:
        bm.verts.new(p)
    bm.verts.ensure_lookup_table()
    for f in Fw:
        bm.faces.new([bm.verts[i] for i in f])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.verts.index_update()
    Fw = [[v.index for v in f.verts] for f in bm.faces]
    manifold = all(e.is_manifold for e in bm.edges)
    bm.free()
    hem = G[:, NW]
    tipA, tipB = A["P"][-1], B["P"][-1]
    WEB[wn] = {"V": Vw, "F": Fw, "UVW": UVW, "A": an, "B": bn, "mid": G[NU // 2].copy(), "across": None}
    web_rep[wn] = {"between": [an, bn], "tris": tri_count_F(Fw), "verts": int(len(Vw)), "closed_manifold": manifold,
                   "gap_width_range": [round(float(GAP.min()), 3), round(float(GAP.max()), 3)],
                   "max_clearance_push": round(float(O.max()), 4), "min_body_clearance": round(float(minclear), 4),
                   "hem_z_range": [round(float(hem[:, 2].min()), 4), round(float(hem[:, 2].max()), 4)],
                   "tentacle_tip_z": [round(float(tipA[2]), 4), round(float(tipB[2]), 4)],
                   "hem_to_tip_gap_at_tentacles": [round(float(hem[0, 2] - tipA[2]), 4), round(float(hem[-1, 2] - tipB[2]), 4)],
                   "depth_fraction_of_tentacle_length": [round(float(VVw[0, -1]), 4), round(float(VVw[NU // 2, -1]), 4)]}
report["webs"] = {"added": "3 water webs (the sculpt has none): thin closed sheets between neighbouring cape tentacles",
                  "per_web": web_rep, "seconds": round(time.time() - t, 1),
                  "shape": {"v_root": WEB_V_ROOT, "v_edge": WEB_V_EDGE, "scallop": WEB_SCALLOP, "billow": WEB_BILLOW,
                            "thickness": WEB_THICK, "clearance": WEB_CLEAR, "grid": [WEB_NU, WEB_NW]},
                  "v1_shape": {"v_edge": 0.62, "scallop": 0.16, "grid": [8, 12]}}

# =========================================================================== 5. low: two shells (remesh + decimate) + webs
t = time.time()
BODY_GROUPS = ["body", "head", "visor", "neck"] + (["belt"] if BELT else [])


def shell(groups, target, keep_face=False):
    V, F = join([PARTS[g] for g in groups])
    tmp = new_obj("remesh_src", V, F)
    rm = tmp.modifiers.new("vox", "REMESH"); rm.mode = "VOXEL"; rm.voxel_size = VOXEL; rm.use_smooth_shade = False
    RV, RF = evaluated_arrays(tmp)
    bpy.data.objects.remove(tmp, do_unlink=True)
    RV, RF, _ = keep_islands(RV, RF, 0.01)
    LV, LF = decimate(RV, RF, target)
    LV, LF, sp = keep_islands(LV, LF, 0.01)
    LV, rx = relax(LV, LF, RV, RF, keep_face=keep_face)
    return LV, LF, {"remesh_tris": tri_count_F(RF), "decimated_tris": tri_count_F(LF), "specks_dropped": sp,
                    "relax": rx}, RV, RF


def relax(LV, LF, RV, RF, keep_face=False):
    """'Smooth out': tangential relaxation of the decimated shell (each vertex toward its neighbours' centroid, then
    snapped back onto the remeshed high surface) -- evens the collapse decimator's irregular triangles so the flat facets
    shade as a smooth gradient instead of lumps. The mask window keeps its crisp edges (not moved); v3: the mouth area is
    relaxed like the rest of the mantle (no dimple any more: it must read as smooth, untouched surface)."""
    if LOW_RELAX_ITERS <= 0:
        return LV, {"iters": 0}
    bvh = BVHTree.FromPolygons(RV.tolist(), RF)
    E = edges_of(LF)
    n = len(LV)
    deg = np.maximum(np.bincount(E.ravel(), minlength=n).astype(float), 1.0)
    free = np.ones(n, bool)
    if keep_face:
        th = np.arctan2(LV[:, 0] - HXC, -(LV[:, 1] - HYC))
        free &= ~((np.cos(th) > 0.0) & (np.abs(th * R_REF) < float(np.abs(MASK_OUT[:, 0]).max()) + 0.4) &
                  (LV[:, 2] > Z_VC + MASK_LO[1] - 0.4) & (LV[:, 2] < Z_VC + MASK_HI[1] + 0.4))
    el_ = np.linalg.norm(LV[E[:, 0]] - LV[E[:, 1]], axis=1)
    mel = (np.bincount(E[:, 0], el_, minlength=n) + np.bincount(E[:, 1], el_, minlength=n)) / deg
    cen0 = np.zeros_like(LV)
    for k in range(3):
        cen0[:, k] = np.bincount(E[:, 0], LV[E[:, 1], k], minlength=n) + np.bincount(E[:, 1], LV[E[:, 0], k], minlength=n)
    cen0 /= deg[:, None]
    bd = np.linalg.norm(cen0 - LV, axis=1) > LOW_RELAX_SPIKE * mel   # tentacle tips / creases: spiky vertices stay put
    bd |= LV[:, 2] < float(LV[:, 2].min()) + LOW_RELAX_FLOOR_KEEP     # the planted foot tips keep their exact contact shape
    X = LV.copy()
    for _ in range(LOW_RELAX_ITERS):
        cen = np.zeros_like(X)
        for k in range(3):
            cen[:, k] = np.bincount(E[:, 0], X[E[:, 1], k], minlength=n) + np.bincount(E[:, 1], X[E[:, 0], k], minlength=n)
        cen /= deg[:, None]
        Y = X + LOW_RELAX_K * (cen - X)
        for i in np.nonzero(free & ~bd)[0]:
            X[i] = bvh.find_nearest(Vector(Y[i]))[0]
    mv = np.linalg.norm(X - LV, axis=1)
    return X, {"iters": LOW_RELAX_ITERS, "k": LOW_RELAX_K, "frozen_vertices": int((~free).sum()), "spiky_kept": int(bd.sum()),
               "mean_move": round(float(mv.mean()), 4), "max_move": round(float(mv.max()), 4)}


SBV, SBF, rb_, RBV, RBF = shell(BODY_GROUPS, LOW_TRIS_BODY, keep_face=True)
SCV, SCF, rc_, RCV, RCF = shell(["cape"], LOW_TRIS_CAPE)
allV = np.vstack([SBV, SCV] + [w["V"] for w in WEB.values()])
lo2, hi2 = allV.min(0), allV.max(0)
SHIFT = np.array([(lo2[0] + hi2[0]) / 2, (lo2[1] + hi2[1]) / 2, lo2[2]])
SBV = SBV - SHIFT; SCV = SCV - SHIFT; RBV = RBV - SHIFT; RCV = RCV - SHIFT
PARTS = {k: (v - SHIFT, f) for k, (v, f) in PARTS.items()}
BV = PARTS["body"][0]; CV_ = PARTS["cape"][0]
BODY_HIGH_V = BODY_HIGH_V - SHIFT
for c in CHAINS.values():
    c["P"] = c["P"] - SHIFT
for w in WEB.values():
    w["V"] = w["V"] - SHIFT; w["mid"] = w["mid"] - SHIFT
HXC, HYC = HXC - SHIFT[0], HYC - SHIFT[1]
Z_VC, Z_MOUTH, X_MID = Z_VC - SHIFT[2], Z_MOUTH - SHIFT[2], X_MID - SHIFT[0]
BELT_SHIFT = SHIFT.copy()                              # the belt frame (axis, radius grid) stays in the pre-shift frame
NB, NT = NB - SHIFT, NT - SHIFT
Z_BODY_TOP, Z_HEAD_BOT = Z_BODY_TOP - SHIFT[2], Z_HEAD_BOT - SHIFT[2]
H = float(hi2[2] - lo2[2])
size = hi2 - lo2
fp = float(max(size[0], size[1]))
k_fit = min(CELL_MAX_H / H, CELL_MAX_FP / fp)
GAME_M_PER_UNIT = k_fit
report["natural"] = {"height": round(H, 4), "width": round(float(size[0]), 4), "depth": round(float(size[1]), 4),
                     "footprint": round(fp, 4), "units": "sculpt units (natural proportions, no refit)",
                     "origin_shift_sculpt_units_after_yaw": SHIFT.round(5).tolist(),
                     "export_cell_fit_report_only": {"scale": round(k_fit, 5), "height_m": round(H * k_fit, 4),
                                                     "footprint_m": round(fp * k_fit, 4),
                                                     "bound_by": "height" if CELL_MAX_H / H <= CELL_MAX_FP / fp else "footprint",
                                                     "ceilings": [CELL_MAX_H, CELL_MAX_FP]}}
report["retopo"] = {"method": "body shell (Body+Head+mask+neck+belt joined) and cape shell each: voxel remesh (%.3f) -> "
                              "collapse decimation (deterministic) -> relax; remeshed separately so the cape never fuses to the "
                              "back; the remeshed shells are the bake/cavity high (v3: no siphon dimple)" % VOXEL,
                    "body_shell": rb_, "cape_shell": rc_, "seconds": round(time.time() - t, 1)}
bvh_highb = BVHTree.FromPolygons(RBV.tolist(), RBF)
bvh_highc = BVHTree.FromPolygons(RCV.tolist(), RCF)
dev_b = np.array([bvh_highb.find_nearest(Vector(p))[3] for p in SBV])
dev_c = np.array([bvh_highc.find_nearest(Vector(p))[3] for p in SCV])
dev = np.concatenate([dev_b, dev_c])
PB = {g: BVHTree.FromPolygons(PARTS[g][0].tolist(), PARTS[g][1]) for g in PARTS}
bvh_lowb = BVHTree.FromPolygons(SBV.tolist(), SBF)
bvh_lowc = BVHTree.FromPolygons(SCV.tolist(), SCF)
hi_s = np.vstack([RBV[::max(1, len(RBV) // 6000)], RCV[::max(1, len(RCV) // 6000)]])
dh = np.array([min(bvh_lowb.find_nearest(Vector(p))[3], bvh_lowc.find_nearest(Vector(p))[3]) for p in hi_s])
report["retopo"]["low_to_high_distance"] = {"mean": round(float(dev.mean()), 4), "p99": round(float(np.percentile(dev, 99)), 4),
                                            "max": round(float(dev.max()), 4), "pct_of_height_p99": round(100 * float(np.percentile(dev, 99)) / H, 3),
                                            "body_shell_p99": round(float(np.percentile(dev_b, 99)), 4),
                                            "cape_shell_p99": round(float(np.percentile(dev_c, 99)), 4)}
report["retopo"]["high_to_low_distance"] = {"mean": round(float(dh.mean()), 4), "p99": round(float(np.percentile(dh, 99)), 4),
                                            "max": round(float(dh.max()), 4), "samples": int(len(dh))}

# =========================================================================== landmarks (final frame)
leg_roots = [CHAINS["leg.L"]["P"][0], CHAINS["leg.R"]["P"][0]]
Z_HIP = float(np.mean([p[2] for p in leg_roots]))
NECK_ZA = Z_BODY_TOP - NECK_RAMP[0]
NECK_ZB = Z_HEAD_BOT + NECK_RAMP[1]
Z_NECK = 0.5 * (NECK_ZA + NECK_ZB)
Z_TOP = float(PARTS["head"][0][:, 2].max())
HB_V = PARTS["body"][0]
torso_hi = (BLAB == 0) & (np.abs(HB_V[:, 0] - X_MID) < 1.5)


def torso_y(z):
    if z > Z_BODY_TOP - 0.2:                  # inside the neck: its axis
        s_ = np.clip((z - NB[2]) / (NT[2] - NB[2]), 0, 1)
        return float(NB[1] + s_ * (NT[1] - NB[1]))
    m = torso_hi & (np.abs(HB_V[:, 2] - z) < 0.3)
    return float(HB_V[m, 1].mean()) if m.any() else float(HB_V[torso_hi, 1].mean())


SPINE_Z = [Z_HIP + f * (Z_NECK - Z_HIP) for f in SPINE_FRACS]
SPINE_J = [np.array([X_MID, torso_y(z), z]) for z in SPINE_Z]
Y_AXIS = float(np.mean([p[1] for p in SPINE_J]))
Z_EMB = Z_HIP + EMBLEM_Z_FRAC * (Z_NECK - Z_HIP)
hit_ = bvh_lowb.ray_cast(Vector((X_MID, -60.0, Z_EMB)), Vector((0.0, 1.0, 0.0)))
EMB_C = np.array(hit_[0])
bvh_head = BVHTree.FromPolygons(PARTS["head"][0].tolist(), PARTS["head"][1])    # the head in the final frame


def uz_of(P):
    th = np.arctan2(P[:, 0] - HXC, -(P[:, 1] - HYC))
    return np.stack([th * R_REF, P[:, 2] - Z_VC], 1), th


def sigil_sdf(P2):
    """The octopus-superhero sigil (emblem units): a mantle dome + 3 mirrored pairs of curling tentacles."""
    (cx, cz), (rx, rz) = EMB_DOME
    k_ = np.hypot((P2[:, 0] - cx) / rx, (P2[:, 1] - cz) / rz)
    d = (k_ - 1.0) * min(rx, rz)
    for tent in EMB_TENTACLES:
        for sgn in (1.0, -1.0):
            C = catmull([(sgn * x, z) for x, z in tent], 16)
            r = np.linspace(EMB_STROKE[0], EMB_STROKE[1], len(C))
            for c_, r_ in zip(C, r):
                d = np.minimum(d, np.hypot(P2[:, 0] - c_[0], P2[:, 1] - c_[1]) - r_)
    return d


# ---- detail zones: subdivide the low body shell where the visor / lens / siphon / emblem boundaries run
t = time.time()
fc_ = np.array([SBV[f].mean(0) for f in SBF])
uzf, thf = uz_of(fc_)
front_ = (np.cos(thf) > 0.15) & (np.abs(uzf[:, 0]) < float(np.abs(MASK_OUT[:, 0]).max()) + 0.5) & \
         (fc_[:, 2] > Z_VC + MASK_LO[1] - 0.5) & (fc_[:, 2] < Z_VC + MASK_HI[1] + 0.5)
zone_head = np.zeros(len(fc_), bool)
fi_ = np.nonzero(front_)[0]
near_ = (np.abs(mask_sdf(uzf[fi_]) - VISOR_REGION_PAD) < DETAIL_BAND) | (np.abs(lens_sdf(uzf[fi_])) < DETAIL_BAND)
zone_head[fi_[near_]] = True
MOUTH_C2 = catmull(MOUTH_LINE, 40)                     # the dense smirk line, (u, dz about Z_MOUTH)
mfront_ = (np.cos(thf) > 0.3) & (np.abs(fc_[:, 2] - Z_MOUTH) < 1.0) & (np.abs(uzf[:, 0]) < 1.5)
zone_mouth = np.zeros(len(fc_), bool)
fm_ = np.nonzero(mfront_)[0]
zone_mouth[fm_[np.abs(polyline_sd(np.stack([uzf[fm_, 0], fc_[fm_, 2] - Z_MOUTH], 1), MOUTH_C2)[0]) < MOUTH_BAND]] = True
zone_head &= ~zone_mouth
chest_ = (np.abs(fc_[:, 0] - X_MID) < EMBLEM_SIZE * 1.1 + 0.3) & (np.abs(fc_[:, 2] - Z_EMB) < EMBLEM_SIZE * 1.1 + 0.3) & \
         (fc_[:, 1] < EMB_C[1] + 1.0)
zone_emb = np.zeros(len(fc_), bool)
fe_ = np.nonzero(chest_)[0]
zone_emb[fe_[np.abs(sigil_sdf(np.stack([(fc_[fe_, 0] - EMB_C[0]) / EMBLEM_SIZE, (fc_[fe_, 2] - EMB_C[2]) / EMBLEM_SIZE], 1)) * EMBLEM_SIZE) < DETAIL_BAND]] = True
zone = zone_head | zone_emb | zone_mouth
tris_pre_detail = tri_count_F(SBF)
bm = bmesh.new()
for p in SBV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in SBF:
    bm.faces.new([bm.verts[i] for i in f])
bm.faces.ensure_lookup_table()
ekey = lambda e: (min(e.verts[0].index, e.verts[1].index), max(e.verts[0].index, e.verts[1].index))
ecut = {}                                              # edge -> cuts; an edge in two zones takes the finer one (disjoint sets)
for zmask, cuts_ in ((zone_emb, DETAIL_CUTS_EMBLEM), (zone_head, DETAIL_CUTS), (zone_mouth, DETAIL_CUTS_MOUTH)):
    for fi in np.nonzero(zmask)[0]:
        for e in bm.faces[int(fi)].edges:
            ecut[e] = max(ecut.get(e, 0), cuts_)
esel = {}
for e, c_ in ecut.items():
    esel.setdefault(c_, []).append(e)
esel = {c_: sorted(es_, key=ekey) for c_, es_ in esel.items()}
for cuts_ in sorted(esel, reverse=True):
    bmesh.ops.subdivide_edges(bm, edges=esel[cuts_], cuts=cuts_, use_grid_fill=True)
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])
bm.verts.index_update()
SBV = np.array([v.co[:] for v in bm.verts])
SBF = [[v.index for v in f.verts] for f in bm.faces]
bm.free()
report["detail_zones"] = {"zones": "faces within DETAIL_BAND of the mask / lens / sigil boundaries; within MOUTH_BAND of "
                                   "the smirk line",
                          "cuts": {"face": DETAIL_CUTS, "emblem": DETAIL_CUTS_EMBLEM, "mouth": DETAIL_CUTS_MOUTH},
                          "band": DETAIL_BAND, "mouth_band": MOUTH_BAND,
                          "faces_selected": {"head": int(zone_head.sum()), "emblem": int(zone_emb.sum()),
                                             "mouth": int(zone_mouth.sum())},
                          "body_shell_tris_before": tris_pre_detail, "body_shell_tris_after": tri_count_F(SBF),
                          "seconds": round(time.time() - t, 1)}

# =========================================================================== fields (per low shell vertex)
t = time.time()
SV = np.vstack([SBV, SCV]); SF = SBF + [[i + len(SBV) for i in f] for f in SCF]
nb_, nc_ = len(SBV), len(SCV)
shell_id = np.concatenate([np.zeros(nb_), np.ones(nc_)])
DGd = np.array([[PB[g].find_nearest(Vector(p))[3] for g in BODY_GROUPS] for p in SBV])
Jg = {g: j for j, g in enumerate(BODY_GROUPS)}
d_headg = np.minimum(DGd[:, Jg["head"]], DGd[:, Jg["visor"]])
d_bodyg = np.minimum(DGd[:, Jg["body"]], DGd[:, Jg["neck"]])
if BELT:
    d_bodyg = np.minimum(d_bodyg, DGd[:, Jg["belt"]])
fhd = np.concatenate([d_bodyg - d_headg, np.full(nc_, -1.0)])          # > 0: the head group (mantle + visor)
uzv, thv = uz_of(SV)
gate_face = (shell_id == 0) & (np.cos(thv) > 0.1) & (fhd > -0.25) & \
            (SV[:, 2] > min(Z_MOUTH - 1.0, Z_VC + MASK_LO[1] - 0.6)) & (SV[:, 2] < Z_VC + MASK_HI[1] + 0.6)
gi_ = np.nonzero(gate_face)[0]
vis_f = np.full(len(SV), 10.0); vis_f[gi_] = mask_sdf(uzv[gi_]) - VISOR_REGION_PAD
lens_f = np.full(len(SV), 10.0); lens_f[gi_] = lens_sdf(uzv[gi_])
# v3 smirk line: signed distance (+ above) to the line in the (u, z - Z_MOUTH) frame, only where the nearest point is
# interior to the line (the cut stops short of the corners: the lips stay joined there)
gate_mouth = (shell_id == 0) & (fhd > 0) & (np.cos(thv) > 0.3) & (np.abs(SV[:, 2] - Z_MOUTH) < 0.8) & (np.abs(uzv[:, 0]) < 1.3)
mq_ = np.nonzero(gate_mouth)[0]
msd_, ms_, mint_ = polyline_sd(np.stack([uzv[mq_, 0], SV[mq_, 2] - Z_MOUTH], 1), MOUTH_C2)
mline_f = np.full(len(SV), 10.0)
mline_f[mq_] = np.where(mint_ & (np.abs(msd_) < MOUTH_SOFT + 0.15), msd_, 10.0)
# v3 belt: the (u = theta x R_W, z) waist frame (pre-shift coordinates), gated to torso vertices on the waist surface
belt_f = np.full(len(SV), 10.0)
if BELT:
    Ppre = SV + BELT_SHIFT
    XM0 = X_MID + BELT_SHIFT[0]
    gate_belt = (shell_id == 0) & (fhd < -0.05) & (Ppre[:, 2] > BZ_LO) & (Ppre[:, 2] < BZ_HI)
    gb_ = np.nonzero(gate_belt)[0]
    thb_ = np.arctan2(Ppre[gb_, 0] - XM0, -(Ppre[gb_, 1] - belt_cy(Ppre[gb_, 2])))
    rv_ = np.hypot(Ppre[gb_, 0] - XM0, Ppre[gb_, 1] - belt_cy(Ppre[gb_, 2]))
    on_ = rv_ < belt_r(thb_, Ppre[gb_, 2]) + PLATE_T + 0.15          # the waist surface + the belt (not an arm)
    gb_ = gb_[on_]
    belt_f[gb_] = belt_sdf(np.stack([thb_[on_] * R_W, Ppre[gb_, 2]], 1)) - BELT_REGION_PAD
kd_b = KDTree(len(BV))
for i, p in enumerate(BV):
    kd_b.insert(p, i)
kd_b.balance()
kd_c = KDTree(len(CV_))
for i, p in enumerate(CV_):
    kd_c.insert(p, i)
kd_c.balance()
CH_KD = {}
for n_, c in CHAINS.items():
    kd = KDTree(len(c["P"]))
    for i, p in enumerate(c["P"]):
        kd.insert(p, i)
    kd.balance()
    CH_KD[n_] = kd
ID_TO_CH = {2: "arm.L", 3: "arm.R", 4: "leg.L", 5: "leg.R"}
ID_TO_CH.update({10 + i: n for i, n in enumerate(CAPE_NAMES)})


def label_and_arc(V, is_cape):
    lab = np.array([(CLAB if is_cape else BLAB)[(kd_c if is_cape else kd_b).find(p)[1]] for p in V], dtype=np.int32)
    s = np.full(len(V), -1.0)
    for i, p in enumerate(V):
        ch = ID_TO_CH.get(int(lab[i]))
        if ch:
            s[i] = float(CHAINS[ch]["s"][CH_KD[ch].find(p)[1]])
    return lab, s


lab_b, s_b = label_and_arc(SBV, False)
lab_c, s_c = label_and_arc(SCV, True)
# the neck / head / visor are not in the Body part: never an arm or leg, whatever the nearest Body vertex says
lab_b = np.where(fhd[:nb_] > -0.05, 0, lab_b)
lab_b = np.where(DGd[:, Jg["neck"]] < DGd[:, Jg["body"]] - 0.02, 0, lab_b)
beltv = np.zeros(len(SV))
if BELT:                                               # the belt is torso (never an arm or leg), whatever the nearest Body vertex says
    bv_ = DGd[:, Jg["belt"]] < DGd[:, Jg["body"]] - 0.02
    lab_b = np.where(bv_, 0, lab_b)
    beltv[:nb_] = bv_
s_b = np.where(lab_b == 0, -1.0, s_b)
LAB0 = np.concatenate([lab_b, lab_c]); S0 = np.concatenate([s_b, s_c])
frac = np.array([S0[i] / CHAINS[ID_TO_CH[int(LAB0[i])]]["L"] if int(LAB0[i]) in ID_TO_CH else -1.0 for i in range(len(SV))])
armf = np.where(np.isin(LAB0, [2, 3]) & (shell_id == 0), frac, -1.0)
legf = np.where(np.isin(LAB0, [4, 5]) & (shell_id == 0), frac, -1.0)
capef = np.where((LAB0 >= 10) & (LAB0 < 20) & (shell_id == 1), frac, -1.0)
cme = bpy.data.meshes.new("tmp_cape"); cme.from_pydata(SCV.tolist(), [], SCF); cme.update()
cn = np.empty(nc_ * 3); cme.vertex_normals.foreach_get("vector", cn); cn = cn.reshape(-1, 3)
bpy.data.meshes.remove(cme)
inward = np.stack([X_MID - SCV[:, 0], Y_AXIS - SCV[:, 1], np.zeros(nc_)], 1)
inward /= np.maximum(np.linalg.norm(inward, axis=1, keepdims=True), 1e-9)
under = np.concatenate([np.full(nb_, -2.0), (cn * inward).sum(1)])
# the sigil: chest-front vertices, front projection (x, z) about EMB_C in emblem units
gate_emb = (shell_id == 0) & (LAB0 == 0) & (fhd < -0.05) & (SV[:, 1] < EMB_C[1] + 1.2) & \
           (np.abs(SV[:, 0] - X_MID) < 2.2 * EMBLEM_SIZE) & (np.abs(SV[:, 2] - Z_EMB) < 2.2 * EMBLEM_SIZE)
emb_f = np.full(len(SV), 10.0)
ge = np.nonzero(gate_emb)[0]
emb_f[ge] = sigil_sdf(np.stack([(SV[ge, 0] - EMB_C[0]) / EMBLEM_SIZE, (SV[ge, 2] - EMB_C[2]) / EMBLEM_SIZE], 1)) * EMBLEM_SIZE
FIELDS = {"fhd": fhd, "vis": vis_f, "lens": lens_f, "mline": mline_f, "belt": belt_f, "armf": armf, "legf": legf,
          "capef": capef, "under": under, "emb": emb_f, "shell": shell_id, "beltv": beltv,
          "pocket": np.zeros(len(SV)), "lipside": np.zeros(len(SV))}
report["fields_seconds"] = round(time.time() - t, 1)

# =========================================================================== iso-contour cuts (duskmaw's cutter)
bm = bmesh.new()
for p in SV:
    bm.verts.new(p)
bm.verts.ensure_lookup_table()
for f in SF:
    bm.faces.new([bm.verts[i] for i in f])
bm.verts.index_update()
LAY = {k: bm.verts.layers.float.new(k) for k in FIELDS}
for v in bm.verts:
    for k, arr in FIELDS.items():
        v[LAY[k]] = float(arr[v.index])


def iso_cut(key, tau, gate=None):
    L = LAY[key]
    eps = 1e-5 * max(1.0, abs(tau))
    on = lambda v: abs(v[L] - tau) <= eps
    side = lambda v: 0 if on(v) else (1 if v[L] > tau else -1)
    ok = (lambda a, b: True) if gate is None else gate
    for e in bm.edges:
        a, b = e.verts
        sa, sb = side(a), side(b)
        if sa * sb < 0 and ok(a, b):
            tt = (tau - a[L]) / (b[L] - a[L])
            if tt < CUT_SNAP:
                a[L] = tau
            elif tt > 1 - CUT_SNAP:
                b[L] = tau
    cuts = [e for e in bm.edges if side(e.verts[0]) * side(e.verts[1]) < 0 and ok(e.verts[0], e.verts[1])]
    nsplit = 0
    for e in cuts:
        a, b = e.verts
        tt = (tau - a[L]) / (b[L] - a[L])
        vals = {k: a[LAY[k]] * (1 - tt) + b[LAY[k]] * tt for k in LAY}
        _, nv = bmesh.utils.edge_split(e, a, tt)
        for k in LAY:
            nv[LAY[k]] = vals[k]
        nv[L] = tau
        nsplit += 1
    pairs = []
    for f in bm.faces:
        vs = list(f.verts)
        sides = [side(v) for v in vs]
        if not (1 in sides and -1 in sides):
            continue
        cv = [v for v, s_ in zip(vs, sides) if s_ == 0]
        if len(cv) == 2:
            pairs.append(cv)
    for cv in pairs:
        bmesh.ops.connect_verts(bm, verts=cv)
    big = [f for f in bm.faces if len(f.verts) > 3]
    if big:
        bmesh.ops.triangulate(bm, faces=big)
    return {"field": key, "tau": round(float(tau), 4), "edge_splits": nsplit, "face_connects": len(pairs)}


in_b = lambda v: v[LAY["shell"]] < 0.5
in_c = lambda v: v[LAY["shell"]] > 0.5
g_body = lambda a, b: in_b(a) and in_b(b)
g_face = lambda a, b: g_body(a, b) and a[LAY["vis"]] < 9 and b[LAY["vis"]] < 9
g_arm = lambda a, b: g_body(a, b) and a[LAY["armf"]] >= 0 and b[LAY["armf"]] >= 0
g_leg = lambda a, b: g_body(a, b) and a[LAY["legf"]] >= 0 and b[LAY["legf"]] >= 0
g_emb = lambda a, b: g_body(a, b) and a[LAY["emb"]] < 9 and b[LAY["emb"]] < 9
g_cape = lambda a, b: in_c(a) and in_c(b)
g_ctip = lambda a, b: g_cape(a, b) and a[LAY["capef"]] >= 0 and b[LAY["capef"]] >= 0
g_mline = lambda a, b: g_body(a, b) and a[LAY["mline"]] < 9 and b[LAY["mline"]] < 9
g_beltc = lambda a, b: g_body(a, b) and a[LAY["belt"]] < 9 and b[LAY["belt"]] < 9
CUTS = [("fhd", 0.0, g_body), ("vis", 0.0, g_face), ("lens", 0.0, g_face), ("mline", 0.0, g_mline),
        ("armf", ARM_TIP_FRAC, g_arm), ("legf", LEG_TIP_FRAC, g_leg), ("emb", 0.0, g_emb), ("under", UNDER_T, g_cape),
        ("capef", CAPE_TIP_FRAC, g_ctip)] + ([("belt", 0.0, g_beltc)] if BELT else [])
t = time.time()
cut_log = [iso_cut(k_, tau_, gate_) for k_, tau_, gate_ in CUTS]
straddle = {}
for key, tau, gate in CUTS:
    L = LAY[key]; eps = 1e-5 * max(1.0, abs(tau)); n_ = 0
    for f in bm.faces:
        vs = list(f.verts)
        if gate is not None and not all(gate(vs[i], vs[(i + 1) % len(vs)]) for i in range(len(vs))):
            continue
        vals = [v[L] for v in vs]
        if min(vals) < tau - eps and max(vals) > tau + eps:
            n_ += 1
    straddle["%s=%.3f" % (key, tau)] = n_
report["iso_cuts"] = {"cuts": cut_log, "seconds": round(time.time() - t, 1),
                      "rule": "edge split at the field's iso-value (snap within CUT_SNAP of a vertex) + face connects; "
                              "fields interpolate linearly along split edges; no face straddles any boundary afterwards",
                      "straddling_faces_after_cut": straddle}
bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 3])


# =========================================================================== v3 smirk mouth: a slit along the line + a pocket
def mouth_uz(P):
    P = np.atleast_2d(np.asarray(P, float))
    th_ = np.arctan2(P[:, 0] - HXC, -(P[:, 1] - HYC))
    return np.stack([th_ * R_REF, P[:, 2] - Z_MOUTH], 1)


t = time.time()
LMl = LAY["mline"]
bad_before = sum(1 for e in bm.edges if not e.is_manifold)
bm.verts.index_update()
line_vs = [v for v in bm.verts if v[LAY["shell"]] < 0.5 and abs(v[LMl]) <= 1e-5]
s_of = {}
for v in line_vs:
    s_of[v] = float(polyline_sd(mouth_uz(v.co), MOUTH_C2)[1][0])
line_vs.sort(key=lambda v: (s_of[v], v.index))
runs, cur = [], [line_vs[0]]
for a_, b_ in zip(line_vs[:-1], line_vs[1:]):
    if bm.edges.get((a_, b_)) is not None:
        cur.append(b_)
    else:
        runs.append(cur); cur = [b_]
runs.append(cur)
chain = max(runs, key=len)
assert len(chain) >= 8, ("smirk cut too short", [len(r_) for r_ in runs])
P_ch = np.array([v.co[:] for v in chain])
arc_ch = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(P_ch, axis=0), axis=1))])
sc_ch = arc_ch / arc_ch[-1]                               # 0 .. 1 along the slit (the lips stay joined at 0 and 1)
cedges = [bm.edges.get((chain[i], chain[i + 1])) for i in range(len(chain) - 1)]
assert all(e is not None for e in cedges)
nv0 = len(bm.verts)
bmesh.ops.split_edges(bm, edges=cedges)
bm.verts.index_update()
bm.verts.ensure_lookup_table()
assert len(bm.verts) == nv0 + len(chain) - 2, ("split did not duplicate exactly the interior slit vertices", len(bm.verts) - nv0)
VLIST = list(bm.verts)                                   # a snapshot: new pocket vertices invalidate bm.verts' lookup table
kd_s = KDTree(len(VLIST))
for j, v in enumerate(VLIST):
    kd_s.insert(v.co, j)
kd_s.balance()
UPPER, LOWER, POCKET = [chain[0]], [chain[0]], [chain[0]]
for i in range(1, len(chain) - 1):
    cps = sorted([VLIST[j] for (_, j, _) in kd_s.find_range(Vector(P_ch[i]), 1e-7)], key=lambda v: v.index)
    assert len(cps) == 2, ("slit vertex copies", i, len(cps))
    side = []
    for v in cps:
        cen = np.array([f.calc_center_median()[:] for f in v.link_faces])
        side.append(float(polyline_sd(mouth_uz(cen), MOUTH_C2)[0].mean()))
    assert side[0] * side[1] < 0, ("slit copies on the same side", side)
    up_, lo_ = (cps[0], cps[1]) if side[0] > 0 else (cps[1], cps[0])
    UPPER.append(up_); LOWER.append(lo_)
    th_ = math.atan2(P_ch[i, 0] - HXC, -(P_ch[i, 1] - HYC))
    nrm_ = np.array([math.sin(th_), -math.cos(th_), 0.0])
    q = bm.verts.new(P_ch[i] - nrm_ * MOUTH_DEPTH * math.sin(math.pi * sc_ch[i]) ** 0.5)
    for k_, L_ in LAY.items():
        q[L_] = up_[L_]
    q[LAY["pocket"]] = 1.0
    POCKET.append(q)
    up_[LAY["lipside"]] = 1.0; lo_[LAY["lipside"]] = -1.0
UPPER.append(chain[-1]); LOWER.append(chain[-1]); POCKET.append(chain[-1])


def traverses(a_, b_):
    e_ = bm.edges.get((a_, b_))
    return any(lp.vert is a_ and lp.link_loop_next.vert is b_ for lp in e_.link_loops)


new_f = []
for i in range(len(chain) - 1):
    for LIP in (UPPER, LOWER):
        quad = [LIP[i], LIP[i + 1], POCKET[i + 1], POCKET[i]]
        if traverses(LIP[i], LIP[i + 1]):              # the surface face owns lip_i -> lip_i+1: the pocket runs the other way
            quad = quad[::-1]
        vs_ = []
        for v in quad:
            if v not in vs_:
                vs_.append(v)
        new_f.append(bm.faces.new(vs_))
bmesh.ops.triangulate(bm, faces=[f for f in new_f if len(f.verts) > 3])
body_edges = [e for e in bm.edges if e.verts[0][LAY["shell"]] < 0.5]
bad_after = sum(1 for e in body_edges if not e.is_manifold)
incons = sum(1 for e in body_edges if not e.is_contiguous)
assert bad_after == 0 and incons == 0, ("mouth surgery broke the body shell", bad_after, incons)
MOUTH_SURGERY = {"rule": "iso-cut along the smirk line (field = signed distance in the (u, z) mantle frame, cut only where "
                         "the nearest point is interior) -> the connected cut chain is split (interior vertices duplicated: "
                         "the lips; the two end vertices stay shared: the corners) -> a pocket strip (upper lip -> pocket "
                         "row -> lower lip) closes the slit INSIDE the mantle, MOUTH_DEPTH x sqrt(sin(pi s)) deep",
                 "cut_runs": [len(r_) for r_ in runs], "slit_vertices": len(chain), "slit_s_range": [round(s_of[chain[0]], 4),
                                                                                                   round(s_of[chain[-1]], 4)],
                 "lip_pairs": len(chain) - 2, "pocket_vertices": len(chain) - 2, "pocket_faces_before_tri": len(new_f),
                 "non_manifold_edges_before_after": [bad_before, bad_after], "winding_inconsistent_edges": incons,
                 "slit_length_3d": round(float(arc_ch[-1]), 4),
                 "corner_to_corner_3d": round(float(np.linalg.norm(P_ch[-1] - P_ch[0])), 4)}
bm.verts.index_update(); bm.faces.index_update()
MOUTH_PAIRS = [(UPPER[i].index, LOWER[i].index, float(sc_ch[i])) for i in range(1, len(chain) - 1)]
MOUTH_CORNERS = [chain[0].index, chain[-1].index]
MOUTH_SC = (s_of[chain[0]], s_of[chain[-1]])
MOUTH_SURGERY["seconds"] = round(time.time() - t, 2)
report["mouth"] = {"surgery": MOUTH_SURGERY}
CV = np.array([v.co[:] for v in bm.verts])
CF = [[v.index for v in f.verts] for f in bm.faces]
FVAL = {k: np.array([np.mean([v[LAY[k]] for v in f.verts]) for f in bm.faces]) for k in LAY}
VFIELD = {k: np.array([v[LAY[k]] for v in bm.verts]) for k in LAY}
bm.free()

# =========================================================================== regions + final mesh (cut shells + webs)
REG = ["skin", "head", "arm_tip", "leg_tip", "emblem", "belt", "visor", "lens", "mouth", "cape", "cape_under", "cape_tip",
       "membrane", "membrane_rim"]
R_ = {n: i for i, n in enumerate(REG)}
nf_c = len(CF)
fsh = FVAL["shell"] > 0.5
rid = np.full(nf_c, R_["skin"], dtype=np.int32)
rid[~fsh & (FVAL["fhd"] > 0)] = R_["head"]
bodyf = ~fsh & (FVAL["fhd"] <= 0)
rid[bodyf & (FVAL["armf"] > ARM_TIP_FRAC)] = R_["arm_tip"]
rid[bodyf & (FVAL["legf"] > LEG_TIP_FRAC)] = R_["leg_tip"]
rid[bodyf & (FVAL["emb"] < 0)] = R_["emblem"]
rid[bodyf & (FVAL["belt"] < 0)] = R_["belt"]
rid[~fsh & (FVAL["vis"] < 0)] = R_["visor"]
rid[~fsh & (FVAL["lens"] < 0)] = R_["lens"]
rid[~fsh & (FVAL["pocket"] > 0)] = R_["mouth"]          # ONLY the pocket faces (enclosed while the lips are shut)
rid[fsh] = R_["cape"]
rid[fsh & (FVAL["under"] > UNDER_T)] = R_["cape_under"]
rid[fsh & (FVAL["capef"] > CAPE_TIP_FRAC)] = R_["cape_tip"]
FV = [CV]; FF = list(CF); o_ = len(CV)
WEB_VRANGE = {}
web_face_w = []
for wn in ("web.R", "web.C", "web.L"):
    w = WEB[wn]
    WEB_VRANGE[wn] = (o_, o_ + len(w["V"]))
    FV.append(w["V"]); FF += [[i + o_ for i in f] for f in w["F"]]
    web_face_w += [float(min(w["UVW"][i, 1] for i in f)) for f in w["F"]]      # min: whole grid rows, no zig-zag
    o_ += len(w["V"])
FV = np.vstack(FV)
web_face_w = np.array(web_face_w)
rid = np.concatenate([rid, np.where(web_face_w > WEB_RIM_W, R_["membrane_rim"], R_["membrane"]).astype(np.int32)])
N_WEB_TRIS = int(len(web_face_w))
low_me = bpy.data.meshes.new(UNIT)
low_me.from_pydata(FV.tolist(), [], FF)
low_me.update()
low = bpy.data.objects.new(UNIT, low_me)
scene.collection.objects.link(low)
me = low.data
nf = len(me.polygons)
assert nf == len(rid)
report["tris_final"] = int(sum(len(f) - 2 for f in FF))
report["tris_breakdown"] = {"body_shell_and_cape_shell_after_cuts": tri_count_F(CF), "webs": N_WEB_TRIS}
report["tris_v1"] = {"final": 11370, "shells_after_cuts": 9978, "webs": 1392}
report["tris_v2"] = {"final": 21640, "shells_after_cuts": 19600, "webs": 2040}
report["region_rule"] = {
    "head": "body-shell faces nearer the mantle/visor than the body/neck (part Voronoi, cut at 0)",
    "visor": "head-front faces inside the mask outline (W or starfish; SDF in the (u, z) mantle frame)",
    "lens": "inside either lens (the W wing inset by VISOR_FRAME_W; the bridge between them stays frame)",
    "mouth": "ONLY the pocket faces behind the slit (enclosed inside the mantle while the lips are shut: invisible at rest)",
    "belt": "torso faces inside the belt outline (band + two side plates) in the (u = theta x R_W, z) waist frame, + pad",
    "emblem": "the octopus sigil SDF (mantle dome + 3 mirrored curling tentacle pairs), front projection about EMB_C",
    "arm_tip / leg_tip": "the traced arm / leg tentacle past ARM_TIP_FRAC / LEG_TIP_FRAC of its centreline",
    "cape_under": "cape surface whose smoothed normal faces the body axis more than UNDER_T (the sucker-side lining)",
    "cape_tip": "cape tentacle past CAPE_TIP_FRAC", "membrane": "the built water webs", "membrane_rim": "web rows past WEB_RIM_W"}

# cavity shade (from the remeshed high) + optional per-face value jitter
t = time.time()
HV_all, HF_all = join([(RBV, RBF), (RCV, RCF)])
HIGH = new_obj(UNIT + "_high", HV_all, HF_all)
kd_h = KDTree(len(HV_all))
for i, p in enumerate(HV_all):
    kd_h.insert(p, i)
kd_h.balance()
hme = HIGH.data
ev_h = np.empty(len(hme.edges) * 2, dtype=np.int64); hme.edges.foreach_get("vertices", ev_h); ev_h = ev_h.reshape(-1, 2)
nv_h = np.empty(len(hme.vertices) * 3); hme.vertex_normals.foreach_get("vector", nv_h); nv_h = nv_h.reshape(-1, 3)
deg = np.bincount(ev_h.ravel(), minlength=len(HV_all)).astype(float)


def nmean(X):
    s_ = np.zeros_like(X)
    for k in range(X.shape[1]):
        s_[:, k] = np.bincount(ev_h[:, 0], X[ev_h[:, 1], k], minlength=len(HV_all)) + np.bincount(ev_h[:, 1], X[ev_h[:, 0], k], minlength=len(HV_all))
    return s_ / np.maximum(deg, 1)[:, None]


el = np.linalg.norm(HV_all[ev_h[:, 0]] - HV_all[ev_h[:, 1]], axis=1).mean()
cav = ((nmean(HV_all) - HV_all) * nv_h).sum(1) / el
for _ in range(10):
    cav = nmean(cav[:, None])[:, 0] * 0.5 + cav * 0.5
cav = np.clip(cav / (np.percentile(np.abs(cav), 95) + 1e-9), -1, 1)
FC = np.empty(nf * 3); me.polygons.foreach_get("center", FC); FC = FC.reshape(-1, 3)
fcav = np.array([np.mean([cav[j] for (_, j, _) in kd_h.find_n(p, 12)]) for p in FC])
fcav[nf_c:] = 0.0                                     # webs: no sculpt beneath them
shade = 1.0 - CAVITY_K * np.clip(fcav, 0, 1) + 0.05 * np.clip(-fcav, 0, 1)
jit = (np.sin(FC @ np.array([12.9898, 78.233, 37.719]) * 43.7585) * 43758.5453) % 1.0
shade *= (1.0 - FACE_JITTER / 2) + FACE_JITTER * jit
PAL.store_regions(me, REG, rid, shade)
pal_default = PAL.load(UNIT, "default")
report["regions_faces"] = PAL.paint(me, pal_default)
report["shade"] = {"cavity_k": CAVITY_K, "face_jitter": FACE_JITTER, "v1": {"cavity_k": 0.40, "face_jitter": 0.08},
                   "shade_range": [round(float(shade[:nf_c].min()), 4), round(float(shade[:nf_c].max()), 4)],
                   "shade_std_body": round(float(shade[:nf_c].std()), 4)}
report["cavity_seconds"] = round(time.time() - t, 1)
fa = np.empty(nf); me.polygons.foreach_get("area", fa)
report["regions_area_share"] = {n: round(float(fa[rid == R_[n]].sum() / fa.sum()), 4) for n in REG}
if BELT:                                               # belt fit: how far the belt surface stands off the (belt-free) body
    bvf_ = np.unique(np.array([v for fi, f in enumerate(FF) if rid[fi] == R_["belt"] for v in f], dtype=np.int64))
    so_ = np.array([PB["body"].find_nearest(Vector(FV[v]))[3] for v in bvf_])
    report["belt"]["fit"] = {
        "rule": "belt-region vertices of the final mesh: distance to the smoothed body sculpt WITHOUT the belt (the waist "
                "it sits on); the belt is fused by the remesh, so 0 = its rim meeting the body, the rest = its thickness",
        "vertices": int(len(bvf_)), "standoff_p50": round(float(np.percentile(so_, 50)), 4),
        "standoff_p95": round(float(np.percentile(so_, 95)), 4), "standoff_max": round(float(so_.max()), 4),
        "design_thickness": {"band": BELT_T, "plate_max": PLATE_T},
        "gap_between_belt_and_body": "none by construction (one fused remesh shell)"}

# alignment report: centroids of the placed parts vs the body midline
def region_centroid(n):
    m_ = rid == R_[n]
    return (FC[m_] * fa[m_, None]).sum(0) / max(fa[m_].sum(), 1e-12)


report["alignment"] = {"body_midline_x": round(X_MID, 4),
                       "visor_centroid_x_offset": round(float(region_centroid("visor")[0] - X_MID), 4),
                       "lens_centroid_x_offset": round(float(region_centroid("lens")[0] - X_MID), 4),
                       "mouth_pocket_centroid_x_offset": round(float(region_centroid("mouth")[0] - X_MID), 4),
                       "belt_centroid_x_offset": round(float(region_centroid("belt")[0] - X_MID), 4) if BELT else None,
                       "emblem_centroid_x_offset": round(float(region_centroid("emblem")[0] - X_MID), 4),
                       "emblem_centre": EMB_C.round(4).tolist(), "v1_emblem_centre_x_offset": -0.314,
                       "neck_axis_x": round(float(NB[0]), 4)}

# facing landmark (direction-free): the head (mantle) bbox centre -> the visor's centroid
HVf = PARTS["head"][0]
anchor = (HVf.min(0) + HVf.max(0)) / 2
landmark = PARTS["visor"][0].mean(0)
dvec = landmark - anchor
src_d = RY.T @ dvec
report["facing"] = {"rule": "head (mantle) bbox centre -> centroid of the W-visor part",
                    "anchor": anchor.round(4).tolist(), "landmark": landmark.round(4).tolist(),
                    "source_angle_from_minusY_deg": round(math.degrees(math.atan2(src_d[0], -src_d[1])), 2),
                    "angle_from_minusY_deg": round(math.degrees(math.atan2(dvec[0], -dvec[1])), 2),
                    "cape_centroid_y": round(float(PARTS["cape"][0][:, 1].mean()), 4)}
MOUTH_CENTRE = CV[[p[0] for p in MOUTH_PAIRS]].mean(0)
LMK = {"visor": (region_centroid("visor") * 0.5 + region_centroid("lens") * 0.5).round(4).tolist(),
       "emblem": EMB_C.round(4).tolist(), "mouth": MOUTH_CENTRE.round(4).tolist(),
       "neck": (0.5 * (NB + NT)).round(4).tolist(), "head": anchor.round(4).tolist(),
       "head_top_z": round(Z_TOP, 4), "neck_bottom_z": round(float(NB[2]), 4)}
if BELT:
    zc_ = 0.5 * (BZ_TOP + BZ_BOT)
    LMK["belt"] = (np.array([X_MID + BELT_SHIFT[0], float(belt_cy(zc_)) - float(belt_r(0.0, zc_)) - BELT_T, zc_]) -
                   BELT_SHIFT).round(4).tolist()
report["landmarks_v3"] = LMK

# =========================================================================== flat + UV
me.shade_flat()
bpy.context.view_layer.objects.active = low
for o in scene.objects:
    o.select_set(o is low)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66.0), island_margin=0.004, area_weight=0.0,
                         correct_aspect=True, scale_to_bounds=False)
bpy.ops.uv.select_all(action="SELECT")
bpy.ops.uv.pack_islands(rotate=True, margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")

# =========================================================================== material
mat = bpy.data.materials.new(UNIT + "_mat")
mat.use_nodes = True
nt = mat.node_tree
bsdf = nt.nodes["Principled BSDF"]
vc = nt.nodes.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, 300)
vg_ = nt.nodes.new("ShaderNodeVertexColor"); vg_.layer_name = "Glow"; vg_.location = (-600, -300)
nt.links.new(vg_.outputs["Color"], bsdf.inputs["Emission Color"])
PAL.apply_material(mat, pal_default)
me.materials.append(mat)
low["conquest_unit"] = UNIT
low["conquest_character_id"] = CHAR_ID
low["conquest_tier"] = "regular"
low["conquest_tri_budget"] = TRI_BUDGET
low["conquest_max_height"] = CELL_MAX_H
low["conquest_max_footprint"] = CELL_MAX_FP
low["conquest_yaw_fix_deg"] = YAW_FIX_DEG
low["conquest_front_anchor"] = anchor.tolist()
low["conquest_front_landmark"] = landmark.tolist()
low["conquest_facing_rule"] = report["facing"]["rule"]
low["conquest_source"] = os.path.basename(bpy.data.filepath)
low["conquest_scale_policy"] = "natural proportions, sculpt units; game scales at import (cell fit report-only)"
low["conquest_locomotion"] = "upright biped stride on the two leg tentacles; cape tentacles + water webs are secondary motion"
low["conquest_version"] = VERSION
low["conquest_mask_style"] = MASK_STYLE
low["v3_landmarks"] = json.dumps(LMK)

# =========================================================================== v3 'smirk' morph target + the mouth evidence
t = time.time()
nvx = len(FV)
PKv = np.concatenate([VFIELD["pocket"], np.zeros(nvx - len(CV))])
LSv = np.concatenate([VFIELD["lipside"], np.zeros(nvx - len(CV))])
SHv = np.concatenate([VFIELD["shell"], np.full(nvx - len(CV), 2.0)])
FHv = np.concatenate([VFIELD["fhd"], np.full(nvx - len(CV), -1.0)])
VIv = np.concatenate([VFIELD["vis"], np.full(nvx - len(CV), 10.0)])
muz = mouth_uz(FV)
thm_ = np.arctan2(FV[:, 0] - HXC, -(FV[:, 1] - HYC))
gm_ = (SHv < 0.5) & (FHv > -0.05) & (np.cos(thm_) > 0.3) & (np.abs(muz[:, 1]) < 0.8) & (np.abs(muz[:, 0]) < 1.3) & (PKv < 0.5)
gi_m = np.nonzero(gm_)[0]
sdm, smm, _ = polyline_sd(muz[gi_m], MOUTH_C2)
scm = np.clip((smm - MOUTH_SC[0]) / (MOUTH_SC[1] - MOUTH_SC[0]), 0.0, 1.0)
GAM = math.log(0.5) / math.log(MOUTH_GAP_PEAK)            # sin(pi sc^GAM) peaks at sc = MOUTH_GAP_PEAK, 0 at both corners
prof_m = np.sin(math.pi * scm ** GAM)
side_m = np.where(LSv[gi_m] != 0, LSv[gi_m], np.sign(sdm))
fall_m = 1.0 - smoothstep(0.0, MOUTH_SOFT, np.abs(sdm))
DISP = np.zeros_like(FV)
DISP[gi_m, 2] = np.where(side_m > 0, MOUTH_GAP_UPPER, np.where(side_m < 0, -MOUTH_GAP_LOWER, 0.0)) * prof_m * fall_m
low.shape_key_add(name="Basis", from_mix=False)
kb_ = low.shape_key_add(name=SMIRK_KEY, from_mix=False)
kb_.data.foreach_set("co", (FV + DISP).ravel())
kb_.slider_min, kb_.slider_max, kb_.value = 0.0, 1.0, 0.0
me.shape_keys.use_relative = True
low.active_shape_key_index = 0
KEYB = me.shape_keys
REST32 = np.empty(nvx * 3, dtype=np.float32); me.vertices.foreach_get("co", REST32); REST32 = REST32.reshape(-1, 3).astype(float)
OPEN32 = np.empty(nvx * 3, dtype=np.float32); kb_.data.foreach_get("co", OPEN32); OPEN32 = OPEN32.reshape(-1, 3).astype(float)
DIG["smirk_key"] = sha(OPEN32)
pu_ = np.array([p[0] for p in MOUTH_PAIRS]); pl_ = np.array([p[1] for p in MOUTH_PAIRS]); psc = np.array([p[2] for p in MOUTH_PAIRS])
gap_rest = np.linalg.norm(REST32[pu_] - REST32[pl_], axis=1)
gap_open = np.linalg.norm(OPEN32[pu_] - OPEN32[pl_], axis=1)
# closed-state flatness: the mouth zone vs the neighbouring mantle, both against the smooth surfaces (no dimple, no crease)
zone_m = gi_m[np.abs(sdm) < MOUTH_SOFT]
ctrl_m = gi_m[(np.abs(sdm) > MOUTH_SOFT + 0.05) & (VIv[gi_m] > 0.1)]
bvh_head_s = BVHTree.FromPolygons(PARTS["head"][0].tolist(), PARTS["head"][1])      # the Taubin-smoothed mantle (final frame)
dev_hi = lambda ids: np.array([bvh_highb.find_nearest(Vector(REST32[i]))[3] for i in ids])       # noqa: E731
dev_sm = lambda ids: np.array([bvh_head_s.find_nearest(Vector(REST32[i]))[3] for i in ids])      # noqa: E731
dz_hi, dc_hi, dz_sm, dc_sm = dev_hi(zone_m), dev_hi(ctrl_m), dev_sm(zone_m), dev_sm(ctrl_m)


def tri_n(X, f):
    n_ = np.cross(X[f[1]] - X[f[0]], X[f[2]] - X[f[0]])
    return n_ / max(np.linalg.norm(n_), 1e-30)


near_f = [fi for fi, f in enumerate(FF) if gm_[f[0]] or gm_[f[1]] or gm_[f[2]] or PKv[f[0]] > 0.5]
e2f = {}
for fi in near_f:
    f = FF[fi]
    for k in range(3):
        e2f.setdefault(tuple(sorted((f[k], f[(k + 1) % 3]))), []).append(fi)
surf = lambda fis: [fi for fi in fis if rid[fi] != R_["mouth"]]          # noqa: E731
crease_slit = []
lips_u = [MOUTH_CORNERS[0]] + list(pu_) + [MOUTH_CORNERS[1]]
lips_l = [MOUTH_CORNERS[0]] + list(pl_) + [MOUTH_CORNERS[1]]
for i in range(len(lips_u) - 1):
    fu = surf(e2f.get(tuple(sorted((lips_u[i], lips_u[i + 1]))), []))
    fl = surf(e2f.get(tuple(sorted((lips_l[i], lips_l[i + 1]))), []))
    if fu and fl:
        c_ = float(np.clip(tri_n(REST32, FF[fu[0]]) @ tri_n(REST32, FF[fl[0]]), -1, 1))
        crease_slit.append(math.degrees(math.acos(c_)))
zone_set = set(zone_m.tolist())
crease_zone = []
for (a_, b_), fis in e2f.items():
    s_ = surf(fis)
    if len(s_) == 2 and a_ in zone_set and b_ in zone_set:
        crease_zone.append(math.degrees(math.acos(float(np.clip(tri_n(REST32, FF[s_[0]]) @ tri_n(REST32, FF[s_[1]]), -1, 1)))))
# visibility: rays from 15 camera directions onto a window round the mouth; the first hit's region is what a viewer sees
VIEWS_M = [(a, e) for a in (0, -30, 30, -55, 55) for e in (-20, 0, 20)]
STEP_M = 0.012


def mouth_visible(X):
    bvh_ = BVHTree.FromPolygons(X.tolist(), FF)
    out = {}
    for a, e in VIEWS_M:
        ar, er = math.radians(a), math.radians(e)
        dc = np.array([math.sin(ar) * math.cos(er), -math.cos(ar) * math.cos(er), math.sin(er)])
        e1 = np.cross([0.0, 0.0, 1.0], dc); e1 /= np.linalg.norm(e1); e2 = np.cross(dc, e1)
        n_hit = n_mouth = 0
        for x in np.arange(-0.9, 0.9001, STEP_M):
            for y in np.arange(-0.55, 0.5501, STEP_M):
                h_ = bvh_.ray_cast(Vector(MOUTH_CENTRE + dc * 10.0 + e1 * x + e2 * y), Vector(-dc))
                if h_[2] is not None:
                    n_hit += 1
                    n_mouth += int(rid[h_[2]] == R_["mouth"])
        out["yaw%+d_elev%+d" % (a, e)] = {"rays_hit": n_hit, "mouth_hits": n_mouth,
                                          "mouth_area": round(n_mouth * STEP_M * STEP_M, 5)}
    return out


vis_rest = mouth_visible(REST32)
vis_open = mouth_visible(OPEN32)
qs_ = [0.1, 0.25, 0.5, MOUTH_GAP_PEAK, 0.75, 0.9]
report["mouth"].update({
    "morph_target": {"name": SMIRK_KEY, "moved_vertices": int((np.abs(DISP[:, 2]) > 1e-9).sum()),
                     "max_offset": round(float(np.abs(DISP[:, 2]).max()), 4),
                     "rule": "z offset: upper side +MOUTH_GAP_UPPER, lower side -MOUTH_GAP_LOWER, x sin(pi sc^g) along the slit "
                             "(0 at both corners, peak at MOUTH_GAP_PEAK) x (1 - smoothstep(0, MOUTH_SOFT, |distance to the "
                             "line|)); lips by their copy's side, the rest by the side of the line; the pocket stays"},
    "closed": {"lip_pairs_max_separation": float(gap_rest.max()), "lip_pairs": int(len(gap_rest)),
               "surface_deviation_vs_remeshed_high": {"mouth_zone_p99": round(float(np.percentile(dz_hi, 99)), 5),
                                                      "mouth_zone_max": round(float(dz_hi.max()), 5),
                                                      "neighbour_mantle_p99": round(float(np.percentile(dc_hi, 99)), 5),
                                                      "neighbour_mantle_max": round(float(dc_hi.max()), 5)},
               "surface_deviation_vs_smoothed_mantle": {"mouth_zone_mean": round(float(dz_sm.mean()), 5),
                                                        "mouth_zone_max": round(float(dz_sm.max()), 5),
                                                        "neighbour_mantle_mean": round(float(dc_sm.mean()), 5),
                                                        "neighbour_mantle_max": round(float(dc_sm.max()), 5)},
               "crease_deg": {"across_the_slit_max": round(max(crease_slit), 3), "across_the_slit_mean": round(float(np.mean(crease_slit)), 3),
                              "mouth_zone_edges_p95": round(float(np.percentile(crease_zone, 95)), 3),
                              "mouth_zone_edges_max": round(max(crease_zone), 3), "slit_segments": len(crease_slit)},
               "visible_mouth_region": vis_rest,
               "visible_mouth_region_total_hits": int(sum(v["mouth_hits"] for v in vis_rest.values())),
               "zone_vertices": int(len(zone_m)), "control_vertices": int(len(ctrl_m))},
    "open": {"gap_max": round(float(gap_open.max()), 4),
             "gap_at": {("sc_%.2f" % q): round(float(np.interp(q, psc, gap_open)), 4) for q in qs_},
             "gap_at_corners": [0.0, 0.0], "gap_peak_at_sc": round(float(psc[int(np.argmax(gap_open))]), 3),
             "visible_mouth_region": vis_open,
             "front_visible_dark_area": vis_open["yaw+0_elev+0"]["mouth_area"]},
    "line_u_dz": MOUTH_LINE, "gap_lower_upper": [MOUTH_GAP_LOWER, MOUTH_GAP_UPPER], "depth": MOUTH_DEPTH,
    "why_invisible_when_closed": "the dark 'mouth' colour is painted ONLY on the pocket faces, which sit behind the lips "
                                 "inside the mantle; at rest the two lips are duplicate vertices at identical positions "
                                 "(identical skin weights too), so the mantle surface is watertight and unchanged and the "
                                 "pocket is enclosed: no crease, no colour patch. The morph target is 0 at rest.",
    "seconds": round(time.time() - t, 1)})
print("MOUTH", json.dumps({k: report["mouth"][k] for k in ("closed", "open")}, default=str)[:3000])
sys.stdout.flush()

if PREVIEW:
    nt.links.new(vc.outputs["Color"], bsdf.inputs["Base Color"])
    for o in list(scene.objects):
        if o is not low:
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=PREVIEW, copy=True, compress=True)
    print("PREVIEW", json.dumps({k: report.get(k) for k in ("tris_final", "tris_breakdown", "retopo", "regions_faces", "regions_area_share",
                                                             "facing", "iso_cuts", "natural", "prep", "webs", "detail_zones",
                                                             "alignment", "shade", "landmarks_v3", "belt", "mouth")}, default=str))
    sys.stdout.flush(); os._exit(0)

# =========================================================================== 7. bake (normal + AO from the remeshed shells)
try:
    import addon_utils
    addon_utils.enable("cycles", default_set=False, persistent=False)
except Exception:
    pass
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
scene.cycles.seed = 0
bk = scene.render.bake
bk.use_selected_to_active = True; bk.cage_extrusion = BAKE_CAGE; bk.max_ray_distance = BAKE_CAGE * 2.0
bk.margin = 16; bk.use_clear = False
RN, RA = BAKE_RES
img_n = bpy.data.images.new(UNIT + "_normal", RN, RN, alpha=False)
img_n.colorspace_settings.name = "Non-Color"; img_n.generated_color = (0.0, 0.0, 0.0, 1.0)
img_ao = bpy.data.images.new(UNIT + "_ao", RA, RA, alpha=False)
img_ao.colorspace_settings.name = "Non-Color"; img_ao.generated_color = (1.0, 0.0, 1.0, 1.0)
tn = nt.nodes.new("ShaderNodeTexImage"); tn.image = img_n; tn.location = (-900, -600)
ta = nt.nodes.new("ShaderNodeTexImage"); ta.image = img_ao; ta.location = (-900, 0)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = False
low.visible_transmission = low.visible_volume_scatter = False
me.shade_smooth()
# v3: the lip edges (surface | pocket) are SHARP for the bake: otherwise the smoothed lip-vertex normals average in the
# folded-in pocket faces, the tangent-space bake compensates for that tilt, and the flat-shaded render shows the slit as a
# faint jagged line while the mouth is shut. Sharp, each lip's normals come from its own surface side (the two sides are
# coplanar across the slit: crease 0 deg). Removed again after the bake (the delivered mesh is flat-shaded).
lip_pairs_set = set()
for LIP in ([MOUTH_CORNERS[0]] + [p[0] for p in MOUTH_PAIRS] + [MOUTH_CORNERS[1]],
            [MOUTH_CORNERS[0]] + [p[1] for p in MOUTH_PAIRS] + [MOUTH_CORNERS[1]]):
    for a_, b_ in zip(LIP[:-1], LIP[1:]):
        lip_pairs_set.add((min(a_, b_), max(a_, b_)))
ev_ = np.empty(len(me.edges) * 2, dtype=np.int64); me.edges.foreach_get("vertices", ev_); ev_ = ev_.reshape(-1, 2)
sharp_ = np.array([(min(a_, b_), max(a_, b_)) in lip_pairs_set for a_, b_ in ev_], dtype=bool)
if "sharp_edge" in me.attributes:
    me.attributes.remove(me.attributes["sharp_edge"])
me.attributes.new("sharp_edge", "BOOLEAN", "EDGE").data.foreach_set("value", sharp_)
UVn = np.empty(len(me.loops) * 2); me.uv_layers.active.data.foreach_get("uv", UVn)
lt_ = np.empty(nf, dtype=np.int64); me.polygons.foreach_get("loop_total", lt_)
assert (lt_ == 3).all(), "low must be all triangles"
UVn = UVn.reshape(-1, 3, 2)


def texels_of(mask_faces, res):
    m = np.zeros((res, res), bool)
    for tri in UVn[mask_faces]:
        p = tri * res
        x0_, y0_ = np.floor(p.min(0)).astype(int); x1_, y1_ = np.ceil(p.max(0)).astype(int)
        xs, ys = np.meshgrid(np.arange(max(x0_, 0), min(x1_, res)), np.arange(max(y0_, 0), min(y1_, res)))
        if xs.size == 0:
            continue
        q = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], 1)
        a, b, c = p

        def ed(u_, v_):
            return (v_[0] - u_[0]) * (q[:, 1] - u_[1]) - (v_[1] - u_[1]) * (q[:, 0] - u_[0])
        d1, d2, d3 = ed(a, b), ed(b, c), ed(c, a)
        ins = ((d1 >= 0) & (d2 >= 0) & (d3 >= 0)) | ((d1 <= 0) & (d2 <= 0) & (d3 <= 0))
        m[ys.ravel()[ins], xs.ravel()[ins]] = True
    return m.ravel()


tb = time.time()
for o in scene.objects:
    o.select_set(o is HIGH or o is low)
bpy.context.view_layer.objects.active = low
bstats = {}
for typ, node, samples in (("NORMAL", tn, 1), ("AO", ta, 16)):
    nt.nodes.active = node
    scene.cycles.samples = samples
    t = time.time()
    r_ = bpy.ops.object.bake(type=typ, use_selected_to_active=True, cage_extrusion=BAKE_CAGE, margin=16, use_clear=False)
    bstats[typ] = {"result": sorted(r_), "seconds": round(time.time() - t, 1)}
px = np.empty(RN * RN * 4, dtype=np.float32); img_n.pixels.foreach_get(px); px = px.reshape(-1, 4)
pa = np.empty(RA * RA * 4, dtype=np.float32); img_ao.pixels.foreach_get(pa); pa = pa.reshape(-1, 4)
me.shade_flat()
if "sharp_edge" in me.attributes:
    me.attributes.remove(me.attributes["sharp_edge"])
report["mouth"]["bake_sharp_lip_edges"] = int(sharp_.sum())
# closed-mouth evidence on the BAKE: the normal map on the faces touching the slit must look like the mantle around it
devn_all = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
lipv_ = set(v for e_ in lip_pairs_set for v in e_)
lipf_ = np.array([rid[fi] != R_["mouth"] and len(lipv_.intersection(f)) >= 2 for fi, f in enumerate(FF)])
zonef_ = np.array([rid[fi] != R_["mouth"] and not lipf_[fi] and all(v in zone_set for v in f) for fi, f in enumerate(FF)])
txl_, txz_ = texels_of(lipf_, RN), texels_of(zonef_, RN)
report["mouth"]["closed"]["baked_normal_deviation_from_flat"] = {
    "slit_faces_mean": round(float(devn_all[txl_].mean()), 4), "slit_faces_p95": round(float(np.percentile(devn_all[txl_], 95)), 4),
    "mouth_zone_faces_mean": round(float(devn_all[txz_].mean()), 4), "mouth_zone_faces_p95": round(float(np.percentile(devn_all[txz_], 95)), 4),
    "slit_faces": int(lipf_.sum()), "zone_faces": int(zonef_.sum())}
web_faces = np.isin(rid, [R_["membrane"], R_["membrane_rim"], R_["mouth"]])   # no sculpt beneath: flat normal, AO 1
web_tx_n = texels_of(web_faces, RN)
web_tx_a = texels_of(web_faces, RA)
devn = np.linalg.norm(px[:, :3] - np.array([0.5, 0.5, 1.0]), axis=1)
cov_n = (px[:, 2] > 0.25) & ~web_tx_n
cov_a = (np.abs(pa[:, 0] - pa[:, 1]) < 0.02) & ~web_tx_a
alltex_n = texels_of(np.ones(nf, bool), RN)
alltex_a = texels_of(np.ones(nf, bool), RA)
sculpt_tex_n = alltex_n & ~web_tx_n
sculpt_tex_a = alltex_a & ~web_tx_a
bstats.update({
    "uv_texels_normal": int(alltex_n.sum()), "uv_coverage": round(float(alltex_n.mean()), 4),
    "web_texels_reset_flat": {"normal": int(web_tx_n.sum()), "ao": int(web_tx_a.sum())},
    "normal_baked_pct_of_sculpt_uv_texels": round(100 * float(cov_n[sculpt_tex_n].mean()), 2),
    "normal_detail_fraction_dev_gt_0.05": round(float((devn[cov_n & sculpt_tex_n] > 0.05).mean()), 4),
    "normal_dev_mean": round(float(devn[cov_n & sculpt_tex_n].mean()), 4),
    "ao_baked_pct_of_sculpt_uv_texels": round(100 * float(cov_a[sculpt_tex_a].mean()), 2),
    "ao_mean": round(float(pa[cov_a & sculpt_tex_a, 0].mean()), 4),
    "ao_p05": round(float(np.percentile(pa[cov_a & sculpt_tex_a, 0], 5)), 4)})
per_region = {}
for n_ in REG:
    fm_ = rid == R_[n_]
    if fm_.any() and n_ not in ("membrane", "membrane_rim", "mouth"):
        tx = texels_of(fm_, RN)
        per_region[n_] = {"texels": int(tx.sum()), "baked_pct": round(100 * float(cov_n[tx].mean()), 2),
                          "normal_dev_mean": round(float(devn[tx & cov_n].mean()), 4) if (tx & cov_n).any() else None}
bstats["per_region_normal"] = per_region
px[~cov_n, :3] = (0.5, 0.5, 1.0); img_n.pixels.foreach_set(px.ravel())
pa[~cov_a, :3] = bstats["ao_mean"]; pa[web_tx_a, :3] = 1.0; img_ao.pixels.foreach_set(pa.ravel())
if not DIGEST_ONLY:
    os.makedirs(TEX_DIR, exist_ok=True)
    for img, nm in ((img_n, OUT_NAME + "_normal.png"), (img_ao, OUT_NAME + "_ao.png")):
        img.filepath_raw = os.path.join(TEX_DIR, nm); img.file_format = "PNG"; img.save(); img.pack()


def set_tex_paths(rel_prefix):
    for img, nm in ((img_n, OUT_NAME + "_normal.png"), (img_ao, OUT_NAME + "_ao.png")):
        img.filepath = rel_prefix + nm


bstats["pixel_sha"] = {"normal": sha(np.clip(np.rint(px[:, :3] * 255.0), 0, 255).astype(np.uint8)),
                       "ao": sha(np.clip(np.rint(pa[:, :1] * 255.0), 0, 255).astype(np.uint8))}
np.save(os.path.join(tempfile.gettempdir(), OUT_NAME + ("_normal_twin.npy" if DIGEST_ONLY else "_normal_main.npy")), px[:, :3])
np.save(os.path.join(tempfile.gettempdir(), OUT_NAME + ("_ao_twin.npy" if DIGEST_ONLY else "_ao_main.npy")), pa[:, :3])
bstats["cage_extrusion"] = BAKE_CAGE
bstats["resolution"] = {"normal": RN, "ao": RA}
bstats["high_tris"] = tri_count_F(HF_all)
bstats["high"] = "the remeshed (smoothed, mask/neck/belt-fused) shells; the mouth pocket texels are reset flat like the webs"
bstats["seconds"] = round(time.time() - tb, 1)
report["bake"] = bstats
DIG["bake_normal"] = bstats["pixel_sha"]["normal"]; DIG["bake_ao"] = bstats["pixel_sha"]["ao"]
mul = nt.nodes.new("ShaderNodeMix"); mul.data_type = "RGBA"; mul.blend_type = "MULTIPLY"; mul.location = (-300, 300)
mul.inputs["Factor"].default_value = 1.0
ia = [i for i in mul.inputs if i.identifier == "A_Color"][0]; ib = [i for i in mul.inputs if i.identifier == "B_Color"][0]
oc = [o for o in mul.outputs if o.identifier == "Result_Color"][0]
nt.links.new(vc.outputs["Color"], ia); nt.links.new(ta.outputs["Color"], ib)
nt.links.new(oc, bsdf.inputs["Base Color"])
nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -600)
nt.links.new(tn.outputs["Color"], nmap.inputs["Color"]); nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
scene.render.engine = "BLENDER_EEVEE"
hm_ = HIGH.data
bpy.data.objects.remove(HIGH, do_unlink=True); bpy.data.meshes.remove(hm_)
low.visible_camera = low.visible_diffuse = low.visible_glossy = low.visible_shadow = True
low.visible_transmission = low.visible_volume_scatter = True

uva = 0.5 * ((UVn[:, 1, 0] - UVn[:, 0, 0]) * (UVn[:, 2, 1] - UVn[:, 0, 1]) - (UVn[:, 2, 0] - UVn[:, 0, 0]) * (UVn[:, 1, 1] - UVn[:, 0, 1]))
report["uv"] = {"method": "Smart UV after the iso cuts (66 deg, margin 0.004), repacked", "faces": nf,
                "zero_area_faces": int((np.abs(uva) < 1e-9).sum()), "flipped_faces": int((uva < -1e-12).sum()),
                "uv_sha": sha(UVn)}
LVf = np.array([v.co[:] for v in me.vertices])
_cd = np.empty(len(me.loops) * 4, dtype=np.float32); me.color_attributes["Col"].data.foreach_get("color", _cd)
report["digest_geometry_colour"] = hashlib.sha256(np.round(LVf, 6).astype(np.float32).tobytes() + np.round(_cd, 5).tobytes()).hexdigest()[:16]
DIG["geometry_colour"] = report["digest_geometry_colour"]; DIG["uv"] = report["uv"]["uv_sha"]
report["final_bbox"] = [LVf.min(0).round(4).tolist(), LVf.max(0).round(4).tolist()]
report["palette"] = {"default": PAL.table(pal_default), "files": pal_default["files"],
                     "provenance": "v2: all-blue body = the v1 cape blue (artist); orange W-visor frame + pale cyan lenses; v3: gold belt (= the sigil), near-black mouth pocket"}
bpy.context.preferences.filepaths.save_version = 0
if not DIGEST_ONLY:
    set_tex_paths("//textures/")
    bpy.ops.wm.save_as_mainfile(filepath=OUT_IMPROVED, copy=True, compress=True, relative_remap=False)
    json.dump(report, open(OUT_IMPROVED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("IMPROVED_SAVED", OUT_IMPROVED, round(time.time() - T0, 1))
sys.stdout.flush()

# =========================================================================== 8. rig
rep = {"unit": UNIT, "version": VERSION, "source": OUT_IMPROVED, "fps": K.FPS}
scene.render.fps = K.FPS; scene.render.fps_base = 1.0
W_ = LVf.copy()
NVS = len(CV)                                          # shell vertices (cut); webs follow
is_web = np.zeros(len(W_), bool); is_web[NVS:] = True
vshell = np.concatenate([VFIELD["shell"], np.full(len(W_) - NVS, 2.0)])
labF = np.full(len(W_), -1, dtype=np.int32); sF = np.full(len(W_), -1.0)
vfhd = np.concatenate([VFIELD["fhd"], np.full(len(W_) - NVS, -1.0)])
for i in range(NVS):
    cape_ = VFIELD["shell"][i] > 0.5
    lab_ = int((CLAB if cape_ else BLAB)[(kd_c if cape_ else kd_b).find(W_[i])[1]])
    if not cape_ and (vfhd[i] > -0.05 or W_[i, 2] > Z_BODY_TOP - 0.2 or VFIELD["beltv"][i] > 0.5):
        lab_ = 0
    labF[i] = lab_
    ch = ID_TO_CH.get(lab_)
    if ch:
        sF[i] = float(CHAINS[ch]["s"][CH_KD[ch].find(W_[i])[1]])
TIP = {}
for side in ("L", "R"):
    m = labF == LAB_ID["leg." + side]
    TIP[side] = W_[np.nonzero(m)[0][np.argmin(W_[m, 2])]].copy()
HAND_TIP = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    m = labF == LAB_ID["arm." + side]
    HAND_TIP[side] = W_[np.nonzero(m)[0][np.argmax((W_[m, 0] - X_MID) * sg)]].copy()

BONES = []                                             # (name, head, tail, parent)
BONES += [("pelvis", SPINE_J[0], SPINE_J[1], "root"), ("spine", SPINE_J[1], SPINE_J[2], "pelvis"),
          ("chest", SPINE_J[2], SPINE_J[3], "spine"), ("head", SPINE_J[3], np.array([X_MID, SPINE_J[3][1], Z_TOP]), "chest")]
CH_BONES = {}
for side in ("L", "R"):
    c = CHAINS["leg." + side]
    kn = chain_at(c, LEG_KNEE_FRAC * c["L"]); an = chain_at(c, LEG_ANKLE_FRAC * c["L"])
    BONES += [("thigh." + side, c["P"][0], kn, "pelvis"), ("shin." + side, kn, an, "thigh." + side), ("foot." + side, an, TIP[side], "shin." + side)]
    CH_BONES["leg." + side] = (["thigh." + side, "shin." + side, "foot." + side], np.array([0.0, LEG_KNEE_FRAC, LEG_ANKLE_FRAC, 1.0]) * c["L"])
for side in ("L", "R"):
    c = CHAINS["arm." + side]
    kn = np.linspace(0.0, c["L"], ARM_BONES + 1)
    pts = chain_at(c, kn); pts[-1] = HAND_TIP[side]
    names = ["arm.%s.%d" % (side, k) for k in range(ARM_BONES)]
    for k in range(ARM_BONES):
        BONES.append((names[k], pts[k], pts[k + 1], "chest" if k == 0 else names[k - 1]))
    CH_BONES["arm." + side] = (names, kn)
for cn_ in CAPE_NAMES:
    c = CHAINS[cn_]
    kn = np.linspace(0.0, c["L"], CAPE_BONES + 1)
    pts = chain_at(c, kn)
    names = ["%s.%d" % (cn_, k) for k in range(CAPE_BONES)]
    for k in range(CAPE_BONES):
        BONES.append((names[k], pts[k], pts[k + 1], "chest" if k == 0 else names[k - 1]))
    CH_BONES[cn_] = (names, kn)
for wn in ("web.R", "web.C", "web.L"):
    mid = WEB[wn]["mid"]
    seg = np.linalg.norm(np.diff(mid, axis=0), axis=1); sm = np.concatenate([[0.0], np.cumsum(seg)])
    Lm = float(sm[-1])
    kn = np.linspace(0.0, Lm, WEB_BONES + 1)
    pts = np.stack([np.interp(kn, sm, mid[:, k]) for k in range(3)], 1)
    names = ["%s.%d" % (wn, k) for k in range(WEB_BONES)]
    for k in range(WEB_BONES):
        BONES.append((names[k], pts[k], pts[k + 1], "chest" if k == 0 else names[k - 1]))
    CH_BONES[wn] = (names, kn)
    WEB[wn]["Lmid"] = Lm
    A_, B_ = CHAINS[WEB[wn]["A"]], CHAINS[WEB[wn]["B"]]
    ax = chain_at(B_, WEB_V_ROOT * B_["L"]) - chain_at(A_, WEB_V_ROOT * A_["L"]); ax /= np.linalg.norm(ax)
    d_ = pts[-1] - pts[0]; d_ /= np.linalg.norm(d_)
    if np.cross(ax, d_) @ np.array([0.0, 1.0, 0.0]) < 0:  # +angle pushes the web's middle outward (+Y, away from the back)
        ax = -ax
    WEB[wn]["across"] = ax
arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
Hh = float(W_[:, 2].max())
eb = arm_data.edit_bones.new("root"); eb.head = (0, 0, 0); eb.tail = (0, 0, 0.06 * Hh); eb.use_deform = False; eb.roll = 0.0
for (nm, h, t_, p) in BONES:
    e = arm_data.edit_bones.new(nm)
    e.head = Vector(h); e.tail = Vector(t_); e.roll = 0.0
    e.parent = arm_data.edit_bones[p]
    e.use_connect = False
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
DEFORM = [b[0] for b in BONES]
J = {n: j for j, n in enumerate(DEFORM)}
PARENT = {b[0]: b[3] for b in BONES}
HEADP = {b[0]: np.array(b[1], float) for b in BONES}
TAILP = {b[0]: np.array(b[2], float) for b in BONES}
REST4 = {b.name: np.array(b.matrix_local) for b in arm_data.bones}


def hat(s, mids):
    s = np.asarray(s, float); n = len(mids)
    W = np.zeros((len(s), n))
    lo = s <= mids[0]; W[lo, 0] = 1.0
    hi = s >= mids[-1]; W[hi, n - 1] = 1.0
    mid = ~lo & ~hi
    if mid.any():
        k = np.clip(np.searchsorted(mids, s[mid]) - 1, 0, n - 2)
        u = (s[mid] - mids[k]) / (mids[k + 1] - mids[k])
        ix = np.nonzero(mid)[0]
        W[ix, k] = 1 - u; W[ix, k + 1] = u
    return W


# ---- analytic weights (<= 4 influences)
nV = len(W_)
Wt = np.zeros((nV, len(DEFORM)))
sp_mid = np.array([(SPINE_Z[k] + SPINE_Z[k + 1]) / 2 for k in range(3)])
WT = np.zeros((nV, len(DEFORM)))
WT[:, [J["pelvis"], J["spine"], J["chest"]]] = hat(W_[:, 2], sp_mid)
headness = smoothstep(NECK_ZA, NECK_ZB, W_[:, 2]) * (vshell == 0) * (labF == 0)
WT = WT * (1 - headness)[:, None]
WT[:, J["head"]] += headness
CHEST1 = np.zeros(len(DEFORM)); CHEST1[J["chest"]] = 1.0


def chain_weights(ch_name, s, parentW, blend_start, blend_len):
    names, kn = CH_BONES[ch_name]
    mids = (kn[:-1] + kn[1:]) / 2
    Wc = hat(s, mids)
    tt = smoothstep(blend_start, blend_start + blend_len, s)
    W = parentW * (1 - tt)[:, None]
    for k, n in enumerate(names):
        W[:, J[n]] += Wc[:, k] * tt
    return W


body_v = (vshell == 0)
Wt[body_v] = WT[body_v]
blend_rep = {}
for side in ("L", "R"):
    for kind in ("arm", "leg"):
        chn = kind + "." + side
        m = body_v & (labF == LAB_ID[chn])
        s_ = sF[m]
        sb = float(np.percentile(s_, 5))
        Wt[m] = chain_weights(chn, s_, WT[m], sb, ROOT_BLEND[kind])
        blend_rep[chn] = {"members": int(m.sum()), "root_blend_from_s": round(sb, 4)}
cape_v = vshell == 1
Wt[cape_v] = CHEST1
for i_c, cn_ in enumerate(CAPE_NAMES):
    m = cape_v & (labF == 10 + i_c)
    Wt[m] = chain_weights(cn_, sF[m], np.tile(CHEST1, (int(m.sum()), 1)), 0.0, ROOT_BLEND["cape"])
    blend_rep[cn_] = {"members": int(m.sum())}
web_wrep = {}
for wn in ("web.R", "web.C", "web.L"):
    a0, a1 = WEB_VRANGE[wn]
    U, Wd, Vv = WEB[wn]["UVW"][:, 0], WEB[wn]["UVW"][:, 1], WEB[wn]["UVW"][:, 2]
    A_, B_ = WEB[wn]["A"], WEB[wn]["B"]
    n_ = a1 - a0
    WA = chain_weights(A_, Vv * CHAINS[A_]["L"], np.tile(CHEST1, (n_, 1)), 0.0, ROOT_BLEND["cape"])
    WB = chain_weights(B_, Vv * CHAINS[B_]["L"], np.tile(CHEST1, (n_, 1)), 0.0, ROOT_BLEND["cape"])
    names, kn = CH_BONES[wn]
    WM = chain_weights(wn, Wd * WEB[wn]["Lmid"], np.tile(CHEST1, (n_, 1)), 0.0, float(kn[1]) * 0.5)
    mA = smoothstep(0.0, 0.5, U); mB = smoothstep(1.0, 0.5, U)
    Wweb = np.where((U <= 0.5)[:, None], WA * (1 - mA)[:, None] + WM * mA[:, None], WB * (1 - mB)[:, None] + WM * mB[:, None])
    Wt[a0:a1] = Wweb
    hem = Wd > 0.99
    edge_cols = [J[n] for n in DEFORM if n.startswith(WEB[wn]["A"] + ".") or n.startswith(WEB[wn]["B"] + ".") or n.startswith(wn + ".")]
    tip_cols = [J["%s.%d" % (WEB[wn]["A"], CAPE_BONES - 1)], J["%s.%d" % (WEB[wn]["B"], CAPE_BONES - 1)],
                J["%s.%d" % (wn, WEB_BONES - 1)],
                J["%s.%d" % (WEB[wn]["A"], CAPE_BONES - 2)], J["%s.%d" % (WEB[wn]["B"], CAPE_BONES - 2)],
                J["%s.%d" % (wn, WEB_BONES - 2)]]
    web_wrep[wn] = {"hem_vertices": int(hem.sum()),
                    "hem_weight_on_chest_max": round(float(Wweb[hem, J["chest"]].max()), 4),
                    "hem_weight_on_last_two_bones_min": round(float(Wweb[hem][:, tip_cols].sum(1).min()), 4),
                    "weight_on_own_chains_min": round(float(Wweb[:, edge_cols + [J["chest"]]].sum(1).min()), 4)}
Wt = np.where(Wt > 1e-4, Wt, 0.0)
infl_pre = int((Wt > 0).sum(1).max())
capped = int(((Wt > 0).sum(1) > 4).sum())
if infl_pre > 4:
    idx_ = np.argsort(-Wt, 1, kind="stable")[:, 4:]
    np.put_along_axis(Wt, idx_, 0.0, 1)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    for i in np.nonzero(Wt[:, j] > 0)[0]:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
infl = (Wt > 0).sum(1)
DIG["weights"] = sha(Wt)
rep["weights"] = {"max_influences": int(infl.max()), "max_influences_before_cap": infl_pre, "vertices_capped_to_4": capped,
                  "unweighted": int((infl == 0).sum()), "sum_dev_max": float(np.abs(Wt.sum(1) - 1.0).max()),
                  "per_bone_dominant": {n: int((np.argmax(Wt, 1) == j).sum()) for j, n in enumerate(DEFORM)},
                  "chains": blend_rep, "webs": web_wrep,
                  "rule": "torso = hat over pelvis/spine/chest by height, head ramped in over the neck (NECK_ZA -> NECK_ZB); "
                          "arm/leg/cape vertices = their traced chain, hat weights along the centreline arc, blended from the "
                          "parent (torso weights / chest) over ROOT_BLEND at the chain root; webs = the two side chains at the "
                          "same arc fraction, mixed into the web's own mid chain toward the middle of the gap"}
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
amod = low.modifiers.new("Armature", "ARMATURE"); amod.object = rig


# ---- clips: FK in numpy (deform transforms D_b, armature space), keyed as pose-bone bases every frame
def Tr(h, R):
    M = np.eye(4); M[:3, :3] = R; M[:3, 3] = h - R @ h
    return M


def Tt(v):
    M = np.eye(4); M[:3, 3] = v
    return M


LEG = {}
for side in ("L", "R"):
    a = HEADP["shin." + side] - HEADP["thigh." + side]
    b = TIP[side] - HEADP["shin." + side]
    LEG[side] = {"a": a, "b": b, "r0": float(np.hypot(a[1] + b[1], a[2] + b[2])), "la": float(np.hypot(a[1], a[2])),
                 "lb": float(np.hypot(b[1], b[2]))}
LEG_R0 = float(np.mean([LEG[s]["r0"] for s in LEG]))
PLANT_Z = 0.0                                          # both leg-tentacle tips plant on the contract floor
STEP_A = WALK_STEP_FRAC * LEG_R0
LIFT = WALK_LIFT_FRAC * LEG_R0
RISE = FLOAT_RISE_FRAC * H


def ik_leg(side, hip_p, ty, tz):
    a, b = LEG[side]["a"], LEG[side]["b"]
    la, lb = LEG[side]["la"], LEG[side]["lb"]
    d = np.array([ty - hip_p[1], tz - hip_p[2]]); Dn = float(np.linalg.norm(d))
    Dc = float(np.clip(Dn, abs(la - lb) + 1e-6, (la + lb) * (1 - 1e-4)))
    ang_d = math.atan2(d[1], d[0])
    g = math.acos(float(np.clip((la * la + Dc * Dc - lb * lb) / (2 * la * Dc), -1.0, 1.0)))
    best = None
    for sgn in (1.0, -1.0):                               # knee forward (toward -Y, the front)
        aa = ang_d + sgn * g
        kn = la * np.array([math.cos(aa), math.sin(aa)])
        if best is None or kn[0] < best[1][0]:
            best = (aa, kn)
    aa, kn = best
    tip2 = Dc * np.array([math.cos(ang_d), math.sin(ang_d)])
    bb = math.atan2(tip2[1] - kn[1], tip2[0] - kn[0])
    p1 = math.degrees(aa - math.atan2(a[2], a[1])); p2 = math.degrees(bb - math.atan2(b[2], b[1]))
    R1, R2 = Rx(p1), Rx(p2)
    knee_p = hip_p + R1 @ a
    D_th = Tt(hip_p) @ Tr(np.zeros(3), R1) @ Tt(-HEADP["thigh." + side])
    D_sh = Tt(knee_p) @ Tr(np.zeros(3), R2) @ Tt(-HEADP["shin." + side])
    return D_th, D_sh, Dn - Dc


# contact: the smoothed tentacle tip is rounded, so as the shin tilts a neighbour of the TIP vertex can become the lowest
# point. solve_leg re-solves the IK with the target raised by that dip, so the LOWEST skinned tip vertex (not a fixed one)
# touches z = PLANT_Z: no floor penetration, the contact rolls over the rounded tip.
LEG_COLS = {}
TIPSET = {}
for side in ("L", "R"):
    cols_ = [J["thigh." + side], J["shin." + side], J["foot." + side]]
    m_ = np.nonzero((labF == LAB_ID["leg." + side]) & (sF > 0.75 * CHAINS["leg." + side]["L"]))[0]
    m_ = m_[Wt[m_][:, cols_].sum(1) > 1 - 1e-6]
    TIPSET[side] = (W_[m_], Wt[m_][:, cols_])
    LEG_COLS[side] = ["thigh." + side, "shin." + side, "foot." + side]


def tip_min_z(D, side):
    P_, W3 = TIPSET[side]
    z = np.zeros(len(P_))
    for k, n in enumerate(LEG_COLS[side]):
        z += W3[:, k] * (P_ @ D[n][2, :3] + D[n][2, 3])
    return float(z.min())


def solve_leg(D, side, hip_p, ty, tz, Rfoot, plant):
    """IK + foot; when the leg is planted (plant=True), lift the target so the lowest skinned tip vertex sits on tz."""
    lift_ = 0.0
    for _ in range(3):
        D["thigh." + side], D["shin." + side], err = ik_leg(side, hip_p, ty, tz + lift_)
        D["foot." + side] = D["shin." + side] @ Tr(HEADP["foot." + side], Rfoot)
        if not plant:
            break
        dip = tip_min_z(D, side) - tz
        if abs(dip) < 1e-6:
            break
        lift_ -= dip
    return err


def hip_rest(side, Rp):
    return HEADP["pelvis"] + Rp @ (HEADP["thigh." + side] - HEADP["pelvis"])


def pelvis_drop(Rp, contacts, reach_k):
    best = math.inf
    for side, ty in contacts:
        h0 = hip_rest(side, Rp)
        r_t = reach_k * LEG[side]["r0"]
        dy = ty - h0[1]
        hz = math.sqrt(max(r_t * r_t - dy * dy, 1e-9))
        best = min(best, (PLANT_Z + hz) - h0[2])
    return best


LOCAL_R = {}


def chain_rel(D, names, rots):
    for k, n in enumerate(names):
        LOCAL_R[n] = rots[k]
        D[n] = D[PARENT[n]] @ Tr(HEADP[n], rots[k])


def rotvec(R):
    c = float(np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0)); th = math.acos(c)
    if th < 1e-9:
        return np.zeros(3)
    return np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]) / (2 * math.sin(th)) * th


def web_follow(wn, k):
    """v2 (the webs now reach 93% down): each web mid-chain bone carries the mean of its two side tentacles' drape at the
    same depth (rotation vectors averaged, rescaled 5 cape bones -> 4 web bones over WEB_V_EDGE), so the web middle
    follows the cape's sway/trail/flare instead of lagging and stretching the hem."""
    def cum(x):                                        # cumulative mean drape (rotation vector) at cape-bone position x
        tot = np.zeros(3)
        for j in range(CAPE_BONES):
            f_ = min(max(x - j, 0.0), 1.0)
            if f_ <= 0:
                break
            tot += f_ * 0.5 * (rotvec(LOCAL_R["%s.%d" % (WEB[wn]["A"], j)]) + rotvec(LOCAL_R["%s.%d" % (WEB[wn]["B"], j)]))
        return tot
    x0 = k / WEB_BONES * WEB_V_EDGE * CAPE_BONES
    x1 = (k + 1) / WEB_BONES * WEB_V_EDGE * CAPE_BONES
    rv = cum(x1) - cum(x0)
    a_ = float(np.linalg.norm(rv))
    return K._rot(rv, a_) if a_ > 1e-12 else np.eye(3)


def rot_between(a, b):
    a = a / np.linalg.norm(a); b = b / np.linalg.norm(b)
    ax = np.cross(a, b); s_ = float(np.linalg.norm(ax)); c_ = float(np.clip(a @ b, -1.0, 1.0))
    if s_ < 1e-12:
        return np.array([1.0, 0.0, 0.0]), 0.0
    return ax / s_, math.atan2(s_, c_)


# ---- float: the crossed-arms target, solved once in the chest's REST frame (joints walk a designed path, bone lengths kept)
Z_CROSS = Z_HIP + FLOAT_CROSS_Z_FRAC * (Z_NECK - Z_HIP)
torso_m = (vshell == 0) & (labF == 0) & (W_[:, 2] < NECK_ZA)
bandc = torso_m & (np.abs(W_[:, 2] - Z_CROSS) < 0.35)
Y_FRONT = float(W_[bandc & (np.abs(W_[:, 0] - X_MID) < 0.8), 1].min())
HALF_W = float(np.abs(W_[bandc, 0] - X_MID).max())
ARM_RAD = float(np.mean([CHAINS["arm.L"]["info"]["median_radius"], CHAINS["arm.R"]["info"]["median_radius"]]))
CROSS = {}
cross_rep = {}
for side, sg in (("L", 1.0), ("R", -1.0)):
    top = side == "L"
    dz = FLOAT_STACK_DZ if top else -FLOAT_STACK_DZ
    yb = Y_FRONT - ARM_RAD - FLOAT_ARM_GAP - (FLOAT_STACK_DY if top else 0.0)
    zc = Z_CROSS + dz
    names, _ = CH_BONES["arm." + side]
    P0 = HEADP[names[0]]
    ctrl = [P0,
            [X_MID + sg * (HALF_W + ARM_RAD * 0.8), 0.5 * (P0[1] + Y_FRONT) - 0.3, zc + 1.0],
            [X_MID + sg * HALF_W * 0.70, yb, zc + 0.20],
            [X_MID, yb - 0.05, zc],
            [X_MID - sg * HALF_W * 0.70, yb, zc - 0.05],
            [X_MID - sg * (HALF_W + ARM_RAD * 0.9), Y_FRONT + 0.4, zc],
            [X_MID - sg * (HALF_W + ARM_RAD * 1.0), Y_FRONT + 2.2, zc + 0.1]]
    path = catmull(ctrl, 60)
    Jt = [P0.copy()]
    j0 = 0
    for k, n in enumerate(names):
        Lb = float(np.linalg.norm(TAILP[n] - HEADP[n]))
        cur = Jt[-1]
        found = None
        for q in range(j0, len(path)):
            if np.linalg.norm(path[q] - cur) >= Lb:
                found = q; break
        if found is None:                              # path ran out: continue along its last direction
            dlast = path[-1] - path[-2]; dlast /= np.linalg.norm(dlast)
            Jt.append(cur + dlast * Lb)
            j0 = len(path) - 1
        else:
            a_, b_ = path[found - 1], path[found]      # exact chord length on the last segment
            lo_, hi_ = 0.0, 1.0
            for _ in range(40):
                mid_ = 0.5 * (lo_ + hi_)
                if np.linalg.norm(a_ + (b_ - a_) * mid_ - cur) < Lb:
                    lo_ = mid_
                else:
                    hi_ = mid_
            Jt.append(a_ + (b_ - a_) * lo_)
            j0 = found
    Rw = np.eye(3)
    rots = []
    for k, n in enumerate(names):
        r_rest = TAILP[n] - HEADP[n]
        t_dir = Jt[k + 1] - Jt[k]
        ax_, ang_ = rot_between(r_rest, Rw.T @ t_dir)
        Rl = K._rot(ax_, ang_)
        rots.append((ax_, ang_))
        Rw = Rw @ Rl
    CROSS[side] = rots
    cross_rep[side] = {"joints": np.array(Jt).round(3).tolist(), "bone_angles_deg": [round(math.degrees(a_), 2) for _, a_ in rots],
                       "forearm_height": round(zc, 3), "forearm_y": round(yb, 3)}
cross_rep["torso"] = {"cross_z": round(Z_CROSS, 3), "front_y": round(Y_FRONT, 3), "half_width": round(HALF_W, 3), "arm_radius": round(ARM_RAD, 3)}


def arm_cross_w(t, k):
    c0, c1, c2, c3 = FLOAT_CROSS_T
    st = FLOAT_BONE_STAGGER
    return ss5(c0 + k * st, c1 + k * st, t) * (1.0 - ss5(c2 - k * st, c3 - k * st, t))


def float_env(t):
    t0, t1, t2, t3 = FLOAT_T
    return ss5(t0, t1, t) * (1.0 - ss5(t2, t3, t))


def mouth_w(clip, t):
    """v3 'smirk' morph weight at loop phase t: 0 = shut (nothing visible). Staged in the float's arms-crossed hold."""
    T_ = {"float": FLOAT_SMIRK_T, "idle": IDLE_SMIRK_T, "walk": WALK_SMIRK_T}[clip]
    if T_ is None:
        return 0.0
    return ss5(T_[0], T_[1], t) * (1.0 - ss5(T_[2], T_[3], t))


def pose(clip, f):
    """Deform transforms of frame f (0-based within the period) -> ({bone: 4x4}, info)."""
    N = CLIP_N[clip]
    t = f / N
    D = {"root": np.eye(4)}
    info = {}
    if clip == "idle":
        Rp = np.eye(3)
        dz0 = min(0.0, pelvis_drop(Rp, [("L", TIP["L"][1]), ("R", TIP["R"][1])], IDLE_REACH_K))
        off = np.array([0.0, 0.0, dz0 - IDLE_BREATH * (0.5 - 0.5 * math.cos(TAU * 2 * t))])
        D["pelvis"] = Tt(off) @ Tr(HEADP["pelvis"], Rp)
        D["spine"] = D["pelvis"] @ Tr(HEADP["spine"], Ry(IDLE_SWAY_DEG * math.sin(TAU * t)) @ Rx(IDLE_BREATH_DEG * math.sin(TAU * 2 * t)))
        D["chest"] = D["spine"] @ Tr(HEADP["chest"], Ry(0.6 * IDLE_SWAY_DEG * math.sin(TAU * t - 0.5)) @ Rx(-0.5 * IDLE_BREATH_DEG * math.sin(TAU * 2 * t)))
        D["head"] = D["chest"] @ Tr(HEADP["head"], Rz(IDLE_HEAD_YAW_DEG * math.sin(TAU * t + 0.8)) @ Rx(IDLE_HEAD_NOD_DEG * math.sin(TAU * 2 * t + 0.7)))
        for side in ("L", "R"):
            err = solve_leg(D, side, D["pelvis"][:3, :3] @ HEADP["thigh." + side] + D["pelvis"][:3, 3], TIP[side][1], PLANT_Z, np.eye(3), True)
            info["ik_err_" + side] = err
        for side, ph in (("L", 0.0), ("R", 2.4)):
            names, _ = CH_BONES["arm." + side]
            sg = 1.0 if side == "L" else -1.0
            rots = [Rx(K.vine_wave(t, k, ARM_BONES, IDLE_ARM_DEG[0], IDLE_ARM_DEG[1], IDLE_ARM_LAG, 1, ph)) @
                    Ry(sg * 0.6 * K.vine_wave(t, k, ARM_BONES, IDLE_ARM_DEG[0], IDLE_ARM_DEG[1], IDLE_ARM_LAG, 2, ph + 1.1))
                    for k in range(ARM_BONES)]
            chain_rel(D, names, rots)
        for c_i, cn_ in enumerate(CAPE_NAMES):
            names, _ = CH_BONES[cn_]
            ph = 1.1 * c_i
            bias = [IDLE_CAPE_BIAS * (IDLE_CAPE_DEG[0] + (IDLE_CAPE_DEG[1] - IDLE_CAPE_DEG[0]) * k / (CAPE_BONES - 1)) for k in range(CAPE_BONES)]
            rots = [Rx(bias[k] + K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], IDLE_CAPE_LAG, 1, ph)) @
                    Ry(0.5 * K.vine_wave(t, k, CAPE_BONES, IDLE_CAPE_DEG[0], IDLE_CAPE_DEG[1], IDLE_CAPE_LAG, 1, ph + 2.0))
                    for k in range(CAPE_BONES)]
            chain_rel(D, names, rots)
        for w_i, wn in enumerate(("web.R", "web.C", "web.L")):
            names, _ = CH_BONES[wn]
            rots = [web_follow(wn, k) @ rotm(WEB[wn]["across"], K.vine_wave(t, k, WEB_BONES, IDLE_WEB_DEG[0], IDLE_WEB_DEG[1], IDLE_WEB_LAG, 3, 0.9 * w_i))
                    for k in range(WEB_BONES)]
            chain_rel(D, names, rots)
        return D, info
    if clip == "float":
        E = float_env(t)
        c_t = FLOAT_CAPE_T
        Wc = ss5(c_t[0], c_t[1], t) * (1.0 - ss5(c_t[2], c_t[3], t))
        Hh_ = ss5(FLOAT_T[1] - 0.04, FLOAT_T[1] + 0.06, t) * (1.0 - ss5(FLOAT_T[2] - 0.06, FLOAT_T[2] + 0.02, t))
        crouch = bump(0.0, FLOAT_T[0] + 0.08, t) + bump(FLOAT_T[3] - 0.07, 1.0, t)
        Rp = np.eye(3)
        dz0 = min(0.0, pelvis_drop(Rp, [("L", TIP["L"][1]), ("R", TIP["R"][1])], IDLE_REACH_K))
        dz = dz0 - FLOAT_CROUCH * crouch + RISE * E + FLOAT_BOB * math.sin(TAU * 4 * t) * Hh_
        D["pelvis"] = Tt(np.array([0.0, 0.0, dz])) @ Tr(HEADP["pelvis"], Rp)
        D["spine"] = D["pelvis"] @ Tr(HEADP["spine"], Rx(-0.4 * FLOAT_CHEST_UP_DEG * E + 2.0 * crouch))
        D["chest"] = D["spine"] @ Tr(HEADP["chest"], Rx(-0.6 * FLOAT_CHEST_UP_DEG * E) @ Ry(0.8 * math.sin(TAU * 2 * t) * Hh_))
        D["head"] = D["chest"] @ Tr(HEADP["head"], Rz(FLOAT_HEAD_SWAY_DEG * math.sin(TAU * 2 * t) * Hh_) @ Rx(-FLOAT_HEAD_UP_DEG * E))
        air = ss5(0.0, 0.5, E)
        for side in ("L", "R"):
            hip_p = D["pelvis"][:3, :3] @ HEADP["thigh." + side] + D["pelvis"][:3, 3]
            ty_s, tz_s = TIP[side][1], PLANT_Z + RISE * E
            ty_h = TIP[side][1] + FLOAT_LEG_TRAIL
            rr = FLOAT_LEG_EXTEND * LEG[side]["r0"]
            tz_h = hip_p[2] - math.sqrt(max(rr * rr - (ty_h - hip_p[1]) ** 2, 1e-9))
            ty, tz = (1 - air) * ty_s + air * ty_h, (1 - air) * tz_s + air * tz_h
            err = solve_leg(D, side, hip_p, ty, tz, Rx(FLOAT_LEG_POINT_DEG * air), True)
            info["ik_err_" + side] = err if E < 1e-9 else 0.0
        for side in ("L", "R"):
            names, _ = CH_BONES["arm." + side]
            rots = [K._rot(CROSS[side][k][0], CROSS[side][k][1] * arm_cross_w(t, k)) for k in range(ARM_BONES)]
            chain_rel(D, names, rots)
        for c_i, cn_ in enumerate(CAPE_NAMES):
            names, _ = CH_BONES[cn_]
            sx = 1.0 if cn_.endswith(".L") else -1.0
            spread_k = 1.0 if cn_.startswith("cape_outer") else 0.5
            ph = FLOAT_CAPE_PHASE * c_i                  # one coherent gust: small phase steps between neighbours
            rots = []
            for k in range(CAPE_BONES):
                fr = FLOAT_CAPE_FLARE_DEG[0] + (FLOAT_CAPE_FLARE_DEG[1] - FLOAT_CAPE_FLARE_DEG[0]) * k / (CAPE_BONES - 1)
                sp = FLOAT_CAPE_SPREAD_DEG[0] + (FLOAT_CAPE_SPREAD_DEG[1] - FLOAT_CAPE_SPREAD_DEG[0]) * k / (CAPE_BONES - 1)
                fl = K.vine_wave(t, k, CAPE_BONES, FLOAT_CAPE_FLUTTER_DEG[0], FLOAT_CAPE_FLUTTER_DEG[1], FLOAT_CAPE_LAG, 5, ph)
                fl2 = K.vine_wave(t, k, CAPE_BONES, FLOAT_CAPE_FLUTTER_DEG[0], FLOAT_CAPE_FLUTTER_DEG[1], FLOAT_CAPE_LAG, 3, ph + 1.7)
                rots.append(Rx(Wc * (fr + fl)) @ Ry(Wc * (-sx * spread_k * sp + 0.4 * fl2)))
            chain_rel(D, names, rots)
        for w_i, wn in enumerate(("web.R", "web.C", "web.L")):
            names, _ = CH_BONES[wn]
            rots = [web_follow(wn, k) @ rotm(WEB[wn]["across"], Wc * (FLOAT_WEB_BILLOW_DEG + K.vine_wave(t, k, WEB_BONES, FLOAT_WEB_DEG[0], FLOAT_WEB_DEG[1], FLOAT_WEB_LAG, 8, FLOAT_CAPE_PHASE * w_i)))
                    for k in range(WEB_BONES)]
            chain_rel(D, names, rots)
        info.update({"pelvis_dz": dz, "E": E, "cape_wind": Wc})
        return D, info
    # ---------------------------------------------------------------- walk (v2 stride)
    c1 = math.cos(TAU * t)
    yaw = -WALK_PELVIS_YAW_DEG * c1                       # left hip forward while the left foot is forward (t = 0)
    roll = -WALK_PELVIS_ROLL_DEG * math.sin(TAU * t)      # the swing side dips a little
    Rp = Rz(yaw) @ Ry(roll)
    tgt, contacts = {}, []
    for side, t0 in (("L", 0.0), ("R", 0.5)):
        u = (t - t0) % 1.0
        if u <= 0.5:                                      # stance: the tip slides front -> back (the ground passing under)
            p = u / 0.5
            tgt[side] = (TIP[side][1] - STEP_A + 2 * STEP_A * p, PLANT_Z, 0.0)
            contacts.append((side, tgt[side][0]))
        else:                                             # swing: back -> front, lifted, the tentacle tip curling
            p = (u - 0.5) / 0.5
            tgt[side] = (TIP[side][1] + STEP_A * math.cos(math.pi * p), PLANT_Z + LIFT * math.sin(math.pi * p),
                         WALK_FOOT_CURL_DEG * math.sin(math.pi * p))
            if p >= 1.0 - 1e-9:
                contacts.append((side, tgt[side][0]))
    dz = pelvis_drop(Rp, contacts, WALK_REACH_K)
    D["pelvis"] = Tt(np.array([0.0, 0.0, dz])) @ Tr(HEADP["pelvis"], Rp)
    D["spine"] = D["pelvis"] @ Tr(HEADP["spine"], Rx(WALK_LEAN_DEG) @ Ry(-0.5 * roll))
    D["chest"] = D["spine"] @ Tr(HEADP["chest"], Rz(-yaw + WALK_CHEST_COUNTER_DEG * c1) @ Rx(-WALK_CHEST_UP_DEG) @ Ry(-0.3 * roll))
    D["head"] = D["chest"] @ Tr(HEADP["head"], Rz(-0.6 * WALK_CHEST_COUNTER_DEG * c1) @
                                Rx(-WALK_LEAN_DEG + WALK_CHEST_UP_DEG - WALK_HEAD_UP_DEG + 1.0 * math.sin(TAU * 2 * t)))
    for side in ("L", "R"):
        hip_p = D["pelvis"][:3, :3] @ HEADP["thigh." + side] + D["pelvis"][:3, 3]
        ty, tz, curl = tgt[side]
        err = solve_leg(D, side, hip_p, ty, tz, Rx(curl), curl == 0.0 and tz == PLANT_Z)
        info["ik_err_" + side] = err
    for side in ("L", "R"):
        names, _ = CH_BONES["arm." + side]
        sg = 1.0 if side == "L" else -1.0                 # left arm back (+Rx) while the left foot is forward
        rots = [Rx(sg * (WALK_ARM_SWING_DEG * c1 if k == 0 else WALK_ARM_FOLLOW_DEG * math.cos(TAU * t - k * WALK_ARM_LAG)))
                @ Ry(sg * 1.5 * math.sin(TAU * t - k * WALK_ARM_LAG)) for k in range(ARM_BONES)]
        chain_rel(D, names, rots)
    for c_i, cn_ in enumerate(CAPE_NAMES):
        names, _ = CH_BONES[cn_]
        ph = 0.9 * c_i
        rots = [Rx(WALK_CAPE_TRAIL_DEG + K.vine_wave(t, k, CAPE_BONES, WALK_CAPE_DEG[0], WALK_CAPE_DEG[1], WALK_CAPE_LAG, 2, ph)) @
                Ry(0.4 * K.vine_wave(t, k, CAPE_BONES, WALK_CAPE_DEG[0], WALK_CAPE_DEG[1], WALK_CAPE_LAG, 1, ph + 1.3))
                for k in range(CAPE_BONES)]
        chain_rel(D, names, rots)
    for w_i, wn in enumerate(("web.R", "web.C", "web.L")):
        names, _ = CH_BONES[wn]
        rots = [web_follow(wn, k) @ rotm(WEB[wn]["across"], WALK_WEB_BILLOW_DEG + K.vine_wave(t, k, WEB_BONES, WALK_WEB_DEG[0], WALK_WEB_DEG[1], WALK_WEB_LAG, 2, 0.9 * w_i))
                for k in range(WEB_BONES)]
        chain_rel(D, names, rots)
    info.update({"pelvis_dz": dz, "targets": tgt})
    return D, info


ORDER = ["root"] + DEFORM
for pb in rig.pose.bones:
    pb.rotation_mode = "QUATERNION"


def basis(D, n):
    return np.linalg.inv(REST4[n]) @ np.linalg.inv(D[PARENT[n]]) @ D[n] @ REST4[n]


CLIP_N = {"idle": IDLE_N, "walk": WALK_N, "float": FLOAT_N}
CLIPS = dict(CLIP_N)
NEW_ACTS, clip_rep, key_rows, REL = {}, {}, [], {}
ik_worst = {}
for cn, N in CLIPS.items():
    act = bpy.data.actions.new(cn)
    act.use_fake_user = True
    K.assign_action(rig, act)
    prevq = {}
    REL[cn] = {n: [] for n in DEFORM}
    for f in range(N + 1):                     # frames 1..N+1; frame N+1 == frame 1 (integer cycles -> exact seam)
        D, info = pose(cn, f % N)
        for side in ("L", "R"):
            ik_worst[(cn, side)] = max(ik_worst.get((cn, side), 0.0), abs(info["ik_err_" + side]))
        for n in DEFORM:
            Bm = basis(D, n)
            q = Matrix(Bm[:3, :3].tolist()).to_quaternion(); q.normalize()
            if n in prevq and prevq[n].dot(q) < 0:
                q.negate()
            prevq[n] = q.copy()
            pb = rig.pose.bones[n]
            pb.location = Vector(Bm[:3, 3]); pb.rotation_quaternion = q
            pb.keyframe_insert("location", frame=f + 1)
            pb.keyframe_insert("rotation_quaternion", frame=f + 1)
            key_rows.append(list(pb.location) + list(pb.rotation_quaternion))
            if f < N:
                Rrel = (np.linalg.inv(D[PARENT[n]]) @ D[n])[:3, :3]
                REL[cn][n].append(Rrel)
    # the 'smirk' morph weight rides the SAME action (a KEY slot on the mesh's shape keys); every clip carries it (0 when
    # shut) so a clip switch in the game always resets the mouth
    K.assign_action(KEYB, act)
    for f in range(N + 1):
        w_ = mouth_w(cn, (f % N) / N)
        KEYB.key_blocks[SMIRK_KEY].value = w_
        KEYB.key_blocks[SMIRK_KEY].keyframe_insert("value", frame=f + 1)
        key_rows.append([w_] * 7)
    KEYB.animation_data.action = None
    KEYB.key_blocks[SMIRK_KEY].value = 0.0
    for fc in K.action_fcurves(act):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    act["contact_cycles"] = 1
    NEW_ACTS[cn] = act
DIG["keys"] = sha(np.array(key_rows))


def eval_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    m_ = ev.to_mesh()
    co = np.empty(len(m_.vertices) * 3); m_.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    M_ = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ M_[:3, :3].T + M_[:3, 3]


def rot_angle(Ra, Rb):
    c = (np.trace(Ra.T @ Rb) - 1.0) / 2.0
    return math.degrees(math.acos(float(np.clip(c, -1.0, 1.0))))


GROUPS_M = {"arm": [n for n in DEFORM if n.startswith("arm.")],
            "leg": [n for n in DEFORM if n.split(".")[0] in ("thigh", "shin", "foot")],
            "cape": [n for n in DEFORM if n.startswith("cape_")],
            "web": [n for n in DEFORM if n.startswith("web.")]}
tip_vid = {side: int(np.nonzero((np.abs(W_ - TIP[side]).sum(1) < 1e-9))[0][0]) for side in ("L", "R")}
arm_vids = np.nonzero(np.isin(labF, [2, 3]))[0]
leg_vids = np.nonzero(np.isin(labF, [4, 5]))[0]
web_vids = np.arange(NVS, nV)
cape_tip_vid = {}
for i_c, cn_ in enumerate(CAPE_NAMES):
    m = np.nonzero((labF == 10 + i_c) & (vshell == 1))[0]
    cape_tip_vid[cn_] = int(m[np.argmax(sF[m])])
FF_all = mesh_arrays(me)[1]
Ew = edges_of([f for f in FF_all if f[0] >= NVS])
rest_len_w = np.linalg.norm(W_[Ew[:, 0]] - W_[Ew[:, 1]], axis=1)
Ew = Ew[rest_len_w > 2.5 * WEB_THICK]                  # the sheet's own edges (not the 0.06 through-thickness rim edges)
rest_len_w = rest_len_w[rest_len_w > 2.5 * WEB_THICK]
arm_side = {s: np.nonzero(labF == LAB_ID["arm." + s])[0] for s in ("L", "R")}
arm_deep = {s: arm_side[s][sF[arm_side[s]] > 0.45 * CHAINS["arm." + s]["L"]] for s in ("L", "R")}   # the forearm (clear of the armpit openings)
torso_vids = np.nonzero((vshell == 0) & (labF == 0) & (W_[:, 2] < NECK_ZA))[0]
belt_vids = np.unique(np.array([v for fi, f in enumerate(FF_all) if rid[fi] == R_["belt"] for v in f], dtype=np.int64))
capeweb_vids = np.nonzero(vshell >= 1)[0]
samples = []
pose_check = 0.0
mouth_track = {}
belt_gap = {}
for cn, N in CLIPS.items():
    act = NEW_ACTS[cn]
    K.assign_action(rig, act)
    K.assign_action(KEYB, act)                         # the smirk weight rides the clip (KEY slot)
    first = last = None
    minz, root_off = 1e9, 0.0
    belt_arm, belt_cape = 1e9, 1e9
    tips, arm_web_gap, leg_web_gap, stretch = [], 1e9, 1e9, [1e9, 0.0]
    ends_minz = []
    flare = 0.0
    arm_torso, arm_arm = None, None
    ctip0 = None
    for f in range(1, N + 2):
        scene.frame_set(f)
        C = eval_coords(low)
        samples.append(C[::7])
        if f == 1:
            first = C
            D1, _ = pose(cn, 0)
            for n in DEFORM:
                pose_check = max(pose_check, float(np.abs(np.array(rig.pose.bones[n].matrix) - D1[n] @ REST4[n]).max()))
        if f == N + 1:
            last = C
        if f in (1, N + 1):
            ends_minz.append(float(C[:, 2].min()))
        if float(C[:, 2].min()) < minz:
            iz_ = int(np.argmin(C[:, 2]))
            minz_at = {"frame": f, "label": int(labF[iz_]), "shell": int(vshell[iz_]), "arc": round(float(sF[iz_]), 3)}
        minz = min(minz, float(C[:, 2].min()))
        root_off = max(root_off, (rig.matrix_world @ rig.pose.bones["root"].head).length)
        tips.append([C[tip_vid["L"]].copy(), C[tip_vid["R"]].copy()])
        lw = np.linalg.norm(C[Ew[:, 0]] - C[Ew[:, 1]], axis=1) / rest_len_w
        if float(lw.max()) > stretch[1]:
            e_ = Ew[int(np.argmax(lw))]
            wn_ = [w for w, (a0_, a1_) in WEB_VRANGE.items() if a0_ <= e_[0] < a1_][0]
            uvw_ = [WEB[wn_]["UVW"][v - WEB_VRANGE[wn_][0]].round(3).tolist() for v in e_]
            stretch_at = {"frame": f, "web": wn_, "uvw_ends": uvw_, "rest_len": round(float(rest_len_w[int(np.argmax(lw))]), 4)}
        stretch = [min(stretch[0], float(lw.min())), max(stretch[1], float(lw.max()))]
        Df, _ = pose(cn, (f - 1) % N)
        Ci = np.linalg.inv(Df["chest"])
        ctl = np.array([(Ci @ np.append(C[v], 1.0))[:3] for v in cape_tip_vid.values()])
        if ctip0 is None:
            ctip0 = ctl
        flare = max(flare, float(np.linalg.norm(ctl - ctip0, axis=1).max()))
        if f % 2 == 1:
            kd_w = KDTree(len(web_vids))
            for i, v in enumerate(web_vids):
                kd_w.insert(C[v], i)
            kd_w.balance()
            arm_web_gap = min(arm_web_gap, min(kd_w.find(C[v])[2] for v in arm_vids[::3]))
            leg_web_gap = min(leg_web_gap, min(kd_w.find(C[v])[2] for v in leg_vids[::2]))
            if len(belt_vids):
                kd_bl = KDTree(len(belt_vids))
                for i, v in enumerate(belt_vids):
                    kd_bl.insert(C[v], i)
                kd_bl.balance()
                belt_arm = min(belt_arm, min(kd_bl.find(C[v])[2] for v in arm_vids[::2]))
                belt_cape = min(belt_cape, min(kd_bl.find(C[v])[2] for v in capeweb_vids[::2]))
        if cn == "float" and f - 1 == int(round(0.48 * N)):
            Cy_mid = float(np.median(C[torso_vids, 1]))
            # forearm vs belly: for every forearm vertex over the torso's front, its y must be ahead of (below) the
            # torso's front surface there (torso vertices within 0.35 in x and z); gap = torso front y - vertex y
            tv_ = torso_vids
            sd_ = {}
            for s_ in ("L", "R"):
                g_ = []
                for v in arm_deep[s_]:
                    nb_ = tv_[(np.abs(C[tv_, 0] - C[v, 0]) < 0.35) & (np.abs(C[tv_, 2] - C[v, 2]) < 0.35) & (C[tv_, 1] < Cy_mid)]
                    if len(nb_):
                        g_.append(float(C[nb_, 1].min() - C[v, 1]) - 0.0)
                sd_[s_] = min(g_) if g_ else None
            kd_r = KDTree(len(arm_side["R"]))
            for i, v in enumerate(arm_side["R"]):
                kd_r.insert(C[v], i)
            kd_r.balance()
            arm_torso = {s_: (round(float(v_), 4) if v_ is not None else None) for s_, v_ in sd_.items()}
            arm_arm = round(float(min(kd_r.find(C[v])[2] for v in arm_side["L"])), 4)
    tips = np.array(tips)
    seam = float(np.linalg.norm(first - last, axis=1).max())
    amp = {}
    for n in DEFORM:
        R_l = REL[cn][n]
        amp[n] = 0.5 * max(rot_angle(R_l[i], R_l[j]) for i in range(0, len(R_l), 2) for j in range(i + 1, len(R_l), 2))
    grp = {g: round(float(np.mean([amp[n] for n in ns])), 3) for g, ns in GROUPS_M.items()}
    row = {"frames": [1, N + 1], "period_frames": N, "seconds": round(N / K.FPS, 4), "cyclic": True,
           "seam_units": round(seam, 8), "clearance_min_z": round(minz, 4), "clearance_min_z_where": minz_at, "min_z_at_loop_ends": [round(v, 5) for v in ends_minz],
           "root_offset_max": round(root_off, 8),
           "ik_unreachable_max": {s: round(ik_worst[(cn, s)], 6) for s in ("L", "R")},
           "mean_bone_amplitude_deg": grp,
           "cape_vs_arm_ratio": round(grp["cape"] / grp["arm"], 3), "cape_vs_leg_ratio": round(grp["cape"] / max(grp["leg"], 1e-9), 3),
           "per_bone_amplitude_deg": {n: round(v, 3) for n, v in amp.items()},
           "arm_to_web_min_gap": round(float(arm_web_gap), 4), "leg_to_web_min_gap": round(float(leg_web_gap), 4),
           "web_edge_stretch_range": [round(stretch[0], 4), round(stretch[1], 4)], "web_edge_stretch_max_at": stretch_at,
           "cape_tip_travel_in_chest_frame_max": round(flare, 4),
           "belt_to_arm_min_gap": round(float(belt_arm), 4) if len(belt_vids) else None,
           "belt_to_cape_or_web_min_gap": round(float(belt_cape), 4) if len(belt_vids) else None}
    mw_ = [mouth_w(cn, f / N) for f in range(N + 1)]
    op_ = [f + 1 for f in range(N + 1) if mw_[f] > 1e-6]
    row["smirk"] = {"staging": {"float": FLOAT_SMIRK_T, "idle": IDLE_SMIRK_T, "walk": WALK_SMIRK_T}[cn],
                    "weight_range": [round(min(mw_), 4), round(max(mw_), 4)],
                    "open_frames": [op_[0], op_[-1]] if op_ else None,
                    "fully_open_frames": None, "weight_at_loop_ends": [mw_[0], mw_[-1]]}
    if op_:
        fo_ = [f + 1 for f in range(N + 1) if mw_[f] > 1 - 1e-6]
        row["smirk"]["fully_open_frames"] = [fo_[0], fo_[-1]] if fo_ else None
    if cn == "walk":
        half = N // 2
        gait = {}
        for si, side in enumerate(("L", "R")):
            st0 = 0 if side == "L" else half
            stance = [(st0 + k) % N for k in range(half + 1)]
            swing = [(st0 + half + k) % N for k in range(1, half)]
            y = tips[:, si, 1]; z = tips[:, si, 2]
            gait[side] = {"tip_y_touchdown": round(float(y[stance[0]]), 4), "tip_y_liftoff": round(float(y[stance[-1]]), 4),
                          "step_length": round(float(y[stance[-1]] - y[stance[0]]), 4),
                          "stance_tip_z_range": [round(float(z[stance].min()), 5), round(float(z[stance].max()), 5)],
                          "stance_tip_x_slip": round(float(np.ptp(tips[stance, si, 0])), 5),
                          "swing_tip_z_max": round(float(z[swing].max()), 4)}
        step = float(np.mean([gait[s]["step_length"] for s in gait]))
        speed = 2.0 * step / (N / K.FPS)
        row.update({"gait": gait, "step_length_units": round(step, 4), "stride_units": round(2 * step, 4),
                    "step_length_x_leg_reach": round(step / LEG_R0, 4),
                    "cadence_steps_per_min": round(2 * 60.0 * K.FPS / N, 2),
                    "implied_ground_speed": {"units_per_s": round(speed, 4), "body_heights_per_s": round(speed / H, 4),
                                             "leg_reaches_per_s": round(speed / LEG_R0, 4),
                                             "m_per_s_at_cell_fit_report_only": round(speed * GAME_M_PER_UNIT, 4),
                                             "rule": "stride (2 x mean stance-tip travel) / cycle time"},
                    "step_half_travel": round(STEP_A, 4), "foot_lift": round(LIFT, 4), "leg_reach_rest": round(LEG_R0, 4),
                    "posture_deg": {"spine_lean_fwd": WALK_LEAN_DEG, "chest_up_back": WALK_CHEST_UP_DEG, "head_up": WALK_HEAD_UP_DEG},
                    "v1": {"cadence_steps_per_min": 102.86, "step_length_units": 3.0287, "stride_units": 6.0574,
                           "units_per_s": 5.1921, "arm_swing_deg": 12.0, "chest_counter_deg": 7.0}})
        assert step > 0, "walk runs backwards (stance tip must slide toward +Y, the character faces -Y)"
    else:
        row["stance_tip_z_range"] = {s: [round(float(tips[:, i, 2].min()), 5), round(float(tips[:, i, 2].max()), 5)] for i, s in enumerate(("L", "R"))}
        row["stance_tip_xy_slip"] = {s: round(float(np.linalg.norm(tips[:, i, :2] - tips[0, i, :2], axis=1).max()), 5) for i, s in enumerate(("L", "R"))}
    if cn == "float":
        pz = [pose(cn, f)[1]["pelvis_dz"] for f in range(N)]
        row["float"] = {"rise_units": round(RISE, 4), "rise_x_height": FLOAT_RISE_FRAC,
                        "pelvis_dz_range": [round(min(pz), 4), round(max(pz), 4)],
                        "pelvis_lift_over_rest": round(max(pz) - pz[0], 4),
                        "foot_tip_max_z": round(float(tips[:, :, 2].max()), 4),
                        "min_z_over_clip": round(minz, 4),
                        "timeline": {"rise": FLOAT_T, "arms": FLOAT_CROSS_T, "cape_wind": FLOAT_CAPE_T},
                        "crossed_pose_frame": int(round(0.48 * N)) + 1,
                        "forearm_ahead_of_belly_min_gap_at_crossed": arm_torso, "arm_L_to_arm_R_min_at_crossed": arm_arm,
                        "cape_flare_tip_travel_max_units": round(flare, 4),
                        "cape_flare_tip_travel_x_height": round(flare / H, 4),
                        "cross": cross_rep}
    clip_rep[cn] = row
    print("CLIP", cn, json.dumps({k: v for k, v in row.items() if k != "per_bone_amplitude_deg"}, default=str))
    sys.stdout.flush()
DIG["clip_samples"] = sha(np.concatenate(samples))
rep["clips"] = clip_rep
rep["fk_vs_blender_pose_max_abs"] = pose_check
rep["motion_hierarchy"] = {"rule": "mean over each chain group's bones of half the largest rotation between two frames of the "
                                   "loop (relative to the parent; constant drape offsets excluded)",
                           **{cn: clip_rep[cn]["mean_bone_amplitude_deg"] for cn in CLIPS},
                           "cape_vs_arm": {cn: clip_rep[cn]["cape_vs_arm_ratio"] for cn in CLIPS}}
rig.animation_data.action = None
KEYB.animation_data.action = None
KEYB.key_blocks[SMIRK_KEY].value = 0.0
for pb in rig.pose.bones:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
scene.frame_start, scene.frame_end = 1, IDLE_N + 1
rep["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                 "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local]} for b in arm_data.bones]
rep["bone_count"] = len(arm_data.bones)
rep["deform_bone_count"] = len(DEFORM)
rep["landmarks"] = {"hip_z": round(Z_HIP, 4), "neck_z": round(Z_NECK, 4), "neck_ramp_z": [round(NECK_ZA, 4), round(NECK_ZB, 4)],
                    "head_top_z": round(Z_TOP, 4), "leg_tips": {s: TIP[s].round(4).tolist() for s in TIP},
                    "emblem_centre": EMB_C.round(4).tolist(), **LMK}
rig["conquest_rig"] = ("supaoctto v3: root (contract) > pelvis > spine > chest > head (over the neck); thigh/shin/foot per leg "
                       "(analytic IK); arm.L/R.0-3; cape_outer/inner.L/R.0-4; web.R/C/L.0-3 (water-web mid chains)")
low["conquest_clips"] = list(CLIPS)
low["conquest_clip_status"] = ("idle + walk (confident stride, in place) + float (rise, arms crossed + smirk, cape flare, "
                               "land); no attack/hit/death")
low["conquest_shape_keys"] = ("smirk: glTF morph target; 0 = mouth shut (nothing visible), 1 = the smirk-line opening; its "
                              "weight is keyed in every clip (float opens it over the arms-crossed hold)")
for m in list(bpy.data.materials):
    if m.users == 0:
        bpy.data.materials.remove(m)
for a in list(bpy.data.actions):
    if a.name not in CLIPS:
        bpy.data.actions.remove(a)
DIG_ALL = hashlib.sha256(json.dumps(DIG, sort_keys=True).encode()).hexdigest()[:16]
rep["digest"] = {"parts": DIG, "combined": DIG_ALL}
if DIGEST_ONLY:
    json.dump({"digest": rep["digest"], "tris_final": report["tris_final"], "seconds": round(time.time() - T0, 1)}, open(DIGEST_ONLY, "w"), indent=1)
    print("DIGEST", DIG_ALL, json.dumps(DIG))
    sys.stdout.flush(); os._exit(0)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_RIGGED), exist_ok=True)
set_tex_paths("//../improved/textures/")
bpy.ops.wm.save_as_mainfile(filepath=OUT_RIGGED, copy=True, compress=True, relative_remap=False)

# =========================================================================== 10. identity-scale glb (+ the smirk morph)
def glb_carries(path):
    import struct
    data = open(path, "rb").read()
    L = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + L])
    b0 = 20 + L + 8                                   # the BIN chunk's payload
    prims = [p for m in js.get("meshes", []) for p in m["primitives"]]
    nodes = js.get("nodes", [])

    def floats(ai):                                   # decode a FLOAT accessor from the BIN chunk (the keyed weights)
        acc = js["accessors"][ai]; bv = js["bufferViews"][acc["bufferView"]]
        assert acc["componentType"] == 5126
        o_ = b0 + bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        return np.frombuffer(data[o_:o_ + 4 * acc["count"]], dtype="<f4")

    anims = []
    for a in js.get("animations", []):
        w = [c for c in a["channels"] if c["target"].get("path") == "weights"]
        wk = []
        for c in w:
            smp = a["samplers"][c["sampler"]]
            vals = floats(smp["output"]); tms = floats(smp["input"])
            on_ = tms[vals > 0.5]
            wk.append({"node": nodes[c["target"]["node"]].get("name"), "values": int(len(vals)),
                       "max": round(float(vals.max()), 4), "min": round(float(vals.min()), 4),
                       "seconds_above_half": [round(float(on_.min()), 3), round(float(on_.max()), 3)] if len(on_) else None})
        anims.append({"name": a.get("name"), "channels": len(a["channels"]), "weights_channels": wk})
    return {"morph_targets_per_primitive": [len(p.get("targets", [])) for p in prims],
            "morph_target_names": [m.get("extras", {}).get("targetNames") for m in js.get("meshes", [])],
            "default_weights": [m.get("weights") for m in js.get("meshes", [])],
            "animations": anims, "meshes": len(js.get("meshes", [])), "skins": len(js.get("skins", [])),
            "joints": len(js["skins"][0]["joints"]) if js.get("skins") else 0}


if TAG is None:
    for o in scene.objects:
        o.select_set(o is rig or o is low)
    bpy.context.view_layer.objects.active = rig
    K.assign_action(rig, NEW_ACTS["idle"])
    # the shape-key channel exports per clip only from the Key's NLA (magmoo's probe, 2026-09-26, Blender 5.0 ACTIONS mode:
    # a KEY slot that is not bound or stacked is skipped): one strip per clip on the Key, for the export only
    kad = KEYB.animation_data or KEYB.animation_data_create()
    kad.action = None
    for cn in CLIPS:
        act = NEW_ACTS[cn]
        tr = kad.nla_tracks.new(); tr.name = act.name
        st = tr.strips.new(act.name, 1, act)
        st.action_slot = next(s_ for s_ in act.slots if s_.target_id_type == "KEY")
    t = time.time()
    bpy.ops.export_scene.gltf(filepath=OUT_GLB, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
                              export_animations=True, export_animation_mode="ACTIONS", export_materials="EXPORT",
                              export_skins=True, export_def_bones=False, export_morph=True, export_morph_animation=True)
    for tr in list(kad.nla_tracks):
        kad.nla_tracks.remove(tr)
    car = glb_carries(OUT_GLB)
    rep["glb"] = {"path": OUT_GLB, "bytes": os.path.getsize(OUT_GLB), "seconds": round(time.time() - t, 1),
                  "sha256_16": hashlib.sha256(open(OUT_GLB, "rb").read()).hexdigest()[:16], "carries": car,
                  "structure": "armature object identity (no scale), mesh child identity with 1 morph target (smirk), natural "
                               "scale; game model_scale %.5f reaches the regular cell (report-only)" % k_fit}
    anim_w = {a["name"]: a["weights_channels"] for a in car["animations"]}
    rep["glb"]["morph_gate"] = {
        "targets": car["morph_target_names"], "animations": sorted(anim_w),
        "every_clip_has_weights": all(anim_w.get(cn) for cn in CLIPS),
        "keyed_weight_range": {cn: [min(w_["min"] for w_ in anim_w.get(cn, [])), max(w_["max"] for w_ in anim_w.get(cn, []))]
                               for cn in CLIPS if anim_w.get(cn)},
        "pass": car["morph_targets_per_primitive"] == [1] and car["morph_target_names"] == [[SMIRK_KEY]] and
        all(anim_w.get(cn) for cn in CLIPS) and len(car["animations"]) == len(CLIPS) and
        max(w_["max"] for w_ in anim_w.get("float", [{"max": 0.0}])) > 0.999 and
        all(max(w_["max"] for w_ in anim_w[cn]) < 1e-6 for cn in CLIPS if cn != "float" and anim_w.get(cn) and
            {"idle": IDLE_SMIRK_T, "walk": WALK_SMIRK_T}[cn] is None)}
    print("GLB", json.dumps(rep["glb"]["morph_gate"]))
    rig.animation_data.action = None
else:
    rep["glb"] = {"skipped": "tagged alternative build (%s): renders only, no glb" % TAG}
rep["improved_report"] = OUT_IMPROVED[:-6] + ".json"
rep["seconds"] = round(time.time() - T0, 1)
json.dump(rep, open(OUT_RIGGED[:-6] + ".json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: rep[k] for k in ("bone_count", "weights", "motion_hierarchy", "digest", "seconds")}, default=str))
sys.stdout.flush()
os._exit(0)
