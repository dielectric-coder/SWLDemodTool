#include "renderer.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

char *renderer_read_file(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "Failed to open: %s\n", path);
        return NULL;
    }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(len + 1);
    if (!buf) { fclose(f); return NULL; }
    fread(buf, 1, len, f);
    buf[len] = '\0';
    fclose(f);
    return buf;
}

GLuint renderer_compile_shader(GLenum type, const char *source) {
    GLuint s = glCreateShader(type);
    glShaderSource(s, 1, &source, NULL);
    glCompileShader(s);

    GLint ok;
    glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetShaderInfoLog(s, sizeof(log), NULL, log);
        fprintf(stderr, "Shader compile error: %s\n", log);
        glDeleteShader(s);
        return 0;
    }
    return s;
}

GLuint renderer_link_program(GLuint vert, GLuint frag) {
    GLuint p = glCreateProgram();
    glAttachShader(p, vert);
    glAttachShader(p, frag);
    glLinkProgram(p);

    GLint ok;
    glGetProgramiv(p, GL_LINK_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetProgramInfoLog(p, sizeof(log), NULL, log);
        fprintf(stderr, "Program link error: %s\n", log);
        glDeleteProgram(p);
        return 0;
    }
    glDeleteShader(vert);
    glDeleteShader(frag);
    return p;
}

static GLuint load_program(const char *shader_dir, const char *vert_name, const char *frag_name) {
    char path[512];

    snprintf(path, sizeof(path), "%s/%s", shader_dir, vert_name);
    char *vert_src = renderer_read_file(path);
    if (!vert_src) return 0;

    snprintf(path, sizeof(path), "%s/%s", shader_dir, frag_name);
    char *frag_src = renderer_read_file(path);
    if (!frag_src) { free(vert_src); return 0; }

    GLuint vs = renderer_compile_shader(GL_VERTEX_SHADER, vert_src);
    GLuint fs = renderer_compile_shader(GL_FRAGMENT_SHADER, frag_src);
    free(vert_src);
    free(frag_src);

    if (!vs || !fs) {
        if (vs) glDeleteShader(vs);
        if (fs) glDeleteShader(fs);
        return 0;
    }

    return renderer_link_program(vs, fs);
}

int renderer_init(Renderer *r, const char *shader_dir) {
    memset(r, 0, sizeof(*r));

    /* Spectrum shader */
    r->spectrum_program = load_program(shader_dir, "spectrum.vert", "spectrum.frag");
    if (!r->spectrum_program) return -1;
    r->u_mvp = glGetUniformLocation(r->spectrum_program, "u_mvp");
    r->u_color = glGetUniformLocation(r->spectrum_program, "u_color");

    /* Waterfall shader */
    r->waterfall_program = load_program(shader_dir, "waterfall.vert", "waterfall.frag");
    if (!r->waterfall_program) return -1;
    r->u_waterfall_tex = glGetUniformLocation(r->waterfall_program, "u_waterfall");
    r->u_row_offset = glGetUniformLocation(r->waterfall_program, "u_row_offset");

    /* Spectrum trace VAO/VBO */
    glGenVertexArrays(1, &r->spectrum_vao);
    glGenBuffers(1, &r->spectrum_vbo);

    /* Grid VAO/VBO */
    glGenVertexArrays(1, &r->grid_vao);
    glGenBuffers(1, &r->grid_vbo);

    /* Text VAO/VBO */
    glGenVertexArrays(1, &r->text_vao);
    glGenBuffers(1, &r->text_vbo);

    /* Waterfall quad VAO/VBO */
    glGenVertexArrays(1, &r->waterfall_vao);
    glGenBuffers(1, &r->waterfall_vbo);

    /* Waterfall texture (R32F, will be sized later) */
    glGenTextures(1, &r->waterfall_texture);

    return 0;
}

void renderer_destroy(Renderer *r) {
    if (r->spectrum_program) glDeleteProgram(r->spectrum_program);
    if (r->waterfall_program) glDeleteProgram(r->waterfall_program);

    glDeleteVertexArrays(1, &r->spectrum_vao);
    glDeleteBuffers(1, &r->spectrum_vbo);
    glDeleteVertexArrays(1, &r->grid_vao);
    glDeleteBuffers(1, &r->grid_vbo);
    glDeleteVertexArrays(1, &r->text_vao);
    glDeleteBuffers(1, &r->text_vbo);
    glDeleteVertexArrays(1, &r->waterfall_vao);
    glDeleteBuffers(1, &r->waterfall_vbo);
    glDeleteTextures(1, &r->waterfall_texture);
}
