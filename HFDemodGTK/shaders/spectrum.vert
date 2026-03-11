#version 330 core

layout(location = 0) in vec2 a_pos;
layout(location = 1) in float a_alpha;

uniform mat4 u_mvp;
out float v_alpha;

void main()
{
    gl_Position = u_mvp * vec4(a_pos, 0.0, 1.0);
    v_alpha = a_alpha;
}
