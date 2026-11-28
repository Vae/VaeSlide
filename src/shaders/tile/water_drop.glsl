// WaterDropShader: a ring wave train. The front moves out from the centre, the
// waves behind it decay, and the whole thing fades out so it ends exactly on
// the plain image.
uniform float t;          // 0..1 through the effect
uniform vec2 centre;      // where the drop lands, 0..1 across the tile
uniform float strength;   // max displacement, fraction of the height
uniform float wavelength; // fraction of the height

vec4 shade(vec2 p) {
    vec2 px = p * size;
    vec2 c = centre * size;
    vec2 d = px - c;
    float dist = length(d);
    float lambda = max(1.0, wavelength * size.y);
    float reach = length(max(c, size - c));  // distance to the farthest corner
    float front = t * (reach + 3.0 * lambda);
    float x = dist - front;  // > 0 ahead of the front, < 0 behind it
    float envelope = x > 0.0 ? exp(-x * x / (0.1 * lambda * lambda)) : exp(x / (1.5 * lambda));
    // Ripples flatten as the ring grows (like real ones, ~1/sqrt(radius)), and
    // fade out over the last part so the effect ends exactly on the plain image
    float amount = smoothstep(0.0, 0.04, t) * (1.0 - smoothstep(0.6, 1.0, t))
                 * inversesqrt(1.0 + front / (4.0 * lambda));
    float phase = 6.2831853 * x / lambda;
    float wave = sin(phase) * envelope * amount;
    float slope = cos(phase) * envelope * amount;
    vec2 dir = dist > 0.0 ? d / dist : vec2(0.0);
    vec2 offset = dir * wave * strength * size.y;
    vec4 col = texture(tile, tile_uv(clamp((px - offset) / size, vec2(0.0), vec2(1.0))));
    float light = 1.0 + 0.35 * slope;  // wave faces towards the light get brighter
    return vec4(min(col.rgb * light, vec3(col.a)), col.a);
}
