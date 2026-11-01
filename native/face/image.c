// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* image.c - pixel primitives of the face renderer. See image.h. */
#define _GNU_SOURCE
#include "image.h"

#include <stdio.h>
#include <jpeglib.h>
#include <math.h>
#include <png.h>
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

xcfa_img xcfa_img_alloc(int w, int h, int c) {
    xcfa_img im = {w, h, c, NULL};
    im.px = malloc((size_t)w * (size_t)h * (size_t)c + 1);
    return im;
}

void xcfa_img_free(xcfa_img *im) { free(im->px); im->px = NULL; }

xcfa_img xcfa_img_clone(const xcfa_img *im) {
    xcfa_img o = xcfa_img_alloc(im->w, im->h, im->c);
    if (o.px) memcpy(o.px, im->px, (size_t)im->w * im->h * im->c);
    return o;
}

xcfa_fimg xcfa_fimg_alloc(int w, int h, int c) {
    xcfa_fimg im = {w, h, c, NULL};
    im.px = malloc(((size_t)w * (size_t)h * (size_t)c + 1) * sizeof(float));
    return im;
}

void xcfa_fimg_free(xcfa_fimg *im) { free(im->px); im->px = NULL; }

/* ---------------------------------------------------------------- JPEG */

struct jerr { struct jpeg_error_mgr pub; jmp_buf jb; };
static void jerr_exit(j_common_ptr cinfo) { longjmp(((struct jerr *)cinfo->err)->jb, 1); }

static xcfa_img decode_jpeg(const uint8_t *data, size_t size, int want_gray) {
    xcfa_img im = {0, 0, 0, NULL};
    struct jpeg_decompress_struct ci;
    struct jerr je;
    ci.err = jpeg_std_error(&je.pub);
    je.pub.error_exit = jerr_exit;
    if (setjmp(je.jb)) { jpeg_destroy_decompress(&ci); free(im.px); im.px = NULL; return im; }
    jpeg_create_decompress(&ci);
    jpeg_mem_src(&ci, data, (unsigned long)size);
    jpeg_read_header(&ci, TRUE);
    ci.out_color_space = want_gray ? JCS_GRAYSCALE : JCS_RGB; /* OpenCV's own settings: default DCT method and fancy upsampling */
    jpeg_start_decompress(&ci);
    int c = ci.output_components;
    im = xcfa_img_alloc((int)ci.output_width, (int)ci.output_height, c);
    if (!im.px) longjmp(je.jb, 1);
    while (ci.output_scanline < ci.output_height) {
        uint8_t *row = im.px + (size_t)ci.output_scanline * im.w * c;
        jpeg_read_scanlines(&ci, &row, 1);
    }
    jpeg_finish_decompress(&ci);
    jpeg_destroy_decompress(&ci);
    if (c == 3)
        for (size_t i = 0; i < (size_t)im.w * im.h; i++) { uint8_t t = im.px[i * 3]; im.px[i * 3] = im.px[i * 3 + 2]; im.px[i * 3 + 2] = t; } /* RGB -> BGR */
    return im;
}

/* ---------------------------------------------------------------- PNG */

struct pngsrc { const uint8_t *p; size_t left; };
static void png_read_cb(png_structp s, png_bytep out, png_size_t n) {
    struct pngsrc *src = png_get_io_ptr(s);
    if (src->left < n) png_error(s, "short png");
    memcpy(out, src->p, n);
    src->p += n;
    src->left -= n;
}

static xcfa_img decode_png(const uint8_t *data, size_t size, int want_gray) {
    xcfa_img im = {0, 0, 0, NULL};
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, NULL, NULL, NULL);
    png_infop info = png ? png_create_info_struct(png) : NULL;
    uint8_t **rows = NULL;
    if (!png || !info) { png_destroy_read_struct(&png, &info, NULL); return im; }
    if (setjmp(png_jmpbuf(png))) { png_destroy_read_struct(&png, &info, NULL); free(rows); free(im.px); im.px = NULL; return im; }
    struct pngsrc src = {data, size};
    png_set_read_fn(png, &src, png_read_cb);
    png_read_info(png, info);
    png_set_strip_16(png);
    png_set_packing(png);
    int ct = png_get_color_type(png, info);
    if (ct == PNG_COLOR_TYPE_PALETTE) png_set_palette_to_rgb(png);
    if (ct == PNG_COLOR_TYPE_GRAY && png_get_bit_depth(png, info) < 8) png_set_expand_gray_1_2_4_to_8(png);
    if (png_get_valid(png, info, PNG_INFO_tRNS)) png_set_tRNS_to_alpha(png);
    png_set_strip_alpha(png);
    if (want_gray) png_set_rgb_to_gray_fixed(png, 1, -1, -1);
    else if (ct == PNG_COLOR_TYPE_GRAY || ct == PNG_COLOR_TYPE_GRAY_ALPHA) png_set_gray_to_rgb(png);
    png_read_update_info(png, info);
    int w = (int)png_get_image_width(png, info), h = (int)png_get_image_height(png, info);
    int c = (int)png_get_channels(png, info);
    im = xcfa_img_alloc(w, h, c);
    rows = malloc((size_t)h * sizeof *rows);
    if (!im.px || !rows) png_error(png, "out of memory");
    for (int y = 0; y < h; y++) rows[y] = im.px + (size_t)y * w * c;
    png_read_image(png, rows);
    png_destroy_read_struct(&png, &info, NULL);
    free(rows);
    if (c == 3)
        for (size_t i = 0; i < (size_t)w * h; i++) { uint8_t t = im.px[i * 3]; im.px[i * 3] = im.px[i * 3 + 2]; im.px[i * 3 + 2] = t; }
    return im;
}

xcfa_img xcfa_decode_color(const uint8_t *data, size_t size) {
    if (size >= 3 && data[0] == 0xff && data[1] == 0xd8) return decode_jpeg(data, size, 0);
    if (size >= 8 && !memcmp(data, "\x89PNG", 4)) return decode_png(data, size, 0);
    xcfa_img none = {0, 0, 0, NULL};
    return none;
}

xcfa_img xcfa_decode_gray(const uint8_t *data, size_t size) {
    if (size >= 3 && data[0] == 0xff && data[1] == 0xd8) return decode_jpeg(data, size, 1);
    if (size >= 8 && !memcmp(data, "\x89PNG", 4)) return decode_png(data, size, 1);
    xcfa_img none = {0, 0, 0, NULL};
    return none;
}

/* ---------------------------------------------------------------- resize (INTER_LINEAR) */

/* OpenCV's source coordinate for a destination index: fx = (d + 0.5) * scale - 0.5, floor, and no right neighbour past the edge */
static void lin_coords(int dst, int src, int *sx, float *fx) {
    double scale = 1.0 / ((double)dst / src); /* OpenCV: scale = 1 / inv_scale, where inv_scale = dsize / ssize (matters at exact rounding ties) */
    for (int d = 0; d < dst; d++) {
        float f = (float)((d + 0.5) * scale - 0.5);
        int s = (int)floorf(f);
        f -= (float)s;
        if (s < 0) { f = 0; s = 0; }
        if (s >= src - 1) { f = 0; s = src - 1; }
        sx[d] = s;
        fx[d] = f;
    }
}

xcfa_fimg xcfa_resize_f32(const xcfa_fimg *src, int dw, int dh) {
    xcfa_fimg dst = xcfa_fimg_alloc(dw, dh, src->c);
    int c = src->c;
    int *sx = malloc((size_t)dw * sizeof(int)), *sy = malloc((size_t)dh * sizeof(int));
    float *fx = malloc((size_t)dw * sizeof(float)), *fy = malloc((size_t)dh * sizeof(float));
    float *row0 = malloc((size_t)dw * c * sizeof(float)), *row1 = malloc((size_t)dw * c * sizeof(float));
    if (!dst.px || !sx || !sy || !fx || !fy || !row0 || !row1) { xcfa_fimg_free(&dst); goto done; }
    lin_coords(dw, src->w, sx, fx);
    lin_coords(dh, src->h, sy, fy);
    for (int y = 0; y < dh; y++) {
        const float *s0 = src->px + (size_t)sy[y] * src->w * c;
        const float *s1 = src->px + (size_t)(sy[y] + 1 < src->h ? sy[y] + 1 : sy[y]) * src->w * c;
        float b1 = fy[y], b0 = 1.0f - b1;
        for (int x = 0; x < dw; x++) {
            int x1 = sx[x] + 1 < src->w ? sx[x] + 1 : sx[x];
            float a1 = fx[x], a0 = 1.0f - a1;
            for (int k = 0; k < c; k++) {
                row0[x * c + k] = s0[sx[x] * c + k] * a0 + s0[x1 * c + k] * a1; /* horizontal pass, float, as OpenCV */
                row1[x * c + k] = s1[sx[x] * c + k] * a0 + s1[x1 * c + k] * a1;
            }
        }
        float *d = dst.px + (size_t)y * dw * c;
        for (int i = 0; i < dw * c; i++) d[i] = row0[i] * b0 + row1[i] * b1;
    }
done:
    free(sx); free(sy); free(fx); free(fy); free(row0); free(row1);
    return dst;
}

#ifndef XCFA_RESIZE_BLOCK
#define XCFA_RESIZE_BLOCK 16
#endif

xcfa_img xcfa_resize_u8(const xcfa_img *src, int dw, int dh) {
    xcfa_img dst = xcfa_img_alloc(dw, dh, src->c);
    int c = src->c;
    int *sx = malloc((size_t)dw * sizeof(int)), *sy = malloc((size_t)dh * sizeof(int));
    float *fx = malloc((size_t)dw * sizeof(float)), *fy = malloc((size_t)dh * sizeof(float));
    int *row0 = malloc((size_t)dw * c * sizeof(int)), *row1 = malloc((size_t)dw * c * sizeof(int));
    if (!dst.px || !sx || !sy || !fx || !fy || !row0 || !row1) { xcfa_img_free(&dst); goto done; }
    lin_coords(dw, src->w, sx, fx);
    lin_coords(dh, src->h, sy, fy);
    /* OpenCV: 11-bit weights, horizontal pass to int; the vertical pass of the SIMD kernel works on (H >> 4) with a signed 16-bit
     * high multiply: ((H0>>4)*b0 >> 16) + ((H1>>4)*b1 >> 16), plus 2, shifted by 2. Elements past the last full block of
     * XCFA_RESIZE_BLOCK of a row go through the scalar path: (H0*b0 + H1*b1 + 2^21) >> 22. */
    for (int y = 0; y < dh; y++) {
        const uint8_t *s0 = src->px + (size_t)sy[y] * src->w * c;
        const uint8_t *s1 = src->px + (size_t)(sy[y] + 1 < src->h ? sy[y] + 1 : sy[y]) * src->w * c;
        int ib1 = (int)lrintf(fy[y] * 2048.0f), ib0 = (int)lrintf((1.0f - fy[y]) * 2048.0f);
        for (int x = 0; x < dw; x++) {
            int x1 = sx[x] + 1 < src->w ? sx[x] + 1 : sx[x];
            int ia1 = (int)lrintf(fx[x] * 2048.0f), ia0 = (int)lrintf((1.0f - fx[x]) * 2048.0f);
            for (int k = 0; k < c; k++) {
                row0[x * c + k] = s0[sx[x] * c + k] * ia0 + s0[x1 * c + k] * ia1;
                row1[x * c + k] = s1[sx[x] * c + k] * ia0 + s1[x1 * c + k] * ia1;
            }
        }
        uint8_t *d = dst.px + (size_t)y * dw * c;
        int n = dw * c, vec_end = n - n % XCFA_RESIZE_BLOCK;
        for (int i = 0; i < n; i++) {
            int v;
            if (i < vec_end) v = ((((row0[i] >> 4) * ib0) >> 16) + (((row1[i] >> 4) * ib1) >> 16) + 2) >> 2;
            else v = (int)(((int64_t)row0[i] * ib0 + (int64_t)row1[i] * ib1 + (1 << 21)) >> 22);
            d[i] = (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v);
        }
    }
done:
    free(sx); free(sy); free(fx); free(fy); free(row0); free(row1);
    return dst;
}
