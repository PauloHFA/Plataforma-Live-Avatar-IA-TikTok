extends Node
class_name NetClient

signal command_received(cmd: Dictionary)
signal connection_changed(connected: bool)

@export var url: String = "ws://127.0.0.1:8765/avatar"
@export var reconnect_delay: float = 2.0

var _ws := WebSocketPeer.new()
var _is_connected: bool = false
var _reconnect_timer: float = 0.0
var _heartbeat_timer: float = 0.0


func _ready() -> void:
	_ws.connect_to_url(url)


func _process(delta: float) -> void:
	_ws.poll()
	var state := _ws.get_ready_state()

	if state == WebSocketPeer.STATE_OPEN:
		if not _is_connected:
			_is_connected = true
			connection_changed.emit(true)

		while _ws.get_available_packet_count() > 0:
			var raw := _ws.get_packet().get_string_from_utf8()
			var parsed = JSON.parse_string(raw)
			if typeof(parsed) == TYPE_DICTIONARY:
				command_received.emit(parsed)

		_heartbeat_timer += delta
		if _heartbeat_timer >= 2.0:
			_heartbeat_timer = 0.0
			send_json({
				"type": "heartbeat",
				"fps": Engine.get_frames_per_second(),
				"memory_mb": snapped(OS.get_static_memory_usage() / 1048576.0, 0.1),
			})
	elif state == WebSocketPeer.STATE_CLOSED:
		if _is_connected:
			_is_connected = false
			connection_changed.emit(false)
		_reconnect_timer += delta
		if _reconnect_timer >= reconnect_delay:
			_reconnect_timer = 0.0
			_ws.connect_to_url(url)


func send_json(payload: Dictionary) -> void:
	if _ws.get_ready_state() == WebSocketPeer.STATE_OPEN:
		_ws.send_text(JSON.stringify(payload))
