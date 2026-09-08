"""Export the add-on suite's synthetic tagged biped to a .glb, headless.

The point of this file is that the mesh handed to UniRig is the *same* mesh
the Phase 3/4 tests rig, not a lookalike.  So the builders are imported from
``addon/tests/`` rather than copied: if the synthetic sculpt changes, this
follows it.  Nothing under ``addon/`` is modified or written to.

Always ``--background``; never open a window::

    "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" ^
        --background --factory-startup ^
        --python rigbridge\\export_test_mesh.py -- --output C:\\path\\biped.glb

The add-on itself is never enabled: ``build_tagged_biped`` only needs bmesh
and the vertex-group tagging, so a factory-startup Blender is enough and the
export cannot be perturbed by add-on state.
"""

import argparse
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, os.pardir))
ADDON_TESTS = os.path.join(REPO, "addon", "tests")


def parse_args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    parser = argparse.ArgumentParser(prog="export_test_mesh.py")
    parser.add_argument("--output", required=True, help=".glb to write")
    parser.add_argument("--tagged", action="store_true", default=True,
                        help="use the Phase 4 tagged biped (default)")
    parser.add_argument("--plain", dest="tagged", action="store_false",
                        help="use the Phase 3 untagged sculpt instead")
    return parser.parse_args(argv)


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def build(tagged):
    if ADDON_TESTS not in sys.path:
        sys.path.insert(0, ADDON_TESTS)

    if tagged:
        import headless_phase4 as phase4

        obj, regions = phase4.build_tagged_biped()
        # build_tagged_biped returns face indices per region; turn them into the
        # tag_* vertex groups the rest of the pipeline expects, so the exported
        # glb carries the same tagging the rig stage would see.
        for tag, faces in regions.items():
            group = obj.vertex_groups.new(name="tag_" + tag)
            vertices = set()
            for index in faces:
                vertices.update(obj.data.polygons[index].vertices)
            group.add(sorted(vertices), 1.0, "REPLACE")
        return obj

    import headless_rigforge as phase3

    return phase3.build_sculpt("Sculpt")


def main():
    args = parse_args()
    out = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    clear_scene()
    obj = build(args.tagged)

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    bpy.ops.export_scene.gltf(
        filepath=out,
        export_format="GLB",
        use_selection=True,
        export_apply=False,
        export_yup=True,
    )

    mesh = obj.data
    low = [min(v.co[i] for v in mesh.vertices) for i in range(3)]
    high = [max(v.co[i] for v in mesh.vertices) for i in range(3)]
    print("EXPORTED %s" % out)
    print("  vertices %d  faces %d" % (len(mesh.vertices), len(mesh.polygons)))
    print("  blender-local bbox (Z-up, metres) min %s max %s"
          % (["%.4f" % v for v in low], ["%.4f" % v for v in high]))
    print("  vertex groups: %s" % ", ".join(g.name for g in obj.vertex_groups))


if __name__ == "__main__":
    main()
