// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* xcfa-features - audio features of a clip (test and debugging tool).
 *
 *   xcfa-features IN.wav OUT.features.f32 OUT.energies.f32 [fps [sample_fps [lead_ms]]]
 *
 * IN.wav: 16 kHz mono, 16-bit PCM (what the reference clips are) or 32-bit float.
 * Writes (n_frames, 16) float32 and (n_frames) float32, the format of the .f32 files in tests/data/face.
 */
#include "features.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
            fmt = b[0] | b[1] << 8;
            ch = b[2] | b[3] << 8;
            rate = b[4] | b[5] << 8 | b[6] << 16 | (uint32_t)b[7] << 24;
            bits = b[14] | b[15] << 8;
            if (size > 16) fseek(f, (long)(size - 16), SEEK_CUR);
        } else if (!memcmp(c, "data", 4)) {
            if (ch != 1 || rate != 16000 || !((fmt == 1 && bits == 16) || (fmt == 3 && bits == 32))) {
                fprintf(stderr, "%s: need 16 kHz mono PCM16 or float32 (got fmt %d, %d ch, %u Hz, %d bit)\n", path, fmt, ch, rate, bits);
                fclose(f);
                return NULL;
            }
            size_t count = size / (bits / 8);
            float *out = malloc((count ? count : 1) * sizeof(float));
            if (!out) { fclose(f); return NULL; }
            if (bits == 16) {
                int16_t *tmp = malloc(count * sizeof(int16_t));
                if (!tmp || fread(tmp, 2, count, f) != count) { fprintf(stderr, "%s: short data chunk\n", path); free(tmp); free(out); fclose(f); return NULL; }
                for (size_t i = 0; i < count; i++) out[i] = tmp[i] / 32768.0f; /* soundfile's int16 -> float32 */
                free(tmp);
            } else if (fread(out, 4, count, f) != count) { fprintf(stderr, "%s: short data chunk\n", path); free(out); fclose(f); return NULL; }
            fclose(f);
            *n = count;
            return out;
        } else fseek(f, (long)(size + (size & 1)), SEEK_CUR);
    }
    fprintf(stderr, "%s: no data chunk\n", path);
    fclose(f);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: xcfa-features IN.wav OUT.features.f32 OUT.energies.f32 [fps [sample_fps [lead_ms]]]\n"); return 2; }
    int fps = argc > 4 ? atoi(argv[4]) : 25, sample_fps = argc > 5 ? atoi(argv[5]) : 25, lead = argc > 6 ? atoi(argv[6]) : XCFA_LEAD_MS_DEFAULT;
    size_t n = 0;
    float *audio = read_wav(argv[1], &n);
    if (!audio) return 1;
    float *feats = NULL;
    double *en = NULL;
    size_t frames = xcfa_features_all(audio, n, fps, sample_fps, lead, &feats, &en);
    if (!frames) { fprintf(stderr, "feature extraction failed\n"); return 1; }
    FILE *a = fopen(argv[2], "wb"), *b = fopen(argv[3], "wb");
    if (!a || !b) { perror("output"); return 1; }
    fwrite(feats, sizeof(float), frames * XCFA_FEAT_DIM, a);
    for (size_t i = 0; i < frames; i++) { float e = (float)en[i]; fwrite(&e, sizeof e, 1, b); }
    fclose(a); fclose(b);
    printf("%zu frames\n", frames);
    free(feats); free(en); free(audio);
    return 0;
}
