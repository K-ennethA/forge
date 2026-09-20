@tool
extends EditorScenePostImport
# Written by Forge (RigForge stage 7) next to werewolf-form-a.glb.
#
# Use it: select werewolf-form-a.glb in Godot's FileSystem dock, open the Import tab, set
# "Import Script" to this file and press Reimport.
#
# What it does:
#   * every animation whose name ends in "-loop" is set to loop, the rest are
#     explicitly set not to;
#   * it prints the animations it found, and warns about any this export was
#     supposed to contain but Godot did not see;
#   * meshes suffixed -col/-colonly keep Godot's own collision-shape behaviour;
#     nothing here has to do that, the suffix does it.
#   * LOD_CHAIN (manual LOD exports only) writes visibility_range_begin/end onto
#     the sibling meshes, so the chain actually SWITCHES. Godot has no "-lodN"
#     import suffix - -col, -convcol, -occ, -navmesh and friends are the whole
#     list - so without these ranges every level renders at once, on top of
#     each other. It is empty on a default export, where the only mesh shipped
#     is LOD0 and Godot's own importer (meshoptimizer) generates the levels.

const LOOP_SUFFIX := "-loop"
const EXPECTED_ACTIONS: Array[String] = ["jump", "punch.L", "punch.R", "walk-loop"]
const EXPECTED_COLLISION: Array[String] = []
const LOD_CHAIN: Array = []


func _post_import(scene: Node) -> Node:
	_apply_lod_chain(scene)
	var player := _find_player(scene)
	if player == null:
		if not EXPECTED_ACTIONS.is_empty():
			push_warning("Forge: no AnimationPlayer in werewolf-form-a.glb")
		return scene
	var found: Array[String] = []
	for library_name in player.get_animation_library_list():
		var library := player.get_animation_library(library_name)
		for animation_name in library.get_animation_list():
			var animation := library.get_animation(animation_name)
			found.append(animation_name)
			if animation_name.ends_with(LOOP_SUFFIX):
				animation.loop_mode = Animation.LOOP_LINEAR
			else:
				animation.loop_mode = Animation.LOOP_NONE
	print("Forge: werewolf-form-a.glb imported with %d animation(s): %s"
		% [found.size(), ", ".join(found)])
	for expected in EXPECTED_ACTIONS:
		if not found.has(expected):
			push_warning("Forge: expected animation '%s' is missing" % expected)
	return scene


func _apply_lod_chain(scene: Node) -> void:
	if LOD_CHAIN.is_empty():
		return
	for entry in LOD_CHAIN:
		var mesh := _find_mesh(scene, entry["mesh"])
		if mesh == null:
			push_warning("Forge: LOD mesh '%s' is not in werewolf-form-a.glb" % entry["mesh"])
			continue
		mesh.visibility_range_begin = entry["begin"]
		mesh.visibility_range_end = entry["end"]
		mesh.visibility_range_fade_mode = GeometryInstance3D.VISIBILITY_RANGE_FADE_DISABLED
	print("Forge: werewolf-form-a.glb LOD chain wired over %d mesh(es)" % LOD_CHAIN.size())


func _find_mesh(node: Node, mesh_name: String) -> MeshInstance3D:
	if node is MeshInstance3D and node.name == mesh_name:
		return node
	for child in node.get_children():
		var found := _find_mesh(child, mesh_name)
		if found != null:
			return found
	return null


func _find_player(node: Node) -> AnimationPlayer:
	if node is AnimationPlayer:
		return node
	for child in node.get_children():
		var found := _find_player(child)
		if found != null:
			return found
	return null
