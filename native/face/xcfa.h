// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* xcfa.h - reader of the .xcfa v2 face model (C port of FaceAnimation/XcfaLoader.py).
 *
 * Layout of the file (see XcfaLoader.py):
 *   [0:4] "XCFA"  [4:8] version u32 LE (=2)  [8:16] catalog_offset u64 LE
 *   data blobs: base portrait (PNG), frame JPEGs, int8 flow blobs, background-mask PNGs
 *   [catalog_offset .. EOF-8] msgpack catalog     [EOF-8 .. EOF] catalog length u64 LE
 *
 * Only the catalog (a few MB) is kept in memory; blobs are read on demand with pread(), so a
 * model of any size (the real ones are about 12 GB) costs nothing until a frame is asked for.
 * No globals, no threads: a model may be read from several threads (pread is thread-safe).
 */
#ifndef XCFA_H
#define XCFA_H

#include <stddef.h>
#include <stdint.h>

#define XCFA_FEATURES 16 /* 13 MFCC + energy + spectral centroid + spectral bandwidth */

typedef struct {
    uint64_t off;
    uint32_t size;
} xcfa_blob;

typedef struct {
    uint64_t off;
    uint32_t size;
    float scale; /* real displacement in pixels = int8 value * scale */
} xcfa_flow;

typedef struct {
    int id;
    int n;      /* number of stored frames */
    int blink;  /* segment contains a blink */
    int h, w;   /* frame size in pixels */
    int silence;           /* tagged as a silence segment */
    int has_silence_mul;
    float silence_mul;
    int has_mean;
    float mean[XCFA_FEATURES];
    float *audio;          /* naudio * XCFA_FEATURES; an all-zero row means "no features" */
    int naudio;            /* rows stored (may differ from n: the Python loader pads or trims to n) */
    xcfa_blob *frames;     /* n JPEG frames */
    xcfa_flow *flows;      /* nflows (= n - 1) int8 flow pairs: (2, h/flow_div, w/flow_div, 2) */
    int nflows;
    xcfa_blob *bg;         /* nbg PNG alpha masks (fg=255, bg=0), present when has_bg */
    int nbg;
} xcfa_segment;

typedef struct {
    int fd;
    uint64_t file_size, catalog_offset, catalog_len;
    int version;     /* container version, 2 */
    int cfa_version; /* catalog "cfa_v" */
    int fps;         /* frames per second of the stored video, 25 */
    int flow_div;    /* flows are stored at 1/flow_div of the frame size */
    int has_bg;
    int has_face_region;
    double face_x, face_y, face_w, face_h, eye_y; /* fractions of the frame */
    xcfa_blob base;    /* base portrait, a PNG in the real models (XcfaLoader.py calls it JPEG) */
    xcfa_blob base_bg; /* its alpha mask PNG (valid when has_bg) */
    xcfa_segment *seg;
    int nseg;
} xcfa_model;

/* Opens a model and parses its catalog. Returns NULL and fills err on failure. */
xcfa_model *xcfa_open(const char *path, char *err, size_t errlen);
void xcfa_close(xcfa_model *m);

/* Reads size bytes at off into buf. Returns 0 on success, -1 on a short read or an error. */
int xcfa_read(const xcfa_model *m, uint64_t off, uint32_t size, void *buf);

/* Index of the segment with the given id (ids are not necessarily 0..n-1), or -1. */
int xcfa_find_segment(const xcfa_model *m, int id);

#endif
