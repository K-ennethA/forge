"""The mocap production path: validation, naming presets, loops, motion statistics.

``rigforge_retarget`` (rigforge_anim) is the transfer itself.  This module is
everything around it that turns "a .bvh somebody licensed" into "a contract
clip the drop-in build can ship":

* :func:`read_bvh` / :func:`validate_bvh` — the file is parsed and refused
  **before** Blender's importer sees it: a NaN in a motion row, a row a value
  short, a frame count that disagrees with the rows, all come back as a
  sentence with the frame, joint and channel in it rather than as a skeleton
  that silently animates to the origin.
* :data:`MAPPING_PRESETS` — explicit source-name tables for the two naming
  conventions most licensed libraries ship in (CMU/ASF and its cgspeed BVH
  spelling, and Mixamo).  **Assumption, labelled:** they are written from the
  published naming conventions, not verified against a vetted file yet — the
  first real take is what confirms or corrects them.  ``"auto"`` (the name
  heuristic) stays the default.
* :func:`find_loop_window` / :func:`close_seam` — mocap does not loop by
  itself.  The best window is searched for in the take (pose AND velocity
  have to match across the seam, so a mirrored half-cycle cannot win), and the
  residual left at the seam is spread across the window so the last frame is
  the first frame exactly.  The residual is reported, and refused past a
  caller-set ceiling.
* :func:`motion_statistics` / ``motion_stats`` — the motion statistics the
  artist's "clearly broken" verdict asked for: joint angular speed and
  acceleration distributions, footfall timing regularity, posture variance.
  :data:`MOTION_QUALITY_THRESHOLDS` pins three gait floors on them, measured
  on retargeted real mocap against the rejected procedural clips (derivation
  beside the table); :func:`motion_quality` applies them and
  ``animation_check {"motion_quality": true}`` gates on them.
* ``rigforge_mocap_clip`` — the driver: .bvh in -> retarget (IK legs, in place,
  loop found and closed) -> ``animation_check`` gates -> ``motion_stats`` -> a
  named action with a contract block the drop-in build can read.
"""

import math
import os
import re
import time

import bpy
from mathutils import Matrix, Quaternion, Vector

from .common import (
    get_bool,
    get_choice,
    get_float,
    get_int,
    get_str,
    resolve_path,
)
from .registry import ForgeError, command

# ---------------------------------------------------------------------------
# BVH reading and refusal
# ---------------------------------------------------------------------------

#: How many individual bad values a refusal quotes before it just counts them.
QUOTE_LIMIT = 5


def read_bvh(path):
    """Parse a .bvh file's structure and motion rows. Never raises on bad data:
    everything wrong is collected into ``problems`` for :func:`validate_bvh`."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        raise ForgeError("Could not read %s: %s" % (path, exc))
    joints = []
    stack = []
    problems = []
    index = 0
    total = len(lines)
    if not lines or lines[0].strip().upper() != "HIERARCHY":
        problems.append("line 1 is %r, not HIERARCHY - this is not a BVH file"
                        % (lines[0].strip()[:40] if lines else ""))
        return {"joints": [], "problems": problems, "rows": [], "frame_time": None,
                "declared_frames": None, "channels_per_frame": 0, "nonfinite": [],
                "nonfinite_count": 0}
    index = 1
    pending_end = False
    while index < total:
        text = lines[index].strip()
        index += 1
        if not text:
            continue
        head = text.split(None, 1)
        word = head[0].upper()
        if word in ("ROOT", "JOINT"):
            name = head[1].strip() if len(head) > 1 else ""
            joints.append({"name": name, "parent": stack[-1] if stack else None,
                           "offset": None, "channels": [], "line": index})
            pending_end = False
        elif word == "END":
            pending_end = True
        elif word == "{":
            # an End Site's brace is pushed as a marker, so a joint's parent is
            # always the joint index on top of the stack when it is declared
            stack.append("end" if pending_end else len(joints) - 1)
        elif word == "}":
            if not stack:
                problems.append("line %d closes a brace that was never opened" % index)
            elif stack.pop() == "end":
                pending_end = False
        elif word == "OFFSET":
            try:
                values = [float(v) for v in text.split()[1:4]]
            except ValueError:
                problems.append("line %d: OFFSET is not three numbers: %r" % (index, text))
                continue
            if pending_end or not joints:
                continue
            joints[-1]["offset"] = values
        elif word == "CHANNELS":
            parts = text.split()
            try:
                count = int(parts[1])
            except (IndexError, ValueError):
                problems.append("line %d: CHANNELS has no count: %r" % (index, text))
                continue
            names = parts[2:]
            if len(names) != count:
                problems.append("line %d: CHANNELS says %d, lists %d" % (index, count, len(names)))
            if joints:
                joints[-1]["channels"] = names
        elif word == "MOTION":
            break
    for joint in joints:
        if joint["parent"] == "end":  # pragma: no cover - a JOINT inside an End Site
            problems.append("line %d: joint %r is declared inside an End Site"
                            % (joint["line"], joint["name"]))
            joint["parent"] = None
    if stack:
        problems.append("the hierarchy leaves %d brace(s) open" % len(stack))
    declared = None
    frame_time = None
    rows = []
    nonfinite = []
    nonfinite_count = 0
    width = sum(len(joint["channels"]) for joint in joints)
    columns = []
    for joint in joints:
        for channel in joint["channels"]:
            columns.append((joint["name"], channel))
    if index >= total and not any(l.strip().upper() == "MOTION" for l in lines):
        problems.append("no MOTION section")
    while index < total:
        text = lines[index].strip()
        index += 1
        if not text:
            continue
        low = text.lower()
        if low.startswith("frames:"):
            try:
                declared = int(text.split(":", 1)[1])
            except ValueError:
                problems.append("line %d: Frames: is not a whole number: %r" % (index, text))
            continue
        if low.startswith("frame time:"):
            try:
                frame_time = float(text.split(":", 1)[1])
            except ValueError:
                problems.append("line %d: Frame Time: is not a number: %r" % (index, text))
            continue
        parts = text.split()
        values = []
        bad_token = None
        for token in parts:
            try:
                values.append(float(token))
            except ValueError:
                bad_token = token
                break
        if bad_token is not None:
            problems.append("line %d (motion row %d): %r is not a number"
                            % (index, len(rows), bad_token))
            rows.append(None)
            continue
        if len(values) != width:
            problems.append("line %d (motion row %d) has %d values; the hierarchy "
                            "declares %d channels" % (index, len(rows), len(values), width))
        for column, value in enumerate(values):
            if not math.isfinite(value):
                nonfinite_count += 1
                if len(nonfinite) < QUOTE_LIMIT:
                    joint, channel = columns[column] if column < len(columns) else ("?", "?")
                    nonfinite.append({"frame": len(rows), "joint": joint,
                                      "channel": channel, "value": repr(value)})
        rows.append(values)
    return {
        "joints": joints,
        "channels_per_frame": width,
        "declared_frames": declared,
        "frame_time": frame_time,
        "rows": rows,
        "problems": problems,
        "nonfinite": nonfinite,
        "nonfinite_count": nonfinite_count,
    }


def validate_bvh(info, path):
    """Raise a ForgeError that names every reason ``info`` cannot be retargeted."""
    reasons = list(info["problems"])
    if not info["joints"]:
        reasons.append("no joints in the hierarchy")
    missing_channels = [j["name"] for j in info["joints"] if not j["channels"]]
    if missing_channels:
        reasons.append("%d joint(s) have no CHANNELS line: %s"
                       % (len(missing_channels), ", ".join(missing_channels[:QUOTE_LIMIT])))
    if info["frame_time"] is None:
        reasons.append("no 'Frame Time:' line")
    elif not math.isfinite(info["frame_time"]) or info["frame_time"] <= 0.0:
        reasons.append("Frame Time is %r; it has to be a positive number of seconds"
                       % info["frame_time"])
    rows = len(info["rows"])
    if info["declared_frames"] is None:
        reasons.append("no 'Frames:' line")
    elif info["declared_frames"] != rows:
        reasons.append("the header declares %d frames and %d motion rows follow"
                       % (info["declared_frames"], rows))
    if rows < 2:
        reasons.append("%d motion row(s); a clip needs at least 2" % rows)
    if info["nonfinite_count"]:
        quoted = ", ".join("frame %d %s %s = %s" % (e["frame"], e["joint"], e["channel"],
                                                   e["value"]) for e in info["nonfinite"])
        reasons.append("%d non-finite value(s) in the motion (%s)"
                       % (info["nonfinite_count"], quoted))
    if reasons:
        raise ForgeError("%s cannot be retargeted: %s. Nothing was imported."
                         % (os.path.basename(path), "; ".join(reasons)))
    return {
        "joints": len(info["joints"]),
        "channels_per_frame": info["channels_per_frame"],
        "frames": rows,
        "frame_time": info["frame_time"],
        "fps": round(1.0 / info["frame_time"], 4),
        "duration_s": round((rows - 1) * info["frame_time"], 4),
    }


# ---------------------------------------------------------------------------
# naming presets (ASSUMPTION: written from the published conventions)
# ---------------------------------------------------------------------------

#: Source joint name (lower-cased, ``mixamorig:`` stripped) -> (slot, side).
#: A name that maps to ``None`` is known and deliberately left out (a second
#: spine joint the Rigify spine has no control for, a hand end).
MAPPING_PRESETS = {
    # CMU mocap, both spellings in circulation: the ASF joint names and the
    # cgspeed BVH conversion's. ASSUMPTION until a vetted file is read.
    "cmu": {
        "root": ("hips", None), "hips": ("hips", None),
        # LowerBack sits ON the hips in both spellings (cgspeed README: "lowerback
        # is in the same location as hip") and bends the spine segment above
        # them. Its old slot, spine_fk.001, is the head of Rigify basic_spine's
        # REVERSED hip chain (spine_fk.001 -> MCH-spine -> spine_fk -> ORG-spine
        # -> DEF-thigh), so keying it swung the pelvis about the torso pivot:
        # measured on CMU 08_01, both hip joints 45-50 mm off the path the
        # retarget placed them on, and every leg-reach sum built on that path
        # wrong by as much. The rig's only upper-body control above the pivot
        # that deforms is `chest`, which the thorax already takes.
        "lowerback": None, "upperback": None, "thorax": ("chest", None),
        "spine": None, "spine1": ("chest", None),
        "lowerneck": ("neck", None), "upperneck": None, "neck": ("neck", None),
        "neck1": None, "head": ("head", None),
        "lhipjoint": None, "rhipjoint": None,
        "lfemur": ("thigh", "L"), "rfemur": ("thigh", "R"),
        "ltibia": ("shin", "L"), "rtibia": ("shin", "R"),
        "lfoot": ("foot", "L"), "rfoot": ("foot", "R"),
        "ltoes": ("toe", "L"), "rtoes": ("toe", "R"),
        "lclavicle": ("shoulder", "L"), "rclavicle": ("shoulder", "R"),
        "lhumerus": ("upperarm", "L"), "rhumerus": ("upperarm", "R"),
        "lradius": ("forearm", "L"), "rradius": ("forearm", "R"),
        "lwrist": ("hand", "L"), "rwrist": ("hand", "R"),
        "lhand": None, "rhand": None,
        "leftupleg": ("thigh", "L"), "rightupleg": ("thigh", "R"),
        "leftleg": ("shin", "L"), "rightleg": ("shin", "R"),
        "leftfoot": ("foot", "L"), "rightfoot": ("foot", "R"),
        "lefttoebase": ("toe", "L"), "righttoebase": ("toe", "R"),
        "leftshoulder": ("shoulder", "L"), "rightshoulder": ("shoulder", "R"),
        "leftarm": ("upperarm", "L"), "rightarm": ("upperarm", "R"),
        "leftforearm": ("forearm", "L"), "rightforearm": ("forearm", "R"),
        "lefthand": ("hand", "L"), "righthand": ("hand", "R"),
    },
    # 100STYLE (Mason et al., Zenodo 8127870). Read off the files themselves
    # (2026-09-24): its "Shoulder" is the upper arm and "Collar" the clavicle -
    # the reverse of what the CMU table calls a shoulder, hence its own table.
    # Chest..Chest3 bend the spine below the rig's one deforming upper-body
    # control; Chest4 (the thorax) takes `chest`, as CMU's thorax does.
    "100style": {
        "hips": ("hips", None), "chest": None, "chest2": None, "chest3": None,
        "chest4": ("chest", None), "neck": ("neck", None), "head": ("head", None),
        "leftcollar": ("shoulder", "L"), "rightcollar": ("shoulder", "R"),
        "leftshoulder": ("upperarm", "L"), "rightshoulder": ("upperarm", "R"),
        "leftelbow": ("forearm", "L"), "rightelbow": ("forearm", "R"),
        "leftwrist": ("hand", "L"), "rightwrist": ("hand", "R"),
        "lefthip": ("thigh", "L"), "righthip": ("thigh", "R"),
        "leftknee": ("shin", "L"), "rightknee": ("shin", "R"),
        "leftankle": ("foot", "L"), "rightankle": ("foot", "R"),
        "lefttoe": ("toe", "L"), "righttoe": ("toe", "R"),
    },
    # Mixamo (``mixamorig:`` prefix optional). Read off 12 real X Bot exports 2026-09-25
    # (C:/forge-assets/thirdparty/mixamo, 52 joints once the importer drops the leaf ends; the
    # prefix IS kept). "Spine" is the first joint above the hips, like CMU's LowerBack, and its
    # old slot (spine -> spine_fk.001) is the head of Rigify's reversed hip chain: keyed, it
    # swung the pelvis off the path the retarget placed it on - measured on the MPFB proxy, the
    # hip joints 45.5 mm off it (sprint-forward) and 27.5 mm (punch-combo), 0.0 with it left out.
    # Spine2 (the thorax) takes `chest`, as CMU's thorax does. Fingers are not slots here.
    "mixamo": {
        "hips": ("hips", None), "spine": None, "spine1": None,
        "spine2": ("chest", None), "neck": ("neck", None), "head": ("head", None),
        "leftupleg": ("thigh", "L"), "rightupleg": ("thigh", "R"),
        "leftleg": ("shin", "L"), "rightleg": ("shin", "R"),
        "leftfoot": ("foot", "L"), "rightfoot": ("foot", "R"),
        "lefttoebase": ("toe", "L"), "righttoebase": ("toe", "R"),
        "leftshoulder": ("shoulder", "L"), "rightshoulder": ("shoulder", "R"),
        "leftarm": ("upperarm", "L"), "rightarm": ("upperarm", "R"),
        "leftforearm": ("forearm", "L"), "rightforearm": ("forearm", "R"),
        "lefthand": ("hand", "L"), "righthand": ("hand", "R"),
    },
}

#: Per preset: verified against real files, or still an assumption.
PRESET_STATUS_BY_NAME = {
    "cmu": ("verified 2026-09-24 against the cgspeed 2010 BVH conversion (CMU takes "
            "08_01, 16_21, 39_01, 16_45, 16_46, 09_06, 78_06, 78_12): all 31 joint "
            "names read, 20 mapped; LowerBack dropped on measurement (see the table)"),
    "100style": ("read off the 100STYLE files (Crouched_FW / Crouched_ID, 2026-09-24): "
                 "23 joints, 20 mapped, Chest..Chest3 left out on purpose"),
    "mixamo": ("verified 2026-09-25 against 12 Mixamo X Bot FBX exports (30 fps, "
               "'mixamorig:' prefix kept, 52 joints after leaf ends): 20 mapped, Spine and "
               "Spine1 left out on measurement (see the table), fingers not slots"),
}
PRESET_STATUS = PRESET_STATUS_BY_NAME["mixamo"]

#: The slots a locomotion clip cannot do without; a sided slot means both sides.
LOCOMOTION_SLOTS = ("hips", "thigh", "shin", "foot")

#: Slots whose rest DIRECTION is reconciled between the two skeletons (T-pose
#: arms onto an A-pose rig). The axial slots are assumed upright in both rests,
#: which the global orientation step already guarantees.
LIMB_SLOTS = ("shoulder", "upperarm", "forearm", "hand", "thigh", "shin", "foot", "toe")

#: Slot -> the slot whose head is this segment's far end (same side).
SEGMENT_END = {"thigh": "shin", "shin": "foot", "foot": "toe", "shoulder": "upperarm",
               "upperarm": "forearm", "forearm": "hand"}


def preset_slot(preset, name, normalise):
    """``(slot, side)``, ``None`` (known, skipped) or ``False`` (not in the table)."""
    table = MAPPING_PRESETS[preset]
    key = normalise(name).replace(" ", "").replace("_", "")
    if key in table:
        return table[key]
    return False


# ---------------------------------------------------------------------------
# rotations
# ---------------------------------------------------------------------------


def same_hemisphere(quats):
    """Flip signs so consecutive quaternions never take the long way round."""
    out = []
    previous = None
    for q in quats:
        q = q.copy()
        if previous is not None and previous.dot(q) < 0.0:
            q.negate()
        out.append(q)
        previous = q
    return out


def angle_deg(a, b):
    """Angle between two rotations (quaternions), degrees, in [0, 180]."""
    d = abs(max(-1.0, min(1.0, a.dot(b))))
    return math.degrees(2.0 * math.acos(d))


def slerp(a, b, t):
    if a.dot(b) < 0.0:
        b = b.copy()
        b.negate()
    return a.slerp(b, t)


# ---------------------------------------------------------------------------
# loops
# ---------------------------------------------------------------------------

#: The largest per-bone residual (degrees) a found loop may spread across its
#: window by default. ASSUMPTION, not measured on real mocap: past this the
#: correction is a visible drift rather than a closed seam. Caller-settable.
LOOP_MAX_RESIDUAL_DEG = 20.0


def _pose_distance(rotations, heights, leg, i, k):
    total = 0.0
    for track in rotations.values():
        total += angle_deg(track[i], track[k])
    mean = total / float(max(1, len(rotations)))
    if heights is not None and leg > 1e-9:
        mean += math.degrees(abs(heights[i] - heights[k]) / leg)
    return mean


def find_loop_window(rotations, heights, leg, min_samples, max_samples=None):
    """Best ``(start, end)`` sample pair for a loop, and its cost in degrees.

    The cost is the mean per-bone rotation distance between the two seam poses
    plus the same distance one sample later (so the velocity has to match too
    and a mirrored half-cycle cannot win), with the hip height difference
    counted in radians of leg length. Ties go to the longer window.
    """
    names = list(rotations)
    count = len(rotations[names[0]]) if names else 0
    best = None
    rows = 0
    if count < min_samples + 2:
        return None
    limit = count - 2
    for start in range(0, limit):
        top = limit if max_samples is None else min(limit, start + max_samples)
        for end in range(start + min_samples, top + 1):
            rows += 1
            cost = (_pose_distance(rotations, heights, leg, start, end)
                    + _pose_distance(rotations, heights, leg, start + 1, end + 1))
            if (best is None or cost < best[2] - 1e-9
                    or (abs(cost - best[2]) <= 1e-9 and end - start > best[1] - best[0])):
                best = (start, end, cost)
    if best is None:
        return None
    return {"start": best[0], "end": best[1], "cost_deg": best[2] / 2.0,
            "windows_tried": rows}


def path_weights(points):
    """Cumulative path length along ``points``, as fractions 0..1 (None if still).

    A located track's seam residual is spread in proportion to how far the
    point has *moved*, not how much time has passed: a planted foot travels
    nowhere, so it takes none of the correction and stays planted, and the
    swing that follows carries all of it. Spreading by time instead put a
    measured 17.8 mm slide into the CMU fixture's planted left foot (a 21.7 mm
    stride-to-stride residual smeared across a 14-frame stance).
    """
    total = 0.0
    out = [0.0]
    for a, b in zip(points, points[1:]):
        total += (b - a).length
        out.append(total)
    if total < 1e-9:
        return None
    return [value / total for value in out]


def close_seam(tracks, travel=None, weights=None):
    """Spread each track's seam residual so sample -1 equals sample 0.

    ``tracks`` is ``{name: {"rot": [Quaternion]|None, "loc": [Vector]|None}}``,
    already cropped to the window. ``travel`` is the displacement a located
    track is *meant* to end the window with (a root-motion clip's stride);
    only what is left over is spread. Rotations are spread linearly in time;
    locations by ``weights[name]`` (see :func:`path_weights`) when given,
    linearly otherwise. Returns the per-track residuals.
    """
    weights = weights or {}
    residuals = {}
    for name, track in tracks.items():
        entry = {}
        rot = track.get("rot")
        if rot:
            count = len(rot)
            last = count - 1
            error = rot[-1] @ rot[0].inverted()
            if error.w < 0.0:
                error.negate()
            fix = error.inverted()
            identity = Quaternion()
            for index in range(count):
                t = index / float(last) if last else 0.0
                rot[index] = identity.slerp(fix, t) @ rot[index]
            rot[-1] = rot[0].copy()
            entry["rot_deg"] = round(math.degrees(2.0 * math.acos(min(1.0, abs(error.w)))), 4)
        loc = track.get("loc")
        if loc:
            count = len(loc)
            last = count - 1
            meant = travel if (travel is not None and track.get("travels", True)) else Vector()
            error = (loc[-1] - loc[0]) - meant
            share = weights.get(name)
            for index in range(count):
                if share is not None:
                    t = share[index]
                else:
                    t = index / float(last) if last else 0.0
                loc[index] = loc[index] - error * t
            loc[-1] = loc[0] + meant
            entry["loc_mm"] = round(error.length * 1000.0, 4)
        residuals[name] = entry
    return residuals


# ---------------------------------------------------------------------------
# motion statistics (the quality-gate scaffold: measured, never judged)
# ---------------------------------------------------------------------------

STATS_STATUS = ("measured. The gait-quality floors (MOTION_QUALITY_THRESHOLDS) were "
                "pinned 2026-09-24 on retargeted real mocap against the procedural "
                "clips the artist rejected; motion_quality() applies them, "
                "animation_check's motion_quality tier gates on them.")

#: The motion-quality tier: floors a gait clip has to clear, each pinned by the
#: honest-pin rule - measured on both populations, placed with margin to both,
#: derivation quoted. Measured 2026-09-24 on the werewolf rig (24 fps, one
#: cycle per loop), motion_stats on:
#:   REAL (retargeted): CMU 08_01, 16_21, 39_01 walks; CMU 16_46, 16_45 runs;
#:     100STYLE Crouched_FW crouch walk. (16_21 and 16_45 miss the slide gate -
#:     12.2 / 35.7 mm - and are kept as motion samples: these metrics read
#:     the trunk and head, which the plant cleanup never touches.)
#:   PROCEDURAL (the clips the artist called "clearly broken"): walk-loop,
#:     run-loop, sprint-loop as build_protagonist_human.py authored them.
#:
#: metric                       procedural max    real min (gaits)  floor
#: trunk_to_leg_speed_ratio     0.0147 (run)      0.1414 (crouch)   0.07
#: trunk_pitch_std_deg          0.0045 (sprint)   0.6796 (39_01)    0.30
#: head_pitch_std_deg           0.0027 (walk)     0.7520 (16_45)    0.35
#:
#: Each floor sits at about half the lowest real reading (2.0x / 2.3x / 2.1x
#: under it, so a real take a bit stiffer than these six still passes) and
#: 4.8x / 67x / 130x over the highest procedural one (so the rejected clips
#: fail on every floor, not on one).
#:
#: Measured and deliberately NOT pinned:
#:   * step-interval CV - a one-cycle loop has two step intervals, so a clean
#:     real take reads 0.0 exactly like a procedural one (08_01: 0.0; the real
#:     range is 0.0-0.125): it cannot separate them.
#:   * accel peak/median - the procedural run (p90 3.21) and sprint (2.89) are
#:     SMOOTHER than real runs (3.66-4.69); only the procedural walk spikes
#:     (max 6497), and the real crouch walk's max reads 407. No floor or
#:     ceiling separates the populations.
#:   * raw trunk angular speed - the real crouch walk (p50 5.96 deg/s) sits
#:     beside the procedural run (4.39); divided by the legs' own speed it
#:     separates 10x, which is why the ratio is pinned instead.
MOTION_QUALITY_THRESHOLDS = {
    "trunk_to_leg_speed_ratio": {"min": 0.07},
    "trunk_pitch_std_deg": {"min": 0.30},
    "head_pitch_std_deg": {"min": 0.35},
}

MOTION_QUALITY_WHY = {
    "trunk_to_leg_speed_ratio": ("the trunk's joints turn at %.4f of the legs' median "
                                 "angular speed - a torso carried rigid on moving legs"),
    "trunk_pitch_std_deg": ("the hip-to-neck line pitches by %.3f deg (std) through the "
                            "cycle - the trunk does not ride the stride"),
    "head_pitch_std_deg": ("the head pitches by %.3f deg (std) through the cycle - a "
                           "head locked to the horizon"),
}


def motion_quality(stats):
    """Apply MOTION_QUALITY_THRESHOLDS to a :func:`motion_statistics` block."""
    posture = stats.get("posture") or {}
    values = {
        "trunk_to_leg_speed_ratio": stats.get("trunk_to_leg_speed_ratio"),
        "trunk_pitch_std_deg": posture.get("trunk_pitch_std_deg"),
        "head_pitch_std_deg": posture.get("head_pitch_std_deg"),
    }
    checks = []
    failed = []
    for name, band in MOTION_QUALITY_THRESHOLDS.items():
        value = values.get(name)
        if value is None:
            checks.append({"metric": name, "value": None, "min": band["min"],
                           "verdict": "unmeasured"})
            continue
        ok = value >= band["min"]
        checks.append({"metric": name, "value": value, "min": band["min"],
                       "verdict": "ok" if ok else "fail"})
        if not ok:
            failed.append("%s (floor %s)" % (MOTION_QUALITY_WHY[name] % value, band["min"]))
    measured = [c for c in checks if c["verdict"] != "unmeasured"]
    verdict = "unmeasured" if not measured else ("fail" if failed else "ok")
    says = ("Motion quality: %s." % "; ".join(failed)) if failed else (
        "Motion quality: every floor cleared." if measured else
        "Motion quality: nothing to measure.")
    return {"verdict": verdict, "checks": checks, "thresholds": MOTION_QUALITY_THRESHOLDS,
            "says": says}

_SPLIT_SEGMENT = re.compile(r"\.[LR]\.\d+$")


def _stats_joints(rig):
    """DEF joints minus the twist/B-bone split segments (``DEF-thigh.L.001``),
    each with the nearest ancestor that is also a joint."""
    names = [b.name for b in rig.data.bones
             if b.name.startswith("DEF-") and not _SPLIT_SEGMENT.search(b.name)]
    wanted = set(names)
    out = []
    for name in names:
        bone = rig.data.bones[name]
        parent = bone.parent
        while parent is not None and parent.name not in wanted:
            parent = parent.parent
        out.append((name, parent.name if parent is not None else None))
    return out


def _group_of(name):
    low = name.lower()
    if any(k in low for k in ("thigh", "shin", "foot", "toe")):
        return "legs"
    if any(k in low for k in ("shoulder", "arm", "hand")):
        return "arms"
    return "trunk"


def _percentiles(values):
    if not values:
        return None
    ordered = sorted(values)

    def at(p):
        if len(ordered) == 1:
            return ordered[0]
        x = p * (len(ordered) - 1)
        lo = int(math.floor(x))
        hi = min(lo + 1, len(ordered) - 1)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (x - lo)

    mean = sum(ordered) / len(ordered)
    return {"n": len(ordered), "mean": round(mean, 3), "p50": round(at(0.5), 3),
            "p90": round(at(0.9), 3), "p99": round(at(0.99), 3),
            "max": round(ordered[-1], 3)}


def _std(values):
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _log_vec(q):
    """Rotation vector (axis * angle, radians) of a quaternion, shortest way."""
    if q.w < 0.0:
        q = q.copy()
        q.negate()
    angle = 2.0 * math.acos(max(-1.0, min(1.0, q.w)))
    s = math.sqrt(max(0.0, 1.0 - q.w * q.w))
    if s < 1e-9:
        return Vector((0.0, 0.0, 0.0))
    return Vector((q.x / s, q.y / s, q.z / s)) * angle


def motion_statistics(rig, action):
    """Per-clip motion statistics for the future quality gate. See STATS_STATUS."""
    from . import rigcheck
    from .rigforge_rig import LOOP_SUFFIX, assign_action

    scene = bpy.context.scene
    fps = float(scene.render.fps) / float(scene.render.fps_base or 1.0)
    start, end = int(math.floor(action.frame_range[0])), int(math.ceil(action.frame_range[1]))
    looping = action.name.endswith(LOOP_SUFFIX) or bool(getattr(action, "use_cyclic", False))
    frames = list(range(start, end + 1))
    joints = _stats_joints(rig)
    heels = rigcheck.heel_points(rig)
    thighs = [n for n in ("DEF-thigh.L", "DEF-thigh.R") if n in rig.pose.bones]
    neck = rig.pose.bones.get("DEF-spine.003")
    spine_heads = sorted((b.name for b in rig.pose.bones
                          if re.match(r"^DEF-spine(\.\d+)?$", b.name)),
                         key=lambda n: int(n.split(".")[1]) if "." in n else 0)
    head_bone = rig.pose.bones.get(spine_heads[-1]) if spine_heads else None
    forward, _how = rigforge_rig_forward(rig)
    up = Vector((0.0, 0.0, 1.0))
    right = forward.cross(up).normalized()
    local = {name: [] for name, _parent in joints}
    heel_tracks = {h["side"]: [] for h in heels}
    trunk_pitch, trunk_roll, head_pitch, head_yaw = [], [], [], []
    hip_h, hip_lat = [], []
    previous_action = rig.animation_data.action if rig.animation_data else None
    previous_frame = scene.frame_current
    mw = rig.matrix_world
    try:
        assign_action(rig, action)
        for frame in frames:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            pose = rig.pose.bones
            for name, parent in joints:
                m = pose[name].matrix
                if parent is not None:
                    m = pose[parent].matrix.inverted_safe() @ m
                local[name].append(m.to_quaternion().normalized())
            for h in heels:
                heel_tracks[h["side"]].append(mw @ pose[h["bone"]].head)
            if len(thighs) == 2:
                hip = sum((mw @ pose[n].head for n in thighs), Vector()) / 2.0
                hip_h.append(hip.dot(up))
                hip_lat.append(hip.dot(right))
                if neck is not None:
                    v = (mw @ neck.tail) - hip
                    trunk_pitch.append(math.degrees(math.atan2(v.dot(forward), v.dot(up))))
                    trunk_roll.append(math.degrees(math.atan2(v.dot(right), v.dot(up))))
            if head_bone is not None:
                axis = (mw.to_3x3() @ head_bone.matrix.to_3x3()) @ Vector((0.0, 1.0, 0.0))
                head_pitch.append(math.degrees(math.atan2(axis.dot(forward), axis.dot(up))))
                head_yaw.append(math.degrees(math.atan2(axis.dot(right),
                                                        max(1e-9, abs(axis.dot(forward))
                                                            + abs(axis.dot(up))))))
    finally:
        scene.frame_set(previous_frame)
        assign_action(rig, previous_action)

    # --- angular speed and acceleration, per joint, in the parent's frame
    speed = {"all": [], "legs": [], "arms": [], "trunk": []}
    accel = {"all": [], "legs": [], "arms": [], "trunk": []}
    jerk_ratio = {}
    posture = []
    for name, _parent in joints:
        track = same_hemisphere(local[name])
        deltas = [track[i + 1] @ track[i].inverted() for i in range(len(track) - 1)]
        if looping and len(track) > 2:
            # the wrap: last frame == first, so the step after it is first -> second
            deltas.append(track[1] @ track[0].inverted())
        vecs = [_log_vec(d) * fps for d in deltas]
        group = _group_of(name)
        speeds = [math.degrees(v.length) for v in vecs[:len(track) - 1]]
        accs = [math.degrees((vecs[i + 1] - vecs[i]).length) * fps
                for i in range(len(vecs) - 1)]
        speed["all"].extend(speeds)
        speed[group].extend(speeds)
        accel["all"].extend(accs)
        accel[group].extend(accs)
        # posture variance: mean angular distance from the joint's mean rotation
        body = track[:-1] if looping and len(track) > 2 else track
        mean = Quaternion((0.0, 0.0, 0.0, 0.0))
        for q in body:
            mean = Quaternion((mean.w + q.w, mean.x + q.x, mean.y + q.y, mean.z + q.z))
        if mean.magnitude > 1e-9:
            mean.normalize()
            posture.append((name, sum(angle_deg(q, mean) for q in body) / len(body)))
        if accs and speeds:
            med = _percentiles(accs)["p50"]
            jerk_ratio[name] = round(max(accs) / med, 3) if med > 1e-9 else None

    # --- footfall timing
    sample = frames[:-1] if looping and len(frames) > 2 else frames
    strikes = {}
    for side, track in heel_tracks.items():
        track = track[:len(sample)]
        strikes[side] = [sample[i] for i in rigcheck.contact_starts(track, looping=looping)]
    footfall = {"strike_frames": strikes, "cycle_frames": len(sample) if looping else None}
    events = sorted((f, side) for side, fs in strikes.items() for f in fs)
    intervals = []
    for (f0, s0), (f1, s1) in zip(events, events[1:]):
        if s0 != s1:
            intervals.append(f1 - f0)
    if looping and len(events) >= 2 and events[0][1] != events[-1][1]:
        intervals.append(events[0][0] + len(sample) - events[-1][0])
    footfall["step_intervals_frames"] = intervals
    if intervals:
        mean = sum(intervals) / len(intervals)
        footfall["step_interval_mean_s"] = round(mean / fps, 4)
        footfall["step_interval_cv"] = round(_std(intervals) / mean, 4) if mean else None
    else:
        footfall["step_interval_mean_s"] = None
        footfall["step_interval_cv"] = None
    if looping and strikes.get("L") and strikes.get("R") and len(sample) > 0:
        lag = (strikes["R"][0] - strikes["L"][0]) % len(sample)
        footfall["left_to_right_phase"] = round(lag / float(len(sample)), 4)
        footfall["step_asymmetry_frac"] = round(abs(2.0 * lag - len(sample)) / len(sample), 4)
    else:
        footfall["left_to_right_phase"] = None
        footfall["step_asymmetry_frac"] = None
    if not any(strikes.values()):
        footfall["says"] = "no heel strike found: this clip is not a gait"

    ranked = sorted(posture, key=lambda item: -item[1])
    legs_p50 = (_percentiles(speed["legs"]) or {}).get("p50")
    trunk_p50 = (_percentiles(speed["trunk"]) or {}).get("p50")
    return {
        "action": action.name,
        "status": STATS_STATUS,
        "thresholds": MOTION_QUALITY_THRESHOLDS,
        "trunk_to_leg_speed_ratio": (round(trunk_p50 / legs_p50, 4)
                                     if legs_p50 and trunk_p50 is not None else None),
        "fps": fps,
        "frames": [start, end],
        "looping": looping,
        "joints": len(joints),
        "angular_speed_deg_s": {k: _percentiles(v) for k, v in speed.items()},
        "angular_accel_deg_s2": {k: _percentiles(v) for k, v in accel.items()},
        "accel_peak_to_median": _percentiles([v for v in jerk_ratio.values() if v]),
        "footfall": footfall,
        "posture": {
            "joint_deviation_deg": _percentiles([v for _n, v in posture]),
            "most_varied": [[n, round(v, 3)] for n, v in ranked[:5]],
            "least_varied": [[n, round(v, 3)] for n, v in ranked[-5:]],
            "trunk_pitch_std_deg": round(_std(trunk_pitch), 4) if trunk_pitch else None,
            "trunk_roll_std_deg": round(_std(trunk_roll), 4) if trunk_roll else None,
            "head_pitch_std_deg": round(_std(head_pitch), 4) if head_pitch else None,
            "head_yaw_std_deg": round(_std(head_yaw), 4) if head_yaw else None,
            "hip_height_std_mm": round(_std(hip_h) * 1000.0, 3) if hip_h else None,
            "hip_lateral_std_mm": round(_std(hip_lat) * 1000.0, 3) if hip_lat else None,
        },
    }


def rigforge_rig_forward(rig):
    from . import rigforge_rig
    forward, how = rigforge_rig.rig_forward_axis(rig)
    forward = Vector((forward.x, forward.y, 0.0))
    if forward.length < 1e-9:
        forward = Vector((0.0, -1.0, 0.0))
    return forward.normalized(), how


def _resolve_action(params, rig):
    wanted = params.get("action")
    if isinstance(wanted, str) and wanted.strip():
        action = bpy.data.actions.get(wanted.strip())
        if action is None:
            raise ForgeError("No action called %r. The actions in this file are: %s."
                             % (wanted.strip(), ", ".join(sorted(a.name for a in
                                                                 bpy.data.actions))))
        return action
    action = rig.animation_data.action if rig.animation_data else None
    if action is None:
        raise ForgeError("%r has no action assigned and none was named." % rig.name)
    return action


@command("motion_stats")
def cmd_motion_stats(params):
    """``motion_stats {"rig"?, "action"?}`` - the motion statistics of one clip.

    Joint angular speed / acceleration distributions (DEF joints, parent frame),
    footfall timing regularity and posture variance. ``quality`` applies the
    pinned gait floors (MOTION_QUALITY_THRESHOLDS) - meaningful on a gait only.
    """
    from . import rigcheck

    started = time.monotonic()
    rig = rigcheck._resolve_rig_loose(params)
    action = _resolve_action(params, rig)
    stats = motion_statistics(rig, action)
    stats["quality"] = motion_quality(stats)
    stats["rig"] = rig.name
    stats["seconds"] = round(time.monotonic() - started, 3)
    return stats


# ---------------------------------------------------------------------------
# the driver: .bvh -> contract clip
# ---------------------------------------------------------------------------


@command("rigforge_mocap_clip")
def cmd_rigforge_mocap_clip(params):
    """.bvh in -> a gated, named action ready for the drop-in build.

    ``rigforge_mocap_clip {"target_rig", "source_path", "clip": "walk",
    "loop"?: true, "root_motion"?: "in_place", "legs"?: "ik", "mapping"?,
    "heading"?, "fps"?, "require_slots"?, "check_mode"?, "strict"?,
    "motion_quality"?: false, "foot_lock"?, "reach_limit"?, ...}``

    ``motion_quality: true`` (gait clips) runs animation_check's motion-quality
    tier and a clip under any pinned floor is not ready.

    Retargets with the production defaults (IK legs so plants survive a
    proportion change, in place because the game's controller moves the body,
    a loop window found in the take and its seam closed), then runs
    ``animation_check`` and ``motion_stats`` on the result. ``contract``
    says whether the clip is ready (slide gate ok, deformation gate ok or
    attention, seam closed on a loop) and why not. ``strict`` turns a
    not-ready clip into an error and removes the action.
    """
    from . import rigcheck
    from . import rigforge_anim

    started = time.monotonic()
    clip = get_str(params, "clip")
    loop = get_bool(params, "loop", True)
    root_motion = get_choice(params, "root_motion",
                             {"IN_PLACE": "in_place", "INPLACE": "in_place",
                              "KEEP": "keep", "ROOT": "root"}, "in_place")
    legs = get_choice(params, "legs", {"IK": "ik", "FK": "fk"}, "ik")
    strict = get_bool(params, "strict", False)
    check_mode = get_choice(params, "check_mode",
                            {"AUTO": "auto", "PLANTED": "planted", "IN_PLACE": "in_place"},
                            "in_place" if root_motion == "in_place" else "planted")
    require = params.get("require_slots")
    if require is None:
        require = list(LOCOMOTION_SLOTS)
    retarget_params = {
        "target_rig": params.get("target_rig"),
        "source_path": params.get("source_path"),
        "action_name": clip,
        "loop": loop,
        "find_loop": get_bool(params, "find_loop", loop),
        "root_motion": root_motion,
        "legs": legs,
        "mapping": params.get("mapping", "auto"),
        "heading": params.get("heading", "auto"),
        "scale": params.get("scale", "auto"),
        "require_slots": require,
        "replace": get_bool(params, "replace", True),
    }
    for key in ("fps", "loop_min_s", "loop_max_s", "loop_max_residual_deg", "foot_lock",
                "reach_limit", "loop_whole_take", "flat_window_s"):
        if params.get(key) is not None:
            retarget_params[key] = params[key]
    retarget = rigforge_anim.cmd_rigforge_retarget(retarget_params)
    action_name = retarget["action"]
    want_quality = get_bool(params, "motion_quality", False)
    check = rigcheck.cmd_animation_check({"rig": retarget["target_rig"],
                                          "action": action_name, "mode": check_mode,
                                          "motion_quality": want_quality})
    rig = bpy.data.objects[retarget["target_rig"]]
    stats = motion_statistics(rig, bpy.data.actions[action_name])

    failed = []
    if check.get("gate") != "ok":
        failed.append("foot slide gate %s (worst step %s mm)"
                      % (check.get("gate"), check.get("worst_drift_mm")))
    if check.get("deformation_gate") not in ("ok", "attention"):
        failed.append("deformation gate %s" % check.get("deformation_gate"))
    seam = check.get("loop_seam_closure") or {}
    if loop and seam.get("verdict") not in (None, "ok"):
        failed.append("loop seam %s (worst %s mm, tolerance %s mm)"
                      % (seam.get("verdict"), seam.get("worst_mm"), seam.get("tolerance_mm")))
    if want_quality and check.get("motion_quality_gate") != "ok":
        failed.append("motion quality %s" % check.get("motion_quality_gate"))
    root = retarget.get("root_motion") or {}
    contract = {
        "action": action_name,
        "loop": retarget.get("loop"),
        "frames": retarget.get("frames"),
        "frame_range": retarget.get("frame_range"),
        "fps": retarget.get("fps"),
        "root_motion": root.get("mode"),
        "natural_speed_mps": root.get("speed_mps"),
        "gate": check.get("gate"),
        "deformation_gate": check.get("deformation_gate"),
        "worst_drift_mm": check.get("worst_drift_mm"),
        "seam": {"verdict": seam.get("verdict"), "worst_mm": seam.get("worst_mm")} if seam else None,
        "motion_quality_gate": check.get("motion_quality_gate"),
        "ready": not failed,
        "failed": failed,
    }
    if strict and failed:
        doomed = bpy.data.actions.get(action_name)
        if doomed is not None:
            if rig.animation_data is not None and rig.animation_data.action is doomed:
                rig.animation_data.action = None
            doomed.use_fake_user = False
            bpy.data.actions.remove(doomed)
        raise ForgeError("The retargeted clip %r is not contract-ready and was removed: %s."
                         % (action_name, "; ".join(failed)))
    return {
        "contract": contract,
        "retarget": retarget,
        "animation_check": check,
        "motion_stats": stats,
        "seconds": round(time.monotonic() - started, 3),
    }
