// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* render.h - the face renderer (C port of FaceAnimation/_render_worker.py; Caroline's C# Renderer.cs is the structural
 * reference). A renderer holds one model; a render turns one speech clip into a stream of frames, produced one at a time
 * as the player needs them, so nothing is encoded and nothing is written to a file.
 *
 *   xcfa_renderer *r = xcfa_renderer_open("CarolineA.xcfa", &opts, err, sizeof err);
 *   xcfa_render *s = xcfa_render_begin(r, audio16k, n_samples);
 *   xcfa_frame f;
 *   while (xcfa_render_next(s, &f) > 0) { ...show f...; xcfa_frame_free(&f); }
 *   xcfa_render_end(s);
 *
 * Both render modes of the original are ported: the frame-level mode (default) with the living-pause state machine, and
 * the segment mode with its planner and morphs.
 */
#ifndef XCFA_RENDER_H
#define XCFA_RENDER_H

#include "image.h"

typedef enum { XCFA_MODE_FRAME = 0, XCFA_MODE_SEGMENT = 1 } xcfa_render_mode;

typedef struct {
    int fps;                 /* output frames per second (25) */
    int width, height;       /* output size; 0 = derive (see _render_worker.main: one given -> the other from the aspect ratio) */
    double scale;            /* used when neither is given; 1.0 = the base image size */
    int resolution_height;   /* a "480p"-style preset resolved to a height by the caller, 0 = none */
    int micro_movement;      /* subtle head movement (1) */
    double style_intensity;  /* its amplitude in pixels (1.0) */
    xcfa_render_mode mode;
    int lead_ms;             /* feature lead (250) */
    int remove_bg;           /* use the stored background masks: background becomes black ... */
    int alpha_output;        /* ... and, with this, frames carry an alpha plane (the original's webm path) */
    const char *bg_color;    /* a CSS name or #RGB / #RRGGBB: the background is filled with it (wins over remove_bg) */
    int segment_cache;       /* decoded segments kept (the original keeps 200; each is ~10 frames, so size it for the RAM budget) */
    unsigned seed;           /* random source of the segment mode (blink pick) */
} xcfa_render_options;

xcfa_render_options xcfa_render_defaults(void);

typedef struct {
    xcfa_img img;            /* BGR, out_w x out_h */
    uint8_t *alpha;          /* out_w * out_h or NULL (alpha_output mode) */
    int index;               /* output frame number */
} xcfa_frame;

void xcfa_frame_free(xcfa_frame *f);

typedef struct xcfa_renderer xcfa_renderer;
typedef struct xcfa_render xcfa_render;

xcfa_renderer *xcfa_renderer_open(const char *model_path, const xcfa_render_options *opt, char *err, size_t errlen);
void xcfa_renderer_close(xcfa_renderer *r);
int xcfa_renderer_width(const xcfa_renderer *r);
int xcfa_renderer_height(const xcfa_renderer *r);
int xcfa_renderer_fps(const xcfa_renderer *r);
/* Memory held by decoded frames right now, in bytes (for the RAM budget). */
size_t xcfa_renderer_cache_bytes(const xcfa_renderer *r);

/* audio: mono float32, 16 kHz; it must stay valid until xcfa_render_end(). */
xcfa_render *xcfa_render_begin(xcfa_renderer *r, const float *audio, size_t n_samples);
size_t xcfa_render_frame_count(const xcfa_render *s);
/* The next frame: 1 = a frame was produced, 0 = the clip is finished, < 0 = an error. */
int xcfa_render_next(xcfa_render *s, xcfa_frame *out);
void xcfa_render_end(xcfa_render *s);

#endif
