#ifndef COLORMAP_H
#define COLORMAP_H

#include <stdint.h>

/* Convert normalized dB value (0.0-1.0) to RGB color.
 * Gradient: black -> blue -> cyan -> green -> yellow -> red */
void colormap_db_to_rgb(float normalized, uint8_t *r, uint8_t *g, uint8_t *b);

#endif
