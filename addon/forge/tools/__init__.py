"""Forge command implementations.

Importing this package registers every protocol command with
:mod:`forge.tools.registry`.
"""

if "bpy" in locals():  # add-on reload (Blender keeps modules alive)
    import importlib

    registry = importlib.reload(registry)  # noqa: F821
    common = importlib.reload(common)  # noqa: F821
    curves = importlib.reload(curves)  # noqa: F821
    workspace = importlib.reload(workspace)  # noqa: F821
    diagnose = importlib.reload(diagnose)  # noqa: F821
    verify = importlib.reload(verify)  # noqa: F821
    partforge = importlib.reload(partforge)  # noqa: F821
    bosses = importlib.reload(bosses)  # noqa: F821
    seating = importlib.reload(seating)  # noqa: F821
    trackforge = importlib.reload(trackforge)  # noqa: F821
    meshopt = importlib.reload(meshopt)  # noqa: F821
    rigforge = importlib.reload(rigforge)  # noqa: F821
    rigforge_joints = importlib.reload(rigforge_joints)  # noqa: F821
    rigforge_landmarks = importlib.reload(rigforge_landmarks)  # noqa: F821
    rigforge_autotag = importlib.reload(rigforge_autotag)  # noqa: F821
    rigforge_rig = importlib.reload(rigforge_rig)  # noqa: F821
    rigforge_skin = importlib.reload(rigforge_skin)  # noqa: F821
    rigcheck = importlib.reload(rigcheck)  # noqa: F821
    rigforge_anim = importlib.reload(rigforge_anim)  # noqa: F821
    rigforge_mocap = importlib.reload(rigforge_mocap)  # noqa: F821
    mechanism = importlib.reload(mechanism)  # noqa: F821
    silhouette = importlib.reload(silhouette)  # noqa: F821
    spriteforge = importlib.reload(spriteforge)  # noqa: F821
    correctives = importlib.reload(correctives)  # noqa: F821
    floorplan = importlib.reload(floorplan)  # noqa: F821
    pov = importlib.reload(pov)  # noqa: F821
    activity = importlib.reload(activity)  # noqa: F821
    assistant = importlib.reload(assistant)  # noqa: F821
    buddy = importlib.reload(buddy)  # noqa: F821
    flows = importlib.reload(flows)  # noqa: F821
    model = importlib.reload(model)  # noqa: F821
    projects = importlib.reload(projects)  # noqa: F821
    services = importlib.reload(services)  # noqa: F821
else:
    from . import registry
    from . import common
    from . import curves
    from . import workspace
    from . import diagnose
    from . import verify
    from . import partforge
    # Reversible boss attachment (union a peg, keep the cutter). Only common +
    # registry behind it — a boolean, a hidden collection and a JSON ledger on
    # the target — so it sits next to the other "change this mesh" module rather
    # than after the things that happen to call it.
    from . import bosses
    # After bosses: the peg half of a seat is read straight out of that module's
    # ledger (and carried along when the part moves, so a seated part keeps its
    # attachments reversible). Only common + bosses + registry behind it — a
    # cylinder fit over mesh vertices and a rigid transform.
    from . import seating
    # Grab-and-track: named trackers (a JSON ledger per object) plus a solved
    # translate-with-axis-locks. Only common + registry behind it.
    from . import trackforge
    # Before rigforge: its LOD stage binds meshoptimizer through it. Registers
    # no commands and imports no bpy - a ctypes wrapper over an optional DLL.
    from . import meshopt
    from . import rigforge
    from . import rigforge_joints
    # Before rigforge_rig: the metarig's default fitting path is the human
    # rigger's workflow (orient, symmetrize, landmark one side, mirror), and it
    # lives here. Only common + rigforge behind it.
    from . import rigforge_landmarks
    # After rigforge_landmarks (it reuses the midplane, the facing gate and the
    # principal-axis solver) and before rigforge_rig (whose metarig path asks it
    # for tags): auto-tagging builds each limb's tag along that limb's own
    # detected axis, so the landmark fitter is handed a tube to slice rather
    # than a box band to refuse.
    from . import rigforge_autotag
    from . import rigforge_rig
    # After rigforge_rig (it reads that module's tag regions, its tag->bone
    # mapping and its deform-bone resolver) and before rigcheck (which reports
    # its in-limb continuity gate): tag-constrained skinning turns each tag's
    # exact per-limb vertex membership into a mask on the automatic weights, so
    # an arm bone cannot carry the chest that bone heat diffused it onto.
    from . import rigforge_skin
    from . import rigcheck
    from . import rigforge_anim
    # After rigforge_anim and rigcheck: the mocap driver calls the retarget and
    # the gates (lazily, inside its commands) and adds motion_stats.
    from . import rigforge_mocap
    # After rigforge_anim/rigforge_rig: the mechanism demos reuse their
    # slotted-action helpers (Blender 5.0 has no ``action.fcurves``).
    from . import mechanism
    # After verify (it borrows the silhouette-mask machinery, so there is one
    # implementation of "which pixels are the subject") and after mechanism (it
    # borrows the near-miss object resolver).
    from . import silhouette
    # After verify (it reads pixels through that module's image loader and wears
    # its credibility tiers, so there is one implementation of "open a picture
    # and say which pixels are the subject"). Nothing else behind it: the card
    # is numpy, bmesh and common's mesh builder.
    from . import spriteforge
    # After rigcheck (it poses the rig and regions the flesh with the harness's
    # own probe, so the corrective and the measurement agree by construction)
    # and after silhouette (same Laplacian smoother over the displacement
    # field, one implementation).
    from . import correctives
    # Phase 19's floor plans. Nothing but common + registry behind it (boxes,
    # prisms and a content hash), so it sits wherever it is convenient to read —
    # next to the other "build me a shape" module.
    from . import floorplan
    # The playtest camera: a first-person stand-in for walking a level.
    # Only common + registry behind it.
    from . import pov
    # The live activity feed.  Only registry + bpy behind it (a ring of names and
    # timestamps, and a wrapper around ``registry.dispatch`` so the feed can tell
    # "Forge built this" from "the artist edited this"), so it sits next to the
    # other always-on machinery rather than after the things it watches.
    from . import activity
    from . import assistant
    from . import buddy
    from . import flows
    from . import model
    from . import projects
    from . import services

import bpy  # noqa: E402,F401  (used by the reload guard above)

from .registry import ForgeError, command_names, dispatch, has_command  # noqa: E402

__all__ = [
    "registry",
    "common",
    "curves",
    "workspace",
    "diagnose",
    "verify",
    "partforge",
    "bosses",
    "seating",
    "trackforge",
    "meshopt",
    "rigforge",
    "rigforge_joints",
    "rigforge_landmarks",
    "rigforge_autotag",
    "rigforge_rig",
    "rigcheck",
    "rigforge_anim",
    "rigforge_mocap",
    "mechanism",
    "silhouette",
    "spriteforge",
    "correctives",
    "floorplan",
    "pov",
    "activity",
    "assistant",
    "buddy",
    "flows",
    "model",
    "projects",
    "services",
    "ForgeError",
    "dispatch",
    "command_names",
    "has_command",
]


def register():
    partforge.register()
    rigforge.register()
    rigforge_rig.register()
    # After rigforge_rig: the deformation harness's operator subclasses its
    # ``_RigOperator``, and its command reads that module's rig/mesh properties.
    rigcheck.register()
    rigforge_anim.register()
    assistant.register()
    # After the assistant: the buddy hangs its check-ins off the same chat log
    # and the same send path, so the props it writes into must already exist.
    buddy.register()
    flows.register()
    model.register()
    projects.register()
    services.register()
    # Last, and first out below: the feed's whole job is to notice scene
    # changes, and registering it after the others means the add-on's own
    # start-up churn is not the first thing it has to say.
    activity.register()


def unregister():
    activity.unregister()
    services.unregister()
    projects.unregister()
    model.unregister()
    flows.unregister()
    buddy.unregister()
    assistant.unregister()
    rigforge_anim.unregister()
    rigcheck.unregister()
    rigforge_rig.unregister()
    rigforge.unregister()
    partforge.unregister()
