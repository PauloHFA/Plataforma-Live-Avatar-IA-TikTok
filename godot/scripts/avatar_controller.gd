extends Node2D
class_name AvatarController

signal speech_started(utterance_id: String)
signal speech_finished(utterance_id: String)

enum State { IDLE, SPEAKING, REACTING, SAFE_MODE }

@export var breath_speed: float = 2.0
@export var blink_interval: float = 3.8
@export var sprite_base_dir: String = "res://assets/avatar"

var current_state: State = State.IDLE
var _time: float = 0.0
var _blink_timer: float = 0.0
var _current_id: String = ""
var _viseme_frames: Array = []
var _speech_elapsed: float = 0.0
var _using_sprites: bool = false
var _is_blinking: bool = false
var _eye_expression: String = "open"

var _eye_textures: Dictionary = {}
var _mouth_textures: Dictionary = {}

@onready var body_root: Node2D = $BodyRoot
@onready var sprite_rig: Node2D = $BodyRoot/SpriteRig
@onready var back_hair_sprite: Sprite2D = $BodyRoot/SpriteRig/BackHairSprite
@onready var body_sprite: Sprite2D = $BodyRoot/SpriteRig/BodySprite
@onready var head_pivot: Node2D = $BodyRoot/SpriteRig/HeadPivot
@onready var head_sprite: Sprite2D = $BodyRoot/SpriteRig/HeadPivot/HeadSprite
@onready var eyes_sprite: Sprite2D = $BodyRoot/SpriteRig/HeadPivot/EyesSprite
@onready var mouth_sprite: Sprite2D = $BodyRoot/SpriteRig/HeadPivot/MouthSprite
@onready var fallback_rig: Node2D = $BodyRoot/FallbackRig
@onready var left_eye: ColorRect = $BodyRoot/FallbackRig/Head/LeftEye
@onready var right_eye: ColorRect = $BodyRoot/FallbackRig/Head/RightEye
@onready var audio_player: AudioStreamPlayer = $VoicePlayer
@onready var lipsync = $LipSync
@onready var emotion_system = $EmotionSystem


func _ready() -> void:
	audio_player.bus = &"Voice"
	audio_player.finished.connect(_on_audio_finished)
	_load_sprite_layers()


func _load_layer_texture(filename: String) -> Texture2D:
	var res_path := "%s/%s" % [sprite_base_dir, filename]
	if ResourceLoader.exists(res_path):
		var loaded := load(res_path)
		if loaded is Texture2D:
			return loaded
	var abs_path := ProjectSettings.globalize_path(res_path)
	if FileAccess.file_exists(abs_path):
		var img := Image.load_from_file(abs_path)
		if img != null and not img.is_empty():
			return ImageTexture.create_from_image(img)
	return null


func _load_sprite_layers() -> void:
	var body_tex := _load_layer_texture("body.png")
	var head_tex := _load_layer_texture("head.png")
	if body_tex == null or head_tex == null:
		_using_sprites = false
		if sprite_rig != null:
			sprite_rig.visible = false
		if fallback_rig != null:
			fallback_rig.visible = true
		return

	var back_hair_tex := _load_layer_texture("back_hair.png")
	if back_hair_sprite != null:
		back_hair_sprite.texture = back_hair_tex
	if body_sprite != null:
		body_sprite.texture = body_tex
	if head_sprite != null:
		head_sprite.texture = head_tex

	for key in ["open", "closed", "happy"]:
		var tex := _load_layer_texture("eyes_%s.png" % key)
		if tex != null:
			_eye_textures[key] = tex

	var mouth_map := {
		"sil": "mouth_sil.png",
		"PP": "mouth_sil.png",
		"aa": "mouth_aa.png",
		"E": "mouth_ee.png",
		"ih": "mouth_ih.png",
		"oh": "mouth_oh.png",
		"ou": "mouth_ou.png",
	}
	for viseme in mouth_map.keys():
		var mtex := _load_layer_texture(String(mouth_map[viseme]))
		if mtex != null:
			_mouth_textures[viseme] = mtex

	_using_sprites = true
	if sprite_rig != null:
		sprite_rig.visible = true
	if fallback_rig != null:
		fallback_rig.visible = false
	_apply_eye_texture()
	if lipsync != null and lipsync.has_method("configure_sprite_mouth"):
		lipsync.configure_sprite_mouth(mouth_sprite, _mouth_textures)


func _apply_eye_texture() -> void:
	if not _using_sprites or eyes_sprite == null:
		return
	var target_key := "closed" if _is_blinking else _eye_expression
	if _eye_textures.has(target_key):
		eyes_sprite.texture = _eye_textures[target_key]
	elif _eye_textures.has("open"):
		eyes_sprite.texture = _eye_textures["open"]


func set_eye_expression(expr: String) -> void:
	_eye_expression = expr if _eye_textures.has(expr) else "open"
	_apply_eye_texture()


func _process(delta: float) -> void:
	_time += delta
	# Respiração procedural suave
	if body_root != null:
		body_root.position.y = sin(_time * breath_speed) * 6.0

	if _using_sprites and head_pivot != null:
		var sway_amp := 0.032 if current_state == State.SPEAKING else 0.012
		head_pivot.rotation = sin(_time * (breath_speed * 0.9)) * sway_amp
		head_pivot.position.y = sin(_time * (breath_speed * 1.8)) * (2.5 if current_state == State.SPEAKING else 1.0)

	# Piscar procedural
	_blink_timer += delta
	if _blink_timer >= blink_interval:
		_blink_timer = 0.0
		_blink()

	if current_state == State.SPEAKING:
		_speech_elapsed += delta
		var viseme := _lookup_viseme(_speech_elapsed)
		if lipsync != null:
			lipsync.update_mouth(delta, viseme)


func _blink() -> void:
	if _using_sprites:
		if not _eye_textures.has("closed"):
			return
		_is_blinking = true
		_apply_eye_texture()
		await get_tree().create_timer(0.11).timeout
		if is_inside_tree():
			_is_blinking = false
			_apply_eye_texture()
		return

	if left_eye == null or right_eye == null:
		return
	left_eye.scale.y = 0.15
	right_eye.scale.y = 0.15
	await get_tree().create_timer(0.11).timeout
	if is_inside_tree():
		left_eye.scale.y = 1.0
		right_eye.scale.y = 1.0


func _lookup_viseme(t: float) -> String:
	var active := "sil"
	for frame in _viseme_frames:
		if typeof(frame) == TYPE_DICTIONARY and float(frame.get("t", 0.0)) <= t:
			active = String(frame.get("v", "sil"))
	return active


func speak_command(cmd: Dictionary) -> void:
	if current_state == State.SAFE_MODE:
		return
	if current_state == State.SPEAKING and not bool(cmd.get("interruptible", true)):
		stop_all()
	_current_id = String(cmd.get("id", ""))
	_viseme_frames = cmd.get("visemes", [])
	_speech_elapsed = 0.0

	if emotion_system != null:
		emotion_system.apply_emotion(String(cmd.get("emotion", "neutral")))

	var audio_path := String(cmd.get("audio_path", ""))
	if audio_path != "" and FileAccess.file_exists(audio_path):
		var stream := AudioStreamWAV.load_from_file(audio_path)
		if stream != null:
			audio_player.stream = stream
			audio_player.play()

	current_state = State.SPEAKING
	speech_started.emit(_current_id)


func stop_all() -> void:
	audio_player.stop()
	_viseme_frames = []
	if lipsync != null:
		lipsync.reset()
	if _current_id != "":
		var done_id := _current_id
		_current_id = ""
		speech_finished.emit(done_id)


func set_safe_mode(enabled: bool) -> void:
	if enabled:
		stop_all()
		current_state = State.SAFE_MODE
	else:
		current_state = State.IDLE


func _on_audio_finished() -> void:
	var done_id := _current_id
	_current_id = ""
	_viseme_frames = []
	if lipsync != null:
		lipsync.reset()
	if current_state != State.SAFE_MODE:
		current_state = State.IDLE
	if done_id != "":
		speech_finished.emit(done_id)
