extends Node
class_name EmotionSystem

@onready var aura: ColorRect = get_node_or_null(^"../BodyRoot/Aura")

const EMOTION_COLORS := {
	"happy": Color(0.38, 0.72, 1.0, 0.35),
	"surprised": Color(1.0, 0.80, 0.25, 0.42),
	"shy": Color(1.0, 0.45, 0.70, 0.38),
	"angry": Color(1.0, 0.30, 0.30, 0.40),
	"neutral": Color(0.55, 0.50, 0.95, 0.28),
}


func apply_emotion(emotion_name: String) -> void:
	if aura != null:
		aura.color = EMOTION_COLORS.get(emotion_name, EMOTION_COLORS["neutral"])
	var controller := get_parent()
	if controller != null and controller.has_method("set_eye_expression"):
		var eye_expr := "happy" if emotion_name == "happy" else "open"
		controller.set_eye_expression(eye_expr)
