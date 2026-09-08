"""Predicted-joint hints: the third landmark source for the metarig fitter.

Phase 4's metarig fitter (``rigforge_rig.fit_biped``) has always had two sources
of truth about where a joint is: the **tags** the artist painted, and the
**template** Rigify hands us.  This module adds a third — a *neural joint
detector's* prediction, read from a JSON file — and, more importantly, decides
how much to believe it.

Why hints and not a rigger
--------------------------
The research is unambiguous (``docs/automation-thesis.md`` build #2): the open
joint detectors are **detectors, not riggers**.  UniRig scores F1 ~0.077 on
out-of-domain skeletons, misses hands, tails and wings entirely, and — this is
the part that matters here — emits joints with **no names at all** on anything
outside its VRoid template mode.  A detector like that is a useful second
opinion about *where* a knee is and a useless opinion about *what* a knee is.

So the blend is deliberately asymmetric:

* **Tags anchor.**  Every landmark still comes from the tagged geometry first.
* **Predictions refine.**  Where a prediction lands near the tag-derived
  landmark (within ``tolerance`` of the mesh's span) the landmark is moved a
  fraction ``weight`` of the way towards it.  Half way, by default: a detector
  that is right pulls the joint into the flesh, a detector that is slightly
  wrong costs half of its error rather than all of it.
* **Tags win ties.**  A prediction that lands *beyond* tolerance but still
  within the disagreement band is recorded as a **disagreement** — with both
  positions in millimetres — and ignored.  Nothing about the fit changes, but
  the report says a second opinion existed and was overruled, which is the
  only honest way to run a gate.
* **Predictions alone are best-effort.**  Roles the tags cannot cover (fingers,
  toes) are placed straight from a *named* prediction when one exists, flagged
  ``best_effort`` in the report, and never counted as fitted.

Matching, when the detector gives no names
------------------------------------------
Two matchers run in order for every role:

1. **By name** — ``hips``/``pelvis``, ``spine``/``chest``, ``upper_arm``,
   ``forearm``/``elbow``, ``hand``/``wrist``, ``thigh``, ``shin``/``calf``,
   ``foot``/``ankle``, with a side suffix (``.L``, ``_r``, ``Left``…) that must
   agree when the prediction declares one.
2. **By position** — the nearest unclaimed prediction to the tag-derived
   landmark.  This is the path that actually runs for UniRig output.

Both are subject to the same distance bands, and a prediction is *claimed* once
used, so two adjacent spine landmarks cannot both collapse onto the same
predicted vertebra.

Frames
------
The file declares its own frame (``mm`` + ``mesh_local``/``world`` +
``axis_up``) and this module converts into the mesh's Blender world space:
millimetres to metres, glTF's Y-up to Blender's Z-up when asked, then through
``matrix_world``.  If fewer than half the converted joints land inside the
mesh's (10%-grown) bounding box the whole file is rejected rather than trusted —
a frame or unit mistake that silently shifted every joint is exactly the failure
that would poison a fit while looking like a successful one.

Pure library: no commands, no operators, stdlib + ``bpy``/``mathutils`` only.
"""

import json
import os
import re

from mathutils import Vector

from .registry import ForgeError

__all__ = [
    "JOINTS_SCHEMA",
    "DEFAULT_WEIGHT",
    "DEFAULT_TOLERANCE",
    "DEFAULT_DISAGREE",
    "ROLE_PATTERNS",
    "BEST_EFFORT_BONES",
    "JointHints",
    "load_joints",
]

#: The contract the detector runner writes (``rigbridge/detect_joints.py``).
JOINTS_SCHEMA = "forge.joints/1"

#: How far towards a *believed* prediction a tag-derived landmark moves.
#: Half way: a right detector pulls the joint in, a wrong one costs half its
#: error. 0 disables refinement without disabling the report; 1 hands the
#: landmark to the detector outright.
DEFAULT_WEIGHT = 0.5

#: Believe-it band, as a fraction of the mesh's largest dimension. A prediction
#: within this of the tagged landmark is a refinement.
DEFAULT_TOLERANCE = 0.12

#: Disagreement band, same units. Beyond ``tolerance`` and within this, the
#: prediction is *reported and overruled*; beyond this it is not considered a
#: match at all (it is presumably some other joint entirely).
DEFAULT_DISAGREE = 3.0

#: Below this fraction of joints inside the mesh box, the file is rejected as a
#: frame/unit mismatch rather than trusted.
MIN_INSIDE_FRACTION = 0.5

#: Role -> the substrings a *named* prediction may use for it. Ordered: the
#: first pattern that matches wins, so ``upper_arm`` cannot be stolen by the
#: bare ``arm`` alternative in a later group.
ROLE_PATTERNS = {
    "hips": (r"hips?$", r"pelvis", r"^root$", r"hip_?bone"),
    "spine": (r"spine", r"chest", r"torso", r"abdomen", r"waist", r"belly"),
    "neck_base": (r"neck", r"chest_?up", r"upper_?chest"),
    "neck_mid": (r"neck",),
    "neck_top": (r"neck", r"head$"),
    "head": (r"head", r"skull"),
    "head_top": (r"head_?(top|end|tip)", r"skull_?top"),
    "clavicle": (r"clavicle", r"shoulder", r"collar"),
    "shoulder": (r"upper_?arm", r"^arm", r"humerus", r"shoulder"),
    "elbow": (r"fore_?arm", r"lower_?arm", r"elbow", r"radius", r"ulna"),
    "wrist": (r"wrist", r"hand"),
    "hand": (r"hand", r"wrist"),
    "hip": (r"thigh", r"upper_?leg", r"femur", r"hip"),
    "knee": (r"shin", r"calf", r"lower_?leg", r"knee", r"tibia"),
    "ankle": (r"ankle", r"foot"),
    "foot": (r"foot", r"ankle"),
    "toe": (r"toe", r"ball$"),
    "finger": (r"finger", r"thumb", r"index", r"middle", r"ring", r"pinky", r"little"),
}

#: Metarig bones the tag fitter never places, that a *named* prediction may.
#: These are the "roles tags can't cover" of the contract — everything past the
#: wrist and the ankle. Name-matched only: guessing a finger by position out of
#: an unnamed point cloud would be noise wearing a report's clothes.
BEST_EFFORT_BONES = (
    ("hand.%s", "hand"),
    ("foot.%s", "foot"),
    ("toe.%s", "toe"),
    ("f_index.01.%s", "finger"),
    ("f_middle.01.%s", "finger"),
    ("f_ring.01.%s", "finger"),
    ("f_pinky.01.%s", "finger"),
    ("thumb.01.%s", "finger"),
)

_SIDE_RE = re.compile(r"(?:^|[._\- ])(l|r|left|right)(?:$|[._\- 0-9])", re.IGNORECASE)

#: Names that are not names.  UniRig's autoregressive decoder emits joints in
#: order and the runner labels them ``bone_0``, ``bone_1`` …; treating those as
#: semantic names would be worse than useless, because the positional matcher
#: deliberately refuses to touch a joint whose name says it is some *other*
#: role.  A placeholder is therefore read as **unnamed**, which is the truth.
_PLACEHOLDER_RE = re.compile(r"^(bone|joint|node|jnt|j|b)?[_.\- ]?\d+$", re.IGNORECASE)


def _side_of(name):
    """``"L"`` / ``"R"`` / ``None`` for a predicted joint's name."""
    if not name:
        return None
    match = _SIDE_RE.search(name)
    if match is None:
        return None
    return "L" if match.group(1).lower().startswith("l") else "R"


def _split_role(role):
    """``"elbow.L"`` -> ``("elbow", "L")``; ``"hips"`` -> ``("hips", None)``."""
    if "." in role:
        base, _dot, side = role.rpartition(".")
        side = side.upper()
        if side in ("L", "R"):
            return base, side
    return role, None


def _to_vector(value, what):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ForgeError("Joints file: %s must be [x, y, z], got %r." % (what, value))
    try:
        return Vector((float(value[0]), float(value[1]), float(value[2])))
    except (TypeError, ValueError):
        raise ForgeError("Joints file: %s has a non-numeric component: %r." % (what, value))


def load_joints(path):
    """Read and structurally validate a ``forge.joints/1`` file."""
    if not os.path.isfile(path):
        raise ForgeError("No joints file at %s. Run rigbridge/detect_joints.py on the "
                         "mesh first, or drop the joints_file parameter." % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ForgeError("Could not read the joints file %s: %s" % (path, exc))
    if not isinstance(data, dict):
        raise ForgeError("The joints file %s is not a JSON object." % path)
    schema = str(data.get("schema") or "")
    if schema and schema.split("/")[0] != JOINTS_SCHEMA.split("/")[0]:
        raise ForgeError("The joints file %s declares schema %r; this add-on reads %r."
                         % (path, schema, JOINTS_SCHEMA))
    joints = data.get("joints")
    if not isinstance(joints, list) or not joints:
        raise ForgeError("The joints file %s has no 'joints' array." % path)
    return data


class JointHints(object):
    """A detector's joints, converted into the mesh's world space and rationed.

    Construct one per fit; call :meth:`refine` for every landmark the tag fitter
    computes; read :meth:`report` afterwards.
    """

    def __init__(self, data, obj, weight=DEFAULT_WEIGHT, tolerance=DEFAULT_TOLERANCE,
                 disagree=DEFAULT_DISAGREE, axis_up=None, path="", warnings=None):
        self.path = path
        self.source = str(data.get("source") or "unknown")
        self.detector = data.get("detector") if isinstance(data.get("detector"), dict) else {}
        self.notes = [str(n) for n in (data.get("notes") or []) if isinstance(n, str)]
        self.weight = max(0.0, min(1.0, float(weight)))
        self.tolerance = max(0.0, float(tolerance))
        self.disagree = max(1.0, float(disagree))
        self.enabled = True

        warnings = warnings if warnings is not None else []
        frame = data.get("frame") if isinstance(data.get("frame"), dict) else {}
        unit = str(frame.get("unit") or "mm").lower()
        space = str(frame.get("space") or "mesh_local").lower()
        self.axis_up = str(axis_up or frame.get("axis_up") or "Z").upper()
        if unit not in ("mm", "m"):
            raise ForgeError("Joints file %s declares unit %r; only 'mm' and 'm' are "
                             "understood." % (path, unit))
        if self.axis_up not in ("Y", "Z"):
            raise ForgeError("Joints file %s declares axis_up %r; only 'Y' and 'Z' are "
                             "understood." % (path, self.axis_up))
        self.unit = unit
        self.space = space
        scale = 0.001 if unit == "mm" else 1.0

        matrix = obj.matrix_world
        self.placeholders = 0
        self.names = []
        self.points = []
        self.parents = []
        self.confidence = []
        for index, entry in enumerate(data["joints"]):
            if not isinstance(entry, dict):
                raise ForgeError("Joints file %s: joint %d is not an object." % (path, index))
            head = _to_vector(entry.get("head_mm"), "joint %d head_mm" % index)
            point = head * scale
            if self.axis_up == "Y":
                point = Vector((point.x, -point.z, point.y))
            if space != "world":
                point = matrix @ point
            self.points.append(point)
            name = entry.get("name")
            name = str(name).strip() if isinstance(name, str) and name.strip() else None
            if name is not None and _PLACEHOLDER_RE.match(name):
                self.placeholders += 1
                name = None
            self.names.append(name)
            parent = entry.get("parent")
            self.parents.append(int(parent) if isinstance(parent, int) else None)
            conf = entry.get("confidence")
            self.confidence.append(float(conf) if isinstance(conf, (int, float)) else None)

        self.named = sum(1 for name in self.names if name)
        self.count = len(self.points)

        # --- the frame sanity gate
        corners = [matrix @ Vector(corner) for corner in obj.bound_box]
        low = Vector((min(c.x for c in corners), min(c.y for c in corners),
                      min(c.z for c in corners)))
        high = Vector((max(c.x for c in corners), max(c.y for c in corners),
                       max(c.z for c in corners)))
        span = max(max(high - low), 1e-6)
        grow = span * 0.10
        inside = 0
        for point in self.points:
            if all(low[i] - grow <= point[i] <= high[i] + grow for i in range(3)):
                inside += 1
        self.span = span
        self.inside = inside
        self.inside_fraction = inside / float(self.count) if self.count else 0.0
        if self.inside_fraction < MIN_INSIDE_FRACTION:
            self.enabled = False
            warnings.append(
                "The joints file %s was ignored: only %d of its %d joints (%.0f%%) land "
                "inside %r's bounding box, which means its frame or its units do not "
                "match this mesh (it declares %s, %s, axis_up=%s). Tags alone were used."
                % (os.path.basename(path) or path, inside, self.count,
                   100.0 * self.inside_fraction, obj.name, unit, space, self.axis_up))

        # How far it is from each prediction to its nearest neighbour. Half of
        # that is the prediction's own territory: a landmark further away than
        # that is closer to the *next* joint's patch than to this one's, and a
        # positional match at that range is a guess about which joint it is.
        self.neighbour = []
        for index, point in enumerate(self.points):
            best = None
            for other, candidate in enumerate(self.points):
                if other == index:
                    continue
                distance = (candidate - point).length
                if best is None or distance < best:
                    best = distance
            self.neighbour.append(best if best is not None else float("inf"))

        self.claimed = {}          # prediction index -> role that took it
        self.refinements = []      # roles that moved
        self.disagreements = []    # roles where the tags overruled a prediction
        self.best_effort = []      # bones placed from predictions alone
        self.considered = []       # every role asked about, matched or not
        self._pending = []         # near-misses, judged once the fit is finished

    # -- matching ----------------------------------------------------------

    def _name_candidates(self, base, side):
        # ``spine_02`` is the second landmark of the ``spine`` role, not a role
        # of its own: numbered variants fall back to their un-numbered parent.
        patterns = ROLE_PATTERNS.get(base)
        if patterns is None:
            patterns = ROLE_PATTERNS.get(re.sub(r"[_.]?\d+$", "", base), ())
        if not patterns:
            return []
        out = []
        for index, name in enumerate(self.names):
            if not name:
                continue
            lowered = name.lower()
            rank = None
            for order, pattern in enumerate(patterns):
                if re.search(pattern, lowered):
                    rank = order
                    break
            if rank is None:
                continue
            joint_side = _side_of(name)
            if side is not None and joint_side is not None and joint_side != side:
                continue
            if side is None and joint_side is not None and base in (
                    "hips", "spine", "neck_base", "neck_top", "head", "head_top"):
                continue  # a centre-line role never takes a sided joint
            out.append((rank, index))
        return out

    def _nearest(self, indices, point, allow_claimed=False):
        best = None
        best_distance = None
        for index in indices:
            if not allow_claimed and index in self.claimed:
                continue
            distance = (self.points[index] - point).length
            if best_distance is None or distance < best_distance:
                best, best_distance = index, distance
        return best, best_distance

    def match(self, role, point):
        """``(index, distance, how)`` for the prediction that best fits ``role``.

        ``how`` is ``"name"`` or ``"position"``.  Returns ``(None, None, None)``
        when nothing is close enough to be worth an opinion.

        The positional pass deliberately ignores predictions whose *name* says
        they are some other joint: in a named file the names are the evidence,
        and a wrist landmark must not swallow the joint labelled ``index_01_L``
        just because it is the nearest thing to hand.
        """
        if not self.enabled or not self.count:
            return None, None, None
        base, side = _split_role(role)
        named = self._name_candidates(base, side)
        if named:
            named.sort()
            top_rank = named[0][0]
            indices = [index for rank, index in named if rank == top_rank]
            index, distance = self._nearest(indices, point)
            if index is None:  # every name match already claimed
                index, distance = self._nearest(indices, point, allow_claimed=True)
            if index is not None:
                return index, distance, "name"
        mine = {index for _rank, index in named}
        positional = [index for index in range(self.count)
                      if self.names[index] is None or index in mine]
        index, distance = self._nearest(positional, point)
        if index is None:
            return None, None, None
        return index, distance, "position"

    # -- the blend ---------------------------------------------------------

    def refine(self, role, point, weight=None):
        """The landmark to actually use for ``role``, tags blended with a hint.

        Always returns a point: with no file, no match, or a disagreement, that
        point is the tag-derived one it was handed.
        """
        point = Vector(point)
        if not self.enabled:
            return point
        index, distance, how = self.match(role, point)
        entry = {"role": role, "matched": bool(index is not None)}
        if index is None:
            self.considered.append(entry)
            return point
        near = self.tolerance * self.span
        far = near * self.disagree
        if how == "position":
            # ... and inside the prediction's own territory (see `neighbour`).
            near = min(near, 0.5 * self.neighbour[index])
        predicted = self.points[index]
        entry.update({
            "joint": index,
            "joint_name": self.names[index],
            "matched_by": how,
            "distance_mm": round(distance * 1000.0, 2),
            "tolerance_mm": round(near * 1000.0, 2),
        })
        if distance > far:
            entry["outcome"] = "no_match"
            self.considered.append(entry)
            return point
        if distance > near:
            # Not a disagreement *yet*: a landmark with no prediction of its own
            # always has some neighbour's joint as its nearest, and calling that
            # a disagreement would fill the report with noise. It becomes one
            # only if no other role claims that prediction (see `finalize`), or
            # if the prediction's own name says it was this role's.
            entry["outcome"] = "pending"
            entry["tag_mm"] = [round(v * 1000.0, 2) for v in point]
            entry["predicted_mm"] = [round(v * 1000.0, 2) for v in predicted]
            self.considered.append(entry)
            self._pending.append(entry)
            return point
        factor = self.weight if weight is None else max(0.0, min(1.0, float(weight)))
        blended = point.lerp(predicted, factor)
        entry.update({
            "outcome": "refined",
            "weight": round(factor, 3),
            "tag_mm": [round(v * 1000.0, 2) for v in point],
            "predicted_mm": [round(v * 1000.0, 2) for v in predicted],
            "used_mm": [round(v * 1000.0, 2) for v in blended],
            "moved_mm": round((blended - point).length * 1000.0, 2),
        })
        self.considered.append(entry)
        self.refinements.append(entry)
        self.claimed[index] = role
        return blended

    def named_only(self, role, point, band=None):
        """A *named* prediction for ``role`` near ``point``, or ``None``.

        The best-effort path: no tag ever covered these, so there is nothing to
        blend with and nothing to overrule — either the detector named a finger
        or this bone keeps the template's guess.
        """
        if not self.enabled or not self.named:
            return None
        base, side = _split_role(role)
        candidates = self._name_candidates(base, side)
        if not candidates:
            return None
        candidates.sort()
        top_rank = candidates[0][0]
        indices = [index for rank, index in candidates if rank == top_rank]
        index, distance = self._nearest(indices, point)
        if index is None:
            return None
        limit = self.span * (self.tolerance * self.disagree if band is None else band)
        if distance > limit:
            return None
        self.claimed[index] = role
        return index, self.points[index], distance

    def record_best_effort(self, bone, role, index, distance, moved_mm):
        self.best_effort.append({
            "bone": bone,
            "role": role,
            "joint": index,
            "joint_name": self.names[index],
            "predicted_mm": [round(v * 1000.0, 2) for v in self.points[index]],
            "distance_mm": round(distance * 1000.0, 2),
            "moved_mm": round(moved_mm * 1000.0, 2),
        })

    # -- the report --------------------------------------------------------

    def finalize(self):
        """Decide which near-misses were real disagreements. Idempotent.

        A near-miss is a disagreement when the prediction it was measured
        against belongs to nobody else — the detector put a joint near this
        landmark, no role wanted it, and it is too far away to use.  When
        another role did claim it, the near-miss is just this landmark being
        next to that joint, and it is recorded as ``explained_elsewhere``
        instead of being reported as a conflict that is not one.
        """
        self.disagreements = []
        for entry in self._pending:
            index = entry.get("joint")
            if entry.get("matched_by") == "name" or index not in self.claimed:
                entry["outcome"] = "disagreement"
                self.disagreements.append(entry)
            else:
                entry["outcome"] = "explained_elsewhere"
                entry["claimed_by"] = self.claimed[index]
        return self.disagreements

    def report(self):
        self.finalize()
        return {
            "file": self.path,
            "source": self.source,
            "detector": self.detector,
            "enabled": self.enabled,
            "joints": self.count,
            "named_joints": self.named,
            "placeholder_names": self.placeholders,
            "frame": {"unit": self.unit, "space": self.space, "axis_up": self.axis_up},
            "inside_bbox": self.inside,
            "inside_fraction": round(self.inside_fraction, 3),
            "weight": round(self.weight, 3),
            "tolerance": round(self.tolerance, 4),
            "tolerance_mm": round(self.tolerance * self.span * 1000.0, 2),
            "disagree_band_mm": round(self.tolerance * self.disagree * self.span * 1000.0, 2),
            "refined": self.refinements,
            "disagreements": self.disagreements,
            "best_effort": self.best_effort,
            "considered": self.considered,
            "unused_joints": sorted(set(range(self.count)) - set(self.claimed)),
            "notes": self.notes,
        }

    def summary_warnings(self):
        """The lines the artist should read even if they never open the report."""
        out = []
        if not self.enabled:
            return out
        self.finalize()
        if self.disagreements:
            worst = max(self.disagreements, key=lambda e: e["distance_mm"])
            out.append(
                "The joint detector disagreed with the tags at %d landmark(s) "
                "(worst: %s, %.1f mm away, tolerance %.1f mm). The tags won every one of "
                "them; see joints.disagreements for both positions."
                % (len(self.disagreements), worst["role"], worst["distance_mm"],
                   worst["tolerance_mm"]))
        if self.refinements:
            moved = max(entry["moved_mm"] for entry in self.refinements)
            out.append(
                "%d landmark(s) were refined by the %s prediction (largest move %.1f mm "
                "at weight %.2f)." % (len(self.refinements), self.source, moved, self.weight))
        if self.count and not self.refinements and not self.disagreements:
            out.append(
                "The joints file was read (%d joints) but nothing matched a landmark "
                "closely enough to use. The fit is the tag-only fit." % self.count)
        return out


def sanity_axis_guess(data, obj):
    """Which ``axis_up`` makes a file's joints land inside ``obj``. Diagnostic only.

    Not used by the fit — a file that has to be *guessed* at is a file whose
    producer should be fixed — but useful in an error message when the declared
    frame was rejected.
    """
    matrix = obj.matrix_world
    corners = [matrix @ Vector(corner) for corner in obj.bound_box]
    low = Vector((min(c.x for c in corners), min(c.y for c in corners),
                  min(c.z for c in corners)))
    high = Vector((max(c.x for c in corners), max(c.y for c in corners),
                   max(c.z for c in corners)))
    best = None
    for axis in ("Z", "Y"):
        for unit, scale in (("mm", 0.001), ("m", 1.0)):
            inside = 0
            total = 0
            for entry in data.get("joints") or ():
                head = entry.get("head_mm")
                if not isinstance(head, (list, tuple)) or len(head) != 3:
                    continue
                total += 1
                point = Vector((float(head[0]), float(head[1]), float(head[2]))) * scale
                if axis == "Y":
                    point = Vector((point.x, -point.z, point.y))
                point = matrix @ point
                if all(low[i] <= point[i] <= high[i] for i in range(3)):
                    inside += 1
            if total and (best is None or inside > best[0]):
                best = (inside, total, axis, unit)
    if best is None:
        return None
    inside, total, axis, unit = best
    if not inside:
        return None
    return {"axis_up": axis, "unit": unit, "inside": inside, "of": total}
