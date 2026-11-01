// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* beroun_native on Android: the window is the Surface of a SurfaceView the native host gives (ANativeWindow), the sound is AAudio.
 * The host calls beroun_set_surface() before beroun_main() and beroun_close_window() when the layer is taken away. */
#define _GNU_SOURCE
#include "beroun_native.h"

#include <aaudio/AAudio.h>
#include <android/native_window.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <unistd.h>

static ANativeWindow *volatile win;
static volatile int closing;
static int wake_fd = -1;
static uint32_t *frame;        /* one buffer: the surface copies it at once, so a second one would only wait */
static int win_w, win_h;

static void wake(void) { if (wake_fd >= 0) { uint64_t one = 1; (void)!write(wake_fd, &one, sizeof one); } }

/* ---- called by the host ------------------------------------------------------------------------------------------------------- */
__attribute__((visibility("default"))) void beroun_set_surface(ANativeWindow *w) {
    if (wake_fd < 0) wake_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    win = w;
    wake();
}
__attribute__((visibility("default"))) void beroun_close_window(void) { closing = 1; wake(); }

/* ---- the window --------------------------------------------------------------------------------------------------------------- */
int bn_window_open(const char *title, const char *app_id, int *w, int *h) {
    if (wake_fd < 0) wake_fd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    for (int i = 0; i < 300 && !win && !closing; i++) usleep(100000);   /* the host shows the layer, then hands its Surface over */
    if (!win) { fprintf(stderr, "beroun_native: no Surface\n"); return 1; }
    win_w = ANativeWindow_getWidth(win);
    win_h = ANativeWindow_getHeight(win);
    if (win_w <= 0 || win_h <= 0 || ANativeWindow_setBuffersGeometry(win, win_w, win_h, WINDOW_FORMAT_RGBA_8888) < 0) return 1;
    frame = malloc((size_t)win_w * win_h * 4);
    if (!frame) return 1;
    *w = win_w;
    *h = win_h;
    return 0;
}

uint32_t *bn_window_acquire(void) { return frame; }

/* 0xAARRGGBB -> the surface's bytes R,G,B,A (a little-endian 0xAABBGGRR) */
void bn_window_present(uint32_t *px) {
    ANativeWindow_Buffer b;
    if (!win || ANativeWindow_lock(win, &b, NULL) < 0) return;
    for (int y = 0; y < win_h && y < b.height; y++) {
        const uint32_t *s = px + (size_t)y * win_w;
        uint32_t *d = (uint32_t *)b.bits + (size_t)y * b.stride;
        for (int x = 0; x < win_w && x < b.width; x++) {
            uint32_t p = s[x];
            d[x] = (p & 0xFF00FF00u) | ((p & 0xFF) << 16) | ((p >> 16) & 0xFF);
        }
    }
    ANativeWindow_unlockAndPost(win);
}

int bn_window_closed(void) { return closing; }

int bn_wait(int *fds, int nfds, short *revents, int timeout_ms) {
    struct pollfd p[8] = {{wake_fd, POLLIN, 0}};
    if (nfds > 7) nfds = 7;
    for (int i = 0; i < nfds; i++) p[i + 1] = (struct pollfd){fds[i], POLLIN, 0};
    int rc = poll(p, (nfds_t)(nfds + 1), timeout_ms);
    if (rc > 0 && (p[0].revents & POLLIN)) { uint64_t v; (void)!read(wake_fd, &v, sizeof v); }
    for (int i = 0; i < nfds; i++) revents[i] = rc > 0 ? p[i + 1].revents : 0;
    return rc;
}

/* ---- the sound ---------------------------------------------------------------------------------------------------------------- */
struct bn_audio { AAudioStream *s; };

bn_audio *bn_audio_open(int rate) {
    AAudioStreamBuilder *b;
    if (AAudio_createStreamBuilder(&b) != AAUDIO_OK) return NULL;
    AAudioStreamBuilder_setDirection(b, AAUDIO_DIRECTION_OUTPUT);
    AAudioStreamBuilder_setFormat(b, AAUDIO_FORMAT_PCM_I16);
    AAudioStreamBuilder_setChannelCount(b, 1);
    AAudioStreamBuilder_setSampleRate(b, rate);
    AAudioStreamBuilder_setSharingMode(b, AAUDIO_SHARING_MODE_SHARED);
    AAudioStream *s = NULL;
    aaudio_result_t r = AAudioStreamBuilder_openStream(b, &s);
    AAudioStreamBuilder_delete(b);
    if (r != AAUDIO_OK) return NULL;
    if (AAudioStream_requestStart(s) != AAUDIO_OK) { AAudioStream_close(s); return NULL; }
    bn_audio *a = malloc(sizeof *a);
    a->s = s;
    return a;
}

long bn_audio_write(bn_audio *a, const int16_t *pcm, size_t n) {
    aaudio_result_t w = AAudioStream_write(a->s, pcm, (int32_t)n, 2000000000LL);
    return w >= 0 ? (long)w : -1;
}

long bn_audio_delay(bn_audio *a) {
    int64_t d = AAudioStream_getFramesWritten(a->s) - AAudioStream_getFramesRead(a->s);
    return d < 0 ? 0 : (long)d;
}

void bn_audio_drop(bn_audio *a) {   /* a stream flushes only when paused; the next write starts it again */
    AAudioStream_requestPause(a->s);
    AAudioStream_requestFlush(a->s);
    AAudioStream_requestStart(a->s);
}
void bn_audio_close(bn_audio *a) { AAudioStream_close(a->s); free(a); }
