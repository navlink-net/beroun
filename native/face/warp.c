// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* warp.c - flow decoding, warping, blending, unsharp, micro movement, background masks. See warp.h. */
#define _GNU_SOURCE
#include "warp.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

static uint8_t sat8(float v) { /* cv::saturate_cast<uchar>(float): round to nearest even, then clamp */
    long r = lrintf(v);
    return (uint8_t)(r < 0 ? 0 : r > 255 ? 255 : r);
}

/* ---------------------------------------------------------------- flows */

int xcfa_flow_pair(const xcfa_model *m, int seg_pos, int pair, int out_w, int out_h, xcfa_fimg *fwd, xcfa_fimg *bwd) {
    const xcfa_segment *s = &m->seg[seg_pos];
    if (pair < 0 || pair >= s->nflows) return -1;
    int div = m->flow_div > 0 ? m->flow_div : 8;
    int fh8 = s->h / div < 1 ? 1 : s->h / div, fw8 = s->w / div < 1 ? 1 : s->w / div;
    size_t n = (size_t)2 * fh8 * fw8 * 2;
    if (s->flows[pair].size != n) return -2;
    int8_t *raw = malloc(n);
    if (!raw || xcfa_read(m, s->flows[pair].off, s->flows[pair].size, raw) != 0) { free(raw); return -3; }
    float scale = s->flows[pair].scale;
    xcfa_fimg *outs[2] = {fwd, bwd};
    for (int d = 0; d < 2; d++) {
        xcfa_fimg small = xcfa_fimg_alloc(fw8, fh8, 2);
        if (!small.px) { free(raw); return -4; }
        const int8_t *r = raw + (size_t)d * fh8 * fw8 * 2;
        for (size_t i = 0; i < (size_t)fh8 * fw8 * 2; i++) small.px[i] = (float)r[i] * scale;
        if (fh8 == out_h && fw8 == out_w) { *outs[d] = small; continue; } /* _upsample_flow: same size, a copy */
        xcfa_fimg up = xcfa_resize_f32(&small, out_w, out_h);
        xcfa_fimg_free(&small);
        if (!up.px) { free(raw); return -4; }
        float sx = (float)((double)out_w / fw8), sy = (float)((double)out_h / fh8); /* displacements grow with the image */
        for (size_t i = 0; i < (size_t)out_w * out_h; i++) { up.px[i * 2] *= sx; up.px[i * 2 + 1] *= sy; }
        *outs[d] = up;
    }
    free(raw);
    return 0;
}

/* ---------------------------------------------------------------- remap */

xcfa_fimg xcfa_warp(const xcfa_fimg *src, const xcfa_fimg *flow, float t) {
    int w = src->w, h = src->h, c = src->c;
    xcfa_fimg dst = xcfa_fimg_alloc(w, h, c);
    if (!dst.px) return dst;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            const float *f = flow->px + ((size_t)y * w + x) * 2;
            float mx = (float)x + f[0] * t, my = (float)y + f[1] * t;
            /* cv2.remap of a float32 image with float maps is an exact bilinear sample (checked against OpenCV: 1e-5) */
            int x0 = (int)floorf(mx), y0 = (int)floorf(my);
            float fx = mx - (float)x0, fy = my - (float)y0;
            float wt[4] = {(1.0f - fy) * (1.0f - fx), (1.0f - fy) * fx, fy * (1.0f - fx), fy * fx};
            int xa = x0 < 0 ? 0 : x0 > w - 1 ? w - 1 : x0, xb = x0 + 1 < 0 ? 0 : x0 + 1 > w - 1 ? w - 1 : x0 + 1;
            int ya = y0 < 0 ? 0 : y0 > h - 1 ? h - 1 : y0, yb = y0 + 1 < 0 ? 0 : y0 + 1 > h - 1 ? h - 1 : y0 + 1;
            const float *p00 = src->px + ((size_t)ya * w + xa) * c, *p01 = src->px + ((size_t)ya * w + xb) * c;
            const float *p10 = src->px + ((size_t)yb * w + xa) * c, *p11 = src->px + ((size_t)yb * w + xb) * c;
            float *d = dst.px + ((size_t)y * w + x) * c;
            for (int k = 0; k < c; k++) d[k] = p00[k] * wt[0] + p01[k] * wt[1] + p10[k] * wt[2] + p11[k] * wt[3];
        }
    return dst;
}

/* ---------------------------------------------------------------- blend, blur, unsharp */

xcfa_img xcfa_add_weighted_u8(const xcfa_img *a, double alpha, const xcfa_img *b, double beta, double gamma) {
    xcfa_img o = xcfa_img_alloc(a->w, a->h, a->c);
    float fa = (float)alpha, fb = (float)beta, fg = (float)gamma;
    size_t n = (size_t)a->w * a->h * a->c;
    for (size_t i = 0; o.px && i < n; i++) o.px[i] = sat8(a->px[i] * fa + b->px[i] * fb + fg);
    return o;
}

xcfa_img xcfa_alpha_blend(const xcfa_img *fa, const xcfa_img *fb, double alpha) {
    double a = alpha < 0.0 ? 0.0 : alpha > 1.0 ? 1.0 : alpha;
    return xcfa_add_weighted_u8(fa, 1.0 - a, fb, a, 0.0);
}

/* cv2.GaussianBlur(img, (0, 0), sigma) on uint8: kernel size round(sigma*3*2+1)|1, coefficients rounded to 1/256 with the
 * centre taking the rest so they sum to exactly 256; horizontal then vertical pass in fixed point; reflect-101 border. */
xcfa_img xcfa_gaussian_blur_u8(const xcfa_img *img, double sigma) {
    int ks = (int)lrint(sigma * 3 * 2 + 1) | 1, r = ks / 2;
    double k[64], sum = 0;
    for (int i = 0; i < ks; i++) { double x = i - r; k[i] = exp(-0.5 / (sigma * sigma) * x * x); sum += k[i]; }
    int c256[64], total = 0;
    for (int i = 0; i < ks; i++) { c256[i] = (int)lrint(k[i] / sum * 256.0); total += c256[i]; }
    c256[r] += 256 - total;
    int w = img->w, h = img->h, c = img->c;
    xcfa_img out = xcfa_img_alloc(w, h, c);
    int32_t *tmp = malloc((size_t)w * h * c * sizeof(int32_t));
    if (!out.px || !tmp) { xcfa_img_free(&out); free(tmp); return out; }
    #define REFLECT(i, n) ((i) < 0 ? -(i) : (i) >= (n) ? 2 * (n) - 2 - (i) : (i))
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            for (int ch = 0; ch < c; ch++) {
                int32_t acc = 0;
                for (int i = 0; i < ks; i++) { int xx = REFLECT(x + i - r, w); acc += img->px[((size_t)y * w + xx) * c + ch] * c256[i]; }
                tmp[((size_t)y * w + x) * c + ch] = acc; /* 8 fractional bits */
            }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++)
            for (int ch = 0; ch < c; ch++) {
                int64_t acc = 0;
                for (int i = 0; i < ks; i++) { int yy = REFLECT(y + i - r, h); acc += (int64_t)tmp[((size_t)yy * w + x) * c + ch] * c256[i]; }
                out.px[((size_t)y * w + x) * c + ch] = (uint8_t)((acc + (1 << 15)) >> 16);
            }
    #undef REFLECT
    free(tmp);
    return out;
}

xcfa_img xcfa_unsharp(const xcfa_img *img, double strength) {
    xcfa_img blurred = xcfa_gaussian_blur_u8(img, 1.2);
    xcfa_img o = blurred.px ? xcfa_add_weighted_u8(img, 1.0 + strength, &blurred, -strength, 0.0) : blurred;
    xcfa_img_free(&blurred);
    return o;
}

xcfa_img xcfa_interpolate(const xcfa_img *fa, const xcfa_img *fb, const xcfa_fimg *flow_ab, const xcfa_fimg *flow_ba, double t) {
    if (t <= 0.0) return xcfa_img_clone(fa);
    if (t >= 1.0) return xcfa_img_clone(fb);
    size_t n = (size_t)fa->w * fa->h * fa->c;
    xcfa_fimg fa_f = xcfa_fimg_alloc(fa->w, fa->h, fa->c), fb_f = xcfa_fimg_alloc(fb->w, fb->h, fb->c);
    xcfa_img res = xcfa_img_alloc(fa->w, fa->h, fa->c);
    if (!fa_f.px || !fb_f.px || !res.px) { xcfa_fimg_free(&fa_f); xcfa_fimg_free(&fb_f); xcfa_img_free(&res); return res; }
    for (size_t i = 0; i < n; i++) { fa_f.px[i] = fa->px[i]; fb_f.px[i] = fb->px[i]; }
    xcfa_fimg wa = xcfa_warp(&fa_f, flow_ab, (float)t), wb = xcfa_warp(&fb_f, flow_ba, (float)(1.0 - t));
    float alpha = (float)(1.0 - t), beta = (float)t;
    for (size_t i = 0; i < n; i++) { /* cv2.addWeighted (float32), np.clip(0, 255), .astype(uint8): the cast truncates */
        float v = wa.px[i] * alpha + wb.px[i] * beta;
        v = v < 0.0f ? 0.0f : v > 255.0f ? 255.0f : v;
        res.px[i] = (uint8_t)v;
    }
    xcfa_fimg_free(&fa_f); xcfa_fimg_free(&fb_f); xcfa_fimg_free(&wa); xcfa_fimg_free(&wb);
    double strength = 4.0 * t * (1.0 - t) * 0.45; /* peaks at t = 0.5, compensates the blur of the bilinear warps */
    if (strength > 0.02) {
        xcfa_img sharp = xcfa_unsharp(&res, strength);
        xcfa_img_free(&res);
        return sharp;
    }
    return res;
}

/* ---------------------------------------------------------------- micro movement */

/* cv2.warpAffine(frame, [[1,0,dx],[0,1,dy]], INTER_LINEAR, BORDER_REPLICATE): the fixed-point scheme of OpenCV
 * (coordinates in 1/1024 pixel, source cell and 1/32 fraction from the high bits, 15-bit weights that sum to 32768). */
static void bilinear_int_tab(int tab[32 * 32][4]) { /* int, not short: an aligned position has the weight 32768 */
    for (int ay = 0; ay < 32; ay++)
        for (int ax = 0; ax < 32; ax++) {
            float fy = ay / 32.0f, fx = ax / 32.0f;
            float v[4] = {(1.0f - fy) * (1.0f - fx), (1.0f - fy) * fx, fy * (1.0f - fx), fy * fx};
            int it[4], sum = 0;
            for (int k = 0; k < 4; k++) { it[k] = (int)lrintf(v[k] * 32768.0f); sum += it[k]; }
            if (sum != 32768) { /* the smallest coefficient gives back an excess, the largest takes a shortfall */
                int mk = 0, Mk = 0;
                for (int k = 1; k < 4; k++) { if (it[k] < it[mk]) mk = k; else if (it[k] > it[Mk]) Mk = k; }
                if (sum < 32768) it[Mk] += 32768 - sum; else it[mk] -= sum - 32768;
            }
            for (int k = 0; k < 4; k++) tab[ay * 32 + ax][k] = it[k];
        }
}

int xcfa_apply_micro(const xcfa_img *frame, double t, double amp, xcfa_img *out) {
    if (amp < 0.1) return 0;
    double dx = amp * sin(t * 0.7), dy = amp * cos(t * 0.5);
    if (fabs(dx) < 0.5 && fabs(dy) < 0.5) return 0;
    int w = frame->w, h = frame->h, c = frame->c;
    int tab[32 * 32][4];
    bilinear_int_tab(tab);
    xcfa_img o = xcfa_img_alloc(w, h, c);
    if (!o.px) return 0;
    double tx = -(double)(float)dx, ty = -(double)(float)dy; /* the inverse of a float32 translation matrix */
    int base_x = (int)lrint(tx * 1024.0) + 16, base_y = (int)lrint(ty * 1024.0) + 16;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            int X = (base_x + x * 1024) >> 5, Y = (base_y + y * 1024) >> 5;
            const int *wt = tab[(Y & 31) * 32 + (X & 31)];
            int sx = X >> 5, sy = Y >> 5;
            int xa = sx < 0 ? 0 : sx > w - 1 ? w - 1 : sx, xb = sx + 1 < 0 ? 0 : sx + 1 > w - 1 ? w - 1 : sx + 1;
            int ya = sy < 0 ? 0 : sy > h - 1 ? h - 1 : sy, yb = sy + 1 < 0 ? 0 : sy + 1 > h - 1 ? h - 1 : sy + 1;
            const uint8_t *p00 = frame->px + ((size_t)ya * w + xa) * c, *p01 = frame->px + ((size_t)ya * w + xb) * c;
            const uint8_t *p10 = frame->px + ((size_t)yb * w + xa) * c, *p11 = frame->px + ((size_t)yb * w + xb) * c;
            uint8_t *d = o.px + ((size_t)y * w + x) * c;
            for (int k = 0; k < c; k++) {
                int v = (p00[k] * wt[0] + p01[k] * wt[1] + p10[k] * wt[2] + p11[k] * wt[3] + (1 << 14)) >> 15;
                d[k] = (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v);
            }
        }
    *out = o;
    return 1;
}

/* ---------------------------------------------------------------- background masks */

xcfa_img xcfa_apply_bg(const xcfa_img *frame, const xcfa_img *mask, const float *fill_bgr) {
    xcfa_img o = xcfa_img_alloc(frame->w, frame->h, frame->c);
    xcfa_img rs = {0, 0, 0, NULL};
    const xcfa_img *m = mask;
    if (mask->w != frame->w || mask->h != frame->h) { rs = xcfa_resize_u8(mask, frame->w, frame->h); m = &rs; }
    if (o.px && m->px)
        for (size_t i = 0; i < (size_t)frame->w * frame->h; i++) {
            float a = (float)m->px[i] / 255.0f; /* mask.astype(float32) / 255.0 */
            for (int k = 0; k < 3; k++) {
                float v = (float)frame->px[i * 3 + k] * a;
                if (fill_bgr) v = v + fill_bgr[k] * (1.0f - a);
                v = v < 0.0f ? 0.0f : v > 255.0f ? 255.0f : v;
                o.px[i * 3 + k] = (uint8_t)v; /* .clip(0, 255).astype(np.uint8) truncates */
            }
        }
    xcfa_img_free(&rs);
    return o;
}
