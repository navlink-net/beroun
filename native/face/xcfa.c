// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* xcfa.c - reader of the .xcfa v2 face model. See xcfa.h. */
#define _FILE_OFFSET_BITS 64
#define _GNU_SOURCE
#include "xcfa.h"

#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

/* ---------------------------------------------------------------- msgpack cursor */

typedef struct {
    const uint8_t *p, *end;
    int bad; /* set on the first error; every later call is a no-op returning zero */
} cur_t;

static uint64_t be(const uint8_t *p, int n) {
    uint64_t v = 0;
    while (n--) v = (v << 8) | *p++;
    return v;
}

static int need(cur_t *c, size_t n) {
    if (c->bad || (size_t)(c->end - c->p) < n) {
        c->bad = 1;
        return 0;
    }
    return 1;
}

/* integer of any msgpack width; floats are rejected */
static int64_t rd_int(cur_t *c) {
    if (!need(c, 1)) return 0;
    uint8_t t = *c->p++;
    if (t <= 0x7f) return t;
    if (t >= 0xe0) return (int8_t)t;
    int n;
    switch (t) {
    case 0xcc: n = 1; break;
    case 0xcd: n = 2; break;
    case 0xce: n = 4; break;
    case 0xcf: n = 8; break;
    case 0xd0: if (!need(c, 1)) return 0; return (int8_t)*c->p++;
    case 0xd1: if (!need(c, 2)) return 0; { int16_t v = (int16_t)be(c->p, 2); c->p += 2; return v; }
    case 0xd2: if (!need(c, 4)) return 0; { int32_t v = (int32_t)be(c->p, 4); c->p += 4; return v; }
    case 0xd3: if (!need(c, 8)) return 0; { int64_t v = (int64_t)be(c->p, 8); c->p += 8; return v; }
    default: c->bad = 1; return 0;
    }
    if (!need(c, n)) return 0;
    uint64_t v = be(c->p, n);
    c->p += n;
    return (int64_t)v;
}

static double rd_float(cur_t *c) {
    if (!need(c, 1)) return 0;
    uint8_t t = *c->p;
    if (t == 0xca) {
        c->p++;
        if (!need(c, 4)) return 0;
        uint32_t u = (uint32_t)be(c->p, 4);
        c->p += 4;
        float f;
        memcpy(&f, &u, 4);
        return f;
    }
    if (t == 0xcb) {
        c->p++;
        if (!need(c, 8)) return 0;
        uint64_t u = be(c->p, 8);
        c->p += 8;
        double d;
        memcpy(&d, &u, 8);
        return d;
    }
    return (double)rd_int(c); /* an int where a float is expected: python's msgpack may pack 0.0 this way */
}

static int rd_bool(cur_t *c) {
    if (!need(c, 1)) return 0;
    uint8_t t = *c->p++;
    if (t == 0xc2) return 0;
    if (t == 0xc3) return 1;
    c->bad = 1;
    return 0;
}

static int64_t rd_array(cur_t *c) {
    if (!need(c, 1)) return 0;
    uint8_t t = *c->p++;
    if (t >= 0x90 && t <= 0x9f) return t & 0x0f;
    if (t == 0xdc) { if (!need(c, 2)) return 0; int64_t n = (int64_t)be(c->p, 2); c->p += 2; return n; }
    if (t == 0xdd) { if (!need(c, 4)) return 0; int64_t n = (int64_t)be(c->p, 4); c->p += 4; return n; }
    c->bad = 1;
    return 0;
}

static int64_t rd_map(cur_t *c) {
    if (!need(c, 1)) return 0;
    uint8_t t = *c->p++;
    if (t >= 0x80 && t <= 0x8f) return t & 0x0f;
    if (t == 0xde) { if (!need(c, 2)) return 0; int64_t n = (int64_t)be(c->p, 2); c->p += 2; return n; }
    if (t == 0xdf) { if (!need(c, 4)) return 0; int64_t n = (int64_t)be(c->p, 4); c->p += 4; return n; }
    c->bad = 1;
    return 0;
}

/* str (any width): pointer and length, not NUL terminated */
static const uint8_t *rd_str(cur_t *c, size_t *len) {
    *len = 0;
    if (!need(c, 1)) return NULL;
    uint8_t t = *c->p++;
    size_t n;
    if (t >= 0xa0 && t <= 0xbf) n = t & 0x1f;
    else if (t == 0xd9) { if (!need(c, 1)) return NULL; n = *c->p++; }
    else if (t == 0xda) { if (!need(c, 2)) return NULL; n = be(c->p, 2); c->p += 2; }
    else if (t == 0xdb) { if (!need(c, 4)) return NULL; n = be(c->p, 4); c->p += 4; }
    else { c->bad = 1; return NULL; }
    if (!need(c, n)) return NULL;
    const uint8_t *s = c->p;
    c->p += n;
    *len = n;
    return s;
}

static const uint8_t *rd_bin(cur_t *c, size_t *len) {
    *len = 0;
    if (!need(c, 1)) return NULL;
    uint8_t t = *c->p++;
    size_t n;
    if (t == 0xc4) { if (!need(c, 1)) return NULL; n = *c->p++; }
    else if (t == 0xc5) { if (!need(c, 2)) return NULL; n = be(c->p, 2); c->p += 2; }
    else if (t == 0xc6) { if (!need(c, 4)) return NULL; n = be(c->p, 4); c->p += 4; }
    else { c->bad = 1; return NULL; }
    if (!need(c, n)) return NULL;
    const uint8_t *s = c->p;
    c->p += n;
    *len = n;
    return s;
}

static void skip(cur_t *c) {
    if (!need(c, 1)) return;
    uint8_t t = *c->p;
    if (t <= 0x7f || t >= 0xe0 || t == 0xc0 || t == 0xc2 || t == 0xc3) { c->p++; return; }
    if (t >= 0x80 && t <= 0x8f) { int64_t n = rd_map(c); while (n-- > 0 && !c->bad) { skip(c); skip(c); } return; }
    if (t == 0xde || t == 0xdf) { int64_t n = rd_map(c); while (n-- > 0 && !c->bad) { skip(c); skip(c); } return; }
    if ((t >= 0x90 && t <= 0x9f) || t == 0xdc || t == 0xdd) { int64_t n = rd_array(c); while (n-- > 0 && !c->bad) skip(c); return; }
    if ((t >= 0xa0 && t <= 0xbf) || t == 0xd9 || t == 0xda || t == 0xdb) { size_t l; rd_str(c, &l); return; }
    if (t == 0xc4 || t == 0xc5 || t == 0xc6) { size_t l; rd_bin(c, &l); return; }
    if (t == 0xca) { c->p++; if (need(c, 4)) c->p += 4; return; }
    if (t == 0xcb) { c->p++; if (need(c, 8)) c->p += 8; return; }
    if (t == 0xcc || t == 0xd0) { c->p++; if (need(c, 1)) c->p += 1; return; }
    if (t == 0xcd || t == 0xd1) { c->p++; if (need(c, 2)) c->p += 2; return; }
    if (t == 0xce || t == 0xd2) { c->p++; if (need(c, 4)) c->p += 4; return; }
    if (t == 0xcf || t == 0xd3) { c->p++; if (need(c, 8)) c->p += 8; return; }
    c->bad = 1; /* ext types are not used by the catalog */
}

static int key_is(const uint8_t *k, size_t n, const char *s) { return strlen(s) == n && memcmp(k, s, n) == 0; }

/* ---------------------------------------------------------------- catalog */

static void seterr(char *err, size_t n, const char *fmt, ...) {
    if (!err || !n) return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(err, n, fmt, ap);
    va_end(ap);
}

/* [[off, size], ...] */
static xcfa_blob *rd_blobs(cur_t *c, int *count) {
    int64_t n = rd_array(c);
    *count = 0;
    if (c->bad || n < 0 || n > (1 << 20)) { c->bad = 1; return NULL; }
    xcfa_blob *b = calloc(n ? n : 1, sizeof *b);
    if (!b) { c->bad = 1; return NULL; }
    for (int64_t i = 0; i < n && !c->bad; i++) {
        if (rd_array(c) != 2) { c->bad = 1; break; }
        b[i].off = (uint64_t)rd_int(c);
        b[i].size = (uint32_t)rd_int(c);
    }
    *count = (int)n;
    return b;
}

/* [[off, size, scale], ...] */
static xcfa_flow *rd_flows(cur_t *c, int *count) {
    int64_t n = rd_array(c);
    *count = 0;
    if (c->bad || n < 0 || n > (1 << 20)) { c->bad = 1; return NULL; }
    xcfa_flow *f = calloc(n ? n : 1, sizeof *f);
    if (!f) { c->bad = 1; return NULL; }
    for (int64_t i = 0; i < n && !c->bad; i++) {
        if (rd_array(c) != 3) { c->bad = 1; break; }
        f[i].off = (uint64_t)rd_int(c);
        f[i].size = (uint32_t)rd_int(c);
        f[i].scale = (float)rd_float(c);
    }
    *count = (int)n;
    return f;
}

static void rd_floats_le(const uint8_t *p, float *out, size_t count) {
    for (size_t i = 0; i < count; i++) memcpy(&out[i], p + i * 4, 4); /* float32 little endian = host order on x86 and ARM */
}

static void rd_segment(cur_t *c, xcfa_segment *s) {
    int64_t nkeys = rd_map(c);
    for (int64_t i = 0; i < nkeys && !c->bad; i++) {
        size_t kl;
        const uint8_t *k = rd_str(c, &kl);
        if (c->bad) return;
        if (key_is(k, kl, "id")) s->id = (int)rd_int(c);
        else if (key_is(k, kl, "n")) s->n = (int)rd_int(c);
        else if (key_is(k, kl, "blink")) s->blink = rd_bool(c);
        else if (key_is(k, kl, "h")) s->h = (int)rd_int(c);
        else if (key_is(k, kl, "w")) s->w = (int)rd_int(c);
        else if (key_is(k, kl, "sil")) s->silence = rd_bool(c);
        else if (key_is(k, kl, "sil_mul")) {
            if (need(c, 1) && *c->p == 0xc0) { c->p++; }
            else { s->silence_mul = (float)rd_float(c); s->has_silence_mul = 1; s->silence = 1; }
        } else if (key_is(k, kl, "mf")) {
            size_t l;
            const uint8_t *b = rd_bin(c, &l);
            if (b && l >= XCFA_FEATURES * 4) { rd_floats_le(b, s->mean, XCFA_FEATURES); s->has_mean = 1; }
        } else if (key_is(k, kl, "af")) {
            size_t l;
            const uint8_t *b = rd_bin(c, &l);
            if (b && l) {
                size_t rows = l / (XCFA_FEATURES * 4);
                s->naudio = (int)rows;
                s->audio = calloc(rows * XCFA_FEATURES, sizeof(float));
                if (!s->audio) { c->bad = 1; return; }
                rd_floats_le(b, s->audio, rows * XCFA_FEATURES);
            }
        } else if (key_is(k, kl, "frames")) {
            int cnt;
            s->frames = rd_blobs(c, &cnt);
            if (cnt != s->n && s->n) s->n = cnt < s->n ? cnt : s->n; /* never index past what is stored */
        } else if (key_is(k, kl, "flows")) s->flows = rd_flows(c, &s->nflows);
        else if (key_is(k, kl, "bg_frames")) s->bg = rd_blobs(c, &s->nbg);
        else skip(c);
    }
}

static void free_model_arrays(xcfa_model *m) {
    if (!m->seg) return;
    for (int i = 0; i < m->nseg; i++) {
        free(m->seg[i].audio);
        free(m->seg[i].frames);
        free(m->seg[i].flows);
        free(m->seg[i].bg);
    }
    free(m->seg);
    m->seg = NULL;
}

static void rd_face_region(cur_t *c, xcfa_model *m) {
    int64_t n = rd_map(c);
    for (int64_t i = 0; i < n && !c->bad; i++) {
        size_t kl;
        const uint8_t *k = rd_str(c, &kl);
        if (c->bad) return;
        if (key_is(k, kl, "x_frac")) m->face_x = rd_float(c);
        else if (key_is(k, kl, "y_frac")) m->face_y = rd_float(c);
        else if (key_is(k, kl, "w_frac")) m->face_w = rd_float(c);
        else if (key_is(k, kl, "h_frac")) m->face_h = rd_float(c);
        else if (key_is(k, kl, "eye_y_frac")) m->eye_y = rd_float(c);
        else skip(c);
    }
    m->has_face_region = 1;
}

static void rd_catalog(cur_t *c, xcfa_model *m) {
    int64_t n = rd_map(c);
    for (int64_t i = 0; i < n && !c->bad; i++) {
        size_t kl;
        const uint8_t *k = rd_str(c, &kl);
        if (c->bad) return;
        if (key_is(k, kl, "cfa_v")) m->cfa_version = (int)rd_int(c);
        else if (key_is(k, kl, "fps")) m->fps = (int)rd_int(c);
        else if (key_is(k, kl, "flow_div")) m->flow_div = (int)rd_int(c);
        else if (key_is(k, kl, "has_bg")) m->has_bg = rd_bool(c);
        else if (key_is(k, kl, "face_region")) {
            if (need(c, 1) && *c->p == 0xc0) c->p++;
            else rd_face_region(c, m);
        } else if (key_is(k, kl, "base")) {
            if (rd_array(c) == 2) { m->base.off = (uint64_t)rd_int(c); m->base.size = (uint32_t)rd_int(c); } else c->bad = 1;
        } else if (key_is(k, kl, "base_bg")) {
            if (rd_array(c) == 2) { m->base_bg.off = (uint64_t)rd_int(c); m->base_bg.size = (uint32_t)rd_int(c); } else c->bad = 1;
        } else if (key_is(k, kl, "segments")) {
            int64_t cnt = rd_array(c);
            if (c->bad || cnt < 0 || cnt > (1 << 22)) { c->bad = 1; return; }
            m->seg = calloc(cnt ? cnt : 1, sizeof *m->seg);
            if (!m->seg) { c->bad = 1; return; }
            m->nseg = (int)cnt;
            for (int64_t j = 0; j < cnt && !c->bad; j++) rd_segment(c, &m->seg[j]);
        } else skip(c);
    }
}

/* ---------------------------------------------------------------- public */

int xcfa_read(const xcfa_model *m, uint64_t off, uint32_t size, void *buf) {
    uint8_t *p = buf;
    while (size) {
        ssize_t r = pread(m->fd, p, size, (off_t)off);
        if (r <= 0) return -1;
        p += r;
        off += (uint64_t)r;
        size -= (uint32_t)r;
    }
    return 0;
}

int xcfa_find_segment(const xcfa_model *m, int id) {
    for (int i = 0; i < m->nseg; i++)
        if (m->seg[i].id == id) return i;
    return -1;
}

xcfa_model *xcfa_open(const char *path, char *err, size_t errlen) {
    xcfa_model *m = calloc(1, sizeof *m);
    if (!m) { seterr(err, errlen, "out of memory"); return NULL; }
    m->fd = open(path, O_RDONLY | O_CLOEXEC);
    if (m->fd < 0) { seterr(err, errlen, "cannot open %s", path); free(m); return NULL; }
    struct stat st;
    uint8_t head[16], tail[8];
    if (fstat(m->fd, &st) != 0 || st.st_size < 32) { seterr(err, errlen, "%s: too small for an xcfa file", path); goto fail; }
    m->file_size = (uint64_t)st.st_size;
    if (xcfa_read(m, 0, 16, head) != 0 || xcfa_read(m, m->file_size - 8, 8, tail) != 0) { seterr(err, errlen, "%s: cannot read the header", path); goto fail; }
    if (memcmp(head, "XCFA", 4) != 0) { seterr(err, errlen, "%s: not an xcfa file (bad magic)", path); goto fail; }
    m->version = (int)(head[4] | head[5] << 8 | head[6] << 16 | (uint32_t)head[7] << 24);
    if (m->version != 2) { seterr(err, errlen, "%s: unsupported xcfa version %d (expected 2)", path, m->version); goto fail; }
    memcpy(&m->catalog_offset, head + 8, 8); /* little endian */
    memcpy(&m->catalog_len, tail, 8);
    if (m->catalog_offset + m->catalog_len + 8 != m->file_size || m->catalog_len > (1u << 30)) {
        seterr(err, errlen, "%s: the catalog does not fit the file (offset %llu, length %llu, file %llu)", path,
               (unsigned long long)m->catalog_offset, (unsigned long long)m->catalog_len, (unsigned long long)m->file_size);
        goto fail;
    }
    uint8_t *cat = malloc(m->catalog_len ? m->catalog_len : 1);
    if (!cat) { seterr(err, errlen, "out of memory for the catalog"); goto fail; }
    if (xcfa_read(m, m->catalog_offset, (uint32_t)m->catalog_len, cat) != 0) { free(cat); seterr(err, errlen, "%s: cannot read the catalog", path); goto fail; }
    cur_t c = {cat, cat + m->catalog_len, 0};
    m->flow_div = 8;
    m->fps = 5;
    rd_catalog(&c, m);
    free(cat);
    if (c.bad || !m->seg) { seterr(err, errlen, "%s: the catalog is damaged", path); goto fail; }
    return m;
fail:
    xcfa_close(m);
    return NULL;
}

void xcfa_close(xcfa_model *m) {
    if (!m) return;
    free_model_arrays(m);
    if (m->fd >= 0) close(m->fd);
    free(m);
}
