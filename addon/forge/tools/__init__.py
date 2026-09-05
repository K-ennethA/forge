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
else:
    from . import registry
    from . import common
    from . import partforge
    from . import rigforge

import bpy  # noqa: E402,F401  (used by the reload guard above)

from .registry import ForgeError, command_names, dispatch, has_command  # noqa: E402

__all__ = [
    "registry",
    "common",
    "partforge",
    "rigforge",
    "ForgeError",
    "dispatch",
    "command_names",
    "has_command",
]


def register():
    partforge.register()
    rigforge.register()


def unregister():
    rigforge.unregister()
    partforge.unregister()
