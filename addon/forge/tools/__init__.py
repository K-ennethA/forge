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
    rigforge = importlib.reload(rigforge)  # noqa: F821
    rigforge_joints = importlib.reload(rigforge_joints)  # noqa: F821
    rigforge_rig = importlib.reload(rigforge_rig)  # noqa: F821
    rigcheck = importlib.reload(rigcheck)  # noqa: F821
    rigforge_anim = importlib.reload(rigforge_anim)  # noqa: F821
    mechanism = importlib.reload(mechanism)  # noqa: F821
    silhouette = importlib.reload(silhouette)  # noqa: F821
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
    from . import rigforge
    from . import rigforge_joints
    from . import rigforge_rig
    from . import rigcheck
    from . import rigforge_anim
    # After rigforge_anim/rigforge_rig: the mechanism demos reuse their
    # slotted-action helpers (Blender 5.0 has no ``action.fcurves``).
    from . import mechanism
    # After verify (it borrows the silhouette-mask machinery, so there is one
    # implementation of "which pixels are the subject") and after mechanism (it
    # borrows the near-miss object resolver).
    from . import silhouette
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
    "rigforge",
    "rigforge_joints",
    "rigforge_rig",
    "rigcheck",
    "rigforge_anim",
    "mechanism",
    "silhouette",
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
