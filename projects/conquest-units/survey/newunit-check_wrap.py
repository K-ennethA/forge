"""Run improve/conquest_contract_check.py on a rigged blend whose armature has NO animation_data
(read only, never saves). The checker does `rig.animation_data.action = None`, which raises on an
armature that was never animated (supaoctto_rig). This creates empty animation_data in memory, then
runs the checker unchanged with the same argv.
    blender --background <copy.blend> --factory-startup --python newunit-check_wrap.py -- <out.json>
"""
import bpy, os, runpy
for o in bpy.data.objects:
    if o.type == "ARMATURE" and o.animation_data is None:
        o.animation_data_create()
HERE = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.normpath(os.path.join(HERE, "..", "improve", "conquest_contract_check.py")), run_name="__main__")
