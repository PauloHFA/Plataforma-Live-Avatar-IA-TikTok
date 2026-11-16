extends Node2D
class_name GiftFX

@onready var banner_label: Label = $GiftBanner
@onready var burst_particles: CPUParticles2D = $BurstParticles
var _hide_timer: float = 0.0


func _process(delta: float) -> void:
	if _hide_timer > 0.0:
		_hide_timer -= delta
		if _hide_timer <= 0.0 and banner_label != null:
			banner_label.visible = false


func trigger(cmd: Dictionary) -> void:
	var gift_name := String(cmd.get("gift", "Presente"))
	var user_name := String(cmd.get("user", "Visitante"))
	var repeat := int(cmd.get("repeat", 1))
	var tier := int(cmd.get("tier", 1))

	if banner_label != null:
		banner_label.text = "%s enviou %s x%d (T%d)!" % [user_name, gift_name, repeat, tier]
		banner_label.visible = true
		_hide_timer = 4.0 if tier < 3 else 8.0

	if burst_particles != null:
		burst_particles.amount = mini(120, 12 * tier)
		burst_particles.restart()
		burst_particles.emitting = true
