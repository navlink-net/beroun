// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* ic-screen - shows one picture (a PNG) on the whole screen, opaque, until it is killed or its time is up.
 * The farewell screen of the power-off and the boot screen of the power-on (powerd starts it; web-shell/screens/cat.png).
 *
 *   ic-screen PICTURE.png [SECONDS]
 *
 * A fullscreen Wayland window. The picture is scaled (nearest neighbour) when the output is not its size.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <png.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>

#include "xdg-shell-client-protocol.h"

static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct xdg_wm_base *wm_base;
static struct wl_surface *surface;
static int win_w = 640, win_h = 480, configured, closing;
static uint32_t *pic;
static int pw, ph;

static void wm_ping(void *d, struct xdg_wm_base *b, uint32_t s) { xdg_wm_base_pong(b, s); }
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

static void on_term(int s) { closing = 1; }

static int load_png(const char *path) {
    png_image im;
    memset(&im, 0, sizeof im);
    im.version = PNG_IMAGE_VERSION;
    if (!png_image_begin_read_from_file(&im, path)) { fprintf(stderr, "ic-screen: %s: %s\n", path, im.message); return -1; }
    im.format = PNG_FORMAT_BGRA;
    pw = (int)im.width; ph = (int)im.height;
    pic = malloc(PNG_IMAGE_SIZE(im));
    if (!pic || !png_image_finish_read(&im, NULL, pic, 0, NULL)) { fprintf(stderr, "ic-screen: %s: %s\n", path, im.message); return -1; }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: ic-screen PICTURE.png [SECONDS]\n"); return 2; }
    double seconds = argc > 2 ? atof(argv[2]) : 0;
    if (load_png(argv[1])) return 1;
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    struct wl_display *dpy = wl_display_connect(NULL);
    if (!dpy) { fprintf(stderr, "ic-screen: no Wayland display\n"); return 1; }
    struct wl_registry *reg = wl_display_get_registry(dpy);
    wl_registry_add_listener(reg, &reg_listener, NULL);
    wl_display_roundtrip(dpy);
    if (!compositor || !shm || !wm_base) { fprintf(stderr, "ic-screen: the compositor lacks wl_compositor/wl_shm/xdg_wm_base\n"); return 1; }
    surface = wl_compositor_create_surface(compositor);
    struct xdg_surface *xs = xdg_wm_base_get_xdg_surface(wm_base, surface);
    xdg_surface_add_listener(xs, &xs_listener, NULL);
    struct xdg_toplevel *tl = xdg_surface_get_toplevel(xs);
    xdg_toplevel_add_listener(tl, &tl_listener, NULL);
    xdg_toplevel_set_title(tl, "IntelliCat");
    xdg_toplevel_set_app_id(tl, "com.intellicat.app.screen");
    xdg_toplevel_set_fullscreen(tl, NULL);
    wl_surface_commit(surface);
    for (int i = 0; i < 50 && !configured; i++) wl_display_roundtrip(dpy);

    size_t size = (size_t)win_w * win_h * 4;
    int fd = memfd_create("ic-screen", 0);
    if (fd < 0 || ftruncate(fd, (off_t)size)) return 1;
    uint32_t *px = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    for (int y = 0; y < win_h; y++)
        for (int x = 0; x < win_w; x++) px[(size_t)y * win_w + x] = pic[(size_t)(y * ph / win_h) * pw + (x * pw / win_w)] | 0xff000000u;
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, (int32_t)size);
    struct wl_buffer *buf = wl_shm_pool_create_buffer(pool, 0, win_w, win_h, win_w * 4, WL_SHM_FORMAT_XRGB8888);
    wl_surface_attach(surface, buf, 0, 0);
    wl_surface_damage_buffer(surface, 0, 0, win_w, win_h);
    wl_surface_commit(surface);
    wl_display_flush(dpy);

    struct timespec t0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    while (!closing) {
        while (wl_display_prepare_read(dpy) != 0) wl_display_dispatch_pending(dpy);
        wl_display_flush(dpy);
        struct pollfd p = {wl_display_get_fd(dpy), POLLIN, 0};
        if (poll(&p, 1, 100) > 0) wl_display_read_events(dpy); else wl_display_cancel_read(dpy);
        if (wl_display_dispatch_pending(dpy) < 0) break;
        if (seconds > 0) {
            struct timespec t;
            clock_gettime(CLOCK_MONOTONIC, &t);
            if ((t.tv_sec - t0.tv_sec) + (t.tv_nsec - t0.tv_nsec) * 1e-9 >= seconds) break;
        }
    }
    return 0;
}
