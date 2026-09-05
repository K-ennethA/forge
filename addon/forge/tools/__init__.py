"""Forge command implementations.

Importing this package registers every protocol command with
:mod:`forge.tools.registry`.
"""

if "bpy" in locals():  # add-on reload (Blender keeps modules alive)
    import importlib

    registry = importlib.reload(registry)  # noqa: F821
    common = importlib.reload(common)  # noqa: F821
    partforge = importlib.reload(partforge)  # noqa: F821
    rigforge = importlib.reload(rigforge)  # noqa: F821
    rigforge_rig = importlib.reload(rigforge_rig)  # noqa: F821
    rigforge_anim = importlib.reload(rigforge_anim)  # noqa: F821
    assistant = importlib.reload(assistant)  # noqa: F821
    flows = importlib.reload(flows)  # noqa: F821
else:
    from . import registry
    from . import common
    from . import partforge
    from . import rigforge
    from . import rigforge_rig
    from . import rigforge_anim
    from . import assistant
    from . import flows

import bpy  # noqa: E402,F401  (used by the reload guard above)

from .registry import ForgeError, command_names, dispatch, has_command  # noqa: E402

__all__ = [
    "registry",
    "common",
    "partforge",
    "rigforge",
    "rigforge_rig",
    "rigforge_anim",
    "assistant",
    "flows",
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
    flows.register()


def unregister():
    flows.unregister()
    assistant.unregister()
    rigforge_anim.unregister()
    rigforge_rig.unregister()
    rigforge.unregister()
    partforge.unregister()
