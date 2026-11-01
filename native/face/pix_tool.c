// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* xcfa-pix - the pixel primitives of the face renderer on files (test and debugging tool).
 *
 * Images travel as ".raw": four int32 (width, height, channels, type 0 = uint8 / 1 = float32) and the pixels.
 *
 *   xcfa-pix decode MODEL KIND SEG IDX OUT.raw     KIND: frame | bg | base | basebg
 *   xcfa-pix resize IN.raw W H OUT.raw             cv2.resize, INTER_LINEAR (uint8 or float32)
 */
#include "image.h"
#include "farneback.h"
#include "warp.h"
#include "xcfa.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int write_raw(const char *path, int w, int h, int c, int type, const void *px) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return -1; }
    int32_t hdr[4] = {w, h, c, type};
    fwrite(hdr, sizeof hdr, 1, f);
    fwrite(px, type ? 4 : 1, (size_t)w * h * c, f);
    fclose(f);
    return 0;
}

static void *read_raw(const char *path, int32_t hdr[4]) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return NULL; }
    if (fread(hdr, sizeof(int32_t), 4, f) != 4) { fclose(f); return NULL; }
    size_t n = (size_t)hdr[0] * hdr[1] * hdr[2] * (hdr[3] ? 4 : 1);
    void *px = malloc(n ? n : 1);
    if (!px || fread(px, 1, n, f) != n) { fprintf(stderr, "%s: short file\n", path); free(px); fclose(f); return NULL; }
    fclose(f);
    return px;
}

static uint8_t *blob(const xcfa_model *m, xcfa_blob b) {
    uint8_t *buf = malloc(b.size ? b.size : 1);
    if (!buf || xcfa_read(m, b.off, b.size, buf) != 0) { free(buf); return NULL; }
    return buf;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: xcfa-pix decode|resize ...\n"); return 2; }
    if (!strcmp(argv[1], "decode") && argc >= 7) {
        char err[256];
        xcfa_model *m = xcfa_open(argv[2], err, sizeof err);
        if (!m) { fprintf(stderr, "%s\n", err); return 1; }
        const char *kind = argv[3];
        int si = xcfa_find_segment(m, atoi(argv[4])), idx = atoi(argv[5]);
        xcfa_blob b = {0, 0};
        int gray = 0;
        if (!strcmp(kind, "base")) b = m->base;
        else if (!strcmp(kind, "basebg")) { b = m->base_bg; gray = 1; }
        else if (si >= 0 && !strcmp(kind, "frame") && idx >= 0 && idx < m->seg[si].n) b = m->seg[si].frames[idx];
        else if (si >= 0 && !strcmp(kind, "bg") && idx >= 0 && idx < m->seg[si].nbg) { b = m->seg[si].bg[idx]; gray = 1; }
        else { fprintf(stderr, "bad kind or index\n"); return 1; }
        uint8_t *data = blob(m, b);
        if (!data) { fprintf(stderr, "read failed\n"); return 1; }
        xcfa_img im = gray ? xcfa_decode_gray(data, b.size) : xcfa_decode_color(data, b.size);
        free(data);
        if (!im.px) { fprintf(stderr, "decode failed\n"); return 1; }
        int rc = write_raw(argv[6], im.w, im.h, im.c, 0, im.px);
        printf("%dx%d c=%d\n", im.w, im.h, im.c);
        xcfa_img_free(&im);
        xcfa_close(m);
        return rc ? 1 : 0;
    }
    if (!strcmp(argv[1], "resize") && argc >= 6) {
        int32_t h[4];
        void *px = read_raw(argv[2], h);
        if (!px) return 1;
        int dw = atoi(argv[3]), dh = atoi(argv[4]);
        int rc;
        if (h[3] == 0) {
            xcfa_img s = {h[0], h[1], h[2], px};
            xcfa_img d = xcfa_resize_u8(&s, dw, dh);
            rc = d.px ? write_raw(argv[5], d.w, d.h, d.c, 0, d.px) : -1;
            xcfa_img_free(&d);
        } else {
            xcfa_fimg s = {h[0], h[1], h[2], px};
            xcfa_fimg d = xcfa_resize_f32(&s, dw, dh);
            rc = d.px ? write_raw(argv[5], d.w, d.h, d.c, 1, d.px) : -1;
            xcfa_fimg_free(&d);
        }
        free(px);
        return rc ? 1 : 0;
    }
    if (!strcmp(argv[1], "flow") && argc >= 9) { /* flow MODEL SEGID PAIR W H FWD.raw BWD.raw */
        char err[256];
        xcfa_model *m = xcfa_open(argv[2], err, sizeof err);
        if (!m) { fprintf(stderr, "%s\n", err); return 1; }
        int si = xcfa_find_segment(m, atoi(argv[3]));
        xcfa_fimg f, b;
        int rc = si < 0 ? -9 : xcfa_flow_pair(m, si, atoi(argv[4]), atoi(argv[5]), atoi(argv[6]), &f, &b);
        if (rc) { fprintf(stderr, "flow failed (%d)\n", rc); return 1; }
        write_raw(argv[7], f.w, f.h, 2, 1, f.px);
        write_raw(argv[8], b.w, b.h, 2, 1, b.px);
        xcfa_fimg_free(&f); xcfa_fimg_free(&b);
        xcfa_close(m);
        return 0;
    }
    if (!strcmp(argv[1], "warp") && argc >= 6) { /* warp IN.raw(u8) FLOW.raw T OUT.raw: _warp(frame.astype(float32), flow * t) */
        int32_t hi[4], hf[4];
        uint8_t *in = read_raw(argv[2], hi);
        float *fl = read_raw(argv[3], hf);
        if (!in || !fl) return 1;
        xcfa_fimg s = xcfa_fimg_alloc(hi[0], hi[1], hi[2]);
        for (size_t i = 0; i < (size_t)hi[0] * hi[1] * hi[2]; i++) s.px[i] = in[i];
        xcfa_fimg flow = {hf[0], hf[1], 2, fl};
        xcfa_fimg o = xcfa_warp(&s, &flow, (float)atof(argv[4]));
        int rc = write_raw(argv[5], o.w, o.h, o.c, 1, o.px);
        return rc ? 1 : 0;
    }
    if (!strcmp(argv[1], "interp") && argc >= 8) { /* interp A.raw B.raw FAB.raw FBA.raw T OUT.raw */
        int32_t ha[4], hb[4], h1[4], h2[4];
        uint8_t *a = read_raw(argv[2], ha), *b = read_raw(argv[3], hb);
        float *f1 = read_raw(argv[4], h1), *f2 = read_raw(argv[5], h2);
        if (!a || !b || !f1 || !f2) return 1;
        xcfa_img ia = {ha[0], ha[1], ha[2], a}, ib = {hb[0], hb[1], hb[2], b};
        xcfa_fimg fab = {h1[0], h1[1], 2, f1}, fba = {h2[0], h2[1], 2, f2};
        xcfa_img o = xcfa_interpolate(&ia, &ib, &fab, &fba, atof(argv[6]));
        return write_raw(argv[7], o.w, o.h, o.c, 0, o.px) ? 1 : 0;
    }
    if (!strcmp(argv[1], "unsharp") && argc >= 5) { /* unsharp IN.raw STRENGTH OUT.raw */
        int32_t h[4];
        uint8_t *a = read_raw(argv[2], h);
        if (!a) return 1;
        xcfa_img ia = {h[0], h[1], h[2], a};
        xcfa_img o = xcfa_unsharp(&ia, atof(argv[3]));
        return write_raw(argv[4], o.w, o.h, o.c, 0, o.px) ? 1 : 0;
    }
    if (!strcmp(argv[1], "blur") && argc >= 5) { /* blur IN.raw SIGMA OUT.raw */
        int32_t h[4];
        uint8_t *a = read_raw(argv[2], h);
        if (!a) return 1;
        xcfa_img ia = {h[0], h[1], h[2], a};
        xcfa_img o = xcfa_gaussian_blur_u8(&ia, atof(argv[3]));
        return write_raw(argv[4], o.w, o.h, o.c, 0, o.px) ? 1 : 0;
    }
    if (!strcmp(argv[1], "blend") && argc >= 6) { /* blend A.raw B.raw ALPHA OUT.raw */
        int32_t ha[4], hb[4];
        uint8_t *a = read_raw(argv[2], ha), *b = read_raw(argv[3], hb);
        if (!a || !b) return 1;
        xcfa_img ia = {ha[0], ha[1], ha[2], a}, ib = {hb[0], hb[1], hb[2], b};
        xcfa_img o = xcfa_alpha_blend(&ia, &ib, atof(argv[4]));
        return write_raw(argv[5], o.w, o.h, o.c, 0, o.px) ? 1 : 0;
    }
    if (!strcmp(argv[1], "micro") && argc >= 6) { /* micro IN.raw T AMP OUT.raw (writes the input itself when the original returns it) */
        int32_t h[4];
        uint8_t *a = read_raw(argv[2], h);
        if (!a) return 1;
        xcfa_img ia = {h[0], h[1], h[2], a}, o;
        if (!xcfa_apply_micro(&ia, atof(argv[3]), atof(argv[4]), &o)) o = ia;
        return write_raw(argv[5], o.w, o.h, o.c, 0, o.px) ? 1 : 0;
    }
    if (!strcmp(argv[1], "bg") && argc >= 6) { /* bg IN.raw MASK.raw MODE(color|alpha) OUT.raw [B G R] */
        int32_t h[4], hm[4];
        uint8_t *a = read_raw(argv[2], h), *m = read_raw(argv[3], hm);
        if (!a || !m) return 1;
        xcfa_img ia = {h[0], h[1], h[2], a}, im = {hm[0], hm[1], 1, m};
        float fill[3] = {0, 0, 0};
        int color = !strcmp(argv[4], "color");
        if (color && argc >= 9) for (int k = 0; k < 3; k++) fill[k] = (float)atof(argv[6 + k]);
        xcfa_img o = xcfa_apply_bg(&ia, &im, color ? fill : NULL);
        return write_raw(argv[5], o.w, o.h, o.c, 0, o.px) ? 1 : 0;
    }
    if (!strcmp(argv[1], "gray") && argc >= 4) { /* gray IN.raw OUT.raw */
        int32_t h[4];
        uint8_t *a = read_raw(argv[2], h);
        if (!a) return 1;
        xcfa_img ia = {h[0], h[1], h[2], a};
        xcfa_img o = xcfa_bgr_to_gray(&ia);
        return write_raw(argv[3], o.w, o.h, 1, 0, o.px) ? 1 : 0;
    }
    if (!strcmp(argv[1], "farneback") && argc >= 5) { /* farneback A.raw B.raw OUT.raw: gray u8 inputs, the renderer's parameters */
        int32_t ha[4], hb[4];
        uint8_t *a = read_raw(argv[2], ha), *b = read_raw(argv[3], hb);
        if (!a || !b) return 1;
        xcfa_img ia = {ha[0], ha[1], 1, a}, ib = {hb[0], hb[1], 1, b};
        xcfa_farneback_params prm = xcfa_farneback_defaults();
        xcfa_fimg o = xcfa_farneback(&ia, &ib, &prm);
        if (!o.px) { fprintf(stderr, "farneback failed\n"); return 1; }
        return write_raw(argv[4], o.w, o.h, 2, 1, o.px) ? 1 : 0;
    }
    fprintf(stderr, "bad command or arguments\n");
    return 2;
}
