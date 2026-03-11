#version 330 core

uniform vec4 u_color;
in float v_alpha;
out vec4 frag_color;

void main()
{
    frag_color = vec4(u_color.rgb, u_color.a * v_alpha);
}
