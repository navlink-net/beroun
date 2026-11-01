// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* xcfa-dump - inspect a .xcfa model and cut blobs out of it (test and debugging tool of the face renderer).
 *
 *   xcfa-dump info  MODEL
 *   xcfa-dump base  MODEL OUT.png
 *   xcfa-dump basebg MODEL OUT.png
 *   xcfa-dump frame MODEL SEG IDX OUT.jpg     (SEG is a segment id)
 *   xcfa-dump bg    MODEL SEG IDX OUT.png
 *   xcfa-dump flow  MODEL SEG IDX OUT.bin     (raw int8 blob, scale printed)
 *   xcfa-dump table MODEL                     (one line per segment, machine readable, used by the tests)
 */
#include "xcfa.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int write_blob(const xcfa_model *m, uint64_t off, uint32_t size, const char *out) {
    uint8_t *buf = malloc(size ? size : 1);
    if (!buf) return -1;
    if (xcfa_read(m, off, size, buf) != 0) { fprintf(stderr, "read failed at %llu\n", (unsigned long long)off); free(buf); return -1; }
    FILE *f = fopen(out, "wb");
    if (!f) { fprintf(stderr, "cannot write %s\n", out); free(buf); return -1; }
    fwrite(buf, 1, size, f);
    fclose(f);
    free(buf);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: xcfa-dump info|base|basebg|frame|bg|flow|table MODEL [SEG IDX] [OUT]\n"); return 2; }
    char err[256];
    xcfa_model *m = xcfa_open(argv[2], err, sizeof err);
    if (!m) { fprintf(stderr, "xcfa-dump: %s\n", err); return 1; }
    const char *cmd = argv[1];
    int rc = 0;
    if (!strcmp(cmd, "info")) {
        long frames = 0, sil = 0, blink = 0;
        for (int i = 0; i < m->nseg; i++) { frames += m->seg[i].n; sil += m->seg[i].silence; blink += m->seg[i].blink; }
        printf("version %d cfa_version %d fps %d flow_div %d has_bg %d\n", m->version, m->cfa_version, m->fps, m->flow_div, m->has_bg);
        printf("file %llu catalog_offset %llu catalog_len %llu\n", (unsigned long long)m->file_size, (unsigned long long)m->catalog_offset, (unsigned long long)m->catalog_len);
        printf("segments %d frames %ld silence %ld blink %ld\n", m->nseg, frames, sil, blink);
        printf("frame_size %dx%d\n", m->nseg ? m->seg[0].w : 0, m->nseg ? m->seg[0].h : 0);
        if (m->has_face_region) printf("face_region x %.4f y %.4f w %.4f h %.4f eye_y %.4f\n", m->face_x, m->face_y, m->face_w, m->face_h, m->eye_y);
        printf("base off %llu size %u\n", (unsigned long long)m->base.off, m->base.size);
    } else if (!strcmp(cmd, "table")) {
        /* seg_id n silence blink first_frame_off first_frame_size first_flow_off first_flow_size first_bg_off first_bg_size mean0 af0_0 */
        for (int i = 0; i < m->nseg; i++) {
            const xcfa_segment *s = &m->seg[i];
            printf("%d %d %d %d %llu %u %llu %u %llu %u %.6f %.6f\n", s->id, s->n, s->silence, s->blink,
                   s->frames ? (unsigned long long)s->frames[0].off : 0ull, s->frames ? s->frames[0].size : 0u,
                   s->nflows ? (unsigned long long)s->flows[0].off : 0ull, s->nflows ? s->flows[0].size : 0u,
                   s->nbg ? (unsigned long long)s->bg[0].off : 0ull, s->nbg ? s->bg[0].size : 0u,
                   s->has_mean ? s->mean[0] : 0.0f, s->audio ? s->audio[0] : 0.0f);
        }
    } else if (!strcmp(cmd, "base") && argc >= 4) {
        rc = write_blob(m, m->base.off, m->base.size, argv[3]);
    } else if (!strcmp(cmd, "basebg") && argc >= 4) {
        rc = write_blob(m, m->base_bg.off, m->base_bg.size, argv[3]);
    } else if ((!strcmp(cmd, "frame") || !strcmp(cmd, "bg") || !strcmp(cmd, "flow")) && argc >= 6) {
        int si = xcfa_find_segment(m, atoi(argv[3]));
        int idx = atoi(argv[4]);
        if (si < 0) { fprintf(stderr, "no segment %s\n", argv[3]); rc = 1; }
        else {
            const xcfa_segment *s = &m->seg[si];
            if (!strcmp(cmd, "frame") && idx >= 0 && idx < s->n && s->frames) rc = write_blob(m, s->frames[idx].off, s->frames[idx].size, argv[5]);
            else if (!strcmp(cmd, "bg") && idx >= 0 && idx < s->nbg) rc = write_blob(m, s->bg[idx].off, s->bg[idx].size, argv[5]);
            else if (!strcmp(cmd, "flow") && idx >= 0 && idx < s->nflows) {
                printf("scale %.9g size %u (2 x %d x %d x 2)\n", s->flows[idx].scale, s->flows[idx].size, s->h / m->flow_div, s->w / m->flow_div);
                rc = write_blob(m, s->flows[idx].off, s->flows[idx].size, argv[5]);
            } else { fprintf(stderr, "index %d out of range for segment %s\n", idx, argv[3]); rc = 1; }
        }
    } else { fprintf(stderr, "bad command or arguments\n"); rc = 2; }
    xcfa_close(m);
    return rc;
}
