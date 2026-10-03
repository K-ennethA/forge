@tool
extends EditorPlugin
const COLOR1_EXT: Script = preload("res://addons/color1/color1_ext.gd")
var ext: GLTFDocumentExtension
func _enter_tree() -> void:
	ext = COLOR1_EXT.new()
	GLTFDocument.register_gltf_document_extension(ext, true)
func _exit_tree() -> void:
	GLTFDocument.unregister_gltf_document_extension(ext)
