// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* warp.h - flow decoding, warping, blending and the small image operations of the face renderer
 * (C port of XcfaLoader.decodeFlowPair / _upsample_flow and _render_worker.py's _warp, _interpolate, _unsharp,
 * _alpha_blend, _apply_micro, _apply_bg_to_frames). Each function reproduces the OpenCV call of the original,
 * including its fixed-point arithmetic where that matters (see the comments in warp.c).
 */
#ifndef XCFA_WARP_H
#define XCFA_WARP_H

#include "image.h"
#include "xcfa.h"

/* A stored flow pair of a segment, upsampled to out_w x out_h as float32 (h, w, 2): channel 0 is dx, channel 1 is dy,
 * both in output pixels. pair: index into the segment's flows (frame `pair` -> frame `pair + 1`). Returns 0 on success. */
int xcfa_flow_pair(const xcfa_model *m, int seg_pos, int pair, int out_w, int out_h, xcfa_fimg *fwd, xcfa_fimg *bwd);

/* _warp(frame_f32, flow * t): bilinear remap of a float32 BGR image, border replicated (OpenCV quantises the
 * source coordinates stay exact for float maps; only the 8-bit warpAffine of the micro movement is quantised). */
xcfa_fimg xcfa_warp(const xcfa_fimg *src, const xcfa_fimg *flow, float t);

/* cv2.addWeighted on uint8 / float32 images: saturate(a*alpha + b*beta + gamma). */
xcfa_img xcfa_add_weighted_u8(const xcfa_img *a, double alpha, const xcfa_img *b, double beta, double gamma);

/* _unsharp: img + strength * (img - GaussianBlur(img, sigma 1.2)), through cv2.GaussianBlur's own fixed-point kernel. */
xcfa_img xcfa_gaussian_blur_u8(const xcfa_img *img, double sigma);
xcfa_img xcfa_unsharp(const xcfa_img *img, double strength);

/* _alpha_blend: (1 - alpha) * fa + alpha * fb, alpha clamped to [0, 1]. */
xcfa_img xcfa_alpha_blend(const xcfa_img *fa, const xcfa_img *fb, double alpha);

/* _interpolate: bidirectional warp blend at fractional t (0 = fa, 1 = fb) with the unsharp compensation. */
xcfa_img xcfa_interpolate(const xcfa_img *fa, const xcfa_img *fb, const xcfa_fimg *flow_ab, const xcfa_fimg *flow_ba, double t);

/* _apply_micro: a sub-pixel head movement. Returns 0 when the original returns its input unchanged (amp < 0.1 or
 * |dx|, |dy| < 0.5) and leaves *out alone; otherwise 1 with the translated image in *out. */
int xcfa_apply_micro(const xcfa_img *frame, double t, double amp, xcfa_img *out);

/* _apply_bg_to_frames for one frame: mask is the stored alpha mask (resized to the frame when the sizes differ).
 * fill_bgr == NULL is the "alpha" mode (the background becomes black), otherwise the "color" mode. */
xcfa_img xcfa_apply_bg(const xcfa_img *frame, const xcfa_img *mask, const float *fill_bgr);

#endif
