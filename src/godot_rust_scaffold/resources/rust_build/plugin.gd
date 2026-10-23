@tool
extends EditorPlugin

var _button: Button
var _panel: VBoxContainer
var _cargo: LineEdit
var _status: Label
var _output: RichTextLabel
var _restart: Button
var _thread: Thread


func _enter_tree() -> void:
	_panel = VBoxContainer.new()
	_panel.custom_minimum_size.y = 220 * get_editor_interface().get_editor_scale()
	var settings_row := HBoxContainer.new()
	_panel.add_child(settings_row)
	var label := Label.new()
	label.text = "Cargo executable:"
	settings_row.add_child(label)
	_cargo = LineEdit.new()
	_cargo.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_cargo.placeholder_text = "cargo (PATH), or an absolute executable path"
	_cargo.text = get_editor_interface().get_editor_settings().get_project_metadata(
		"rust_build", "cargo_path", _default_cargo()
	)
	settings_row.add_child(_cargo)
	_restart = Button.new()
	_restart.text = "Save & Restart Editor"
	_restart.pressed.connect(_restart_editor)
	settings_row.add_child(_restart)
	_status = Label.new()
	_status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_status.text = "Ready. Save Rust source files before building."
	_panel.add_child(_status)
	_output = RichTextLabel.new()
	_output.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_output.selection_enabled = true
	_output.scroll_following = true
	_panel.add_child(_output)
	add_control_to_bottom_panel(_panel, "Rust Build")
	_button = Button.new()
	_button.text = "Build Rust"
	_button.tooltip_text = "Build the Rust extension (debug). Output appears when Cargo finishes."
	_button.pressed.connect(_start_build)
	add_control_to_container(CONTAINER_TOOLBAR, _button)
	set_process(false)


func _exit_tree() -> void:
	set_process(false)
	# OS.execute has no cancellation handle on Godot 4.2. Join before unloading.
	if _thread != null:
		_thread.wait_to_finish()
		_thread = null
	remove_control_from_container(CONTAINER_TOOLBAR, _button)
	_button.queue_free()
	remove_control_from_bottom_panel(_panel)
	_panel.queue_free()


func _default_cargo() -> String:
	var executable := "cargo.exe" if OS.get_name() == "Windows" else "cargo"
	var home := OS.get_environment("CARGO_HOME")
	if home.is_empty():
		var user_home := OS.get_environment("USERPROFILE" if OS.get_name() == "Windows" else "HOME")
		if not user_home.is_empty():
			home = user_home.path_join(".cargo")
	if not home.is_empty():
		var candidate := home.path_join("bin").path_join(executable)
		if FileAccess.file_exists(candidate):
			return candidate
	return executable


func _start_build() -> void:
	if _thread != null:
		return
	make_bottom_panel_item_visible(_panel)
	if get_editor_interface().is_playing_scene():
		_status.text = "Stop the running game before rebuilding its native library."
		return
	var cargo := _cargo.text.strip_edges()
	if cargo.is_empty():
		_status.text = "Enter Cargo's executable name or absolute path (without quotes or arguments)."
		return
	get_editor_interface().get_editor_settings().set_project_metadata("rust_build", "cargo_path", cargo)
	var rust := ProjectSettings.globalize_path("res://").path_join("../rust").simplify_path()
	if not FileAccess.file_exists(rust.path_join("Cargo.toml")):
		_status.text = "Cannot build: sibling rust/Cargo.toml is missing."
		return
	# --manifest-path alone does not load rust/.cargo/config.toml from Godot's cwd.
	var args := PackedStringArray([
		"build", "--manifest-path", rust.path_join("Cargo.toml"),
		"--target-dir", rust.path_join("target"), "--color", "never",
	])
	var config := rust.path_join(".cargo/config.toml")
	if FileAccess.file_exists(config):
		args.append_array(["--config", config])
	_output.clear()
	_output.add_text("Executable: %s\nArguments: %s\n\n" % [cargo, JSON.stringify(args)])
	_status.text = "Building Rust… Output appears on completion. Closing or disabling the plugin waits for Cargo."
	_set_building(true)
	_thread = Thread.new()
	var error := _thread.start(_execute.bind(cargo, args))
	if error != OK:
		_thread = null
		_set_building(false)
		_status.text = "Could not start build worker: %s" % error_string(error)
		return
	set_process(true)


static func _execute(cargo: String, args: PackedStringArray) -> Dictionary:
	# No editor objects are touched from the worker thread.
	var output: Array = []
	var exit_code := OS.execute(cargo, args, output, true)
	return {"exit_code": exit_code, "output": "".join(output)}


func _process(_delta: float) -> void:
	if _thread == null or _thread.is_alive():
		return
	var result: Dictionary = _thread.wait_to_finish()
	_thread = null
	set_process(false)
	_set_building(false)
	_output.add_text(result.output)
	if result.exit_code == -1:
		_status.text = "Could not launch Cargo. Check the executable path and permissions."
	elif result.exit_code != 0:
		_status.text = "Rust build failed (exit %s). See compiler output below; no reload requested." % result.exit_code
	else:
		_status.text = "Build succeeded. Restart the game to use it. To refresh editor classes, refocus for hot reload if supported, or use Save & Restart Editor."


func _set_building(building: bool) -> void:
	_button.disabled = building
	_cargo.editable = not building
	_restart.disabled = building


func _build() -> bool:
	# Do not launch a game against a library that Cargo is still replacing.
	if _thread != null:
		make_bottom_panel_item_visible(_panel)
		_status.text = "Wait for the Rust build to finish before running the game."
		return false
	return true


func _restart_editor() -> void:
	if _thread != null:
		return
	if get_editor_interface().is_playing_scene():
		_status.text = "Stop the running game before restarting the editor."
		return
	get_editor_interface().restart_editor(true)
