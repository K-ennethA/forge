"""Conquest unit rig + idle/walk clip wave (artist 2026-09-25: "no animations for attackes, lets
try to do idle animations and walking animations"). Attacks / deaths are DEFERRED.

    blender --background <improved/X.blend> --factory-startup --python rig_unit.py -- <unit> <out.blend> <out.json>

Opens an improved blend, never saves over it (save_as_mainfile copy=True to rigged/).
Per unit:
  1. minimal archetype armature: 'root' at the origin (never animated -- clips are IN PLACE,
     Conquest glides the model between tiles) + the bones the artist's gait needs. Every limb
     that PLANTS is a 2-bone chain: a 1-bone limb's tip rides an arc (rise = L(1-cos a)) and
     cannot hold a contact on the floor while the belt carries it back.
  2. weights by geometric proximity bands: every vertex gets a part label (nearest part
     polyline minus that part's radius, or a region rule), each bone is gated to its parts,
     weight = 1/d^4 to the bone segment, 3 neighbour-smoothing passes, top-3 influences,
     detached islands (petalfang thorns) copy their nearest body vertex.
  3. clips 'idle' and 'walk' (Conquest UnitAnimator CLIP_IDLE / CLIP_WALK, exact-name pass of
     _find_clip), 24 fps, every frame keyed, last frame == first (seam-closed), action
     use_frame_range + use_cyclic. Planted limbs are solved by analytic 2-bone IK every frame
     (knee in the rest bend plane) and keyed as plain FK -- no constraints ship.
  4. measures: per-contact slide against the belt, seam residual on the evaluated mesh, bone
     stretch, IK reach, clip extents; forge rigcheck.animation_check (in_place) on the walk.
"""
import bpy, sys, os, json, math, time, hashlib
import numpy as np
from mathutils import Vector, Matrix, Euler, Quaternion
from mathutils.kdtree import KDTree

T0 = time.time()
argv = sys.argv[sys.argv.index("--") + 1:]
UNIT, OUT_BLEND, OUT_JSON = argv[0], argv[1], argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "..", "addon")))
import rigkit as K  # noqa: E402

TAU = 2.0 * math.pi
FPS = K.FPS


def mx(p):
    return (-p[0], p[1], p[2]) if len(p) == 3 else (-p[0], p[1])


def mpoly(pts):
    return [mx(p) for p in pts]


def sn(t, k=1.0, ph=0.0):
    return math.sin(TAU * k * t + ph)


def cs(t, k=1.0, ph=0.0):
    return math.cos(TAU * k * t + ph)


def smooth01(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def pulse(t, c, w=0.25):
    """Periodic one-hump pulse starting at phase c, width w (0 elsewhere)."""
    d = (t - c) % 1.0
    return math.sin(math.pi * d / w) if d < w else 0.0


def bump(t, c, w):
    """Periodic smooth bump centred on c, half-width w."""
    d = abs(((t - c + 0.5) % 1.0) - 0.5)
    return math.cos(0.5 * math.pi * d / w) ** 2 if d < w else 0.0


# =========================================================================== unit table
# Landmarks (game units, front = -Y, after improve_unit.py) come from the deterministic limb
# traces (rigkit.trace_limb) and slab-component centroids measured on improved/<unit>.blend;
# the trace poly-points are quoted beside each part. Contact points are re-measured at run time
# (lowest verts near the quoted xy, or the farthest vertex of a part for an airborne tip).
SPEC = {}

# --- Barkling: 4 root legs in a plus (+-X sides, front, back), arms held in a ready carry.
_bl_L = [(0.12, -0.035, 0.24), (0.42, -0.035, 0.17), (0.44, -0.035, 0.01)]   # trace r(0.44,-0.035,z.03): 0.50/.12 -> .44/.08 -> .27/.17
_bl_F = [(0.0, -0.10, 0.24), (0.0, -0.37, 0.12), (0.0, -0.317, 0.0)]        # trace r(0,-0.36): (0,-.375,.10) (0,-.306,.055) (0,-.25,.13)
_bl_B = [(0.0, 0.10, 0.24), (0.0, 0.40, 0.14), (0.0, 0.359, 0.0)]           # trace r(0,0.38): (0,.40,.137) (0,.344,.108) (0,.16,.21)
_bl_A = [(0.30, -0.15, 0.92), (0.73, -0.22, 0.85), (0.92, -0.14, 1.01)]     # trace r(0.9,-0.14,z.9): .923/1.01 -> .725/.85 merged
SPEC["barkling"] = dict(
    parts={"core": ([(0, -0.03, 0.12), (0, -0.06, 1.39)], 0.20),
           "leg.L": (_bl_L, 0.06), "leg.R": (mpoly(_bl_L), 0.06), "leg.F": (_bl_F, 0.06), "leg.B": (_bl_B, 0.06),
           "arm.L": (_bl_A, 0.07), "arm.R": (mpoly(_bl_A), 0.07)},
    body=[("trunk", (0, -0.03, 0.22), (0, -0.06, 1.25), "root", None),
          ("arm.L", _bl_A[0], _bl_A[2], "trunk", ["arm.L"]),
          ("arm.R", mx(_bl_A[0]), mx(_bl_A[2]), "trunk", ["arm.R"])],
    chains=[("thigh.L", "shin.L", _bl_L[0], _bl_L[1], ("ground", _bl_L[2][:2]), "trunk", ["leg.L"], None),
            ("thigh.R", "shin.R", mx(_bl_L[0]), mx(_bl_L[1]), ("ground", mx(_bl_L[2])[:2]), "trunk", ["leg.R"], None),
            ("thigh.F", "shin.F", _bl_F[0], _bl_F[1], ("ground", _bl_F[2][:2]), "trunk", ["leg.F"], None),
            ("thigh.B", "shin.B", _bl_B[0], _bl_B[1], ("ground", _bl_B[2][:2]), "trunk", ["leg.B"], None)],
    walk=dict(N=16, duty=0.6, S=0.16, lift=0.07,
              offsets={"thigh.F": 0.0, "thigh.B": 0.0, "thigh.L": 0.5, "thigh.R": 0.5}),
    idle=dict(N=48),
)

# --- Blightcap: biped strider on two short root legs; cap = its own bone, small arm nubs.
_bc_A = [(0.55, 0.02, 0.64), (0.77, 0.02, 0.52)]                            # trace r(0.75,0,z.52): .767/.523 -> .602/.623 merged


def _bc_labels(W):
    r = np.hypot(W[:, 0], W[:, 1] - 0.05)
    lab = np.full(len(W), "core", dtype=object)
    cap = ((W[:, 2] > 0.91) & (r > 0.38)) | (W[:, 2] > 1.13)                # improve_unit cap bands (h>0.58 & r>0.4 rcap | h>0.72), H 1.569
    lab[cap] = "cap"
    # leg nubs merge into the body at z 0.15-0.20 (trace z(0.23,0.1)); the thigh also carries
    # the body's lower flank on its own side (|x| > 0.10 up to z 0.27) so the stride reads --
    # with the nubs alone the 0.26 m stride was near-invisible on the preview sheet.
    legs = (W[:, 2] < 0.20) | ((W[:, 2] < 0.27) & (np.abs(W[:, 0]) > 0.10))
    lab[legs & (W[:, 0] > 0)] = "leg.L"; lab[legs & (W[:, 0] <= 0)] = "leg.R"
    arms = (r > 0.45) & (W[:, 2] > 0.35) & (W[:, 2] < 0.85) & ~cap
    lab[arms & (W[:, 0] > 0)] = "arm.L"; lab[arms & (W[:, 0] <= 0)] = "arm.R"
    return lab


SPEC["blightcap"] = dict(
    labeler=_bc_labels,
    body=[("stalk", (0, 0.06, 0.28), (0, 0.05, 0.90), "root", ("not", ["cap"])),
          ("cap", (0, 0.05, 0.90), (0, -0.25, 1.50), "stalk", ["cap"]),
          ("arm.L", _bc_A[0], _bc_A[1], "stalk", ["arm.L"]),
          ("arm.R", mx(_bc_A[0]), mx(_bc_A[1]), "stalk", ["arm.R"])],
    chains=[("thigh.L", "shin.L", (0.20, 0.08, 0.30), (0.22, 0.00, 0.17), ("ground", (0.235, 0.10)), "stalk", ["leg.L"], None),
            ("thigh.R", "shin.R", (-0.20, 0.08, 0.30), (-0.22, 0.00, 0.17), ("ground", (-0.235, 0.10)), "stalk", ["leg.R"], None)],
    walk=dict(N=12, duty=0.5, S=0.26, lift=0.12, offsets={"thigh.L": 0.0, "thigh.R": 0.5}),
    idle=dict(N=48),
)

# --- Petalfang: bulb + serpent head column + 7 tendrils; the two FRONT tendrils pull.
_pf_F = [(0.197, -0.244, 0.254), (0.268, -0.417, 0.43), (0.364, -0.561, 0.613), (0.524, -0.658, 0.586)]              # trace r(0.47,-0.62)
_pf_FS = [(0.205, -0.241, 0.258), (0.32, -0.215, 0.319), (0.497, -0.252, 0.493), (0.665, -0.291, 0.628), (0.91, -0.291, 0.719)]  # trace r(0.84,-0.29)
_pf_S = [(0.296, 0.016, 0.241), (0.401, 0.064, 0.317), (0.518, 0.046, 0.431), (0.635, 0.042, 0.607), (0.691, 0.049, 0.713)]      # trace r(0.69,0.05)
_pf_B = [(0.002, 0.176, 0.17), (0.0, 0.294, 0.244), (0.0, 0.41, 0.358), (0.0, 0.522, 0.576), (0.001, 0.637, 0.634)]              # trace r(0,0.6)
_pf_H = [(0.0, -0.09, 0.24), (0.0, -0.14, 0.42), (0.0, -0.10, 0.60)]       # head-column centroids per z band (0.24..0.60)
SPEC["petalfang"] = dict(
    parts={"core": ([(0, -0.06, 0.0), (0, -0.06, 0.20)], 0.20), "head": (_pf_H, 0.09),
           "tF.L": (_pf_F, 0.05), "tF.R": (mpoly(_pf_F), 0.05), "tFS.L": (_pf_FS, 0.05), "tFS.R": (mpoly(_pf_FS), 0.05),
           "tS.L": (_pf_S, 0.05), "tS.R": (mpoly(_pf_S), 0.05), "tB": (_pf_B, 0.05)},
    body=[("bulb", (0, -0.06, 0.02), (0, -0.06, 0.24), "root", None),
          ("neck", (0, -0.09, 0.24), (0, -0.11, 0.60), "bulb", ["head"]),
          ("tendril.FS.L", _pf_FS[0], _pf_FS[-1], "bulb", ["tFS.L"]),
          ("tendril.FS.R", mx(_pf_FS[0]), mx(_pf_FS[-1]), "bulb", ["tFS.R"]),
          ("tendril.S.L", _pf_S[0], _pf_S[-1], "bulb", ["tS.L"]),
          ("tendril.S.R", mx(_pf_S[0]), mx(_pf_S[-1]), "bulb", ["tS.R"]),
          ("tendril.B", _pf_B[0], _pf_B[-1], "bulb", ["tB"])],
    chains=[("pull1.L", "pull2.L", (0.20, -0.28, 0.27), (0.33, -0.53, 0.60), ("tip", "tF.L"), "bulb", ["tF.L"], (0.3, 0.2, 1.0)),
            ("pull1.R", "pull2.R", (-0.20, -0.28, 0.27), (-0.33, -0.53, 0.60), ("tip", "tF.R"), "bulb", ["tF.R"], (-0.3, 0.2, 1.0))],
    walk=dict(N=40, duty=0.5, S=0.28, lift=0.20, offsets={"pull1.L": 0.0, "pull1.R": 0.5},
              # tip-bone height on the floor: 0.08 -- at 0.02 the curled tendril tip sank 57 mm
              # below the floor (walk min_z -0.0572, measured)
              ground={"pull1.L": (0.40, -0.62, 0.08), "pull1.R": (-0.40, -0.62, 0.08)}),
    idle=dict(N=72),
)

# --- Mycothrall (after the 2026-09-25 180 yaw): cup-shelled crawler, 4 contact lobes, spine tail.
_my_T = [(0.0, 0.401, 0.238), (0.0, 0.572, 0.112), (0.0, 0.743, 0.089), (0.0, 0.925, 0.065)]   # trace r(0,0.94)
_my_F = [(0.05, -0.66, 0.18), (0.10, -0.85, 0.20), (0.16, -0.93, 0.0)]     # trace r(0.15,-0.93): .146/-.931/.064 -> .01/-.868/.12 -> merged -0.74
_my_M = [(0.24, -0.26, 0.16), (0.55, -0.42, 0.16), (0.65, -0.39, 0.0)]     # trace r(0.66,-0.44): .663/-.44/.052 -> .594/-.433/.111 merged
SPEC["mycothrall"] = dict(
    parts={"core": ([(0, 0.35, 0.2), (0, -0.55, 0.2)], 0.35), "tail": (_my_T, 0.07),
           "lobeF.L": (_my_F, 0.06), "lobeF.R": (mpoly(_my_F), 0.06),
           "lobeM.L": (_my_M, 0.07), "lobeM.R": (mpoly(_my_M), 0.07)},
    # body split in two (rear + fore) so the crawl can carry a travelling ripple front->back
    body=[("body", (0, 0.30, 0.15), (0, -0.05, 0.20), "root", None),
          ("fore", (0, -0.05, 0.20), (0, -0.45, 0.22), "body", None),
          ("tail.1", (0, 0.38, 0.20), (0, 0.66, 0.10), "body", ["tail"]),
          ("tail.2", (0, 0.66, 0.10), (0, 0.93, 0.065), "tail.1", ["tail"])],
    chains=[("lobeF1.L", "lobeF2.L", _my_F[0], _my_F[1], ("ground", _my_F[2][:2]), "fore", ["lobeF.L"], None),
            ("lobeF1.R", "lobeF2.R", mx(_my_F[0]), mx(_my_F[1]), ("ground", mx(_my_F[2])[:2]), "fore", ["lobeF.R"], None),
            ("lobeM1.L", "lobeM2.L", _my_M[0], _my_M[1], ("ground", _my_M[2][:2]), "fore", ["lobeM.L"], None),
            ("lobeM1.R", "lobeM2.R", mx(_my_M[0]), mx(_my_M[1]), ("ground", mx(_my_M[2])[:2]), "fore", ["lobeM.R"], None)],
    walk=dict(N=24, duty=0.75, S=0.12, lift=0.06,
              offsets={"lobeF1.L": 0.0, "lobeM1.R": 0.25, "lobeF1.R": 0.5, "lobeM1.L": 0.75}),
    idle=dict(N=64),
)

# --- Eldroot (boss): stump body on two front root-legs + two knuckle arms that reach the floor.
_el_L = [(0.40, -0.78, 0.70), (0.60, -1.00, 0.42), (0.61, -0.84, 0.0)]     # trace z(0.61,-0.84): .608/.056 .646/.17 .595/.28 .581/.40 merged .37/.52
_el_A = [(0.95, -0.15, 0.95), (1.42, -0.35, 0.87), (1.39, -0.37, 0.0)]     # trace z(1.39,-0.37) up to .88; r(1.47,-.36,z.97): 1.78/.94 .. 1.02/.87 merged
SPEC["eldroot"] = dict(
    parts={"core": ([(0, -0.2, 0.2), (0, -0.1, 2.7)], 0.55),
           "leg.L": (_el_L, 0.13), "leg.R": (mpoly(_el_L), 0.13), "arm.L": (_el_A, 0.16), "arm.R": (mpoly(_el_A), 0.16)},
    body=[("pelvis", (0, -0.25, 0.35), (0, -0.15, 1.25), "root", None),
          ("chest", (0, -0.15, 1.25), (0, -0.10, 2.25), "pelvis", ("not", ["leg.L", "leg.R", "arm.L", "arm.R"])),
          ("crown", (0, -0.10, 2.25), (0, -0.12, 2.71), "chest", ("not", ["leg.L", "leg.R", "arm.L", "arm.R"]))],
    chains=[("thigh.L", "shin.L", _el_L[0], _el_L[1], ("ground", _el_L[2][:2]), "pelvis", ["leg.L"], None),
            ("thigh.R", "shin.R", mx(_el_L[0]), mx(_el_L[1]), ("ground", mx(_el_L[2])[:2]), "pelvis", ["leg.R"], None),
            ("upperarm.L", "forearm.L", _el_A[0], _el_A[1], ("ground", _el_A[2][:2]), "pelvis", ["arm.L"], None),
            ("upperarm.R", "forearm.R", mx(_el_A[0]), mx(_el_A[1]), ("ground", mx(_el_A[2])[:2]), "pelvis", ["arm.R"], None)],
    # two-beat lumber: L leg + R arm swing in [0,.25), pause (double support) [.25,.5),
    # R leg + L arm swing in [.5,.75), pause [.75,1). phase = (t + offset) % 1, swing = [duty,1).
    walk=dict(N=64, duty=0.75, S=0.28, lift=0.12, lift_arm=0.10, stance_dy=0.05,
              offsets={"thigh.L": 0.75, "upperarm.R": 0.75, "thigh.R": 0.25, "upperarm.L": 0.25}),
    idle=dict(N=96),
)
U = SPEC[UNIT]


# =========================================================================== motion (per unit)
def carry_arms(fk, breath=0.0):
    fk["arm.L"] = {"rot": (0.0, 25.0 + breath, -20.0)}
    fk["arm.R"] = {"rot": (0.0, -25.0 - breath, 20.0)}


def motion(clip, t, ctx):
    """-> (fk, ik): fk = {bone: {"rot": world XYZ euler deg about the bone head, "loc": world
    offset, "aa": [(axis, deg), ...]}}, ik = {upper bone: world target of the lower bone tail}."""
    fk, ik = {}, {}
    if UNIT == "barkling":
        if clip == "walk":
            fk["trunk"] = {"rot": (5.0 + 1.5 * sn(t, 2, 0.5), 3.0 * sn(t), 2.5 * sn(t, 1, 0.8)),
                           "loc": (0, 0, -0.010 - 0.009 * cs(t, 2))}
            carry_arms(fk)
        else:
            fk["trunk"] = {"rot": (1.0 * sn(t, 2), 1.5 * sn(t), 1.0 * sn(t, 1, 1.3)), "loc": (0, 0, 0.006 * sn(t) - 0.003)}
            carry_arms(fk, 1.5 * sn(t, 1, 0.6))
    elif UNIT == "blightcap":
        if clip == "walk":
            fk["stalk"] = {"rot": (9.0 + 2.5 * sn(t, 2, 0.6), 4.0 * sn(t), 8.0 * cs(t)),
                           "loc": (0, 0, -0.030 - 0.022 * cs(t, 2))}
            fk["cap"] = {"rot": (3.0 * sn(t, 2, -1.0), -2.0 * sn(t, 1, -0.6), 0.0)}
            fk["arm.L"] = {"rot": (0.0, 10.0, 18.0 * cs(t))}
            fk["arm.R"] = {"rot": (0.0, -10.0, 18.0 * cs(t))}
        else:
            fk["stalk"] = {"rot": (1.2 * sn(t, 1, 0.4), 1.5 * sn(t), 0.0), "loc": (0, 0, 0.008 * sn(t))}
            fk["cap"] = {"rot": (2.5 * sn(t, 1, -0.9), -2.0 * sn(t, 1, -1.1), 1.5 * sn(t, 2))}
            fk["arm.L"] = {"rot": (0.0, 3.0 * sn(t, 1, 0.5), 0.0)}
            fk["arm.R"] = {"rot": (0.0, -3.0 * sn(t, 1, 0.5), 0.0)}
    elif UNIT == "petalfang":
        tang = ctx["tangent"]
        if clip == "walk":
            fk["bulb"] = {"rot": (3.0 + 2.0 * cs(t, 2), 3.0 * sn(t), 6.0 * sn(t)), "loc": (0, 0, -0.005 * cs(t, 2))}
            fk["neck"] = {"rot": (-3.0 - 2.0 * cs(t, 2), 0.0, -8.0 * sn(t, 1, -1.1))}
            for b in ("tendril.FS.L", "tendril.FS.R", "tendril.S.L", "tendril.S.R"):
                fk[b] = {"aa": [(tang[b], 6.0 + 3.0 * sn(t, 2, ctx["az"][b])), ((0, 0, 1), 5.0 * sn(t, 1, -1.2 + ctx["az"][b]))]}
            fk["tendril.B"] = {"aa": [(tang["tendril.B"], 8.0), ((0, 0, 1), 10.0 * sn(t, 1, -1.6))]}
        else:
            fk["bulb"] = {"rot": (2.5 * sn(t), 2.5 * cs(t), 0.0)}
            fk["neck"] = {"rot": (3.0 * sn(t, 2), 2.0 * sn(t), 7.0 * sn(t, 1, 0.8))}
            for b in ("tendril.FS.L", "tendril.FS.R", "tendril.S.L", "tendril.S.R", "tendril.B", "pull1.L", "pull1.R"):
                fk[b] = {"aa": [(tang[b], 6.0 * sn(t, 1, ctx["az"][b]))]}
            for b, up in (("pull2.L", "pull1.L"), ("pull2.R", "pull1.R")):
                fk[b] = {"aa": [(tang[up], 8.0 * sn(t, 1, ctx["az"][up] - 0.7))]}
    elif UNIT == "mycothrall":
        if clip == "walk":
            fk["body"] = {"rot": (2.0 * sn(t, 2), 2.5 * sn(t, 1, 0.5), 1.5 * sn(t)), "loc": (0, 0, 0.006 * cs(t, 4))}
            fk["fore"] = {"rot": (2.0 * sn(t, 2, -1.6), 0.0, -2.0 * sn(t, 1, -0.8))}
            fk["tail.1"] = {"rot": (3.0, 0.0, 9.0 * sn(t, 1, -math.pi / 2))}
            fk["tail.2"] = {"rot": (2.0, 0.0, 13.0 * sn(t, 1, -math.pi))}
        else:
            fk["body"] = {"rot": (1.2 * sn(t, 1, 1.7) + 0.6 * sn(t, 5), 1.5 * sn(t, 2, 0.3), 1.0 * sn(t, 3)),
                          "loc": (0, 0, 0.005 * sn(t) + 0.0025 * sn(t, 3, 1.0))}
            fk["fore"] = {"rot": (1.5 * sn(t, 2, -1.2) + 0.8 * sn(t, 5, 0.7), 1.0 * sn(t, 3, 0.2), 0.0)}
            fk["tail.1"] = {"rot": (6.0 + 4.0 * sn(t, 2, 1.0), 0.0, 12.0 * sn(t) + 5.0 * sn(t, 3, 0.4))}
            fk["tail.2"] = {"rot": (4.0 + 3.0 * sn(t, 2, 0.2), 0.0, 10.0 * sn(t, 1, -0.9) + 6.0 * sn(t, 4))}
            for up, c in (("lobeF1.L", 0.12), ("lobeM1.R", 0.38), ("lobeF1.R", 0.57), ("lobeM1.L", 0.81)):
                F0 = ctx["F0"][up]
                b = bump(t, c, 0.07)
                inward = -np.array([F0[0], F0[1] + 0.2, 0.0]); inward /= max(np.linalg.norm(inward), 1e-9)
                ik[up] = tuple(np.array(F0) + np.array([0, 0, 0.025 * b]) + inward * 0.015 * b)
    elif UNIT == "eldroot":
        def lean(tt):
            tt %= 1.0
            if tt < 0.25:
                return -4.5
            if tt < 0.5:
                return -4.5 + 9.0 * smooth01((tt - 0.25) / 0.25)
            if tt < 0.75:
                return 4.5
            return 4.5 - 9.0 * smooth01((tt - 0.75) / 0.25)
        if clip == "walk":
            dip = pulse(t, 0.25) + pulse(t, 0.75)
            effort = pulse(t, 0.0) + pulse(t, 0.5)
            fk["pelvis"] = {"rot": (1.5 + 1.5 * dip, 0.78 * lean(t), (2.5 / 4.5) * lean(t)),
                            "loc": (0, 0, 0.045 - 0.025 * dip + 0.012 * effort)}
            fk["chest"] = {"rot": (1.5 * pulse(t, 0.29) + 1.5 * pulse(t, 0.79), 0.5 * lean(t - 0.06), -0.4 * (2.5 / 4.5) * lean(t))}
            fk["crown"] = {"rot": (2.0 * pulse(t, 0.35) + 2.0 * pulse(t, 0.85), 0.6 * lean(t - 0.12), 0.0)}
        else:
            fk["pelvis"] = {"rot": (0.6 * sn(t, 2), 1.2 * sn(t), 0.5 * sn(t, 1, 0.9)), "loc": (0, 0, 0.010 + 0.006 * sn(t))}
            fk["chest"] = {"rot": (1.0 * sn(t, 2, -0.5), 1.5 * sn(t, 1, -0.5), 1.0 * sn(t, 1, -0.3))}
            fk["crown"] = {"rot": (1.5 * sn(t, 1, -1.4), 3.0 * sn(t, 1, -1.2), 2.0 * sn(t, 1, -0.4))}
    # locomotion targets: every chain named in the walk offsets steps; idle keeps contacts planted
    if clip == "walk":
        wk = U["walk"]
        for up, off in wk["offsets"].items():
            F0 = np.array(wk.get("ground", {}).get(up, ctx["F0"][up]), float)
            F0 = F0 + np.array([0.0, wk.get("stance_dy", 0.0) if up.startswith("thigh") else 0.0, 0.0])
            lift = wk.get("lift_arm", wk["lift"]) if up.startswith("upperarm") else wk["lift"]
            ik[up] = gait_target(F0, (t + off) % 1.0 if UNIT == "eldroot" else (t - off) % 1.0, wk["duty"], wk["S"], lift)
    else:
        for ch in U["chains"]:
            if ch[0] not in ik and ch[0] not in fk and ch[4][0] == "ground":
                ik[ch[0]] = tuple(ctx["F0"][ch[0]])
    return fk, ik


def gait_target(F0, phase, duty, S, lift):
    """In-place foot path: stance [0,duty) = linear belt from front (-Y) to back (+Y) on the
    floor; swing = cubic Hermite return whose end tangents MATCH the belt velocity (the foot
    leaves and meets the floor moving with it -- no skate at lift-off / touch-down, measured
    7.8 mm by animation_check on a cosine-eased swing), with a sine lift."""
    if phase < duty:
        u = phase / duty
        return (F0[0], F0[1] - S / 2 + S * u, F0[2])
    u = (phase - duty) / (1.0 - duty)
    m = S * (1.0 - duty) / duty                       # belt displacement over the swing time
    h00, h10 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u
    h01, h11 = -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
    y = h00 * (S / 2) + h10 * m + h01 * (-S / 2) + h11 * m
    return (F0[0], F0[1] + y, F0[2] + lift * math.sin(math.pi * u))


# =========================================================================== 1. rig
scene = bpy.context.scene
scene.render.fps = FPS
scene.render.fps_base = 1.0
low = bpy.data.objects[UNIT]
M = K.MeshData(low)
W = M.W
report = {"unit": UNIT, "source": bpy.data.filepath, "fps": FPS, "verts": M.n}

# part labels
if "labeler" in U:
    labels = U["labeler"](W)
else:
    names = list(U["parts"].keys())
    D = np.stack([K.poly_dist(W, U["parts"][n][0]) - U["parts"][n][1] for n in names])
    labels = np.array(names, dtype=object)[np.argmin(D, axis=0)]
report["part_label_counts"] = {str(k): int(v) for k, v in zip(*np.unique(labels.astype(str), return_counts=True))}


def ground_foot(xy):
    rf = 0.06 * M.maxdim
    m = M.main & (np.hypot(W[:, 0] - xy[0], W[:, 1] - xy[1]) < rf)
    z0 = W[m, 2].min()
    sel = m & (W[:, 2] < z0 + 0.004 * M.maxdim)
    c = W[sel].mean(0)
    return np.array([c[0], c[1], z0])


chain_info = {}
bones_spec = []   # (name, head, tail, parent, gate, connect)
for (up, lo_, hip, knee, foot, parent, gate, pole) in U["chains"]:
    if foot[0] == "ground":
        F = ground_foot(foot[1])
    else:
        sel = M.main & (labels == foot[1])
        dd = np.linalg.norm(W[sel] - np.array(hip), axis=1)
        F = W[sel][int(np.argmax(dd))]
    chain_info[up] = {"lower": lo_, "hip": np.array(hip, float), "knee": np.array(knee, float), "foot": F,
                      "pole": pole, "parent": parent, "kind": foot[0]}
    bones_spec.append((up, hip, knee, parent, gate, False))
    bones_spec.append((lo_, knee, tuple(F), up, gate, True))

arm_data = bpy.data.armatures.new(UNIT + "_rig")
rig = bpy.data.objects.new(UNIT + "_rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
for o in scene.objects:
    o.select_set(o is rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = arm_data.edit_bones.new("root")
eb.head = (0, 0, 0); eb.tail = (0, 0, 0.15 * M.H); eb.use_deform = False
all_bones = [(n, h, t, p, g, False) for (n, h, t, p, g) in U["body"]] + bones_spec
for (n, h, t, p, g, conn) in all_bones:
    e = arm_data.edit_bones.new(n)
    e.head = Vector(h); e.tail = Vector(t)
    e.parent = arm_data.edit_bones[p]
    e.use_connect = conn
    e.use_deform = True
bpy.ops.object.mode_set(mode="OBJECT")
GATES = {n: g for (n, h, t, p, g, c) in all_bones}
DEFORM = [n for (n, h, t, p, g, c) in all_bones]

# =========================================================================== 2. weights
nb = len(DEFORM)
Wt = np.zeros((M.n, nb))
lab_s = labels.astype(str)
for j, n in enumerate(DEFORM):
    b = arm_data.bones[n]
    d = np.maximum(K.seg_dist(W, np.array(b.head_local), np.array(b.tail_local)), 0.003)
    w = 1.0 / d ** 4
    g = GATES[n]
    if g is None:
        mask = np.ones(M.n, bool)
    elif isinstance(g, tuple) and g[0] == "not":
        mask = ~np.isin(lab_s, g[1])
    else:
        mask = np.isin(lab_s, g)
    Wt[:, j] = w * mask
rs = Wt.sum(1)
report["weights_gate_fallback_verts"] = int((rs <= 0).sum())
Wt /= np.maximum(rs, 1e-30)[:, None]
for _ in range(3):
    Wt = 0.5 * Wt + 0.5 * M.neighbour_mean(Wt)
# top-3 influences
order = np.argsort(-Wt, axis=1)
keep = np.zeros_like(Wt, bool)
np.put_along_axis(keep, order[:, :3], True, axis=1)
Wt = np.where(keep, Wt, 0.0)
Wt /= np.maximum(Wt.sum(1), 1e-30)[:, None]
# detached islands (petalfang thorns): copy the nearest main-mesh vertex
det = ~M.main
if det.any():
    kd = KDTree(int(M.main.sum()))
    mi = np.nonzero(M.main)[0]
    for k_, i in enumerate(mi):
        kd.insert(W[i], k_)
    kd.balance()
    for isl in np.unique(M.island[det]):
        vs = np.nonzero(M.island == isl)[0]
        c = W[vs].mean(0)
        base = vs[int(np.argmin([kd.find(W[v])[2] for v in vs]))]
        _, k_, _ = kd.find(W[base])
        Wt[vs] = Wt[mi[k_]]
    report["detached_islands_rigid"] = int(len(np.unique(M.island[det])))
low.vertex_groups.clear()
for j, n in enumerate(DEFORM):
    vg = low.vertex_groups.new(name=n)
    nz = np.nonzero(Wt[:, j] > 1e-4)[0]
    for i in nz:
        vg.add([int(i)], float(Wt[i, j]), "REPLACE")
report["weights"] = {n: {"verts_any": int((Wt[:, j] > 1e-4).sum()), "verts_dominant": int((np.argmax(Wt, 1) == j).sum())}
                     for j, n in enumerate(DEFORM)}
report["weighting"] = ("proximity bands: part label = argmin(polyline distance - part radius)"
                       + (" / region rule" if "labeler" in U else "") +
                       "; bone gated to its parts; w = 1/d^4 to the bone segment; 3 neighbour-mean passes; top-3; "
                       "detached islands rigid to their nearest body vertex")
low.parent = rig
low.matrix_parent_inverse = Matrix.Identity(4)
mod = low.modifiers.new("Armature", "ARMATURE")
mod.object = rig

# =========================================================================== 3. clips
pose = rig.pose.bones
for pb in pose:
    pb.rotation_mode = "QUATERNION"
depth = {}
for b in arm_data.bones:
    d_ = 0; p = b.parent
    while p:
        d_ += 1; p = p.parent
    depth[b.name] = d_
FK_ORDER = sorted([b.name for b in arm_data.bones], key=lambda n: depth[n])

ctx = {"F0": {up: tuple(ci["foot"]) for up, ci in chain_info.items()}}
if UNIT == "petalfang":
    ctx["tangent"], ctx["az"] = {}, {}
    for n in ("tendril.FS.L", "tendril.FS.R", "tendril.S.L", "tendril.S.R", "tendril.B", "pull1.L", "pull1.R"):
        b = arm_data.bones[n]
        tip = np.array(b.tail_local if not n.startswith("pull") else chain_info[n]["foot"])
        rh = np.array([tip[0], tip[1], 0.0]); rh /= np.linalg.norm(rh)
        ctx["tangent"][n] = tuple(np.cross([0, 0, 1], rh))
        ctx["az"][n] = math.atan2(tip[0], -tip[1])


def rest_pole(up):
    ci = chain_info[up]
    if ci["pole"] is not None:
        return Vector(ci["pole"]).normalized()
    H, Kn, F = ci["hip"], ci["knee"], ci["foot"]
    dv = (F - H) / np.linalg.norm(F - H)
    p = (Kn - H) - dv * ((Kn - H) @ dv)
    return Vector(p / max(np.linalg.norm(p), 1e-9))


REST_POLE = {up: rest_pole(up) for up in chain_info}


def aim(pb, target):
    Mc = pb.matrix.copy()
    head = Mc.translation.copy()
    u0 = Vector(Mc.col[1][:3]).normalized()
    u1 = (Vector(target) - head).normalized()
    q = u0.rotation_difference(u1)
    R = q.to_matrix().to_4x4()
    pb.matrix = Matrix.Translation(head) @ R @ Matrix.Translation(-head) @ Mc


reach_log = {}


def apply_pose(fk, ik):
    for pb in pose:
        pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0); pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    for dl in sorted(set(depth.values())):
        touched = False
        for n in FK_ORDER:
            if depth[n] != dl or n not in fk:
                continue
            e = fk[n]
            pb = pose[n]
            Mc = pb.matrix.copy()
            head = Mc.translation.copy()
            R = Matrix.Identity(4)
            if "rot" in e:
                R = Euler([math.radians(a) for a in e["rot"]], "XYZ").to_matrix().to_4x4()
            for ax, deg in e.get("aa", []):
                R = Matrix.Rotation(math.radians(deg), 4, Vector(ax)) @ R
            loc = Vector(e.get("loc", (0, 0, 0)))
            pb.matrix = Matrix.Translation(head + loc) @ R @ Matrix.Translation(-head) @ Mc
            touched = True
        if touched:
            bpy.context.view_layer.update()
    if not ik:
        return
    knees = {}
    for up, T in ik.items():
        ci = chain_info[up]
        pbU, pbL = pose[up], pose[ci["lower"]]
        H = pbU.head.copy()
        L1, L2 = pbU.bone.length, pbL.bone.length
        T = Vector(T)
        d = T - H
        Dn = d.length
        reach_log.setdefault(up, []).append(Dn / (L1 + L2))
        Dc = min(max(Dn, abs(L1 - L2) + 1e-5), (L1 + L2) * 0.9999)
        dv = d.normalized()
        par = pbU.parent
        Rp = (par.matrix.to_3x3() @ par.bone.matrix_local.to_3x3().inverted())
        pole = Rp @ REST_POLE[up]
        pp = pole - dv * pole.dot(dv)
        pp.normalize()
        a = (L1 * L1 - L2 * L2 + Dc * Dc) / (2 * Dc)
        h = math.sqrt(max(L1 * L1 - a * a, 0.0))
        Kp = H + dv * a + pp * h
        knees[up] = (Kp, H + dv * Dc)
        aim(pbU, Kp)
    bpy.context.view_layer.update()
    for up, (Kp, Tp) in knees.items():
        aim(pose[chain_info[up]["lower"]], Tp)
    bpy.context.view_layer.update()


def key_all(frame, prevq):
    for pb in pose:
        if pb.name == "root":
            continue
        q = pb.rotation_quaternion.copy()
        if pb.name in prevq and prevq[pb.name].dot(q) < 0:
            q.negate(); pb.rotation_quaternion = q
        prevq[pb.name] = q.copy()
        pb.keyframe_insert("rotation_quaternion", frame=frame, group=pb.name)
        pb.keyframe_insert("location", frame=frame, group=pb.name)


clips = {}
for clip in ("idle", "walk"):
    N = U[clip]["N"]
    act = bpy.data.actions.new(clip)
    act.use_fake_user = True
    K.assign_action(rig, act)
    reach_log.clear()
    prevq = {}
    for f in range(1, N + 2):
        t = ((f - 1) / N) % 1.0
        fk, ik = motion(clip, t, ctx)
        apply_pose(fk, ik)
        key_all(f, prevq)
    act.use_frame_range = True
    act.frame_start, act.frame_end = 1, N + 1
    act.use_cyclic = True
    clips[clip] = {"action": act.name, "frames": N + 1, "cycle_frames": N, "cycle_s": round(N / FPS, 4),
                   "reach_max": {k: round(max(v), 4) for k, v in reach_log.items()}}
    if clip == "walk":
        wk = U["walk"]
        v = wk["S"] / (wk["duty"] * N) * FPS
        clips[clip].update({"stride_m": wk["S"], "duty": wk["duty"], "speed_m_per_s": round(v, 4),
                            "belt_m_per_frame": wk["S"] / (wk["duty"] * N)})
    # Mycothrall idle: thread shimmer = the glow emission strength, keyed into the SAME 'idle'
    # action on a node-tree slot (so the clip carries it; integer harmonics -> seam-closed).
    if UNIT == "mycothrall" and clip == "idle":
        mat = low.data.materials[0]
        nt = mat.node_tree
        sock = nt.nodes["Principled BSDF"].inputs["Emission Strength"]
        base = sock.default_value
        try:
            K.assign_action(nt, act)
            for f in range(1, N + 2):
                t = ((f - 1) / N) % 1.0
                sock.default_value = base * (1.0 + 0.45 * sn(t, 3) + 0.25 * sn(t, 7, 1.0))
                sock.keyframe_insert("default_value", frame=f)
            clips[clip]["thread_shimmer"] = {"socket": "Principled BSDF.Emission Strength", "base": base,
                                             "law": "base*(1+0.45 sin(3*2pi t)+0.25 sin(7*2pi t+1))",
                                             "slots": [s.identifier for s in act.slots]}
        except Exception as exc:  # noqa: BLE001
            clips[clip]["thread_shimmer"] = {"error": repr(exc)}
        nt.animation_data.action = None
        sock.default_value = base

# =========================================================================== 4. measure
dg = bpy.context.evaluated_depsgraph_get()


def mesh_coords():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = low.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3); me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    return co.reshape(-1, 3)


def runs_cyclic(flags):
    n = len(flags)
    if all(flags):
        return [list(range(n))]
    start = next(i for i in range(n) if not flags[i])
    runs, cur = [], []
    for k in range(1, n + 1):
        i = (start + k) % n
        if flags[i]:
            cur.append(i)
        elif cur:
            runs.append(cur); cur = []
    if cur:
        runs.append(cur)
    return runs


for clip, info in clips.items():
    act = bpy.data.actions[clip]
    K.assign_action(rig, act)
    N = info["cycle_frames"]
    tips = {up: [] for up in chain_info}
    root_dev = 0.0
    stretch = 0.0
    ext_lo, ext_hi = np.full(3, 1e9), np.full(3, -1e9)
    first = last = None
    for f in range(1, N + 2):
        scene.frame_set(f)
        for up, ci in chain_info.items():
            tips[up].append(np.array(pose[ci["lower"]].tail))
        root_dev = max(root_dev, float(np.linalg.norm(np.array(pose["root"].head))))
        for pb in pose:
            stretch = max(stretch, abs(pb.length - pb.bone.length) / pb.bone.length)
            if pb.bone.use_connect:
                stretch = max(stretch, (pb.head - pb.parent.tail).length / pb.bone.length)
        C = mesh_coords()
        ext_lo = np.minimum(ext_lo, C.min(0)); ext_hi = np.maximum(ext_hi, C.max(0))
        if f == 1:
            first = C
        if f == N + 1:
            last = C
    info["seam_residual_mm"] = round(float(np.linalg.norm(first - last, axis=1).max()) * 1000, 4)
    info["root_max_offset_mm"] = round(root_dev * 1000, 4)
    info["bone_stretch_max_pct"] = round(stretch * 100, 5)
    info["extent"] = {"footprint": round(float(max(ext_hi[0] - ext_lo[0], ext_hi[1] - ext_lo[1])), 4),
                      "width": round(float(ext_hi[0] - ext_lo[0]), 4), "depth": round(float(ext_hi[1] - ext_lo[1]), 4),
                      "height": round(float(ext_hi[2] - ext_lo[2]), 4), "min_z": round(float(ext_lo[2]), 4)}
    belt = info.get("belt_m_per_frame", 0.0)
    slides = {}
    for up, P in tips.items():
        P = np.array(P[:N])
        if chain_info[up]["kind"] != "ground" and clip == "idle":
            continue
        zmin = P[:, 2].min()
        flags = list(P[:, 2] <= zmin + 0.0005)
        worst, nruns = 0.0, 0
        for run in runs_cyclic(flags):
            if len(run) < 2:
                continue
            nruns += 1
            q = [P[i, :2] - np.array([0.0, belt * k]) for k, i in enumerate(run)]
            dia = max(np.linalg.norm(a - b) for a in q for b in q)
            worst = max(worst, dia)
        slides[up] = {"contact": chain_info[up]["lower"] + ":tail", "stance_runs": nruns,
                      "stance_frames": int(sum(flags)), "worst_drift_mm": round(worst * 1000, 4),
                      "ground_z_mm": round(float(zmin) * 1000, 2)}
    info["contact_slide"] = slides

# forge animation_check (in_place walk) -- the mechanical gate where it applies to these rigs
try:
    from forge.tools import rigcheck
    for clip in ("walk", "idle"):
        feet = [chain_info[up]["lower"] + ":tail" for up in chain_info
                if clip == "walk" and up in U["walk"]["offsets"] or clip == "idle" and chain_info[up]["kind"] == "ground"]
        if not feet:
            clips[clip]["forge_animation_check"] = {"skipped": "no planted contacts in this clip"}
            continue
        res = rigcheck.cmd_animation_check({"rig": rig.name, "action": clip,
                                            "mode": "in_place" if clip == "walk" else "planted", "feet": feet})
        seam = res.get("loop_seam_closure") or {}
        clips[clip]["forge_animation_check"] = {
            "mode": res.get("mode"), "gate": res.get("gate"), "deformation_gate": res.get("deformation_gate"),
            "worst_drift_mm": max([f_.get("worst_drift_mm") or 0 for f_ in res.get("feet", [])] or [None]),
            "feet": [{"bone": f_["bone"], "steps": f_["steps_measured"], "worst_drift_mm": f_["worst_drift_mm"],
                      "verdict": f_["verdict"]} for f_ in res.get("feet", [])],
            "loop_seam_closure": {k: seam.get(k) for k in ("verdict", "max_mm", "value_mm", "says") if k in seam},
            "says": res.get("says")}
except Exception as exc:  # noqa: BLE001
    import traceback
    report["forge_animation_check_error"] = traceback.format_exc()[-1500:]

# =========================================================================== finish
rig.animation_data.action = None
for pb in pose:
    pb.location = (0, 0, 0); pb.rotation_quaternion = (1, 0, 0, 0)
scene.frame_set(1)
report["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None, "deform": b.use_deform,
                    "head": [round(v, 4) for v in b.head_local], "tail": [round(v, 4) for v in b.tail_local],
                    "gate": (list(GATES[b.name]) if isinstance(GATES.get(b.name), list) else
                             (["not"] + list(GATES[b.name][1]) if isinstance(GATES.get(b.name), tuple) else None))}
                   for b in arm_data.bones]
report["bone_count"] = len(arm_data.bones)
report["chains"] = {up: {"lower": ci["lower"], "contact": [round(v, 4) for v in ci["foot"]], "kind": ci["kind"],
                         "pole": [round(v, 3) for v in REST_POLE[up]]} for up, ci in chain_info.items()}
report["clips"] = clips
rig["conquest_rig"] = "archetype-minimal v1"
low["conquest_clips"] = list(clips.keys())
report["seconds"] = round(time.time() - T0, 1)
bpy.context.preferences.filepaths.save_version = 0
os.makedirs(os.path.dirname(OUT_BLEND), exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND, copy=True, compress=True)
json.dump(report, open(OUT_JSON, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("RIG_DONE", json.dumps({k: report[k] for k in ("unit", "bone_count", "seconds")}))
