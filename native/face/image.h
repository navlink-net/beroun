// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* image.h - the pixel primitives of the face renderer: decoders and the OpenCV operations the Python renderer
 * calls (imdecode, resize, remap, GaussianBlur, addWeighted, warpAffine), written to give OpenCV's results.
 *
 * Images are interleaved 8-bit BGR (xcfa_img, c = 3) or 8-bit gray masks (c = 1); flows and float images are
 * float32. Everything allocates with malloc; free with xcfa_img_free().
 */
#ifndef XCFA_IMAGE_H
#define XCFA_IMAGE_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    int w, h, c;       /* c = 1 or 3 */
    uint8_t *px;       /* h * w * c */
} xcfa_img;

typedef struct {
    int w, h, c;
    float *px;         /* h * w * c */
} xcfa_fimg;

xcfa_img xcfa_img_alloc(int w, int h, int c);
void xcfa_img_free(xcfa_img *im);
xcfa_img xcfa_img_clone(const xcfa_img *im);
xcfa_fimg xcfa_fimg_alloc(int w, int h, int c);
void xcfa_fimg_free(xcfa_fimg *im);

/* cv2.imdecode(..., IMREAD_COLOR): JPEG or PNG to BGR. Returns an image with px == NULL on failure. */
xcfa_img xcfa_decode_color(const uint8_t *data, size_t size);
/* cv2.imdecode(..., IMREAD_GRAYSCALE) of a PNG. */
xcfa_img xcfa_decode_gray(const uint8_t *data, size_t size);

/* cv2.resize(src, (dw, dh)) with the default INTER_LINEAR, 8-bit and float32, any channel count. */
xcfa_img xcfa_resize_u8(const xcfa_img *src, int dw, int dh);
xcfa_fimg xcfa_resize_f32(const xcfa_fimg *src, int dw, int dh);

#endif
