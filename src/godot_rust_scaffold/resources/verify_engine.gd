extends SceneTree

func _initialize():
    var version = Engine.get_version_info()
    print("GODOT_RUST_SCAFFOLD:" + JSON.stringify({
        "version": "%s.%s.%s" % [version.major, version.minor, version.patch],
        "status": version.status,
        "double": OS.has_feature("double"),
    }))
    quit()
