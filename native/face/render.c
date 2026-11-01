// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* render.c - the face renderer. See render.h.
 *
 * Structure follows _render_worker.py: main() set-up (sizes, base portrait, background masks, silence library), then one of
 * two modes. Each call of xcfa_render_next() runs one iteration of the original's loop, which writes exactly one frame.
 * What differs on purpose (results are the same, only the cost changes): frames and masks are decoded when first needed
 * instead of a whole segment at once, the silence library does not decode optical flows nobody reads, nothing is encoded.
 */
#define _GNU_SOURCE
#include "render.h"

#include "farneback.h"
#include "features.h"
#include "library.h"
#include "warp.h"
#include "xcfa.h"

#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_PER_MUL 5      /* silence segments kept per multiplier */
#define SILENCE_ONSET 3
#define SIL_STEPS 6

static const double SIL_SUSTAIN_MULS[SIL_STEPS] = {0.0, 0.1, 0.2, 0.3, 0.2, 0.1};

xcfa_render_options xcfa_render_defaults(void) {
    xcfa_render_options o;
    memset(&o, 0, sizeof o);
    o.fps = 25;
    o.scale = 1.0;
    o.micro_movement = 1;
    o.style_intensity = 1.0;
    o.mode = XCFA_MODE_FRAME;
    o.lead_ms = XCFA_LEAD_MS_DEFAULT;
    o.segment_cache = 24;
    o.seed = 1;
    return o;
}

void xcfa_frame_free(xcfa_frame *f) {
    xcfa_img_free(&f->img);
    free(f->alpha);
    f->alpha = NULL;
}

/* Python round(x, 1): the correctly rounded decimal of the binary value */
static double round1(double x) {
    char b[64];
    snprintf(b, sizeof b, "%.1f", x);
    return strtod(b, NULL);
}

/* ---------------------------------------------------------------- renderer */

typedef struct {
    int seg_pos, n;
    xcfa_img *fr;           /* n decoded frames (px == NULL until decoded) */
    unsigned long stamp;
} cache_entry;

typedef struct { double mul; int n; int pos[MAX_PER_MUL]; } sil_group;

struct xcfa_renderer {
    xcfa_model *m;
    xcfa_lib *lib;
    xcfa_render_options o;
    int out_w, out_h;
    xcfa_img base;                 /* base portrait at the output size, background processed */
    uint8_t *alpha;                /* static alpha plane or NULL */
    int bg_mode;                   /* 0 none, 1 colour fill, 2 black ("alpha") */
    float fill[3];
    int has_face;
    int fr_x, fr_y, fr_w, fr_h;
    int sample_fps, interp_per_pair, min_seg_frames, lookahead_frames;
    cache_entry *cache;
    int ncache, cache_cap;
    unsigned long clock;
    sil_group *sil;                /* silence_data_by_mul, ascending multiplier */
    int nsil;
    int sil_default;               /* silence_data: index of the group of multiplier 0.0, else the lowest */
    int sil_energy_fallback;
};

static int parse_color(const char *s, float *bgr) {
    static const struct { const char *n; int b, g, r; } named[] = {
        {"black", 0, 0, 0}, {"white", 255, 255, 255}, {"red", 0, 0, 255}, {"green", 0, 128, 0}, {"lime", 0, 255, 0},
        {"blue", 255, 0, 0}, {"yellow", 0, 255, 255}, {"cyan", 255, 255, 0}, {"magenta", 255, 0, 255}, {"gray", 128, 128, 128},
        {"grey", 128, 128, 128}, {"silver", 192, 192, 192}, {"navy", 128, 0, 0}, {"olive", 0, 128, 128}, {"teal", 128, 128, 0},
        {"purple", 128, 0, 128}, {"maroon", 0, 0, 128}, {"orange", 0, 165, 255}, {"pink", 203, 192, 255}, {"brown", 19, 69, 139}};
    char b[32];
    size_t n = 0;
    while (s[n] && n < sizeof b - 1) { b[n] = (char)((s[n] >= 'A' && s[n] <= 'Z') ? s[n] + 32 : s[n]); n++; }
    b[n] = 0;
    for (size_t i = 0; i < sizeof named / sizeof *named; i++)
        if (!strcmp(b, named[i].n)) { bgr[0] = (float)named[i].b; bgr[1] = (float)named[i].g; bgr[2] = (float)named[i].r; return 0; }
    const char *h = b[0] == '#' ? b + 1 : b;
    char full[8];
    if (strlen(h) == 3) { for (int i = 0; i < 3; i++) full[i * 2] = full[i * 2 + 1] = h[i]; full[6] = 0; h = full; }
    if (strlen(h) != 6) return -1;
    unsigned v;
    if (sscanf(h, "%6x", &v) != 1) return -1;
    bgr[0] = (float)(v & 255); bgr[1] = (float)((v >> 8) & 255); bgr[2] = (float)((v >> 16) & 255);
    return 0;
}

static void seterr(char *err, size_t n, const char *msg) { if (err && n) snprintf(err, n, "%s", msg); }

/* the decoded frame of a stored position at the output size, background processed; cached (LRU by segment) */
static const xcfa_img *get_frame(xcfa_renderer *r, int seg_pos, int idx) {
    const xcfa_segment *sg = &r->m->seg[seg_pos];
    if (idx < 0 || idx >= sg->n || !sg->frames) return NULL;
    cache_entry *e = NULL;
    for (int i = 0; i < r->ncache; i++) if (r->cache[i].seg_pos == seg_pos) { e = &r->cache[i]; break; }
    if (!e) {
        if (r->ncache == r->cache_cap) { /* evict the least recently used segment */
            int oldest = 0;
            for (int i = 1; i < r->ncache; i++) if (r->cache[i].stamp < r->cache[oldest].stamp) oldest = i;
            for (int f = 0; f < r->cache[oldest].n; f++) xcfa_img_free(&r->cache[oldest].fr[f]);
            free(r->cache[oldest].fr);
            r->cache[oldest] = r->cache[--r->ncache];
        }
        e = &r->cache[r->ncache++];
        e->seg_pos = seg_pos;
        e->n = sg->n;
        e->fr = calloc((size_t)sg->n, sizeof *e->fr);
        if (!e->fr) { r->ncache--; return NULL; }
    }
    e->stamp = ++r->clock;
    if (e->fr[idx].px) return &e->fr[idx];
    xcfa_blob b = sg->frames[idx];
    uint8_t *jp = malloc(b.size ? b.size : 1);
    if (!jp || xcfa_read(r->m, b.off, b.size, jp) != 0) { free(jp); return NULL; }
    xcfa_img im = xcfa_decode_color(jp, b.size);
    free(jp);
    if (!im.px) return NULL;
    if (im.w != r->out_w || im.h != r->out_h) { xcfa_img rs = xcfa_resize_u8(&im, r->out_w, r->out_h); xcfa_img_free(&im); im = rs; }
    if (r->bg_mode && idx < sg->nbg) { /* _apply_bg_to_frames at first decode */
        xcfa_blob mb = sg->bg[idx];
        uint8_t *pn = malloc(mb.size ? mb.size : 1);
        if (pn && xcfa_read(r->m, mb.off, mb.size, pn) == 0) {
            xcfa_img mask = xcfa_decode_gray(pn, mb.size);
            if (mask.px) {
                xcfa_img o = xcfa_apply_bg(&im, &mask, r->bg_mode == 1 ? r->fill : NULL);
                xcfa_img_free(&mask);
                if (o.px) { xcfa_img_free(&im); im = o; }
            }
        }
        free(pn);
    }
    e->fr[idx] = im;
    return &e->fr[idx];
}

size_t xcfa_renderer_cache_bytes(const xcfa_renderer *r) {
    size_t n = 0;
    for (int i = 0; i < r->ncache; i++)
        for (int f = 0; f < r->cache[i].n; f++)
            if (r->cache[i].fr[f].px) n += (size_t)r->out_w * r->out_h * 3;
    return n;
}

int xcfa_renderer_width(const xcfa_renderer *r) { return r->out_w; }
int xcfa_renderer_height(const xcfa_renderer *r) { return r->out_h; }
int xcfa_renderer_fps(const xcfa_renderer *r) { return r->o.fps; }

/* silence_data_by_mul: tagged segments grouped by their multiplier (new models), else the quietest segments (old models) */
static void build_silence(xcfa_renderer *r) {
    const xcfa_model *m = r->m;
    int cap = 8;
    r->sil = calloc((size_t)cap, sizeof *r->sil);
    for (int i = 0; i < m->nseg; i++) {
        const xcfa_segment *sg = &m->seg[i];
        if (!sg->silence || sg->n < 2) continue; /* _load_entry: a segment needs at least two frames */
        double mul = round1(sg->has_silence_mul ? (double)sg->silence_mul : 0.0);
        int g = -1;
        for (int k = 0; k < r->nsil; k++) if (r->sil[k].mul == mul) { g = k; break; }
        if (g < 0) {
            if (r->nsil == cap) { cap *= 2; r->sil = realloc(r->sil, (size_t)cap * sizeof *r->sil); }
            g = r->nsil++;
            r->sil[g].mul = mul; r->sil[g].n = 0;
        }
        if (r->sil[g].n < MAX_PER_MUL) r->sil[g].pos[r->sil[g].n++] = i; /* the first five, in catalog order */
    }
    /* Python skips tagged segments with fewer than two frames only after taking the first five of a group; the models
     * seen have ten frames everywhere, so the order of the two steps does not matter */
    if (r->nsil) {
        for (int a = 0; a < r->nsil; a++) /* ascending multiplier */
            for (int b = a + 1; b < r->nsil; b++)
                if (r->sil[b].mul < r->sil[a].mul) { sil_group t = r->sil[a]; r->sil[a] = r->sil[b]; r->sil[b] = t; }
        r->sil_default = 0;
        for (int k = 0; k < r->nsil; k++) if (r->sil[k].mul == 0.0) r->sil_default = k;
        return;
    }
    /* old models: the quietest segments by the energy entry (index 13) of the mean features */
    int n = m->nseg;
    double *e = malloc((size_t)(n + 1) * sizeof *e);
    int *idx = malloc((size_t)(n + 1) * sizeof *idx);
    for (int i = 0; i < n; i++) { e[i] = m->seg[i].has_mean ? (double)m->seg[i].mean[13] : 1.0; idx[i] = i; }
    for (int a = 0; a < n; a++) /* ascending (energy, id): a selection of the first few is enough */
        for (int b = a + 1; b < n && a < 10; b++)
            if (e[idx[b]] < e[idx[a]] || (e[idx[b]] == e[idx[a]] && m->seg[idx[b]].id < m->seg[idx[a]].id)) { int t = idx[a]; idx[a] = idx[b]; idx[b] = t; }
    int nq = (n ? n : 1) / 8;
    nq = nq < 10 ? nq : 10;
    nq = nq > 3 ? nq : 3;
    r->nsil = 1;
    r->sil[0].mul = 0.0; r->sil[0].n = 0;
    for (int i = 0; i < nq && i < n && r->sil[0].n < MAX_PER_MUL; i++) if (m->seg[idx[i]].n >= 2) r->sil[0].pos[r->sil[0].n++] = idx[i];
    if (!r->sil[0].n && n && m->seg[0].n >= 2) r->sil[0].pos[r->sil[0].n++] = 0;
    if (!r->sil[0].n) r->nsil = 0;
    r->sil_energy_fallback = 1;
    free(e); free(idx);
}

xcfa_renderer *xcfa_renderer_open(const char *model_path, const xcfa_render_options *opt, char *err, size_t errlen) {
    xcfa_renderer *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    r->o = opt ? *opt : xcfa_render_defaults();
    r->m = xcfa_open(model_path, err, errlen);
    if (!r->m) { free(r); return NULL; }
    r->lib = xcfa_lib_open(r->m, err, errlen);
    if (!r->lib) { xcfa_renderer_close(r); return NULL; }
    uint8_t *pn = malloc(r->m->base.size ? r->m->base.size : 1);
    if (!pn || xcfa_read(r->m, r->m->base.off, r->m->base.size, pn) != 0) { free(pn); seterr(err, errlen, "cannot read the base portrait"); xcfa_renderer_close(r); return NULL; }
    xcfa_img base = xcfa_decode_color(pn, r->m->base.size);
    free(pn);
    if (!base.px) { seterr(err, errlen, "cannot decode the base portrait"); xcfa_renderer_close(r); return NULL; }

    /* output size: explicit width and height; one of them (the other keeps the aspect ratio); a scale; the base size */
    int bw = base.w, bh = base.h, W = r->o.width, H = r->o.height;
    if (!W && !H && r->o.resolution_height) H = r->o.resolution_height;
    if (W && H) { r->out_w = W; r->out_h = H; }
    else if (W) { r->out_w = W; r->out_h = (int)nearbyint((double)bh * W / bw); }
    else if (H) { r->out_h = H; r->out_w = (int)nearbyint((double)bw * H / bh); }
    else if (r->o.scale != 1.0) { r->out_w = (int)nearbyint(bw * r->o.scale); r->out_h = (int)nearbyint(bh * r->o.scale); }
    else { r->out_w = bw; r->out_h = bh; }
    r->out_w += r->out_w % 2; /* most video codecs want even sizes, and the layer keeps the original's sizes */
    r->out_h += r->out_h % 2;
    if (r->out_w != bw || r->out_h != bh) { xcfa_img rs = xcfa_resize_u8(&base, r->out_w, r->out_h); xcfa_img_free(&base); base = rs; }

    /* background masks (stored in the model): colour fill, or black with an alpha plane */
    if ((r->o.bg_color || r->o.remove_bg) && !r->m->has_bg) { r->o.bg_color = NULL; r->o.remove_bg = 0; } /* "no bg masks": ignored, like the original */
    if (r->o.bg_color || r->o.remove_bg) {
        if (r->o.bg_color && parse_color(r->o.bg_color, r->fill)) { seterr(err, errlen, "cannot parse the background colour"); xcfa_img_free(&base); xcfa_renderer_close(r); return NULL; }
        uint8_t *pb = malloc(r->m->base_bg.size ? r->m->base_bg.size : 1);
        xcfa_img mask = {0, 0, 0, NULL};
        if (pb && xcfa_read(r->m, r->m->base_bg.off, r->m->base_bg.size, pb) == 0) mask = xcfa_decode_gray(pb, r->m->base_bg.size);
        free(pb);
        if (!mask.px) { seterr(err, errlen, "cannot decode the base background mask"); xcfa_img_free(&base); xcfa_renderer_close(r); return NULL; }
        xcfa_img rm = (mask.w != r->out_w || mask.h != r->out_h) ? xcfa_resize_u8(&mask, r->out_w, r->out_h) : xcfa_img_clone(&mask);
        xcfa_img_free(&mask);
        r->bg_mode = r->o.bg_color ? 1 : 2;
        if (r->bg_mode == 2 && r->o.alpha_output) { /* the alpha plane carries the background, the base stays as it is */
            r->alpha = malloc((size_t)r->out_w * r->out_h);
            for (size_t i = 0; r->alpha && i < (size_t)r->out_w * r->out_h; i++) { float a = (float)rm.px[i] / 255.0f; r->alpha[i] = (uint8_t)(a * 255.0f); }
        } else {
            xcfa_img nb = xcfa_apply_bg(&base, &rm, r->bg_mode == 1 ? r->fill : NULL);
            if (nb.px) { xcfa_img_free(&base); base = nb; }
        }
        xcfa_img_free(&rm);
    }
    r->base = base;

    if (r->m->has_face_region) {
        r->has_face = 1;
        r->fr_x = (int)(r->m->face_x * r->out_w); r->fr_y = (int)(r->m->face_y * r->out_h);
        r->fr_w = (int)(r->m->face_w * r->out_w); r->fr_h = (int)(r->m->face_h * r->out_h);
    }
    r->sample_fps = r->m->fps > 0 ? r->m->fps : 5;
    int ipp = (int)nearbyint((double)r->o.fps / r->sample_fps);
    r->interp_per_pair = ipp < 1 ? 1 : ipp;
    int msf = r->interp_per_pair * 9, fs = (int)(r->o.fps * 0.8);
    r->min_seg_frames = msf > fs ? msf : fs;
    r->lookahead_frames = (int)(r->o.fps * 1.0) < 1 ? 1 : (int)(r->o.fps * 1.0);
    r->cache_cap = r->o.segment_cache < 2 ? 2 : r->o.segment_cache;
    r->cache = calloc((size_t)r->cache_cap, sizeof *r->cache);
    build_silence(r);
    return r;
}

void xcfa_renderer_close(xcfa_renderer *r) {
    if (!r) return;
    for (int i = 0; i < r->ncache; i++) { for (int f = 0; f < r->cache[i].n; f++) xcfa_img_free(&r->cache[i].fr[f]); free(r->cache[i].fr); }
    free(r->cache); free(r->sil); free(r->alpha);
    xcfa_img_free(&r->base);
    xcfa_lib_close(r->lib);
    xcfa_close(r->m);
    free(r);
}

/* ---------------------------------------------------------------- morph frames */

typedef struct { xcfa_img *v; int n; } img_list;


/* mean(|a - b|) / 255 over the face region (or the whole frame) in float32, as np.mean of float32 arrays */
static double mean_abs_diff(const xcfa_renderer *r, const xcfa_img *a, const xcfa_img *b) {
    int x0 = 0, y0 = 0, w = a->w, h = a->h;
    if (r->has_face) { x0 = r->fr_x; y0 = r->fr_y; w = r->fr_w; h = r->fr_h; }
    double sum = 0;
    long cnt = 0;
    for (int y = y0; y < y0 + h && y < a->h; y++)
        for (int x = x0; x < x0 + w && x < a->w; x++)
            for (int k = 0; k < 3; k++) { sum += fabs((double)a->px[((size_t)y * a->w + x) * 3 + k] - b->px[((size_t)y * a->w + x) * 3 + k]); cnt++; }
    return cnt ? sum / (double)cnt : 0.0;
}

typedef struct { const xcfa_img *a, *b; const xcfa_farneback_params *p; xcfa_fimg out; } fb_job;
static void *fb_thread(void *arg) { fb_job *j = arg; j->out = xcfa_farneback(j->a, j->b, j->p); return NULL; }

/* _compute_morph_frames(fa, fb): frames 1..n of a warp blend, ending on fb. n = clip(diff * 25, 1, 5) */
static img_list compute_morph_frames(const xcfa_renderer *r, const xcfa_img *fa, const xcfa_img *fb) {
    img_list out = {NULL, 0};
    double diff = mean_abs_diff(r, fa, fb) / 255.0;
    double nd = diff * 25.0;
    int n = (int)(nd < 1.0 ? 1.0 : nd > 5.0 ? 5.0 : nd);
    int w = fa->w, h = fa->h;
    int hw = w / 2 < 1 ? 1 : w / 2, hh = h / 2 < 1 ? 1 : h / 2;
    xcfa_img a2 = xcfa_resize_u8(fa, hw, hh), b2 = xcfa_resize_u8(fb, hw, hh);
    xcfa_img ga = xcfa_bgr_to_gray(&a2), gb = xcfa_bgr_to_gray(&b2);
    xcfa_img_free(&a2); xcfa_img_free(&b2);
    xcfa_farneback_params p = xcfa_farneback_defaults();
    fb_job job = {&gb, &ga, &p, {0, 0, 0, NULL}}; /* the backward flow in a second thread, the forward one here */
    pthread_t th;
    int threaded = pthread_create(&th, NULL, fb_thread, &job) == 0;
    xcfa_fimg fab_h = xcfa_farneback(&ga, &gb, &p);
    if (threaded) pthread_join(th, NULL); else job.out = xcfa_farneback(&gb, &ga, &p);
    xcfa_fimg fba_h = job.out;
    xcfa_img_free(&ga); xcfa_img_free(&gb);
    if (!fab_h.px || !fba_h.px) { xcfa_fimg_free(&fab_h); xcfa_fimg_free(&fba_h); return out; }
    xcfa_fimg fab = xcfa_resize_f32(&fab_h, w, h), fba = xcfa_resize_f32(&fba_h, w, h); /* flows up to full size, ... */
    xcfa_fimg_free(&fab_h); xcfa_fimg_free(&fba_h);
    for (size_t i = 0; fab.px && fba.px && i < (size_t)w * h * 2; i++) { fab.px[i] *= 2.0f; fba.px[i] *= 2.0f; } /* ... and doubled */
    out.v = calloc((size_t)n, sizeof *out.v);
    if (out.v && fab.px && fba.px)
        for (int i = 1; i <= n; i++) out.v[out.n++] = xcfa_interpolate(fa, fb, &fab, &fba, (double)i / n);
    xcfa_fimg_free(&fab); xcfa_fimg_free(&fba);
    return out;
}

static void img_list_free(img_list *l) { for (int i = 0; i < l->n; i++) xcfa_img_free(&l->v[i]); free(l->v); l->v = NULL; l->n = 0; }

/* ---------------------------------------------------------------- render state */

/* one queued frame of a silence phase: a stored frame (fetched when shown) or an owned image (a morph step, or a held frame) */
typedef struct { int seg_pos, idx; xcfa_img *own; int *rc; } qitem;

typedef struct { qitem *v; int head, tail, cap; } queue_t;

typedef enum { SIL_NONE, SIL_ACTIVE, SIL_EXIT } sil_state_t;

struct xcfa_render {
    xcfa_renderer *r;
    const float *audio;
    size_t n_samples;
    xcfa_feat_stream *feats;
    size_t n_frames;
    double *energies_owned;
    const double *energies;
    int *silence_runs;
    size_t frame_cursor;
    xcfa_img composite;          /* composite_buf */
    xcfa_img last;               /* last_frame (owned copy) */
    /* frame-level mode */
    int cur_seg_id, cur_frame_idx, cooldown_left;
    sil_state_t sil_state;
    queue_t q;
    xcfa_img sil_cur;            /* the last frame built inside the machine (owned copy; px == NULL = none) */
    long sil_end_frame, sustain_run_end;
    double sil_entry_mul;
    int sustain_cycle_pos;
    /* segment mode state is added below */
    struct { double muls[SIL_STEPS]; int n, next, frames_per_step; xcfa_img cur; } bld; /* the silence phase being built */
    void *seg;
    unsigned long long rng;
};

static void queue_push(queue_t *q, qitem it) {
    if (q->tail == q->cap) {
        if (q->head > 0) { memmove(q->v, q->v + q->head, (size_t)(q->tail - q->head) * sizeof *q->v); q->tail -= q->head; q->head = 0; }
        if (q->tail == q->cap) { q->cap = q->cap ? q->cap * 2 : 64; q->v = realloc(q->v, (size_t)q->cap * sizeof *q->v); }
    }
    q->v[q->tail++] = it;
}

static int queue_empty(const queue_t *q) { return q->head == q->tail; }

static void qitem_release(qitem *it) {
    if (it->rc && --*it->rc == 0) { xcfa_img_free(it->own); free(it->own); free(it->rc); }
    it->own = NULL; it->rc = NULL;
}

static qitem queue_pop(queue_t *q) { return q->v[q->head++]; }

static void queue_clear(queue_t *q) { while (!queue_empty(q)) { qitem it = queue_pop(q); qitem_release(&it); } q->head = q->tail = 0; }

static qitem owned_item(xcfa_img img) { /* takes the image */
    qitem it = {-1, -1, malloc(sizeof(xcfa_img)), malloc(sizeof(int))};
    *it.own = img;
    *it.rc = 1;
    return it;
}

static int ensure_features(xcfa_render *s, size_t upto) { /* frames [0, upto) have features */
    while (xcfa_feat_ready(s->feats) < upto && xcfa_feat_ready(s->feats) < s->n_frames)
        if (!xcfa_feat_next(s->feats)) return -1;
    return 0;
}

static int *compute_silence_runs(const double *e, double thresh, size_t n) {
    int *res = calloc(n ? n : 1, sizeof(int));
    size_t i = 0;
    while (res && i < n) {
        if (e[i < n ? i : n - 1] < thresh) {
            size_t j = i + 1;
            while (j < n && e[j] < thresh) j++;
            for (size_t k = i; k < j; k++) res[k] = (int)(j - i);
            i = j;
        } else i++;
    }
    return res;
}

xcfa_render *xcfa_render_begin(xcfa_renderer *r, const float *audio, size_t n_samples) {
    xcfa_render *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->r = r;
    s->audio = audio;
    s->n_samples = n_samples;
    s->feats = xcfa_feat_open(audio, n_samples, r->o.fps, r->sample_fps, r->o.lead_ms);
    if (!s->feats) { free(s); return NULL; }
    s->n_frames = xcfa_feat_frames(s->feats);
    s->energies = xcfa_feat_energies(s->feats);
    s->silence_runs = compute_silence_runs(s->energies, XCFA_SILENCE_THRESHOLD, s->n_frames);
    s->composite = xcfa_img_clone(&r->base);
    s->last = xcfa_img_clone(&r->base);
    s->cur_seg_id = -1;
    s->sil_end_frame = -1;
    s->sil_entry_mul = 0.5;
    s->rng = 0x9E3779B97F4A7C15ull ^ r->o.seed;
    return s;
}

size_t xcfa_render_frame_count(const xcfa_render *s) { return s->n_frames; }

/* ---------------------------------------------------------------- writing a frame */

/* _write_frame / the segment mode's compose+micro: the frame that leaves the renderer (a copy the caller owns) */
static int emit(xcfa_render *s, xcfa_frame *out) {
    xcfa_renderer *r = s->r;
    xcfa_img *img = &s->composite;
    xcfa_img micro;
    if (r->o.micro_movement) {
        if (xcfa_apply_micro(img, (double)s->frame_cursor / r->o.fps, r->o.style_intensity, &micro)) {
            out->img = micro;
            goto done;
        }
    }
    out->img = xcfa_img_clone(img);
done:
    out->alpha = NULL;
    if (r->alpha) { out->alpha = malloc((size_t)r->out_w * r->out_h); if (out->alpha) memcpy(out->alpha, r->alpha, (size_t)r->out_w * r->out_h); }
    out->index = (int)s->frame_cursor;
    s->frame_cursor++;
    return out->img.px ? 1 : -1;
}

/* composite_buf[face region] = f[face region]; without a face region the frame is the whole picture */
static void put_frame(xcfa_render *s, const xcfa_img *f) {
    xcfa_renderer *r = s->r;
    if (!r->has_face) { memcpy(s->composite.px, f->px, (size_t)f->w * f->h * 3); return; }
    for (int y = r->fr_y; y < r->fr_y + r->fr_h && y < f->h; y++)
        memcpy(s->composite.px + ((size_t)y * f->w + r->fr_x) * 3, f->px + ((size_t)y * f->w + r->fr_x) * 3,
               (size_t)(r->fr_x + r->fr_w <= f->w ? r->fr_w : f->w - r->fr_x) * 3);
}

static void set_last(xcfa_render *s, const xcfa_img *f) { memcpy(s->last.px, f->px, (size_t)f->w * f->h * 3); }

/* ---------------------------------------------------------------- silence state machine helpers */

static double nearest_mul(const xcfa_renderer *r, double target, int *group) {
    if (!r->nsil) { *group = -1; return 0.0; }
    int best = 0;
    for (int i = 1; i < r->nsil; i++) if (fabs(r->sil[i].mul - target) < fabs(r->sil[best].mul - target)) best = i;
    *group = best;
    return r->sil[best].mul;
}

/* _pick_silence_seg: the entry of the nearest multiplier whose first frame is closest (mean abs diff in the face region) to last */
static int pick_silence_seg(xcfa_renderer *r, double target_mul, const xcfa_img *last) {
    int g;
    nearest_mul(r, target_mul, &g);
    if (g < 0 || !r->sil[g].n) return -1;
    const sil_group *grp = &r->sil[g];
    if (grp->n == 1 || !last) return grp->pos[0];
    int best = grp->pos[0];
    double bd = 1e300;
    for (int i = 0; i < grp->n; i++) {
        const xcfa_img *f0 = get_frame(r, grp->pos[i], 0);
        if (!f0) continue;
        double d = mean_abs_diff(r, f0, last);
        if (d < bd) { bd = d; best = grp->pos[i]; }
    }
    return best;
}

/* _build_silence_phase, built lazily: the original builds every step of a phase at once (a pause of ~0.6 s in a real-time player);
 * a step only depends on the last stored frame of the step before it, so the steps are built one at a time, when the queue runs
 * dry, and the frames come out exactly the same. */
static void phase_start(xcfa_render *s, const double *muls, int nmul, int frames_per_step, const xcfa_img *cur) {
    xcfa_img_free(&s->bld.cur);
    memcpy(s->bld.muls, muls, (size_t)nmul * sizeof(double));
    s->bld.n = nmul;
    s->bld.next = 0;
    s->bld.frames_per_step = frames_per_step;
    s->bld.cur = xcfa_img_clone(cur);
}

static void phase_step(xcfa_render *s) { /* one iteration of the per-multiplier loop of _build_silence_phase */
    xcfa_renderer *r = s->r;
    xcfa_img *cur = &s->bld.cur;
    int k = s->bld.next++, frames_per_step = s->bld.frames_per_step;
    int pos = pick_silence_seg(r, s->bld.muls[k], cur->px ? cur : NULL);
    if (pos < 0) { /* no silence data: hold the current frame */
        int *rc = malloc(sizeof(int));
        xcfa_img *own = malloc(sizeof(xcfa_img));
        *own = xcfa_img_clone(cur);
        *rc = frames_per_step;
        for (int i = 0; i < frames_per_step; i++) { qitem it = {-1, -1, own, rc}; queue_push(&s->q, it); }
        return;
    }
    int nseg = r->m->seg[pos].n;
    const xcfa_img *f0 = get_frame(r, pos, 0);
    if (!f0) return;
    img_list morph = compute_morph_frames(r, cur, f0); /* morph into the first frame of the segment */
    for (int i = 0; i < morph.n; i++) { qitem it = owned_item(morph.v[i]); queue_push(&s->q, it); }
    free(morph.v); /* the images moved into the queue */
    for (int i = 0; i < frames_per_step; i++) { qitem it = {pos, i % nseg, NULL, NULL}; queue_push(&s->q, it); }
    const xcfa_img *lastf = get_frame(r, pos, (frames_per_step - 1) % nseg);
    if (lastf) { xcfa_img c = xcfa_img_clone(lastf); xcfa_img_free(cur); *cur = c; }
}

/* make sure a queued frame exists when the phase still has steps to build; 1 = the queue has a frame */
static int fill_queue(xcfa_render *s) {
    while (queue_empty(&s->q) && s->bld.next < s->bld.n) phase_step(s);
    return !queue_empty(&s->q);
}

static const xcfa_img *item_image(xcfa_render *s, const qitem *it) {
    return it->own ? it->own : get_frame(s->r, it->seg_pos, it->idx);
}

/* pops the next queued frame, makes it the current one (last, sil_cur) and emits it */
static int drain_one(xcfa_render *s, xcfa_frame *out) {
    qitem it = queue_pop(&s->q);
    const xcfa_img *f = item_image(s, &it);
    if (!f) { qitem_release(&it); return -1; }
    if (s->sil_cur.px) xcfa_img_free(&s->sil_cur);
    s->sil_cur = xcfa_img_clone(f);
    set_last(s, f);
    put_frame(s, f);
    qitem_release(&it);
    return emit(s, out);
}

/* ---------------------------------------------------------------- frame-level mode */

static int render_frame_level(xcfa_render *s, xcfa_frame *out) {
    xcfa_renderer *r = s->r;
    const int fps = r->o.fps;
    const int jump_cooldown = (int)nearbyint(fps / 5.0) < 1 ? 1 : (int)nearbyint(fps / 5.0);
    const float switch_threshold = 0.12f;
    const long min_sil_frames = (long)fps * 2;
    const int frames_per_step = fps / SIL_STEPS > 2 ? fps / SIL_STEPS : 2;
    const int sustain_step_frames = frames_per_step;
    const int has_silence_segs = r->nsil > 0;
    size_t cur_i = s->frame_cursor;
    size_t ne = s->n_frames;
    double energy = s->energies[cur_i < ne ? cur_i : ne - 1];
    int in_sil = energy < XCFA_SILENCE_THRESHOLD;
    int run_len = s->silence_runs[cur_i < ne ? cur_i : ne - 1];

    if (s->sil_state == SIL_ACTIVE) { /* drain the queue, or refill the sustain, or build the exit */
        if (fill_queue(s)) return drain_one(s, out);
        if ((long)s->frame_cursor < s->sustain_run_end) {
            double mul = SIL_SUSTAIN_MULS[s->sustain_cycle_pos % SIL_STEPS];
            s->sustain_cycle_pos++;
            phase_start(s, &mul, 1, sustain_step_frames, &s->sil_cur);
            if (fill_queue(s)) return drain_one(s, out);
            /* the step built nothing: fall through to the exit check */
        } else {
            double max_avail = 0.5;
            if (r->nsil) { max_avail = r->sil[0].mul; for (int i = 1; i < r->nsil; i++) if (r->sil[i].mul > max_avail) max_avail = r->sil[i].mul; }
            double exit_top = s->sil_entry_mul < max_avail ? s->sil_entry_mul : max_avail;
            double exit_muls[SIL_STEPS];
            for (int i = 0; i < SIL_STEPS; i++) exit_muls[i] = round1(exit_top * i / (SIL_STEPS - 1));
            phase_start(s, exit_muls, SIL_STEPS, frames_per_step, &s->sil_cur);
            s->sil_state = SIL_EXIT;
            if (fill_queue(s)) return drain_one(s, out);
        }
    }
    if (s->sil_state == SIL_EXIT) {
        if (fill_queue(s)) return drain_one(s, out);
        s->sil_state = SIL_NONE; /* the exit queue is drained */
    }

    /* the start of a long silence run */
    double prev_energy = s->frame_cursor > 0 ? s->energies[(s->frame_cursor - 1) < ne ? s->frame_cursor - 1 : ne - 1] : 1.0;
    int is_run_start = in_sil && prev_energy >= XCFA_SILENCE_THRESHOLD;
    if (is_run_start && run_len >= min_sil_frames && s->sil_state == SIL_NONE && has_silence_segs && (long)s->frame_cursor > s->sil_end_frame) {
        double max_avail = r->sil[0].mul;
        for (int i = 1; i < r->nsil; i++) if (r->sil[i].mul > max_avail) max_avail = r->sil[i].mul;
        s->sil_entry_mul = 0.5 < max_avail ? 0.5 : max_avail;
        double entry_muls[SIL_STEPS];
        for (int i = 0; i < SIL_STEPS; i++) entry_muls[i] = round1(s->sil_entry_mul * (1.0 - (double)i / (SIL_STEPS - 1)));
        xcfa_img start = xcfa_img_clone(&s->last);
        if (s->sil_cur.px) xcfa_img_free(&s->sil_cur);
        s->sil_cur = start;
        phase_start(s, entry_muls, SIL_STEPS, frames_per_step, &s->sil_cur);
        s->sustain_run_end = (long)s->frame_cursor + run_len - fps; /* leave a second for the exit */
        s->sil_end_frame = (long)s->frame_cursor + run_len;
        s->sil_state = SIL_ACTIVE;
        s->sustain_cycle_pos = 0;
        if (fill_queue(s)) return drain_one(s, out);
    }

    /* normal speech / short silence */
    if (s->cooldown_left <= 0) {
        if (ensure_features(s, cur_i + 1) == 0 && r->lib->nflat > 0) {
            xcfa_pos cur = {s->cur_seg_id, s->cur_frame_idx};
            cur = xcfa_lib_find_best_frame(r->lib, xcfa_feat_vector(s->feats, cur_i < ne ? cur_i : ne - 1), cur, switch_threshold);
            s->cur_seg_id = cur.seg; s->cur_frame_idx = cur.frame;
        }
        s->cooldown_left = jump_cooldown;
    } else {
        if (xcfa_lib_flat_index(r->lib, s->cur_seg_id, s->cur_frame_idx + 1) >= 0) s->cur_frame_idx++;
        s->cooldown_left--;
    }
    if (s->cur_seg_id >= 0) {
        int p = xcfa_lib_position_of_id(r->lib, s->cur_seg_id);
        if (p >= 0 && r->m->seg[p].n > 0) {
            int idx = s->cur_frame_idx < r->m->seg[p].n - 1 ? s->cur_frame_idx : r->m->seg[p].n - 1;
            const xcfa_img *f = get_frame(r, p, idx);
            if (f) set_last(s, f);
        }
    }
    put_frame(s, &s->last);
    return emit(s, out);
}

/* ---------------------------------------------------------------- segment mode */

/* _render_segment_mode as a state machine: planner -> (fallback | morph -> pairs -> hold) per segment, one frame per call */
typedef enum { SM_PLAN, SM_FALLBACK, SM_MORPH, SM_PAIRS, SM_HOLD } seg_phase_t;

typedef struct {
    seg_phase_t phase;
    int last_seg;            /* position of the previous segment, -1 at the start */
    int silence_streak;
    int cur;                 /* position of the segment being played */
    int fb_left;             /* base-portrait frames left of the fallback */
    img_list morph;
    int morph_i;
    int pair_idx, sub_idx, seg_silence_cnt, interrupted;
    xcfa_img last_rendered;  /* owned */
    int frames_this_seg;
    int *quiet_ids, n_quiet; /* ids of the default silence group, ascending */
    int fresh_morph;         /* the first morph of a segment was already built */
    xcfa_fimg flow_ab, flow_ba;
    int flow_pair;           /* the pair the flows belong to, -1 = none */
} seg_state_t;

static double rng_next(void *arg) { /* xorshift64*, uniform in [0, 1) */
    xcfa_render *s = arg;
    s->rng ^= s->rng >> 12; s->rng ^= s->rng << 25; s->rng ^= s->rng >> 27;
    return (double)((s->rng * 2685821657736338717ull) >> 11) / 9007199254740992.0;
}

static int cmp_int(const void *a, const void *b) { return *(const int *)a - *(const int *)b; }

static int segment_mode_init(xcfa_render *s) {
    xcfa_renderer *r = s->r;
    seg_state_t *st = calloc(1, sizeof *st);
    if (!st) return -1;
    st->last_seg = -1;
    st->flow_pair = -1;
    if (r->nsil) { /* Python: {sd[0] for sd in library.silence_data} */
        const sil_group *g = &r->sil[r->sil_default];
        st->quiet_ids = malloc((size_t)(g->n + 1) * sizeof(int));
        for (int i = 0; i < g->n; i++) st->quiet_ids[st->n_quiet++] = r->m->seg[g->pos[i]].id;
        qsort(st->quiet_ids, (size_t)st->n_quiet, sizeof(int), cmp_int);
    }
    s->seg = st;
    return 0;
}

static void segment_mode_free(xcfa_render *s) {
    seg_state_t *st = s->seg;
    if (!st) return;
    img_list_free(&st->morph);
    xcfa_img_free(&st->last_rendered);
    xcfa_fimg_free(&st->flow_ab); xcfa_fimg_free(&st->flow_ba);
    free(st->quiet_ids);
    free(st);
}

static int render_segment(xcfa_render *s, xcfa_frame *out) {
    xcfa_renderer *r = s->r;
    seg_state_t *st = s->seg;
    const double thresh = XCFA_SILENCE_THRESHOLD;
    size_t ne = s->n_frames;
    for (;;) {
        if (s->frame_cursor >= s->n_frames) return 0;
        size_t cur_i = s->frame_cursor;
        switch (st->phase) {
        case SM_PLAN: {
            double energy = s->energies[cur_i < ne ? cur_i : ne - 1];
            st->silence_streak = energy < thresh ? st->silence_streak + 1 : 0;
            size_t end = cur_i + (size_t)r->lookahead_frames < ne ? cur_i + (size_t)r->lookahead_frames : ne;
            if (ensure_features(s, end) != 0) return -1;
            const float *ahead[256];
            int n_ahead = (int)(end - cur_i) < 256 ? (int)(end - cur_i) : 256;
            for (int i = 0; i < n_ahead; i++) ahead[i] = xcfa_feat_vector(s->feats, cur_i + (size_t)i);
            int pos = xcfa_lib_select_segment(r->lib, ahead, n_ahead, st->last_seg, st->silence_streak, st->quiet_ids, st->n_quiet,
                                              SILENCE_ONSET, rng_next, s);
            st->cur = pos;
            const xcfa_segment *sg = &r->m->seg[pos];
            if (sg->n < 2) { /* fewer than two frames: the base portrait stands in */
                st->fb_left = (sg->n - 1) * r->interp_per_pair;
                st->phase = SM_FALLBACK;
                break;
            }
            img_list_free(&st->morph);
            st->morph_i = 0;
            if (st->last_seg >= 0) {
                const xcfa_img *f0 = get_frame(r, pos, 0);
                if (!f0) return -1;
                st->morph = compute_morph_frames(r, &s->last, f0);
            }
            xcfa_img_free(&st->last_rendered);
            const xcfa_img *f0 = get_frame(r, pos, 0);
            if (!f0) return -1;
            st->last_rendered = xcfa_img_clone(f0);
            st->pair_idx = 0; st->sub_idx = 0; st->seg_silence_cnt = 0; st->interrupted = 0; st->flow_pair = -1;
            st->phase = SM_MORPH;
            break;
        }
        case SM_FALLBACK:
            if (st->fb_left > 0) {
                st->fb_left--;
                put_frame(s, &r->base);
                return emit(s, out);
            }
            set_last(s, &r->base);
            st->last_seg = st->cur;
            st->phase = SM_PLAN;
            break;
        case SM_MORPH:
            if (st->morph_i < st->morph.n) {
                put_frame(s, &st->morph.v[st->morph_i++]);
                return emit(s, out);
            }
            st->phase = SM_PAIRS;
            break;
        case SM_PAIRS: {
            const xcfa_segment *sg = &r->m->seg[st->cur];
            if (st->interrupted || st->pair_idx >= sg->n - 1) { /* the pair loop is over */
                set_last(s, &st->last_rendered);
                st->last_seg = st->cur;
                if (st->interrupted && st->seg_silence_cnt >= SILENCE_ONSET) st->silence_streak = SILENCE_ONSET;
                if (!st->interrupted) { /* hold the last frame up to the minimum length */
                    st->frames_this_seg = r->interp_per_pair * (sg->n - 1);
                    put_frame(s, &st->last_rendered); /* seg_composed */
                    st->phase = SM_HOLD;
                } else st->phase = SM_PLAN;
                break;
            }
            if (st->sub_idx >= r->interp_per_pair) { st->sub_idx = 0; st->pair_idx++; break; }
            double cur_energy = s->energies[cur_i < ne ? cur_i : ne - 1];
            if (cur_energy < thresh) {
                st->seg_silence_cnt++;
                if (st->seg_silence_cnt >= SILENCE_ONSET) { st->interrupted = 1; break; }
            } else st->seg_silence_cnt = 0;
            const xcfa_img *fa = get_frame(r, st->cur, st->pair_idx);
            if (!fa) return -1;
            double t = (double)st->sub_idx / r->interp_per_pair;
            xcfa_img interp;
            if (t <= 0.0) interp = xcfa_img_clone(fa); /* _interpolate returns fa.copy() at t = 0: no flows needed */
            else {
                const xcfa_img *fb = get_frame(r, st->cur, st->pair_idx + 1);
                fa = get_frame(r, st->cur, st->pair_idx);
                if (!fb || !fa) return -1;
                if (st->flow_pair != st->pair_idx) {
                    xcfa_fimg_free(&st->flow_ab); xcfa_fimg_free(&st->flow_ba);
                    if (xcfa_flow_pair(r->m, st->cur, st->pair_idx, r->out_w, r->out_h, &st->flow_ab, &st->flow_ba) != 0) return -1;
                    st->flow_pair = st->pair_idx;
                }
                interp = xcfa_interpolate(fa, fb, &st->flow_ab, &st->flow_ba, t);
            }
            if (!interp.px) return -1;
            xcfa_img_free(&st->last_rendered);
            st->last_rendered = interp;
            st->sub_idx++;
            put_frame(s, &st->last_rendered);
            return emit(s, out);
        }
        case SM_HOLD: {
            if (st->frames_this_seg >= r->min_seg_frames) { st->phase = SM_PLAN; break; }
            double cur_energy = s->energies[cur_i < ne ? cur_i : ne - 1];
            if (cur_energy < thresh) {
                st->silence_streak++;
                if (st->silence_streak >= SILENCE_ONSET) { st->phase = SM_PLAN; break; }
            } else st->silence_streak = 0;
            st->frames_this_seg++;
            return emit(s, out); /* the composite still holds seg_composed */
        }
        }
    }
}

/* ---------------------------------------------------------------- dispatch */

int xcfa_render_next(xcfa_render *s, xcfa_frame *out) {
    if (s->frame_cursor >= s->n_frames) return 0;
    if (s->r->o.mode == XCFA_MODE_SEGMENT) {
        if (!s->seg && segment_mode_init(s) != 0) return -1;
        return render_segment(s, out);
    }
    return render_frame_level(s, out);
}

void xcfa_render_end(xcfa_render *s) {
    if (!s) return;
    segment_mode_free(s);
    queue_clear(&s->q);
    free(s->q.v);
    xcfa_img_free(&s->composite); xcfa_img_free(&s->last); xcfa_img_free(&s->sil_cur); xcfa_img_free(&s->bld.cur);
    xcfa_feat_close(s->feats);
    free(s->silence_runs); free(s->energies_owned);
    free(s);
}
