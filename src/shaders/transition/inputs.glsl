// Inputs of every shader transition (ShaderTransition). The transition's own
// file defines vec4 shade(vec2 uv), returning the blended, premultiplied colour.
uniform sampler2D old_tile;  // unit 0; sample with tile_uv(uv)
uniform sampler2D new_tile;  // unit 1; sample with tile_uv(uv)
uniform float progress;      // 0..1
uniform vec2 size;           // tile size in device pixels
