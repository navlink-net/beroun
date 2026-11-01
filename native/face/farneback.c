// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* farneback.c - dense optical flow (Farneback). See farneback.h.
 *
 * Per pyramid level (coarse to fine):
 *   1. both images are blurred (sigma follows the level), resized to the level size and expanded in a local
 *      quadratic polynomial: R = (r_x, r_y, r_xx, r_yy, r_xy) per pixel;
 *   2. from the current flow, R1 sampled at the displaced position gives, per pixel, the 2x2 matrix A = (A0+A1)/2 and the
 *      vector b = -(b1-b0)/2 + A*d; the normal-equation terms (A^T A, A^T b) are stored (5 numbers per pixel);
 *   3. the terms are averaged over a winsize x winsize box (border replicated) and solved for the new flow;
 *      steps 2-3 repeat `iterations` times.
 * The coarser flow, resized and multiplied by 1/pyr_scale, starts the next level.
 */
#define _GNU_SOURCE
#include "farneback.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

xcfa_farneback_params xcfa_farneback_defaults(void) {
    xcfa_farneback_params p = {0.5, 3, 15, 3, 5, 1.2};
    return p;
}

xcfa_img xcfa_bgr_to_gray(const xcfa_img *bgr) {
    xcfa_img g = xcfa_img_alloc(bgr->w, bgr->h, 1);
    for (size_t i = 0; g.px && i < (size_t)bgr->w * bgr->h; i++)
        g.px[i] = (uint8_t)((bgr->px[i * 3] * 3735 + bgr->px[i * 3 + 1] * 19235 + bgr->px[i * 3 + 2] * 9798 + 16384) >> 15);
    return g;
}

/* ---------------------------------------------------------------- float Gaussian blur (cv2.GaussianBlur on float32) */

static void gauss_kernel(int n, double sigma, float *k) { /* cv::getGaussianKernel(n, sigma, CV_32F) */
    static const float small3[3] = {0.25f, 0.5f, 0.25f};
    static const float small5[5] = {0.0625f, 0.25f, 0.375f, 0.25f, 0.0625f};
    static const float small7[7] = {0.03125f, 0.109375f, 0.21875f, 0.28125f, 0.21875f, 0.109375f, 0.03125f};
    if (sigma <= 0 && n <= 7 && (n & 1)) { memcpy(k, n == 3 ? small3 : n == 5 ? small5 : small7, (size_t)n * sizeof(float)); if (n == 1) k[0] = 1.0f; return; }
    if (sigma <= 0) sigma = 0.3 * ((n - 1) * 0.5 - 1) + 0.8;
    double sum = 0, v[128];
    for (int i = 0; i < n; i++) { double x = i - (n - 1) * 0.5; v[i] = exp(-0.5 * x * x / (sigma * sigma)); sum += v[i]; }
    for (int i = 0; i < n; i++) k[i] = (float)(v[i] / sum);
}

static xcfa_fimg blur_f32(const xcfa_fimg *src, int ksize, double sigma) { /* single channel, reflect-101 border */
    int w = src->w, h = src->h, r = ksize / 2;
    float k[128];
    gauss_kernel(ksize, sigma, k);
    xcfa_fimg tmp = xcfa_fimg_alloc(w, h, 1), out = xcfa_fimg_alloc(w, h, 1);
    if (!tmp.px || !out.px) { xcfa_fimg_free(&tmp); xcfa_fimg_free(&out); return out; }
    #define REFL(i, n) ((n) == 1 ? 0 : ((i) < 0 ? -(i) : (i) >= (n) ? 2 * (n) - 2 - (i) : (i)))
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            float acc = 0;
            for (int i = 0; i < ksize; i++) acc += src->px[(size_t)y * w + REFL(x + i - r, w)] * k[i];
            tmp.px[(size_t)y * w + x] = acc;
        }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            float acc = 0;
            for (int i = 0; i < ksize; i++) acc += tmp.px[(size_t)REFL(y + i - r, h) * w + x] * k[i];
            out.px[(size_t)y * w + x] = acc;
        }
    #undef REFL
    xcfa_fimg_free(&tmp);
    return out;
}

/* ---------------------------------------------------------------- polynomial expansion */

typedef struct { double g[64], xg[64], xxg[64], ig11, ig03, ig33, ig55; } poly_t;

static void poly_prepare(int n, double sigma, poly_t *p) {
    if (sigma < 1.1920929e-7) sigma = n * 0.3;
    double s = 0, e[64];
    for (int x = -n; x <= n; x++) { e[x + n] = exp(-x * x / (2 * sigma * sigma)); s += e[x + n]; }
    s = 1.0 / s;
    for (int x = -n; x <= n; x++) { /* stored as float32 in OpenCV */
        float g = (float)(e[x + n] * s);
        p->g[x + n] = g;
        p->xg[x + n] = (float)(x * (double)g);
        p->xxg[x + n] = (float)(x * x * (double)g);
    }
    double G00 = 0, G11 = 0, G33 = 0, G55 = 0;
    for (int y = -n; y <= n; y++)
        for (int x = -n; x <= n; x++) {
            double gg = p->g[y + n] * p->g[x + n];
            G00 += gg; G11 += gg * x * x; G33 += gg * x * x * x * x; G55 += gg * x * x * y * y;
        }
    /* G = moment matrix of the basis (1, x, y, x^2, y^2, xy): index 1, 2 and 5 decouple; {0, 3, 4} form a 3x3 block
     * [[G00, e, e], [e, G33, G55], [e, G55, G33]] with e = G11 */
    double e11 = G11, a = G00, c = G33, u = G55;
    double m[3][3] = {{a, e11, e11}, {e11, c, u}, {e11, u, c}}, inv[3][3];
    double det = m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    inv[0][1] = (m[0][2] * m[2][1] - m[0][1] * m[2][2]) / det;
    inv[1][1] = (m[0][0] * m[2][2] - m[0][2] * m[2][0]) / det;
    p->ig11 = 1.0 / G11;
    p->ig03 = inv[0][1];
    p->ig33 = inv[1][1];
    p->ig55 = 1.0 / G55;
}

/* src: float32 (h, w); returns float32 (h, w, 5): r_x, r_y, r_xx, r_yy, r_xy. Border replicated. */
static xcfa_fimg poly_exp(const xcfa_fimg *src, int n, double sigma) {
    int w = src->w, h = src->h;
    poly_t p;
    poly_prepare(n, sigma, &p);
    xcfa_fimg R = xcfa_fimg_alloc(w, h, 5);
    double *v = malloc((size_t)w * h * 3 * sizeof(double)); /* vertical sums: sum g I, sum yg I, sum yyg I */
    if (!R.px || !v) { xcfa_fimg_free(&R); free(v); return R; }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            double s0 = 0, s1 = 0, s2 = 0;
            for (int j = -n; j <= n; j++) {
                int yy = y + j < 0 ? 0 : y + j >= h ? h - 1 : y + j;
                double val = src->px[(size_t)yy * w + x];
                s0 += p.g[j + n] * val; s1 += p.xg[j + n] * val; s2 += p.xxg[j + n] * val;
            }
            double *o = v + ((size_t)y * w + x) * 3;
            o[0] = s0; o[1] = s1; o[2] = s2;
        }
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            double b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
            for (int i = -n; i <= n; i++) {
                int xx = x + i < 0 ? 0 : x + i >= w ? w - 1 : x + i;
                const double *o = v + ((size_t)y * w + xx) * 3;
                b1 += p.g[i + n] * o[0]; b2 += p.xg[i + n] * o[0]; b4 += p.xxg[i + n] * o[0];
                b3 += p.g[i + n] * o[1]; b6 += p.xg[i + n] * o[1];
                b5 += p.g[i + n] * o[2];
            }
            float *r = R.px + ((size_t)y * w + x) * 5;
            r[0] = (float)(b2 * p.ig11);
            r[1] = (float)(b3 * p.ig11);
            r[2] = (float)(b1 * p.ig03 + b4 * p.ig33);
            r[3] = (float)(b1 * p.ig03 + b5 * p.ig33);
            r[4] = (float)(b6 * p.ig55);
        }
    free(v);
    return R;
}

/* ---------------------------------------------------------------- one iteration */

static void update_matrices(const xcfa_fimg *R0, const xcfa_fimg *R1, const xcfa_fimg *flow, xcfa_fimg *M) {
    int w = flow->w, h = flow->h;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            double dx = flow->px[((size_t)y * w + x) * 2], dy = flow->px[((size_t)y * w + x) * 2 + 1];
            double fx = x + dx, fy = y + dy;
            int x1 = (int)floor(fx), y1 = (int)floor(fy);
            double r2[5];
            const float *r1 = R0->px + ((size_t)y * w + x) * 5;
            if (x1 >= 0 && x1 < w - 1 && y1 >= 0 && y1 < h - 1) {
                double ax = fx - x1, ay = fy - y1;
                double a00 = (1 - ax) * (1 - ay), a01 = ax * (1 - ay), a10 = (1 - ax) * ay, a11 = ax * ay;
                const float *p = R1->px + ((size_t)y1 * w + x1) * 5;
                for (int k = 0; k < 5; k++) r2[k] = a00 * p[k] + a01 * p[5 + k] + a10 * p[(size_t)w * 5 + k] + a11 * p[(size_t)w * 5 + 5 + k];
            } else for (int k = 0; k < 5; k++) r2[k] = 0.0; /* the displaced position is outside the image */
            double q0 = (r1[0] - r2[0]) * 0.5, q1 = (r1[1] - r2[1]) * 0.5;
            double a11 = (r1[2] + r2[2]) * 0.5, a22 = (r1[3] + r2[3]) * 0.5, a12 = (r1[4] + r2[4]) * 0.25;
            double f0 = q0 + a11 * dx + a12 * dy, f1 = q1 + a12 * dx + a22 * dy;
            float *m = M->px + ((size_t)y * w + x) * 5;
            m[0] = (float)(a11 * a11 + a12 * a12);
            m[1] = (float)(a12 * (a11 + a22));
            m[2] = (float)(a22 * a22 + a12 * a12);
            m[3] = (float)(a11 * f0 + a12 * f1);
            m[4] = (float)(a12 * f0 + a22 * f1);
        }
}

/* box average (winsize x winsize, border replicated) of the 5 terms, then the 2x2 solve per pixel */
static int update_flow(const xcfa_fimg *M, xcfa_fimg *flow, int win) {
    int w = M->w, h = M->h, m = win / 2;
    double *hs = malloc((size_t)w * h * 5 * sizeof(double)), *vs = malloc((size_t)w * 5 * sizeof(double));
    if (!hs || !vs) { free(hs); free(vs); return -1; }
    for (int y = 0; y < h; y++) { /* horizontal sums */
        for (int k = 0; k < 5; k++) {
            const float *row = M->px + (size_t)y * w * 5 + k;
            double acc = 0;
            for (int i = -m; i <= m; i++) acc += row[(size_t)(i < 0 ? 0 : i >= w ? w - 1 : i) * 5];
            hs[(size_t)y * w * 5 + k] = acc;
            for (int x = 1; x < w; x++) {
                int add = x + m >= w ? w - 1 : x + m, sub = x - m - 1 < 0 ? 0 : x - m - 1;
                acc += row[(size_t)add * 5] - row[(size_t)sub * 5];
                hs[((size_t)y * w + x) * 5 + k] = acc;
            }
        }
    }
    double scale = 1.0 / ((double)win * win);
    for (int x = 0; x < w * 5; x++) { /* vertical window for y = 0 */
        double acc = 0;
        for (int j = -m; j <= m; j++) acc += hs[(size_t)(j < 0 ? 0 : j >= h ? h - 1 : j) * w * 5 + x];
        vs[x] = acc;
    }
    for (int y = 0; y < h; y++) {
        if (y > 0) {
            int add = y + m >= h ? h - 1 : y + m, sub = y - m - 1 < 0 ? 0 : y - m - 1;
            for (int x = 0; x < w * 5; x++) vs[x] += hs[(size_t)add * w * 5 + x] - hs[(size_t)sub * w * 5 + x];
        }
        for (int x = 0; x < w; x++) {
            double g11 = vs[x * 5] * scale, g12 = vs[x * 5 + 1] * scale, g22 = vs[x * 5 + 2] * scale;
            double h1 = vs[x * 5 + 3] * scale, h2 = vs[x * 5 + 4] * scale;
            double idet = 1.0 / (g11 * g22 - g12 * g12 + 1e-3);
            flow->px[((size_t)y * w + x) * 2] = (float)((g22 * h1 - g12 * h2) * idet);
            flow->px[((size_t)y * w + x) * 2 + 1] = (float)((g11 * h2 - g12 * h1) * idet);
        }
    }
    free(hs); free(vs);
    return 0;
}

/* ---------------------------------------------------------------- the pyramid */

xcfa_fimg xcfa_farneback(const xcfa_img *prev, const xcfa_img *next, const xcfa_farneback_params *p) {
    xcfa_fimg none = {0, 0, 0, NULL};
    if (prev->w != next->w || prev->h != next->h || prev->c != 1 || next->c != 1 || (p->winsize & 1) == 0) return none;
    int w0 = prev->w, h0 = prev->h;
    int levels = p->levels, k;
    double scale = 1;
    for (k = 0; k < levels; k++) { /* the pyramid stops where an image would get smaller than 32 px */
        scale *= p->pyr_scale;
        if (w0 * scale < 32 || h0 * scale < 32) break;
    }
    levels = k;
    xcfa_fimg prev_flow = {0, 0, 0, NULL}, flow = {0, 0, 0, NULL};
    for (k = levels; k >= 0; k--) {
        scale = 1;
        for (int i = 0; i < k; i++) scale *= p->pyr_scale;
        double sigma = (1.0 / scale - 1) * 0.5;
        int sz = (int)lrint(sigma * 5) | 1;
        if (sz < 3) sz = 3;
        int width = (int)lrint(w0 * scale), height = (int)lrint(h0 * scale);
        if (!prev_flow.px) {
            flow = xcfa_fimg_alloc(width, height, 2);
            if (flow.px) memset(flow.px, 0, (size_t)width * height * 2 * sizeof(float));
        } else {
            flow = xcfa_resize_f32(&prev_flow, width, height);
            float inv = (float)(1.0 / p->pyr_scale);
            for (size_t i = 0; flow.px && i < (size_t)width * height * 2; i++) flow.px[i] *= inv;
            xcfa_fimg_free(&prev_flow);
        }
        if (!flow.px) return none;
        xcfa_fimg R[2];
        const xcfa_img *src[2] = {prev, next};
        for (int i = 0; i < 2; i++) {
            xcfa_fimg f = xcfa_fimg_alloc(w0, h0, 1);
            for (size_t j = 0; f.px && j < (size_t)w0 * h0; j++) f.px[j] = src[i]->px[j];
            xcfa_fimg b = f.px ? blur_f32(&f, sz, sigma) : f;
            xcfa_fimg_free(&f);
            xcfa_fimg I = b.px ? xcfa_resize_f32(&b, width, height) : b;
            xcfa_fimg_free(&b);
            R[i] = I.px ? poly_exp(&I, p->poly_n, p->poly_sigma) : I;
            xcfa_fimg_free(&I);
            if (!R[i].px) { xcfa_fimg_free(&flow); if (i) xcfa_fimg_free(&R[0]); return none; }
        }
        xcfa_fimg M = xcfa_fimg_alloc(width, height, 5);
        if (!M.px) return none;
        update_matrices(&R[0], &R[1], &flow, &M);
        for (int it = 0; it < p->iterations; it++) {
            if (update_flow(&M, &flow, p->winsize)) return none;
            if (it < p->iterations - 1) update_matrices(&R[0], &R[1], &flow, &M);
        }
        xcfa_fimg_free(&M); xcfa_fimg_free(&R[0]); xcfa_fimg_free(&R[1]);
        prev_flow = flow;
    }
    return flow;
}
