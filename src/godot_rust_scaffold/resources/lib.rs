use godot::classes::{INode, Node};
use godot::prelude::*;

struct GameExtension;

#[gdextension]
unsafe impl ExtensionLibrary for GameExtension {}

#[derive(GodotClass)]
#[class(base=Node)]
struct HelloRust {
    base: Base<Node>,
}

#[godot_api]
impl INode for HelloRust {
    fn init(base: Base<Node>) -> Self {
        Self { base }
    }

    fn ready(&mut self) {
        godot_print!("Hello from Rust! Your GDExtension is running.");
    }
}
