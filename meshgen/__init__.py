"""Forge meshgen - image-to-3D behind one stable local API (port 8902).

The model is a plug, not a dependency: adapters in ``meshgen/backends/`` expose
``info()`` / ``ensure_ready()`` / ``generate()``, and swapping which one runs is
an env var (``FORGE_MESHGEN_BACKEND``) or one line of ``meshgen/config.json``.

Meshgen generates; the rest of Forge finishes.  Its output .glb goes on into
the existing pipeline - Blender import, voxel repair, /check_mesh, then
/segment_mesh or retopo.

Run it with:  python -m meshgen
"""

__version__ = "0.1.0"
