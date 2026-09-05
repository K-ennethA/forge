"""Forge user interface (sidebar panels)."""

if "bpy" in locals():  # add-on reload
    import importlib

    panels = importlib.reload(panels)  # noqa: F821
else:
    from . import panels

import bpy  # noqa: E402,F401  (used by the reload guard above)


def register():
    panels.register()


def unregister():
    panels.unregister()


__all__ = ["panels", "register", "unregister"]
