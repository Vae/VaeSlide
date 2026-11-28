// Inputs of every tile shader (TileShader). The shader's own file defines
// vec4 shade(vec2 uv), returning a premultiplied colour.
uniform sampler2D tile;  // the tile so far (unit 0): the tile, or the previous shader's output; sample with tile_uv(uv)
uniform vec2 size;       // the texture's size in device pixels
