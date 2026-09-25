"""Synthesize the mocap retarget fixtures: deterministic .bvh files, written locally.

Nothing here is downloaded or derived from a downloaded file.  Every number is
authored below, the motion is closed-form (sines and a two-bone IK), and the
text is formatted to four decimals, so the bytes are the same on every run and
every machine.  ``DIGESTS`` pins them (docs/recipes/fixture-freezing.md): the
suite regenerates the files into a temp folder, compares each sha256 with the
pinned one AND with the committed copy next to this script, and fails loudly if
the builder drifted.

    python make_bvh_fixtures.py            # (re)write the committed .bvh files
    python make_bvh_fixtures.py --check    # regenerate in memory, compare digests

The fixtures, and what each one stresses:

``walk_cmu_zup_in120.bvh``
    CMU/ASF joint names (``root``, ``lfemur``, ``ltibia``, ``lhumerus`` ...,
    plus ``lhipjoint``/``upperneck``/``lfingers``/``lthumb`` a mapping must
    refuse or skip), **Z-up** file axes with the character facing **+X**,
    lengths in **inches**, **120 fps**, channel order ``Z Y X``, ROOT OFFSET at
    the origin with the hip height carried in the position channels (the
    CMU/cgspeed habit), T-pose rest.  2.6 s of walk, 1.05 s cycle, stride and
    cadence varying a few percent from cycle to cycle the way a take does.

``walk_mixamo_yup_cm30.bvh``
    Mixamo joint names (``mixamorig:Hips`` ... ``LeftHandIndex1``), the usual
    Y-up / +Z-facing axes, **centimetres**, **30 fps** (a non-integer 1.25
    ratio to 24), channel order ``Z X Y``, ROOT OFFSET at the rest hip height.
    The whole performance is yawed **30 degrees** off the skeleton's rest
    facing - the actor walked diagonally across the volume - so heading has to
    come from the travel, not from the rest pose.

``broken_upper_body.bvh``
    A valid file whose skeleton has no legs at all (root, spine, arms, head).
    A locomotion retarget has to refuse it, naming what is missing.

``broken_nan.bvh``
    The CMU skeleton with ``nan`` and ``inf`` in two motion rows.

``broken_truncated.bvh``
    Declares 30 frames and carries 24, one of them a value short.

The anatomy is authored in an internal right-handed frame (x forward, y up,
z the character's right) in metres, then mapped to each file's axes and units.
:func:`truth` hands the suite the same world-space joint positions the file
was written from, so retarget correctness is measured against the source of
truth rather than against Blender's importer or the code under test.
"""

import hashlib
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: sha256 of each fixture's bytes. Re-pin ONLY when the builder is changed on
#: purpose, and say so in the report that changes it.
DIGESTS = {
"walk_cmu_zup_in120.bvh": "8c37d6b0f6b59aa9b20fc482a0e8a71cc89071ce77554928aa592b7e793b2999",
    "walk_mixamo_yup_cm30.bvh": "638722f4f725f229f3ccbe674b66a0103fafb3f87989215c6ba62499e3e800af",
    "broken_upper_body.bvh": "8866f95e398dd88f7689d78585b4d90eaf3bc0b49de222bc51b6ec63ecb9929d",
    "broken_nan.bvh": "9980b02e6860defecaf83f704311d7700ff4e8c11dfc062cd46df475b7c0b49e",
    "broken_truncated.bvh": "aa142cd75ac93a431569e0b14b15105554f097698380d401a2730c4ab7c7c27e",
}

# ---------------------------------------------------------------------------
# small 3x3 algebra (row-major tuples of tuples; no numpy, no mathutils)
# ---------------------------------------------------------------------------


def mat_mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                 for i in range(3))


def mat_vec(a, v):
    return tuple(sum(a[i][k] * v[k] for k in range(3)) for i in range(3))


def transpose(a):
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def rot_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def rot_y(angle):
    c, s = math.cos(angle), math.sin(angle)
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def rot_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def scale(a, k):
    return tuple(x * k for x in a)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def norm(a):
    length = math.sqrt(dot(a, a))
    return scale(a, 1.0 / length) if length > 1e-12 else a


def frame_from(direction, side):
    """Columns (x, y, z) with y = direction, x = side made perpendicular."""
    y = norm(direction)
    x = norm(sub(side, scale(y, dot(side, y))))
    z = cross(x, y)
    return tuple((x[i], y[i], z[i]) for i in range(3))


def euler_for(m, order):
    """Angles (degrees) in channel order so that M = R1 @ R2 @ R3."""
    if order == "ZYX":  # M = Rz(a) Ry(b) Rx(c)
        b = math.asin(max(-1.0, min(1.0, -m[2][0])))
        a = math.atan2(m[1][0], m[0][0])
        c = math.atan2(m[2][1], m[2][2])
        return [math.degrees(a), math.degrees(b), math.degrees(c)]
    if order == "ZXY":  # M = Rz(a) Rx(b) Ry(c)
        b = math.asin(max(-1.0, min(1.0, m[2][1])))
        c = math.atan2(-m[2][0], m[2][2])
        a = math.atan2(-m[0][1], m[1][1])
        return [math.degrees(a), math.degrees(b), math.degrees(c)]
    raise ValueError(order)


AXIS_ROT = {"X": rot_x, "Y": rot_y, "Z": rot_z}


def matrix_from_euler(angles_deg, order):
    m = IDENTITY
    for axis, angle in zip(order, angles_deg):
        m = mat_mul(m, AXIS_ROT[axis](math.radians(angle)))
    return m


# ---------------------------------------------------------------------------
# the body (internal frame: x forward, y up, z right; metres)
# ---------------------------------------------------------------------------

ANKLE_H = 0.08
FEMUR = 0.43
TIBIA = 0.42
HIP_DROP = 0.06       # hip joints below the pelvis centre
HIP_WIDTH = 0.09      # hip joints either side of it
FOOT = (0.14, -0.055, 0.0)    # ankle -> ball
TOE = (0.06, 0.0, 0.0)        # ball -> toe tip
LEG = FEMUR + TIBIA

#: (name, parent, offset from parent joint, internal metres). ``side`` fills
#: in L (right = -1) / R (right = +1) for the templated rows.
SKELETON = [
    ("pelvis", None, (0.0, 0.0, 0.0)),
    ("hipjoint.L", "pelvis", (0.0, 0.0, 0.0)),
    ("femur.L", "hipjoint.L", (0.0, -HIP_DROP, -HIP_WIDTH)),
    ("tibia.L", "femur.L", (0.0, -FEMUR, 0.0)),
    ("foot.L", "tibia.L", (0.0, -TIBIA, 0.0)),
    ("toes.L", "foot.L", FOOT),
    ("hipjoint.R", "pelvis", (0.0, 0.0, 0.0)),
    ("femur.R", "hipjoint.R", (0.0, -HIP_DROP, HIP_WIDTH)),
    ("tibia.R", "femur.R", (0.0, -FEMUR, 0.0)),
    ("foot.R", "tibia.R", (0.0, -TIBIA, 0.0)),
    ("toes.R", "foot.R", FOOT),
    ("lowerback", "pelvis", (0.0, 0.0, 0.0)),
    ("upperback", "lowerback", (0.0, 0.12, 0.0)),
    ("thorax", "upperback", (0.0, 0.14, 0.0)),
    ("lowerneck", "thorax", (0.0, 0.20, 0.0)),
    ("upperneck", "lowerneck", (0.0, 0.06, 0.0)),
    ("head", "upperneck", (0.0, 0.06, 0.0)),
    ("clavicle.L", "thorax", (0.0, 0.15, -0.02)),
    ("humerus.L", "clavicle.L", (0.0, 0.02, -0.16)),
    ("radius.L", "humerus.L", (0.0, 0.0, -0.29)),
    ("wrist.L", "radius.L", (0.0, 0.0, -0.25)),
    ("hand.L", "wrist.L", (0.0, 0.0, -0.04)),
    ("thumb.L", "wrist.L", (0.03, 0.0, -0.03)),
    ("clavicle.R", "thorax", (0.0, 0.15, 0.02)),
    ("humerus.R", "clavicle.R", (0.0, 0.02, 0.16)),
    ("radius.R", "humerus.R", (0.0, 0.0, 0.29)),
    ("wrist.R", "radius.R", (0.0, 0.0, 0.25)),
    ("hand.R", "wrist.R", (0.0, 0.0, 0.04)),
    ("thumb.R", "wrist.R", (0.03, 0.0, 0.03)),
]

#: End Site offsets (internal metres) for the leaves.
END_SITES = {
    "toes.L": TOE, "toes.R": TOE, "head": (0.0, 0.14, 0.0),
    "hand.L": (0.0, 0.0, -0.08), "hand.R": (0.0, 0.0, 0.08),
    "thumb.L": (0.02, 0.0, -0.02), "thumb.R": (0.02, 0.0, 0.02),
}

REST_PELVIS = (0.0, ANKLE_H + TIBIA + FEMUR + HIP_DROP, 0.0)   # 0.99 m

NAMES_CMU = {
    "pelvis": "root", "hipjoint.L": "lhipjoint", "femur.L": "lfemur",
    "tibia.L": "ltibia", "foot.L": "lfoot", "toes.L": "ltoes",
    "hipjoint.R": "rhipjoint", "femur.R": "rfemur", "tibia.R": "rtibia",
    "foot.R": "rfoot", "toes.R": "rtoes", "lowerback": "lowerback",
    "upperback": "upperback", "thorax": "thorax", "lowerneck": "lowerneck",
    "upperneck": "upperneck", "head": "head", "clavicle.L": "lclavicle",
    "humerus.L": "lhumerus", "radius.L": "lradius", "wrist.L": "lwrist",
    "hand.L": "lhand", "thumb.L": "lthumb", "clavicle.R": "rclavicle",
    "humerus.R": "rhumerus", "radius.R": "rradius", "wrist.R": "rwrist",
    "hand.R": "rhand", "thumb.R": "rthumb",
}

#: Mixamo has no hip-joint or upper-neck joint and no separate wrist: those
#: internal joints are folded out of the written hierarchy (see ``_fold``).
NAMES_MIXAMO = {
    "pelvis": "mixamorig:Hips", "femur.L": "mixamorig:LeftUpLeg",
    "tibia.L": "mixamorig:LeftLeg", "foot.L": "mixamorig:LeftFoot",
    "toes.L": "mixamorig:LeftToeBase", "femur.R": "mixamorig:RightUpLeg",
    "tibia.R": "mixamorig:RightLeg", "foot.R": "mixamorig:RightFoot",
    "toes.R": "mixamorig:RightToeBase", "lowerback": "mixamorig:Spine",
    "upperback": "mixamorig:Spine1", "thorax": "mixamorig:Spine2",
    "lowerneck": "mixamorig:Neck", "head": "mixamorig:Head",
    "clavicle.L": "mixamorig:LeftShoulder", "humerus.L": "mixamorig:LeftArm",
    "radius.L": "mixamorig:LeftForeArm", "wrist.L": "mixamorig:LeftHand",
    "hand.L": "mixamorig:LeftHandIndex1", "clavicle.R": "mixamorig:RightShoulder",
    "humerus.R": "mixamorig:RightArm", "radius.R": "mixamorig:RightForeArm",
    "wrist.R": "mixamorig:RightHand", "hand.R": "mixamorig:RightHandIndex1",
}

UPPER_BODY = {"pelvis", "lowerback", "upperback", "thorax", "lowerneck", "upperneck",
              "head", "clavicle.L", "humerus.L", "radius.L", "wrist.L", "hand.L",
              "clavicle.R", "humerus.R", "radius.R", "wrist.R", "hand.R"}

# ---------------------------------------------------------------------------
# the walk
# ---------------------------------------------------------------------------

CYCLE_S = 1.05          # one full gait cycle (two steps)
STRIDE_M = 1.00         # one full cycle's travel, before the take's variation
STANCE = 0.60           # fraction of the cycle a foot is down
LEAD = 0.30             # heel lands this share of a stride ahead of the hip joint
STEP_LIFT = 0.07        # swing ankle lift, metres
VARIATION = 0.035       # stride varies +-3.5% over a 2.9 s swell
VARIATION_S = 2.9
HIP_LOW = 0.853         # hip-joint height at double support
HIP_BOB = 0.022         # extra at mid-stance
SWAY = 0.025
PELVIS_YAW = math.radians(6.0)
PELVIS_LIST = math.radians(3.0)
PELVIS_TILT = math.radians(-5.0)     # negative about +right = tipped forward
ARM_ABDUCT = math.radians(80.0)
ARM_SWING = math.radians(22.0)
ELBOW_BASE = math.radians(15.0)
ELBOW_SWING = math.radians(10.0)
HEEL_TOE_UP = math.radians(15.0)
PUSH_OFF = math.radians(30.0)


def stride_at(t):
    return STRIDE_M * (1.0 + VARIATION * math.sin(2.0 * math.pi * t / VARIATION_S))


def travel_at(t):
    """Integral of the hip's forward speed stride(t)/CYCLE_S, from t = 0."""
    k = VARIATION * VARIATION_S / (2.0 * math.pi)
    return (STRIDE_M / CYCLE_S) * (t + k * (1.0 - math.cos(2.0 * math.pi * t / VARIATION_S)))


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


#: The heel, relative to the ankle (on the floor, behind it). A heel strike
#: pivots about it, as a real one does; pivoting about the ankle instead skids
#: the ball forward as it comes down.
HEEL = (-0.05, -ANKLE_H)


def _pivot(anchor, offset, pitch):
    """``anchor - R(pitch) @ offset`` in the sagittal plane (x forward, y up)."""
    c, s = math.cos(pitch), math.sin(pitch)
    return (anchor[0] - (c * offset[0] - s * offset[1]),
            anchor[1] - (s * offset[0] + c * offset[1]))


def _foot_state(t, phase):
    """(ankle forward position, ankle height, foot pitch) for one foot."""
    cycles = t / CYCLE_S + phase
    k = math.floor(cycles)
    u = cycles - k
    strike_t = (k - phase) * CYCLE_S
    plant = travel_at(strike_t) + LEAD * stride_at(strike_t)
    next_t = (k + 1 - phase) * CYCLE_S
    next_plant = travel_at(next_t) + LEAD * stride_at(next_t)
    heel_to_ankle = (-HEEL[0], -HEEL[1])
    if u < STANCE:
        if u < 0.08:                      # heel strike: toes come down about the heel
            pitch = HEEL_TOE_UP * (1.0 - _smooth(u / 0.08))
            heel = (plant + HEEL[0], ANKLE_H + HEEL[1])
            x, y = _pivot(heel, (-heel_to_ankle[0], -heel_to_ankle[1]), pitch)
            return x, y, pitch
        if u < 0.45:                      # flat
            return plant, ANKLE_H, 0.0
        # push-off: the heel rises about the planted ball
        pitch = -PUSH_OFF * _smooth((u - 0.45) / (STANCE - 0.45))
        x, y = _pivot((plant + FOOT[0], ANKLE_H + FOOT[1]), FOOT, pitch)
        return x, y, pitch
    # swing: the foot lifts first (heel-up posture held), travels, and arrives
    # in the next heel strike's opening pose
    p = (u - STANCE) / (1.0 - STANCE)
    off_x, off_y = _pivot((plant + FOOT[0], ANKLE_H + FOOT[1]), FOOT, -PUSH_OFF)
    heel = (next_plant + HEEL[0], ANKLE_H + HEEL[1])
    end_x, end_y = _pivot(heel, (-heel_to_ankle[0], -heel_to_ankle[1]), HEEL_TOE_UP)
    e = _smooth(p)
    x = off_x + (end_x - off_x) * e
    y = off_y + (end_y - off_y) * e + STEP_LIFT * math.sin(math.pi * p) ** 0.7
    pitch = -PUSH_OFF + (HEEL_TOE_UP + PUSH_OFF) * _smooth((p - 0.2) / 0.8)
    return x, y, pitch


def _two_bone(hip, ankle, forward):
    """Knee position for a femur/tibia chain, knee toward ``forward``."""
    d_vec = sub(ankle, hip)
    d = math.sqrt(dot(d_vec, d_vec))
    d = min(d, LEG * 0.999)
    u = norm(d_vec)
    a = (FEMUR ** 2 - TIBIA ** 2 + d * d) / (2.0 * d)
    h = math.sqrt(max(0.0, FEMUR ** 2 - a * a))
    n = norm(sub(forward, scale(u, dot(forward, u))))
    knee = add(add(hip, scale(u, a)), scale(n, h))
    return knee, u, n


REST_LEG = frame_from((0.0, -1.0, 0.0), (0.0, 0.0, 1.0))
REST_FOOT = frame_from(FOOT, (0.0, 0.0, 1.0))


def pose_at(t):
    """World rotations (internal frame) of every joint plus the pelvis position."""
    u_left = (t / CYCLE_S) % 1.0
    swing = math.cos(2.0 * math.pi * u_left)       # +1 when the left heel strikes
    yaw = PELVIS_YAW * swing
    roll = PELVIS_LIST * math.sin(4.0 * math.pi * u_left)
    pelvis_w = mat_mul(rot_y(yaw), mat_mul(rot_x(roll), rot_z(PELVIS_TILT)))
    height = HIP_LOW + HIP_BOB * (0.5 - 0.5 * math.cos(4.0 * math.pi * (u_left - 0.05)))
    sway = -SWAY * math.sin(2.0 * math.pi * u_left)
    hip_mid = (travel_at(t), height, sway)
    pelvis_pos = add(hip_mid, mat_vec(pelvis_w, (0.0, HIP_DROP, 0.0)))

    world = {"pelvis": pelvis_w}
    world["hipjoint.L"] = pelvis_w
    world["hipjoint.R"] = pelvis_w
    forward = mat_vec(pelvis_w, (1.0, 0.0, 0.0))
    for side, sign, phase in (("L", -1.0, 0.0), ("R", 1.0, 0.5)):
        hip = add(pelvis_pos, mat_vec(pelvis_w, (0.0, -HIP_DROP, sign * HIP_WIDTH)))
        ax, ay, pitch = _foot_state(t, phase)
        ankle = (ax, ay, sign * HIP_WIDTH)
        knee, u, n = _two_bone(hip, ankle, forward)
        hinge = cross(u, n)
        femur = mat_mul(frame_from(sub(knee, hip), hinge), transpose(REST_LEG))
        shin_dir = norm(sub(ankle, knee))
        tibia = mat_mul(frame_from(shin_dir, hinge), transpose(REST_LEG))
        foot = rot_z(pitch)
        world["femur." + side] = femur
        world["tibia." + side] = tibia
        world["foot." + side] = foot
        # the toes stay flat on the floor while the heel is up (push-off)
        world["toes." + side] = IDENTITY if pitch < 0 else foot
    lean = math.radians(-3.0)
    world["lowerback"] = mat_mul(rot_y(0.3 * yaw), rot_z(lean))
    world["upperback"] = mat_mul(rot_y(-0.3 * yaw), rot_z(lean - math.radians(1.0)))
    world["thorax"] = mat_mul(rot_y(-0.6 * yaw),
                              mat_mul(rot_x(-0.5 * roll), rot_z(math.radians(-2.0))))
    world["lowerneck"] = mat_mul(rot_y(-0.2 * yaw), rot_z(math.radians(4.0)))
    nod = math.radians(1.5) * math.sin(4.0 * math.pi * u_left)
    # the upper neck rides the lower one, so folding it out (Mixamo) is exact
    world["upperneck"] = world["lowerneck"]
    world["head"] = rot_z(nod)
    thorax = world["thorax"]
    for side, sign in (("L", -1.0), ("R", 1.0)):
        world["clavicle." + side] = thorax
        # the left arm swings with the RIGHT leg: back when the left heel strikes
        swing_side = -swing if side == "L" else swing
        # T-pose arm along -right (L) / +right (R); a turn of sign * ABDUCT
        # about forward brings it down to the side
        arm = mat_mul(thorax, mat_mul(rot_z(ARM_SWING * swing_side),
                                      rot_x(sign * ARM_ABDUCT)))
        elbow = ELBOW_BASE + ELBOW_SWING * (0.5 + 0.5 * swing_side)
        fore = mat_mul(arm, rot_y(sign * elbow))
        world["humerus." + side] = arm
        world["radius." + side] = fore
        world["wrist." + side] = fore
        world["hand." + side] = fore
        world["thumb." + side] = fore
    return world, pelvis_pos


def joint_positions(world, pelvis_pos, joints=None):
    """World positions (internal metres) of every joint, plus end sites."""
    offsets = {name: offset for name, _parent, offset in SKELETON}
    parents = {name: parent for name, parent, _offset in SKELETON}
    positions = {"pelvis": pelvis_pos}
    for name, parent, offset in SKELETON:
        if parent is None:
            continue
        positions[name] = add(positions[parent], mat_vec(world[parent], offsets[name]))
    for name, offset in END_SITES.items():
        positions[name + "#end"] = add(positions[name], mat_vec(world[name], offset))
    del parents
    return positions


def truth(t, yaw_deg=0.0):
    """Joint world positions (internal frame, metres) at time ``t`` seconds."""
    world, pelvis_pos = pose_at(t)
    positions = joint_positions(world, pelvis_pos)
    if yaw_deg:
        r = rot_y(math.radians(yaw_deg))
        positions = {k: mat_vec(r, v) for k, v in positions.items()}
    return positions


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

#: internal (forward, up, right) -> file axes, as the matrix C with p_file = C p.
AXES = {
    # Z-up, facing +X: forward -> +X, up -> +Z, right -> -Y
    "zup_x": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    # Y-up, facing +Z: forward -> +Z, up -> +Y, right -> -X
    "yup_z": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
}

UNITS = {"in": 1.0 / 0.0254, "cm": 100.0}


def _fold(names):
    """The written hierarchy: joints absent from ``names`` fold into their parent."""
    parents = {name: parent for name, parent, _offset in SKELETON}
    offsets = {name: offset for name, _parent, offset in SKELETON}
    rows = []
    for name, _parent, _offset in SKELETON:
        if name not in names:
            continue
        parent = parents[name]
        offset = offsets[name]
        while parent is not None and parent not in names:
            offset = add(offsets[parent], offset)
            parent = parents[parent]
        rows.append((name, parent, offset))
    return rows


def _fmt(value):
    text = "%.4f" % value
    return "0.0000" if text == "-0.0000" else text


def write_bvh(spec):
    """The fixture's text. ``spec`` keys: names, axes, units, fps, seconds,
    order, root_offset (bool), yaw_deg, keep (set of internal joints)."""
    names = spec["names"]
    keep = spec.get("keep") or set(names)
    rows = _fold({n for n in names if n in keep})
    c = AXES[spec["axes"]]
    k = UNITS[spec["units"]]
    order = spec["order"]
    children = {}
    for name, parent, _offset in rows:
        children.setdefault(parent, []).append(name)
    offsets = {name: offset for name, _parent, offset in rows}
    channel_names = " ".join("%srotation" % axis for axis in order)
    lines = ["HIERARCHY"]
    sequence = []

    def emit(name, depth):
        pad = "\t" * depth
        root = depth == 0
        lines.append("%s%s %s" % (pad, "ROOT" if root else "JOINT", names[name]))
        lines.append("%s{" % pad)
        offset = offsets[name]
        if root:
            offset = REST_PELVIS if spec.get("root_offset") else (0.0, 0.0, 0.0)
        o = scale(mat_vec(c, offset), k)
        lines.append("%s\tOFFSET %s %s %s" % (pad, _fmt(o[0]), _fmt(o[1]), _fmt(o[2])))
        if root:
            lines.append("%s\tCHANNELS 6 Xposition Yposition Zposition %s"
                         % (pad, channel_names))
        else:
            lines.append("%s\tCHANNELS 3 %s" % (pad, channel_names))
        sequence.append(name)
        kids = children.get(name, [])
        for kid in kids:
            emit(kid, depth + 1)
        if not kids:
            end = END_SITES.get(name, (0.0, 0.05, 0.0))
            e = scale(mat_vec(c, end), k)
            lines.append("%s\tEnd Site" % pad)
            lines.append("%s\t{" % pad)
            lines.append("%s\t\tOFFSET %s %s %s" % (pad, _fmt(e[0]), _fmt(e[1]), _fmt(e[2])))
            lines.append("%s\t}" % pad)
        lines.append("%s}" % pad)

    emit(rows[0][0], 0)
    parent_of = {name: parent for name, parent, _offset in rows}
    fps = spec["fps"]
    count = int(round(spec["seconds"] * fps)) + 1
    yaw = rot_y(math.radians(spec.get("yaw_deg", 0.0)))
    ct = transpose(c)
    lines.append("MOTION")
    lines.append("Frames: %d" % spec.get("declared_frames", count))
    lines.append("Frame Time: %.8f" % (1.0 / fps))
    for index in range(count):
        t = index / float(fps)
        world, pelvis_pos = pose_at(t)
        world = {n: mat_mul(yaw, m) for n, m in world.items()}
        pelvis_pos = mat_vec(yaw, pelvis_pos)
        row = []
        for name in sequence:
            w = world[name]
            parent = parent_of[name]
            local = w if parent is None else mat_mul(transpose(world[parent]), w)
            local_file = mat_mul(c, mat_mul(local, ct))
            if parent is None:
                row.extend(scale(mat_vec(c, pelvis_pos), k))
            row.extend(euler_for(local_file, order))
        lines.append(" ".join(_fmt(v) for v in row))
    return lines, sequence, count


FIXTURES = {
    "walk_cmu_zup_in120.bvh": {
        "names": NAMES_CMU, "axes": "zup_x", "units": "in", "fps": 120,
        "seconds": 2.6, "order": "ZYX", "root_offset": False, "yaw_deg": 0.0,
    },
    "walk_mixamo_yup_cm30.bvh": {
        "names": NAMES_MIXAMO, "axes": "yup_z", "units": "cm", "fps": 30,
        "seconds": 2.6, "order": "ZXY", "root_offset": True, "yaw_deg": 30.0,
    },
    "broken_upper_body.bvh": {
        "names": NAMES_CMU, "axes": "yup_z", "units": "cm", "fps": 30,
        "seconds": 1.0, "order": "ZXY", "root_offset": True, "yaw_deg": 0.0,
        "keep": UPPER_BODY,
    },
}


def build(name):
    """The bytes of fixture ``name``."""
    if name in FIXTURES:
        lines, _seq, _count = write_bvh(FIXTURES[name])
    elif name == "broken_nan.bvh":
        spec = dict(FIXTURES["walk_cmu_zup_in120.bvh"], seconds=0.25)
        lines, _seq, _count = write_bvh(spec)
        start = lines.index("MOTION") + 3
        for frame, column, text in ((7, 10, "nan"), (12, 4, "inf")):
            values = lines[start + frame].split(" ")
            values[column] = text
            lines[start + frame] = " ".join(values)
    elif name == "broken_truncated.bvh":
        spec = dict(FIXTURES["walk_cmu_zup_in120.bvh"], seconds=23 / 120.0,
                    declared_frames=30)
        lines, _seq, _count = write_bvh(spec)
        values = lines[-5].split(" ")
        lines[-5] = " ".join(values[:-1])
    else:
        raise KeyError(name)
    return ("\n".join(lines) + "\n").encode("ascii")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_all(folder=HERE):
    out = {}
    for name in DIGESTS:
        data = build(name)
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(data)
        out[name] = digest(data)
    return out


def check_all():
    """[(name, built digest, pinned digest, committed digest)] for every fixture."""
    rows = []
    for name, pinned in DIGESTS.items():
        built = digest(build(name))
        path = os.path.join(HERE, name)
        committed = None
        if os.path.isfile(path):
            with open(path, "rb") as handle:
                committed = digest(handle.read())
        rows.append((name, built, pinned, committed))
    return rows


if __name__ == "__main__":
    if "--check" in sys.argv:
        bad = 0
        for name, built, pinned, committed in check_all():
            ok = built == pinned == committed
            bad += 0 if ok else 1
            print("%s %s built=%s pinned=%s committed=%s"
                  % ("OK  " if ok else "FAIL", name, built[:12], (pinned or "-")[:12],
                     (committed or "-")[:12]))
        sys.exit(1 if bad else 0)
    for name, value in write_all().items():
        print('    "%s": "%s",' % (name, value))
