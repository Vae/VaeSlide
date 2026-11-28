// MaskTransition: reveals the new tile where the mask is below the progress,
// with a soft edge. Same formula as MaskTransition.alpha(), per pixel.
uniform sampler2D mask;  // unit 2, R32F, row 0 at the top (not flipped)
uniform float softness;

vec4 shade(vec2 p) {
    float m = texture(mask, p).r;
    float a = softness <= 0.0 ? float(m < progress)
                              : clamp((progress * (1.0 + softness) - m) / softness, 0.0, 1.0);
    return mix(texture(old_tile, tile_uv(p)), texture(new_tile, tile_uv(p)), a);
}
