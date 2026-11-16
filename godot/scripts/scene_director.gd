extends Node
class_name SceneDirector

@onready var bg_rect: ColorRect = get_node_or_null(^"../Background")


func apply_scene_command(cmd: Dictionary) -> void:
	var color_hex := String(cmd.get("color_hex", ""))
	if color_hex != "" and bg_rect != null:
		bg_rect.color = Color.html(color_hex)

	var scene_name := String(cmd.get("scene_name", ""))
	if scene_name == "neon_arcade" and bg_rect != null:
		bg_rect.color = Color(0.10, 0.04, 0.22, 1.0)
	elif scene_name == "studio_space" and bg_rect != null:
		bg_rect.color = Color(0.05, 0.08, 0.16, 1.0)
