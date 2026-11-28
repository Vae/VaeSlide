// The end of every fragment shader: runs the shader's shade() and applies the
// painter's opacity and clip, so a shader quad behaves like QPainter drawing.
uniform float opacity;
uniform bool clipped;
uniform sampler2D clip_mask;
uniform vec4 clip_box;  // device px: x, y, w, h of clip_mask
uniform vec2 target;

void main() {
    float coverage = 1.0;
    if (clipped) {
        vec2 p = (vec2(gl_FragCoord.x, target.y - gl_FragCoord.y) - clip_box.xy) / clip_box.zw;
        coverage = (any(lessThan(p, vec2(0.0))) || any(greaterThan(p, vec2(1.0))))
                 ? 0.0 : texture(clip_mask, tile_uv(p)).a;
    }
    color = shade(uv) * (opacity * coverage);
}
