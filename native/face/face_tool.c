// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* ic-face - renders a speech clip with a face model into raw frames (test, benchmark and debugging tool of the renderer).
 *
 *   ic-face MODEL CLIP.wav OUT.raw [--width W] [--height H] [--mode frame|segment] [--no-micro] [--seed N]
 *                                  [--cache N] [--bg COLOR] [--remove-bg [--alpha]] [--fps N] [--lead MS] [--stats]
 *
 * OUT.raw: the frames one after another, BGR24 (BGRA with --alpha), without any header; the size is printed.
 * CLIP.wav: 16 kHz mono, 16-bit PCM or float32.
 */
#include "render.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static float *read_wav(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return NULL; }
    uint8_t h[12];
    if (fread(h, 1, 12, f) != 12 || memcmp(h, "RIFF", 4) || memcmp(h + 8, "WAVE", 4)) { fprintf(stderr, "%s: not a WAV file\n", path); fclose(f); return NULL; }
    int fmt = 0, ch = 0, bits = 0;
    uint32_t rate = 0;
    for (;;) {
        uint8_t c[8];
        if (fread(c, 1, 8, f) != 8) break;
        uint32_t size = c[4] | c[5] << 8 | c[6] << 16 | (uint32_t)c[7] << 24;
        if (!memcmp(c, "fmt ", 4)) {
            uint8_t b[16];
            if (size < 16 || fread(b, 1, 16, f) != 16) break;
            fmt = b[0] | b[1] << 8; ch = b[2] | b[3] << 8;
            rate = b[4] | b[5] << 8 | b[6] << 16 | (uint32_t)b[7] << 24; bits = b[14] | b[15] << 8;
            if (size > 16) fseek(f, (long)(size - 16), SEEK_CUR);
        } else if (!memcmp(c, "data", 4)) {
            if (ch != 1 || rate != 16000 || !((fmt == 1 && bits == 16) || (fmt == 3 && bits == 32))) { fprintf(stderr, "%s: need 16 kHz mono PCM16 or float32\n", path); fclose(f); return NULL; }
            size_t count = size / (bits / 8);
            float *out = malloc((count ? count : 1) * sizeof(float));
            if (bits == 16) {
                int16_t *tmp = malloc(count * sizeof(int16_t));
                if (!tmp || fread(tmp, 2, count, f) != count) { fprintf(stderr, "short data\n"); return NULL; }
                for (size_t i = 0; i < count; i++) out[i] = tmp[i] / 32768.0f;
                free(tmp);
            } else if (fread(out, 4, count, f) != count) { fprintf(stderr, "short data\n"); return NULL; }
            fclose(f);
            *n = count;
            return out;
        } else fseek(f, (long)(size + (size & 1)), SEEK_CUR);
    }
    fclose(f);
    return NULL;
}

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: ic-face MODEL CLIP.wav OUT.raw [options]\n"); return 2; }
    xcfa_render_options o = xcfa_render_defaults();
    int stats = 0;
    for (int i = 4; i < argc; i++) {
        if (!strcmp(argv[i], "--width") && i + 1 < argc) o.width = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--height") && i + 1 < argc) o.height = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--mode") && i + 1 < argc) o.mode = !strcmp(argv[++i], "segment") ? XCFA_MODE_SEGMENT : XCFA_MODE_FRAME;
        else if (!strcmp(argv[i], "--no-micro")) o.micro_movement = 0;
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) o.seed = (unsigned)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--cache") && i + 1 < argc) o.segment_cache = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--bg") && i + 1 < argc) o.bg_color = argv[++i];
        else if (!strcmp(argv[i], "--remove-bg")) o.remove_bg = 1;
        else if (!strcmp(argv[i], "--alpha")) o.alpha_output = 1;
        else if (!strcmp(argv[i], "--fps") && i + 1 < argc) o.fps = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--lead") && i + 1 < argc) o.lead_ms = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--stats")) stats = 1;
        else { fprintf(stderr, "unknown option %s\n", argv[i]); return 2; }
    }
    char err[256];
    double t0 = now();
    xcfa_renderer *r = xcfa_renderer_open(argv[1], &o, err, sizeof err);
    if (!r) { fprintf(stderr, "ic-face: %s\n", err); return 1; }
    double t_open = now() - t0;
    size_t n = 0;
    float *audio = read_wav(argv[2], &n);
    if (!audio) return 1;
    FILE *out = fopen(argv[3], "wb");
    if (!out) { perror(argv[3]); return 1; }
    xcfa_render *s = xcfa_render_begin(r, audio, n);
    if (!s) { fprintf(stderr, "cannot start the render\n"); return 1; }
    xcfa_frame f;
    int count = 0, rc;
    double worst = 0, t1 = now();
    for (;;) {
        double a = now();
        rc = xcfa_render_next(s, &f);
        double d = now() - a;
        if (rc <= 0) break;
        if (d > worst) worst = d;
        size_t px = (size_t)f.img.w * f.img.h;
        if (o.alpha_output && f.alpha) {
            for (size_t i = 0; i < px; i++) { fwrite(f.img.px + i * 3, 1, 3, out); fwrite(f.alpha + i, 1, 1, out); }
        } else fwrite(f.img.px, 1, px * 3, out);
        xcfa_frame_free(&f);
        count++;
    }
    double t_render = now() - t1;
    fclose(out);
    printf("%dx%d %d frames\n", xcfa_renderer_width(r), xcfa_renderer_height(r), count);
    if (stats)
        printf("open %.2f s, render %.2f s (%.1f ms/frame, slowest %.0f ms), decoded cache %.0f MB, audio %.1f s\n", t_open, t_render,
               count ? t_render * 1000 / count : 0, worst * 1000, xcfa_renderer_cache_bytes(r) / 1048576.0, n / 16000.0);
    xcfa_render_end(s);
    xcfa_renderer_close(r);
    free(audio);
    return rc < 0 ? 1 : 0;
}
