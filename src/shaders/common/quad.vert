#version 330
// Every shader quad uses this vertex shader: a triangle strip of 4 vertices
// generated from gl_VertexID, so no vertex buffer is needed. The quad is
// `rect` (x, y, w, h) in painter coordinates, mapped to device pixels by
// `transform` (the painter's device transform, possibly a perspective one)
// and from there to clip space. w = d.z keeps texturing perspective-correct.
uniform vec4 rect;
uniform mat3 transform;
uniform vec2 target;
out vec2 uv;

void main() {
    vec2 corner = vec2(gl_VertexID & 1, gl_VertexID >> 1);  // (0,0) (1,0) (0,1) (1,1)
    uv = corner;                                             // (0, 0) at the rect's top left
    vec3 d = transform * vec3(rect.xy + corner * rect.zw, 1.0);
    vec2 ndc = vec2(2.0 * d.x / d.z / target.x - 1.0, 1.0 - 2.0 * d.y / d.z / target.y);
    gl_Position = vec4(ndc * d.z, 0.0, d.z);
}
