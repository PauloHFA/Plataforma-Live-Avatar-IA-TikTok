extends CanvasLayer
class_name SafeModeOverlay


func set_active(enabled: bool, reason: String = "") -> void:
	visible = enabled
	var label := get_node_or_null(^"Panel/Message") as Label
	if label != null:
		if reason != "":
			label.text = "Já voltamos! (Modo seguro ativo)"
		else:
			label.text = "Já voltamos!"
