import bpy
for o in bpy.data.objects:
    print("OBJ", o.name, o.type, o.data.name if o.data else None, len(o.data.polygons) if o.type == 'MESH' else '', [m.type for m in o.modifiers], o.parent.name if o.parent else None)
for m in bpy.data.materials:
    print("MAT", m.name)
for i in bpy.data.images:
    print("IMG", i.name, i.size[:], i.filepath)
