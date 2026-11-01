// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* beroun_native on Linux: the window is a Wayland xdg toplevel with ARGB shm buffers, the sound is ALSA's default device. */
#define _GNU_SOURCE
#include "beroun_native.h"

#include <alsa/asoundlib.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <wayland-client.h>

#include "xdg-shell-client-protocol.h"

static struct wl_display *dpy;
static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct xdg_wm_base *wm_base;
static struct wl_surface *surface;
static struct xdg_surface *xsurface;
static struct xdg_toplevel *toplevel;
static int win_w = 640, win_h = 480, configured, closing;

typedef struct { struct wl_buffer *b; uint32_t *px; int busy; } wbuf;
static wbuf bufs[2];

static void buf_release(void *d, struct wl_buffer *b) { ((wbuf *)d)->busy = 0; }
static const struct wl_buffer_listener buf_listener = { .release = buf_release };

static int make_buffers(int w, int h) {
    size_t one = (size_t)w * h * 4;
    int fd = memfd_create("beroun-native", 0);
    if (fd < 0 || ftruncate(fd, (off_t)(one * 2))) return -1;
    uint8_t *data = mmap(NULL, one * 2, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (data == MAP_FAILED) return -1;
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, (int32_t)(one * 2));
    for (int i = 0; i < 2; i++) {
        bufs[i].px = (uint32_t *)(data + one * i);
        bufs[i].b = wl_shm_pool_create_buffer(pool, (int32_t)(one * i), w, h, w * 4, WL_SHM_FORMAT_ARGB8888);
        wl_buffer_add_listener(bufs[i].b, &buf_listener, &bufs[i]);
        bufs[i].busy = 0;
    }
    wl_shm_pool_destroy(pool);
    close(fd);
    return 0;
}

static void wm_ping(void *d, struct xdg_wm_base *b, uint32_t serial) { xdg_wm_base_pong(b, serial); }
static const struct xdg_wm_base_listener wm_listener = { .ping = wm_ping };
static void xs_configure(void *d, struct xdg_surface *s, uint32_t serial) { xdg_surface_ack_configure(s, serial); configured = 1; }
static const struct xdg_surface_listener xs_listener = { .configure = xs_configure };
static void tl_configure(void *d, struct xdg_toplevel *t, int32_t w, int32_t h, struct wl_array *st) { if (!configured && w > 0 && h > 0) { win_w = w; win_h = h; } }
static void tl_close(void *d, struct xdg_toplevel *t) { closing = 1; }
static const struct xdg_toplevel_listener tl_listener = { .configure = tl_configure, .close = tl_close };
static void reg_global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver) {
    if (!strcmp(iface, wl_compositor_interface.name)) compositor = wl_registry_bind(r, name, &wl_compositor_interface, 4);
    else if (!strcmp(iface, wl_shm_interface.name)) shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
    else if (!strcmp(iface, xdg_wm_base_interface.name)) { wm_base = wl_registry_bind(r, name, &xdg_wm_base_interface, 1); xdg_wm_base_add_listener(wm_base, &wm_listener, NULL); }
}
static void reg_remove(void *d, struct wl_registry *r, uint32_t name) {}
static const struct wl_registry_listener reg_listener = { .global = reg_global, .global_remove = reg_remove };

/* The window is maximized (a fullscreen one would get the shell's black backdrop) and takes no input: the touches go through. */
int bn_window_open(const char *title, const char *app_id, int *w, int *h) {
    dpy = wl_display_connect(NULL);
    if (!dpy) { fprintf(stderr, "beroun_native: no Wayland display\n"); return 1; }
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &reg_listener, NULL);
    wl_display_roundtrip(dpy);
    if (!compositor || !shm || !wm_base) { fprintf(stderr, "beroun_native: the compositor lacks wl_compositor/wl_shm/xdg_wm_base\n"); return 1; }
    surface = wl_compositor_create_surface(compositor);
    struct wl_region *none = wl_compositor_create_region(compositor);
    wl_surface_set_input_region(surface, none);
    wl_region_destroy(none);
    xsurface = xdg_wm_base_get_xdg_surface(wm_base, surface);
    xdg_surface_add_listener(xsurface, &xs_listener, NULL);
    toplevel = xdg_surface_get_toplevel(xsurface);
    xdg_toplevel_add_listener(toplevel, &tl_listener, NULL);
    xdg_toplevel_set_title(toplevel, title);
    xdg_toplevel_set_app_id(toplevel, app_id);
    xdg_toplevel_set_maximized(toplevel);
    wl_surface_commit(surface);
    for (int i = 0; i < 50 && !configured; i++) wl_display_roundtrip(dpy);
    if (make_buffers(win_w, win_h)) { fprintf(stderr, "beroun_native: no buffers\n"); return 1; }
    *w = win_w;
    *h = win_h;
    return 0;
}

uint32_t *bn_window_acquire(void) {
    wbuf *b = bufs[0].busy ? (bufs[1].busy ? NULL : &bufs[1]) : &bufs[0];
    return b ? b->px : NULL;
}

void bn_window_present(uint32_t *px) {
    wbuf *b = px == bufs[0].px ? &bufs[0] : &bufs[1];
    b->busy = 1;
    wl_surface_attach(surface, b->b, 0, 0);
    wl_surface_damage_buffer(surface, 0, 0, win_w, win_h);
    wl_surface_commit(surface);
}

int bn_window_closed(void) { return closing; }

int bn_wait(int *fds, int nfds, short *revents, int timeout_ms) {
    struct pollfd p[8] = {{wl_display_get_fd(dpy), POLLIN, 0}};
    if (nfds > 7) nfds = 7;
    while (wl_display_prepare_read(dpy) != 0) wl_display_dispatch_pending(dpy);
    wl_display_flush(dpy);
    for (int i = 0; i < nfds; i++) p[i + 1] = (struct pollfd){fds[i], POLLIN, 0};
    int rc = poll(p, (nfds_t)(nfds + 1), timeout_ms);
    if (rc > 0 && (p[0].revents & POLLIN)) wl_display_read_events(dpy); else wl_display_cancel_read(dpy);
    wl_display_dispatch_pending(dpy);
    for (int i = 0; i < nfds; i++) revents[i] = rc > 0 ? p[i + 1].revents : 0;
    return rc;
}

/* ---- the sound ---------------------------------------------------------------------------------------------------------------- */
struct bn_audio { snd_pcm_t *pcm; };

bn_audio *bn_audio_open(int rate) {
    snd_pcm_t *pcm = NULL;
    if (snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0) < 0) return NULL;
    if (snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED, 1, (unsigned)rate, 1, 120000) < 0) { snd_pcm_close(pcm); return NULL; }
    bn_audio *a = malloc(sizeof *a);
    a->pcm = pcm;
    return a;
}

long bn_audio_write(bn_audio *a, const int16_t *pcm, size_t n) {
    snd_pcm_sframes_t w = snd_pcm_writei(a->pcm, pcm, n);
    if (w >= 0) return (long)w;
    if (snd_pcm_recover(a->pcm, (int)w, 1) < 0) return -1;
    return 0;   /* recovered: nothing was taken, the caller writes again */
}

long bn_audio_delay(bn_audio *a) {
    snd_pcm_sframes_t d = 0;
    return snd_pcm_delay(a->pcm, &d) < 0 || d < 0 ? 0 : (long)d;
}

void bn_audio_drop(bn_audio *a) { snd_pcm_drop(a->pcm); }
void bn_audio_close(bn_audio *a) { snd_pcm_close(a->pcm); free(a); }
