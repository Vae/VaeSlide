// HueRotateShader: turns the colour about the grey axis (Rodrigues' formula),
// which keeps greys grey and brightness roughly the same.
uniform float angle;  // radians

vec4 shade(vec2 p) {
    vec4 c = texture(tile, tile_uv(p));
    if (c.a <= 0.0) return c;
    vec3 rgb = c.rgb / c.a;
    const vec3 k = vec3(0.57735027);
    float s = sin(angle), co = cos(angle);
    rgb = rgb * co + cross(k, rgb) * s + k * dot(k, rgb) * (1.0 - co);
    return vec4(clamp(rgb, 0.0, 1.0) * c.a, c.a);
}
