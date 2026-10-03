@tool
class_name Color1Ext
extends GLTFDocumentExtension
# Decodes glTF COLOR_1 from the raw accessor and re-adds each surface with it as ARRAY_CUSTOM0 (RGBA_FLOAT).

func _read_accessor(state: GLTFState, ai: int) -> PackedColorArray:
	var j: Dictionary = state.json
	var acc: Dictionary = j["accessors"][ai]
	var bv: Dictionary = j["bufferViews"][int(acc["bufferView"])]
	var buf: PackedByteArray = state.buffers[int(bv.get("buffer", 0))]
	var ncomp := 4 if acc["type"] == "VEC4" else 3
	var ct := int(acc["componentType"])
	var csz: int = {5126: 4, 5123: 2, 5121: 1}[ct]
	var stride := int(bv.get("byteStride", ncomp * csz))
	var base := int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
	var out := PackedColorArray()
	for i in int(acc["count"]):
		var c := [0.0, 0.0, 0.0, 1.0]
		for k in ncomp:
			var o: int = base + i * stride + k * csz
			match ct:
				5126: c[k] = buf.decode_float(o)
				5123: c[k] = buf.decode_u16(o) / 65535.0
				5121: c[k] = buf.decode_u8(o) / 255.0
		out.append(Color(c[0], c[1], c[2], c[3]))
	return out

func _import_post_parse(state: GLTFState) -> Error:
	var j: Dictionary = state.json
	var meshes := state.get_meshes()
	for mi in meshes.size():
		var prims: Array = j["meshes"][mi]["primitives"]
		var im: ImporterMesh = meshes[mi].mesh
		var saved := []
		for si in im.get_surface_count():
			var attrs: Dictionary = prims[si]["attributes"]
			var arrays := im.get_surface_arrays(si)
			var fmt := im.get_surface_format(si)
			if attrs.has("COLOR_1"):
				var cols := _read_accessor(state, int(attrs["COLOR_1"]))
				var f := PackedFloat32Array()
				for c in cols:
					f.append_array([c.r, c.g, c.b, c.a])
				if f.size() / 4 == (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size():
					arrays[Mesh.ARRAY_CUSTOM0] = f
					fmt |= Mesh.ARRAY_CUSTOM_RGBA_FLOAT << Mesh.ARRAY_FORMAT_CUSTOM0_SHIFT
					print("Color1Ext: COLOR_1 -> CUSTOM0 on mesh ", mi, " surface ", si, " (", cols.size(), " verts)")
				else:
					print("Color1Ext: vertex count mismatch, skipped")
			var bsa := []
			for b in im.get_blend_shape_count():
				bsa.append(im.get_surface_blend_shape_arrays(si, b))
			saved.append([im.get_surface_primitive_type(si), arrays, bsa, im.get_surface_material(si), im.get_surface_name(si), fmt])
		im.clear()
		for s in saved:
			im.add_surface(s[0], s[1], s[2], {}, s[3], s[4], s[5])
	return OK
