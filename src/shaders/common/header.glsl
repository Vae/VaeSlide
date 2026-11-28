#version 330
// The start of every fragment shader. GLSupport.shader_program puts the
// shader's own files after this, then main.glsl, which calls their shade().
in vec2 uv;      // 0..1 across the quad, (0, 0) at the top left
out vec4 color;

// Textures rendered by TileTarget are upside down (GL's origin is bottom left)
vec2 tile_uv(vec2 p) { return vec2(p.x, 1.0 - p.y); }
