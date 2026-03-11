#ifndef RENDERER_H
#define RENDERER_H

#include <epoxy/gl.h>

typedef struct {
    /* Spectrum shader (lines, grid, text) */
    GLuint spectrum_program;
    GLint  u_mvp;
    GLint  u_color;

    /* Waterfall shader (textured quad) */
    GLuint waterfall_program;
    GLint  u_waterfall_tex;
    GLint  u_row_offset;

    /* Spectrum plot VAO/VBOs */
    GLuint spectrum_vao;
    GLuint spectrum_vbo;

    GLuint grid_vao;
    GLuint grid_vbo;

    GLuint text_vao;
    GLuint text_vbo;

    /* Waterfall VAO/VBO/texture */
    GLuint waterfall_vao;
    GLuint waterfall_vbo;
    GLuint waterfall_texture;
} Renderer;

/* Read a file into a malloc'd string. Returns NULL on failure. */
char *renderer_read_file(const char *path);

/* Compile a shader from source. Returns 0 on failure. */
GLuint renderer_compile_shader(GLenum type, const char *source);

/* Link vertex+fragment shaders into a program. Returns 0 on failure. */
GLuint renderer_link_program(GLuint vert, GLuint frag);

/* Initialize all shaders and GPU resources. Returns 0 on success. */
int renderer_init(Renderer *r, const char *shader_dir);

/* Clean up GPU resources. */
void renderer_destroy(Renderer *r);

#endif
