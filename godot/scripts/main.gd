extends Node2D

@onready var net_client = $NetClient
@onready var avatar = $Avatar
@onready var gift_fx = $GiftFX
@onready var overlay_ui = $OverlayUI
@onready var safe_mode_ui = $SafeModeUI
@onready var scene_director = $SceneDirector


func _ready() -> void:
	net_client.command_received.connect(_on_command_received)
	avatar.speech_started.connect(_on_speech_started)
	avatar.speech_finished.connect(_on_speech_finished)


func _on_command_received(cmd: Dictionary) -> void:
	var kind := String(cmd.get("cmd", ""))
	match kind:
		"speak":
			if overlay_ui != null and bool(cmd.get("caption", true)):
				overlay_ui.show_caption(String(cmd.get("text", "")))
			avatar.speak_command(cmd)
		"gift_fx":
			gift_fx.trigger(cmd)
		"overlay":
			overlay_ui.handle_overlay_command(cmd)
		"safe_mode":
			var on := bool(cmd.get("on", false))
			avatar.set_safe_mode(on)
			safe_mode_ui.set_active(on, String(cmd.get("reason", "")))
		"stop_speech":
			avatar.stop_all()
		"scene":
			scene_director.apply_scene_command(cmd)


func _on_speech_started(utterance_id: String) -> void:
	net_client.send_json({"type": "speech_started", "id": utterance_id})


func _on_speech_finished(utterance_id: String) -> void:
	net_client.send_json({"type": "speech_finished", "id": utterance_id})
