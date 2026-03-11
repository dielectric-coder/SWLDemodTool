#include "colormap.h"

void colormap_db_to_rgb(float normalized, uint8_t *r, uint8_t *g, uint8_t *b) {
    if (normalized < 0.0f) normalized = 0.0f;
    if (normalized > 1.0f) normalized = 1.0f;

    if (normalized < 0.2f) {
        float t = normalized / 0.2f;
        *r = 0;
        *g = 0;
        *b = (uint8_t)(t * 255);
    } else if (normalized < 0.4f) {
        float t = (normalized - 0.2f) / 0.2f;
        *r = 0;
        *g = (uint8_t)(t * 255);
        *b = 255;
    } else if (normalized < 0.6f) {
        float t = (normalized - 0.4f) / 0.2f;
        *r = 0;
        *g = 255;
        *b = (uint8_t)((1.0f - t) * 255);
    } else if (normalized < 0.8f) {
        float t = (normalized - 0.6f) / 0.2f;
        *r = (uint8_t)(t * 255);
        *g = 255;
        *b = 0;
    } else {
        float t = (normalized - 0.8f) / 0.2f;
        *r = 255;
        *g = (uint8_t)((1.0f - t) * 255);
        *b = 0;
    }
}
