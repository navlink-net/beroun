// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* library.h - the segment library of the face renderer (C port of _SegmentLibrary and the selectors of
 * FaceAnimation/_render_worker.py; the C# SegmentLibrary.cs of Caroline is the structural reference).
 * Normalised features per segment and per stored frame, cosine matching with hysteresis, and the
 * lookahead planner of the segment mode.
 *
 * Id versus position (as in the Python and C# originals): the frame-level members (xcfa_pos, find_best_frame)
 * work with a segment's real catalog id; the whole-segment members (top_k, best_stitch, select_segment,
 * random_blink) work with its POSITION in the catalog. The two coincide in the models seen so far, but are
 * kept apart. Thumbnails (first/last frame crops of the older .cfa format) are not stored in .xcfa, so the
 * stitch quality step always keeps the best audio match, exactly as the originals do with a missing thumbnail.
 *
 * Quiet-segment sets are sets of segment ids (Python: quiet_seg_ids); the originals test positions against them
 * and iterate them in set order. Here a set is an ascending array of ids and iteration is ascending (the order of
 * a Python set of ints is an implementation detail).
 */
#ifndef XCFA_LIBRARY_H
#define XCFA_LIBRARY_H

#include "xcfa.h"

typedef struct {
    const xcfa_model *m;
    int nseg;
    int n_blink;
    float *seg_norm;           /* nseg * 16, unit mean-feature vectors, by position (zero where the segment has none) */
    unsigned char *seg_valid;  /* by position: the segment has mean features */
    int nflat;                 /* stored frames with usable features */
    float *flat_norm;          /* nflat * 16, unit feature vectors */
    int *flat_seg;             /* segment ID of each flat row */
    int *flat_frame;
    int *seg_base;             /* by position: index of the first (segment, frame) cell in frame_flat */
    int *frame_flat;           /* (segment position, frame) -> index into flat_*, or -1 */
    int ncell;
    int *sorted_ids, *sorted_pos; /* ids ascending with their positions, for id -> position lookups */
} xcfa_lib;

/* A stored frame. seg is a catalog id; seg < 0 means "nothing chosen yet". */
typedef struct { int seg, frame; } xcfa_pos;

xcfa_lib *xcfa_lib_open(const xcfa_model *m, char *err, size_t errlen);
void xcfa_lib_close(xcfa_lib *l);

int xcfa_lib_position_of_id(const xcfa_lib *l, int id); /* -1 when there is no such segment */
int xcfa_lib_id_at(const xcfa_lib *l, int position);

/* The flat row of (id, frame), or -1 when that stored frame has no usable features (Python: seg_frame_idx). */
int xcfa_lib_flat_index(const xcfa_lib *l, int seg_id, int frame);

/* Python: _SegmentLibrary.find_best_frame. feat == NULL gives (max(0, cur.seg), cur.frame). */
xcfa_pos xcfa_lib_find_best_frame(const xcfa_lib *l, const float *feat, xcfa_pos cur, float switch_threshold);

/* Python: _find_best_quiet_frame (defined in the worker but never called by it; ported for completeness).
 * quiet_flat: one byte per flat row (non-zero = the row belongs to a quiet segment); quiet_ids: ascending ids. */
xcfa_pos xcfa_lib_find_best_quiet_frame(const xcfa_lib *l, const unsigned char *quiet_flat, const int *quiet_ids, int n_quiet,
                                        const float *feat, xcfa_pos cur, float switch_threshold);

/* Python: cosine_top_k. Fills out (capacity k) with segment positions, best first; returns the count.
 * Equal scores: the higher position first (Python sorts (score, index) tuples in reverse). */
int xcfa_lib_cosine_top_k(const xcfa_lib *l, const float *query, int k, int exclude, int *out);

/* Python: best_stitch (no thumbnails in .xcfa: the first candidate, the best audio match). */
int xcfa_lib_best_stitch(const xcfa_lib *l, const int *cands, int n, int last_seg);

/* Python: random_blink_segment, with an explicit random source: rnd() returns a uniform value in [0,1). */
int xcfa_lib_random_blink_segment(const xcfa_lib *l, double (*rnd)(void *), void *arg);

/* Python: _select_segment (segment mode). ahead: the lookahead feature vectors (n_ahead pointers, NULL = none). */
int xcfa_lib_select_segment(const xcfa_lib *l, const float *const *ahead, int n_ahead, int last_seg, int silence_streak,
                            const int *quiet_ids, int n_quiet, int silence_onset, double (*rnd)(void *), void *arg);

#endif
