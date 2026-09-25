"""Run hero_facing.py on a blend whose REAL mesh is named 'Icosphere*' (read only, never saves).

hero_facing.py skips 'Icosphere*' objects (the glTF importer's bone-shape icosphere); in blend mode
that also drops a sculpt that simply kept Blender's default name (magmoo). This renames such meshes
in memory, then runs hero_facing.py unchanged with the same argv.
    blender --background <copy.blend> --factory-startup --python newunit-facing_wrap.py -- blend <out.json>
"""
import bpy, os, runpy
for o in bpy.data.objects:
    if o.type == "MESH" and o.name.startswith("Icosphere"):
        o.name = "sculpt_" + o.name
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hero_facing.py"), run_name="__main__")
