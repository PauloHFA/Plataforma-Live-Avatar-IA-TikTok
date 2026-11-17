extends Node
class_name LipSyncController

@export var mouth_node_path: NodePath = ^"../BodyRoot/FallbackRig/Head/Mouth"
@export var sync_offset_seconds: float = 0.0

var _spectrum: AudioEffectSpectrumAnalyzerInstance
var _mouth_open: float = 0.0
var _mouth_sprite: Sprite2D = null
var _mouth_textures: Dictionary = {}
@onready var _mouth: ColorRect = get_node_or_null(mouth_node_path)


func _ready() -> void:
	var idx := AudioServer.get_bus_index(&"Voice")
	if idx >= 0 and AudioServer.get_bus_effect_count(idx) > 0:
		_spectrum = AudioServer.get_bus_effect_instance(idx, 0) as AudioEffectSpectrumAnalyzerInstance


func configure_sprite_mouth(sprite: Sprite2D, textures: Dictionary) -> void:
	_mouth_sprite = sprite
	_mouth_textures = textures
	reset()


func update_mouth(delta: float, viseme: String = "sil") -> void:
	var amp_open := 0.0
	if _spectrum != null:
		var mag := _spectrum.get_magnitude_for_frequency_range(80.0, 1200.0).length()
		amp_open = clamp(mag * 32.0, 0.0, 1.0)

	var vis_open := 0.12
	match viseme:
		"aa", "oh":
			vis_open = 0.95
		"E", "ih", "ou":
			vis_open = 0.65
		"sil", "PP":
			vis_open = 0.10
		_:
			vis_open = 0.45

	var target := maxf(amp_open, vis_open)
	_mouth_open = lerpf(_mouth_open, target, 1.0 - exp(-20.0 * delta))

	if _mouth_sprite != null and not _mouth_textures.is_empty():
		var selected := viseme
		if selected == "sil" and amp_open > 0.20:
			if amp_open > 0.72:
				selected = "aa"
			elif amp_open > 0.42:
				selected = "E"
			else:
				selected = "ih"
		if _mouth_textures.has(selected):
			_mouth_sprite.texture = _mouth_textures[selected]
		elif _mouth_textures.has("sil"):
			_mouth_sprite.texture = _mouth_textures["sil"]
		return

	if _mouth != null:
		_mouth.scale.y = lerpf(0.25, 1.8, _mouth_open)


func reset() -> void:
	_mouth_open = 0.0
	if _mouth_sprite != null and _mouth_textures.has("sil"):
		_mouth_sprite.texture = _mouth_textures["sil"]
	if _mouth != null:
		_mouth.scale.y = 0.25
