// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* farneback.h - dense optical flow after Gunnar Farneback ("Two-Frame Motion Estimation Based on Polynomial
 * Expansion", 2003), the CPU algorithm behind cv2.calcOpticalFlowFarneback that the Python renderer uses for the
 * transition morphs and the silence phases. Own implementation: the pyramid, the polynomial expansion with a
 * Gaussian window, the per-iteration normal equations over a box window with a replicated border. Checked against
 * OpenCV on real frames of the model (tests/face_pixels_test.py); the agreement is a mean of about 1e-4 pixel.
 */
#ifndef XCFA_FARNEBACK_H
#define XCFA_FARNEBACK_H

#include "image.h"

typedef struct {
    double pyr_scale;   /* 0.5 */
    int levels;         /* 3 */
    int winsize;        /* 15 (box window, must be odd) */
    int iterations;     /* 3 */
    int poly_n;         /* 5 */
    double poly_sigma;  /* 1.2 */
} xcfa_farneback_params;

/* The parameters the renderer uses (_compute_flow). */
xcfa_farneback_params xcfa_farneback_defaults(void);

/* Flow from prev to next (8-bit gray, equal sizes) as float32 (h, w, 2): channel 0 = dx, channel 1 = dy.
 * px == NULL on failure. */
xcfa_fimg xcfa_farneback(const xcfa_img *prev, const xcfa_img *next, const xcfa_farneback_params *p);

/* cv2.cvtColor(img, COLOR_BGR2GRAY): (B*3735 + G*19235 + R*9798 + 16384) >> 15. */
xcfa_img xcfa_bgr_to_gray(const xcfa_img *bgr);

#endif
