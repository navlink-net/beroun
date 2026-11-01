// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* library.c - the segment library. See library.h. */
#define _GNU_SOURCE
#include "library.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define D XCFA_FEATURES

/* numpy: v / (np.linalg.norm(v) + 1e-8) in float32; the norm is sqrt(dot(v, v)) */
static void normalise(const float *v, float *out) {
    float s = 0.0f;
    for (int i = 0; i < D; i++) s += v[i] * v[i];
    float n = sqrtf(s) + 1e-8f;
    for (int i = 0; i < D; i++) out[i] = v[i] / n;
}

static float dot(const float *a, const float *b) {
    float s = 0.0f;
    for (int k = 0; k < D; k++) s += a[k] * b[k];
    return s;
}

static int usable_row(const float *row) { /* Python: an all-zero feature row is None */
    for (int k = 0; k < D; k++) if (row[k] != 0.0f) return 1;
    return 0;
}

static int cmp_id(const void *a, const void *b) {
    const int *x = a, *y = b;
    return x[0] < y[0] ? -1 : x[0] > y[0];
}

xcfa_lib *xcfa_lib_open(const xcfa_model *m, char *err, size_t errlen) {
    xcfa_lib *l = calloc(1, sizeof *l);
    if (!l) return NULL;
    l->m = m;
    l->nseg = m->nseg;
    l->seg_norm = calloc((size_t)l->nseg * D + 1, sizeof(float));
    l->seg_valid = calloc((size_t)l->nseg + 1, 1);
    l->seg_base = calloc((size_t)l->nseg + 1, sizeof(int));
    l->sorted_ids = malloc((size_t)(l->nseg + 1) * sizeof(int));
    l->sorted_pos = malloc((size_t)(l->nseg + 1) * sizeof(int));
    int (*pairs)[2] = malloc((size_t)(l->nseg + 1) * sizeof *pairs);
    if (!l->seg_norm || !l->seg_valid || !l->seg_base || !l->sorted_ids || !l->sorted_pos || !pairs) goto fail;
    for (int i = 0; i < l->nseg; i++) {
        const xcfa_segment *s = &m->seg[i];
        l->seg_base[i] = l->ncell;
        l->ncell += s->n;
        if (s->blink) l->n_blink++;
        if (s->has_mean) { normalise(s->mean, l->seg_norm + (size_t)i * D); l->seg_valid[i] = 1; }
        pairs[i][0] = s->id;
        pairs[i][1] = i;
    }
    l->seg_base[l->nseg] = l->ncell;
    qsort(pairs, (size_t)l->nseg, sizeof *pairs, cmp_id);
    for (int i = 0; i < l->nseg; i++) {
        if (i && pairs[i][0] == pairs[i - 1][0]) {
            if (err) snprintf(err, errlen, "two segments have the id %d", pairs[i][0]);
            goto fail;
        }
        l->sorted_ids[i] = pairs[i][0];
        l->sorted_pos[i] = pairs[i][1];
    }
    free(pairs);
    pairs = NULL;

    l->frame_flat = malloc((size_t)(l->ncell ? l->ncell : 1) * sizeof(int));
    if (!l->frame_flat) goto fail;
    for (int i = 0; i < l->ncell; i++) l->frame_flat[i] = -1;
    int cap = 0;
    for (int i = 0; i < l->nseg; i++) {
        const xcfa_segment *s = &m->seg[i];
        for (int f = 0; f < s->n && f < s->naudio; f++) /* the rows are padded or trimmed to n, as in the Python loader */
            if (usable_row(s->audio + (size_t)f * D)) cap++;
    }
    l->flat_norm = malloc((size_t)(cap ? cap : 1) * D * sizeof(float));
    l->flat_seg = malloc((size_t)(cap ? cap : 1) * sizeof(int));
    l->flat_frame = malloc((size_t)(cap ? cap : 1) * sizeof(int));
    if (!l->flat_norm || !l->flat_seg || !l->flat_frame) goto fail;
    for (int i = 0; i < l->nseg; i++) {
        const xcfa_segment *s = &m->seg[i];
        for (int f = 0; f < s->n && f < s->naudio; f++) {
            const float *row = s->audio + (size_t)f * D;
            if (!usable_row(row)) continue;
            normalise(row, l->flat_norm + (size_t)l->nflat * D);
            l->flat_seg[l->nflat] = s->id;
            l->flat_frame[l->nflat] = f;
            l->frame_flat[l->seg_base[i] + f] = l->nflat;
            l->nflat++;
        }
    }
    return l;
fail:
    free(pairs);
    xcfa_lib_close(l);
    return NULL;
}

void xcfa_lib_close(xcfa_lib *l) {
    if (!l) return;
    free(l->seg_norm); free(l->seg_valid); free(l->flat_norm); free(l->flat_seg); free(l->flat_frame);
    free(l->seg_base); free(l->frame_flat); free(l->sorted_ids); free(l->sorted_pos);
    free(l);
}

int xcfa_lib_position_of_id(const xcfa_lib *l, int id) {
    int lo = 0, hi = l->nseg - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (l->sorted_ids[mid] == id) return l->sorted_pos[mid];
        if (l->sorted_ids[mid] < id) lo = mid + 1; else hi = mid - 1;
    }
    return -1;
}

int xcfa_lib_id_at(const xcfa_lib *l, int position) { return l->m->seg[position].id; }

int xcfa_lib_flat_index(const xcfa_lib *l, int seg_id, int frame) {
    int p = xcfa_lib_position_of_id(l, seg_id);
    if (p < 0 || frame < 0 || frame >= l->m->seg[p].n) return -1;
    return l->frame_flat[l->seg_base[p] + frame];
}

xcfa_pos xcfa_lib_find_best_frame(const xcfa_lib *l, const float *feat, xcfa_pos cur, float thr) {
    if (l->nflat == 0 || !feat) { xcfa_pos p = {cur.seg > 0 ? cur.seg : 0, cur.frame}; return p; }
    float q[D];
    normalise(feat, q);
    int best = 0;
    float best_sim = -INFINITY;
    for (int i = 0; i < l->nflat; i++) {
        float s = dot(l->flat_norm + (size_t)i * D, q);
        if (s > best_sim) { best_sim = s; best = i; } /* the first maximum, like numpy argmax */
    }
    int nf = xcfa_lib_flat_index(l, cur.seg, cur.frame + 1);
    if (nf >= 0) {
        float next_sim = dot(l->flat_norm + (size_t)nf * D, q);
        if (best_sim <= next_sim + thr) { xcfa_pos p = {cur.seg, cur.frame + 1}; return p; } /* stay sequential */
    }
    xcfa_pos p = {l->flat_seg[best], l->flat_frame[best]};
    return p;
}

static int in_set(const int *set, int n, int v) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (set[mid] == v) return 1;
        if (set[mid] < v) lo = mid + 1; else hi = mid - 1;
    }
    return 0;
}

xcfa_pos xcfa_lib_find_best_quiet_frame(const xcfa_lib *l, const unsigned char *quiet_flat, const int *quiet_ids, int n_quiet,
                                        const float *feat, xcfa_pos cur, float thr) {
    int any = 0;
    if (quiet_flat) for (int i = 0; i < l->nflat; i++) if (quiet_flat[i]) { any = 1; break; }
    if (l->nflat == 0 || !feat || !quiet_flat || !any) return xcfa_lib_find_best_frame(l, feat, cur, thr);
    float q[D];
    normalise(feat, q);
    int best = -1;
    float best_sim = -INFINITY;
    for (int i = 0; i < l->nflat; i++) {
        if (!quiet_flat[i]) continue;
        float s = dot(l->flat_norm + (size_t)i * D, q);
        if (s > best_sim) { best_sim = s; best = i; }
    }
    int ni = xcfa_lib_flat_index(l, cur.seg, cur.frame + 1);
    if (in_set(quiet_ids, n_quiet, cur.seg) && ni >= 0 && quiet_flat[ni]) {
        float next_sim = dot(l->flat_norm + (size_t)ni * D, q);
        if (best_sim <= next_sim + thr) { xcfa_pos p = {cur.seg, cur.frame + 1}; return p; }
    }
    xcfa_pos p = {l->flat_seg[best], l->flat_frame[best]};
    return p;
}

int xcfa_lib_cosine_top_k(const xcfa_lib *l, const float *query, int k, int exclude, int *out) {
    float q[D];
    normalise(query, q);
    float *score = malloc((size_t)(l->nseg + 1) * sizeof(float));
    int *idx = malloc((size_t)(l->nseg + 1) * sizeof(int));
    int n = 0, cnt = 0;
    if (!score || !idx) { free(score); free(idx); return 0; }
    for (int i = 0; i < l->nseg; i++) {
        if (i == exclude || !l->seg_valid[i]) continue;
        score[n] = dot(q, l->seg_norm + (size_t)i * D);
        idx[n] = i;
        n++;
    }
    while (cnt < k && cnt < n) { /* k is small: repeated selection beats sorting 4479 entries */
        int b = -1;
        for (int i = 0; i < n; i++) {
            if (idx[i] < 0) continue;
            if (b < 0 || score[i] > score[b] || (score[i] == score[b] && idx[i] > idx[b])) b = i;
        }
        out[cnt++] = idx[b];
        idx[b] = -1;
    }
    free(score); free(idx);
    return cnt;
}

int xcfa_lib_best_stitch(const xcfa_lib *l, const int *cands, int n, int last_seg) {
    (void)l; (void)n; (void)last_seg;
    return cands[0];
}

int xcfa_lib_random_blink_segment(const xcfa_lib *l, double (*rnd)(void *), void *arg) {
    if (l->n_blink > 0) {
        int pick = (int)(rnd(arg) * l->n_blink);
        for (int i = 0; i < l->nseg; i++)
            if (l->m->seg[i].blink && pick-- == 0) return i;
    }
    return (int)(rnd(arg) * l->nseg);
}

int xcfa_lib_select_segment(const xcfa_lib *l, const float *const *ahead, int n_ahead, int last_seg, int silence_streak,
                            const int *quiet_ids, int n_quiet, int silence_onset, double (*rnd)(void *), void *arg) {
    float mean[D] = {0};
    int nvalid = 0;
    for (int a = 0; a < n_ahead; a++) {
        if (!ahead[a]) continue;
        for (int k = 0; k < D; k++) mean[k] += ahead[a][k];
        nvalid++;
    }
    if (!nvalid) {
        if (n_quiet) return xcfa_lib_best_stitch(l, quiet_ids, n_quiet, last_seg); /* pure silence: the best quiet segment */
        return xcfa_lib_random_blink_segment(l, rnd, arg);
    }
    for (int k = 0; k < D; k++) mean[k] /= (float)nvalid;
    int top[20];
    int nt = xcfa_lib_cosine_top_k(l, mean, 20, last_seg, top);
    if (!nt) nt = xcfa_lib_cosine_top_k(l, mean, 20, -1, top);
    if (silence_streak >= silence_onset && n_quiet) { /* during silence only quiet candidates */
        int qc[20], qn = 0;
        for (int i = 0; i < nt; i++) if (in_set(quiet_ids, n_quiet, top[i])) qc[qn++] = top[i];
        if (qn) return xcfa_lib_best_stitch(l, qc, qn, last_seg);
        return xcfa_lib_best_stitch(l, quiet_ids, n_quiet, last_seg);
    }
    return xcfa_lib_best_stitch(l, top, nt, last_seg);
}
