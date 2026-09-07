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
    partforge = importlib.reload(partforge)  # noqa: F821
    rigforge = importlib.reload(rigforge)  # noqa: F821
    rigforge_rig = importlib.reload(rigforge_rig)  # noqa: F821
    rigforge_anim = importlib.reload(rigforge_anim)  # noqa: F821
    assistant = importlib.reload(assistant)  # noqa: F821
    buddy = importlib.reload(buddy)  # noqa: F821
    flows = importlib.reload(flows)  # noqa: F821
    model = importlib.reload(model)  # noqa: F821
    services = importlib.reload(services)  # noqa: F821
else:
    from . import registry
    from . import common
    from . import curves
    from . import workspace
    from . import diagnose
    from . import partforge
    from . import rigforge
    from . import rigforge_rig
    from . import rigforge_anim
    from . import assistant
    from . import buddy
    from . import flows
    from . import model
    from . import services

import bpy  # noqa: E402,F401  (used by the reload guard above)

from .registry import ForgeError, command_names, dispatch, has_command  # noqa: E402

__all__ = [
    "registry",
    "common",
    "curves",
    "workspace",
    "diagnose",
    "partforge",
    "rigforge",
    "rigforge_rig",
    "rigforge_anim",
    "assistant",
    "buddy",
    "flows",
    "model",
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
    rigforge_anim.register()
    assistant.register()
    # After the assistant: the buddy hangs its check-ins off the same chat log
    # and the same send path, so the props it writes into must already exist.
    buddy.register()
    flows.register()
    model.register()
    services.register()


def unregister():
    services.unregister()
    model.unregister()
    flows.unregister()
    buddy.unregister()
    assistant.unregister()
    rigforge_anim.unregister()
    rigforge_rig.unregister()
    rigforge.unregister()
    partforge.unregister()
