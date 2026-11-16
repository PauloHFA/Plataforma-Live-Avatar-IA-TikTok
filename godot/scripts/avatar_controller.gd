extends Node2D
class_name AvatarController

signal speech_started(utterance_id: String)
signal speech_finished(utterance_id: String)

enum State { IDLE, SPEAKING, REACTING, SAFE_MODE }

@export var breath_speed: float = 2.0
@export var blink_interval: float = 3.8

var current_state: State = State.IDLE
var _time: float = 0.0
var _blink_timer: float = 0.0
var _current_id: String = ""
var _viseme_frames: Array = []
var _speech_elapsed: float = 0.0

@onready var body_root: Node2D = $BodyRoot
@onready var left_eye: ColorRect = $BodyRoot/Head/LeftEye
@onready var right_eye: ColorRect = $BodyRoot/Head/RightEye
@onready var audio_player: AudioStreamPlayer = $VoicePlayer
@onready var lipsync = $LipSync
@onready var emotion_system = $EmotionSystem


func _ready() -> void:
	audio_player.bus = &"Voice"
	audio_player.finished.connect(_on_audio_finished)


func _process(delta: float) -> void:
	_time += delta
	# Respiração procedural suave
	if body_root != null:
		body_root.position.y = sin(_time * breath_speed) * 6.0

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
