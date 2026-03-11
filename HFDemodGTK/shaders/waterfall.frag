#version 330 core

in vec2 v_uv;
out vec4 frag_color;

uniform sampler2D u_waterfall;  // R32F texture with dB values
uniform float u_row_offset;     // Circular buffer newest row (0.0 - 1.0)

// Colormap: black -> blue -> cyan -> green -> yellow -> red
vec3 colormap(float t) {
    t = clamp(t, 0.0, 1.0);

    if (t < 0.2) {
        float s = t / 0.2;
        return vec3(0.0, 0.0, s);
    } else if (t < 0.4) {
        float s = (t - 0.2) / 0.2;
        return vec3(0.0, s, 1.0);
    } else if (t < 0.6) {
        float s = (t - 0.4) / 0.2;
        return vec3(0.0, 1.0, 1.0 - s);
    } else if (t < 0.8) {
        float s = (t - 0.6) / 0.2;
        return vec3(s, 1.0, 0.0);
    } else {
        float s = (t - 0.8) / 0.2;
        return vec3(1.0, 1.0 - s, 0.0);
    }
}

void main()
{
    // Newest row at top (v_uv.y=0), oldest at bottom (v_uv.y=1).
    // Walk backwards through the circular buffer from the newest row.
    float v = fract(u_row_offset - v_uv.y);
    float db_norm = texture(u_waterfall, vec2(v_uv.x, v)).r;
    frag_color = vec4(colormap(db_norm), 1.0);
}
