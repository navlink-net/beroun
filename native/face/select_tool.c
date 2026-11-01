// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* xcfa-select - which stored frame the frame-level mode shows at every output frame (test and debugging tool).
 *
 *   xcfa-select MODEL FEATURES.f32 OUT.i32 [fps]
 *
 * Runs the speech branch of _render_frame_level_mode (hysteresis, jump cooldown) on precomputed features
 * and writes (segment id, frame index) per output frame as int32 pairs; (-1, -1) is the base portrait
 * (no frame chosen yet). The silence state machine is not part of this tool.
 */
#include "features.h"
#include "library.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: xcfa-select MODEL FEATURES.f32 OUT.i32 [fps]\n"); return 2; }
    int fps = argc > 4 ? atoi(argv[4]) : 25;
    char err[256];
    xcfa_model *m = xcfa_open(argv[1], err, sizeof err);
    if (!m) { fprintf(stderr, "%s\n", err); return 1; }
    xcfa_lib *lib = xcfa_lib_open(m, err, sizeof err);
    if (!lib) { fprintf(stderr, "%s\n", err); return 1; }
    FILE *f = fopen(argv[2], "rb");
    if (!f) { perror(argv[2]); return 1; }
    fseek(f, 0, SEEK_END);
    long bytes = ftell(f);
    fseek(f, 0, SEEK_SET);
    size_t n = (size_t)bytes / (XCFA_FEAT_DIM * sizeof(float));
    float *feats = malloc((size_t)bytes);
    if (fread(feats, 1, (size_t)bytes, f) != (size_t)bytes) { fprintf(stderr, "short read\n"); return 1; }
    fclose(f);

    const int cooldown = (int)nearbyint(fps / 5.0) < 1 ? 1 : (int)nearbyint(fps / 5.0); /* max(1, round(fps / 5)) */
    const float threshold = 0.12f;
    int32_t *out = malloc(n * 2 * sizeof(int32_t));
    xcfa_pos cur = {-1, 0};
    int cooldown_left = 0;
    for (size_t i = 0; i < n; i++) {
        if (cooldown_left <= 0) {
            cur = xcfa_lib_find_best_frame(lib, feats + i * XCFA_FEAT_DIM, cur, threshold);
            cooldown_left = cooldown;
        } else {
            if (xcfa_lib_flat_index(lib, cur.seg, cur.frame + 1) >= 0) cur.frame++;
            cooldown_left--;
        }
        int shown_frame = cur.frame;
        if (cur.seg >= 0) {
            int p = xcfa_lib_position_of_id(lib, cur.seg);
            int cnt = p >= 0 ? m->seg[p].n : 0;
            if (cnt > 0 && shown_frame > cnt - 1) shown_frame = cnt - 1; /* decoded_seg[min(cur_frame_idx, len - 1)] */
        }
        out[2 * i] = cur.seg < 0 ? -1 : cur.seg;
        out[2 * i + 1] = cur.seg < 0 ? -1 : shown_frame;
    }
    FILE *o = fopen(argv[3], "wb");
    if (!o) { perror(argv[3]); return 1; }
    fwrite(out, sizeof(int32_t), n * 2, o);
    fclose(o);
    printf("%zu frames, %d flat frames indexed\n", n, lib->nflat);
    free(out); free(feats);
    xcfa_lib_close(lib);
    xcfa_close(m);
    return 0;
}
