extends CanvasLayer
class_name OverlayUI

@onready var ai_badge: Label = $TopBar/AIBadge
@onready var goal_label: Label = $TopBar/GoalLabel
@onready var donors_label: Label = $SidePanel/TopDonorsLabel
@onready var gifts_label: Label = $SidePanel/RecentGiftsLabel
@onready var caption_label: Label = $BottomCaption/CaptionLabel


func _ready() -> void:
	if ai_badge != null:
		ai_badge.text = "Avatar de IA (Em tempo real)"


func show_caption(text: String) -> void:
	if caption_label != null:
		caption_label.text = text


func handle_overlay_command(cmd: Dictionary) -> void:
	var kind := String(cmd.get("type", ""))
	var data = cmd.get("data")

	match kind:
		"ai_badge":
			if ai_badge != null and typeof(data) == TYPE_DICTIONARY:
				ai_badge.text = String(data.get("label", "Avatar de IA"))
				ai_badge.visible = bool(data.get("visible", true))
		"goal":
			if goal_label != null and typeof(data) == TYPE_DICTIONARY:
				var title := String(data.get("title", "Meta"))
				var current := int(data.get("current", 0))
				var target := int(data.get("target", 5000))
				goal_label.text = "%s: %d / %d" % [title, current, target]
		"top_donors":
			if donors_label != null and typeof(data) == TYPE_ARRAY:
				var lines: PackedStringArray = ["Top Doadores:"]
				for item in data:
					if typeof(item) == TYPE_DICTIONARY:
						lines.append("• %s (%d)" % [String(item.get("nick", "?")), int(item.get("total_coins", 0))])
				donors_label.text = "\n".join(lines)
		"recent_gifts":
			if gifts_label != null and typeof(data) == TYPE_ARRAY:
				var glines: PackedStringArray = ["Últimos Presentes:"]
				for item in data.slice(0, 5):
					if typeof(item) == TYPE_DICTIONARY:
						glines.append("• %s: %s x%d" % [
							String(item.get("nick", "?")),
							String(item.get("gift", "Gift")),
							int(item.get("repeat", 1)),
						])
				gifts_label.text = "\n".join(glines)
