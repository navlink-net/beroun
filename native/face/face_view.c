// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* ic-face-view - Caroline's talking head shown directly on the screen, in one process: it renders the frames (libxcfa, in process),
 * plays the sound of the answer and draws every frame with an alpha channel in a window of its own (both through beroun_native), so the
 * face stands over whatever is on the screen and the rest of the window stays transparent. The frames are timed from the position of the
 * SOUND CARD (bn_audio_delay), so the mouth and the voice cannot drift apart.
 *
 *   ic-face-view MODEL SOCKET
 *
 * Between the answers the face shows its living pause (the renderer's own silence animation, looped). shelld talks to it over the Unix socket:
 *   PLAY <id> <audio file>   decode the file (ffmpeg), render it and play it; the frames follow the sound
 *   STOP                     stop what is playing
 *   QUIT
 * and gets back
 *   READY <w> <h> <fps>      once, when a client connects
 *   DONE <id> <1|0> [text]   the answer was played to its end (1), or not (0: the text says why: stopped, decode, audio)
 *
 * The window is maximized (a fullscreen one would get the shell's black backdrop), takes no input (the touches go through) and has the
 * title/app id of a Caroline overlay.
 */
#define _GNU_SOURCE
#include "render.h"

#include <errno.h>
#include <stdarg.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "beroun_native.h"

#define RATE 16000

static double now_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000.0 + t.tv_nsec * 1e-6; }

static int win_w = 640, win_h = 480, quit;

/* ---- the renderer and the picture ------------------------------------------------------------------------------------------ */
static xcfa_renderer *rend;
static int fw, fh, fps = 25;
static float *silence;
static xcfa_render *idle_render, *speech_render;
static xcfa_frame last;
static int have_last;

static void begin_idle(void) {
    if (idle_render) xcfa_render_end(idle_render);
    idle_render = xcfa_render_begin(rend, silence, (size_t)RATE * 3);
}

/* the frame (BGR + alpha plane) into a free ARGB buffer, premultiplied, bottom-centred in the window, the rest transparent */
static void draw(const xcfa_frame *f) {
    uint32_t *px = bn_window_acquire();
    if (!px) return;
    memset(px, 0, (size_t)win_w * win_h * 4);
    int ox = (win_w - f->img.w) / 2, oy = win_h - f->img.h;
    if (ox < 0) ox = 0;
    if (oy < 0) oy = 0;
    for (int y = 0; y < f->img.h && y + oy < win_h; y++) {
        const uint8_t *s = f->img.px + (size_t)y * f->img.w * 3;
        const uint8_t *al = f->alpha ? f->alpha + (size_t)y * f->img.w : NULL;
        uint32_t *d = px + (size_t)(y + oy) * win_w + ox;
        for (int x = 0; x < f->img.w && x + ox < win_w; x++) {
            unsigned a = al ? al[x] : 255, bl = s[x * 3], g = s[x * 3 + 1], r = s[x * 3 + 2];
            unsigned rb = r > bl ? r : bl;
            if (g > rb) g = rb;   /* despill: the model was shot on a green screen, its edge keeps a green tint */
            d[x] = (a << 24) | (((r * a + 127) / 255) << 16) | (((g * a + 127) / 255) << 8) | ((bl * a + 127) / 255);
        }
    }
    bn_window_present(px);
}

/* ---- the sound; its position is the clock of the animation -------------------------------------------------------- */
static pthread_t ath;
static volatile int a_active, a_stop, a_done, a_failed;
static volatile long a_played;               /* samples that have really been heard (written - what is still in the card's buffer) */
static int16_t *a_pcm;
static size_t a_n;

static void *audio_main(void *arg) {
    bn_audio *pcm = bn_audio_open(RATE);
    if (!pcm) { a_failed = 1; a_done = 1; return NULL; }
    size_t off = 0;
    const size_t chunk = RATE / 25;   /* 40 ms */
    while (off < a_n && !a_stop) {
        size_t n = a_n - off < chunk ? a_n - off : chunk;
        long w = bn_audio_write(pcm, a_pcm + off, n);
        if (w < 0) { a_failed = 1; break; }
        off += (size_t)w;
        long played = (long)off - bn_audio_delay(pcm);
        a_played = played > 0 ? played : 0;
    }
    if (!a_stop && !a_failed) {         /* the tail: what is still in the card's buffer, counted down by the clock (the delay of some plugins does not fall to 0) */
        long d0 = bn_audio_delay(pcm);
        double t0 = now_ms();
        for (;;) {
            double left = d0 - (now_ms() - t0) * RATE / 1000.0;
            if (left <= 0 || a_stop) break;
            a_played = (long)a_n - (long)left;
            usleep(10000);
        }
        a_played = (long)a_n;
    }
    bn_audio_drop(pcm);
    bn_audio_close(pcm);
    a_done = 1;
    return NULL;
}

static void audio_stop(void) {
    if (!a_active) return;
    a_stop = 1;
    pthread_join(ath, NULL);
    a_active = 0;
    free(a_pcm); a_pcm = NULL;
}

/* ---- speech -------------------------------------------------------------------------------------------------------------------- */
static int client = -1;
static char speech_id[64];
static int speech_on, speech_render_done, speech_produced;
static float *speech_audio;
static double speech_t0;

static void send_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void send_line(const char *fmt, ...) {
    if (client < 0) return;
    char b[256];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    if (send(client, b, (size_t)n, MSG_NOSIGNAL) < 0) { close(client); client = -1; }
}

static void speech_end(int played, const char *why) {
    if (!speech_on) return;
    speech_on = 0;
    audio_stop();
    if (speech_render) { xcfa_render_end(speech_render); speech_render = NULL; }
    free(speech_audio); speech_audio = NULL;
    send_line("DONE %s %d%s%s\n", speech_id, played, why ? " " : "", why ? why : "");
    begin_idle();
}

/* the whole file as 16 kHz mono float32 through ffmpeg */
static float *decode(const char *path, size_t *n) {
    char cmd[1300];
    const char *ff = getenv("BEROUN_FFMPEG");   /* Android: the firmware's own ffmpeg, there is none on PATH */
    snprintf(cmd, sizeof cmd, "%s -loglevel error -i '%s' -f f32le -ac 1 -ar 16000 pipe:1 2>/dev/null", ff && *ff ? ff : "ffmpeg", path);
    FILE *p = popen(cmd, "r");
    if (!p) return NULL;
    size_t cap = 1 << 20, len = 0;
    uint8_t *buf = malloc(cap);
    for (;;) {
        if (len + 65536 > cap) buf = realloc(buf, cap *= 2);
        size_t r = fread(buf + len, 1, 65536, p);
        if (r == 0) break;
        len += r;
    }
    pclose(p);
    if (len < 4) { free(buf); return NULL; }
    *n = len / 4;
    return (float *)buf;
}

static void speech_start(const char *id, const char *path) {
    if (speech_on) speech_end(0, "replaced");
    snprintf(speech_id, sizeof speech_id, "%s", id);
    size_t n = 0;
    float *au = decode(path, &n);
    if (!au) { send_line("DONE %s 0 decode\n", id); return; }
    xcfa_render *r = xcfa_render_begin(rend, au, n);
    if (!r) { free(au); send_line("DONE %s 0 render\n", id); return; }
    a_pcm = malloc(n * sizeof(int16_t));
    for (size_t i = 0; i < n; i++) { float v = au[i] * 32767.0f; a_pcm[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v); }
    a_n = n; a_played = 0; a_stop = a_done = a_failed = 0; a_active = 1;
    pthread_create(&ath, NULL, audio_main, NULL);
    speech_audio = au; speech_render = r;
    speech_on = 1; speech_render_done = 0; speech_produced = -1;
    speech_t0 = now_ms();
}

/* ---- the control socket -------------------------------------------------------------------------------------------------------- */
static void handle_line(char *line) {
    char cmd[16] = "", id[64] = "", path[1100] = "";
    int n = sscanf(line, "%15s %63s %1099[^\n]", cmd, id, path);
    if (n >= 3 && !strcmp(cmd, "PLAY")) speech_start(id, path);
    else if (n >= 1 && !strcmp(cmd, "STOP")) speech_end(0, "stopped");
    else if (n >= 1 && !strcmp(cmd, "QUIT")) quit = 1;
}

static char inbuf[2048];
static size_t inlen;
static void client_read(void) {
    ssize_t r = recv(client, inbuf + inlen, sizeof inbuf - inlen - 1, 0);
    if (r <= 0) { close(client); client = -1; inlen = 0; quit = 1; return; }   /* shelld went away: the face goes with it */
    inlen += (size_t)r;
    inbuf[inlen] = 0;
    char *nl;
    while ((nl = strchr(inbuf, '\n'))) {
        *nl = 0;
        handle_line(inbuf);
        size_t rest = inlen - (size_t)(nl + 1 - inbuf);
        memmove(inbuf, nl + 1, rest);
        inlen = rest;
        inbuf[inlen] = 0;
    }
}

/* ---- the loop ------------------------------------------------------------------------------------------------------------------ */
static void frame_tick(void) {
    static double idle_next;
    if (speech_on) {
        long due = a_played > 0 ? (long)((double)a_played / RATE * fps) : 0;
        int changed = 0;
        while (!speech_render_done && speech_produced < due) {
            xcfa_frame f;
            int rc = xcfa_render_next(speech_render, &f);
            if (rc > 0) { if (have_last) xcfa_frame_free(&last); last = f; have_last = 1; speech_produced = f.index; changed = 1; }
            else { speech_render_done = 1; if (rc < 0) { speech_end(0, "render"); return; } }
        }
        if (changed && have_last) draw(&last);
        if (a_done && (a_failed || speech_render_done || speech_produced >= due)) speech_end(!a_failed && !a_stop, a_failed ? "audio" : NULL);
        return;
    }
    double t = now_ms();
    if (t < idle_next) return;
    idle_next = t + 1000.0 / fps;
    xcfa_frame f;
    int rc = idle_render ? xcfa_render_next(idle_render, &f) : 0;
    if (rc <= 0) { begin_idle(); rc = idle_render ? xcfa_render_next(idle_render, &f) : 0; }
    if (rc > 0) { if (have_last) xcfa_frame_free(&last); last = f; have_last = 1; draw(&last); }
}

#ifdef BEROUN_LIB   /* Android: a part is a library the native host loads; this is its entry */
__attribute__((visibility("default"))) int beroun_main(int argc, char **argv) {
#else
int main(int argc, char **argv) {
#endif
    if (argc < 3) { fprintf(stderr, "usage: ic-face-view MODEL SOCKET\n"); return 2; }
    signal(SIGPIPE, SIG_IGN);
    if (bn_window_open("Caroline", "com.intellicat.app.caroline_face", &win_w, &win_h)) return 1;

    xcfa_render_options o = xcfa_render_defaults();
    o.height = win_h; o.remove_bg = 1; o.alpha_output = 1; o.segment_cache = 60;
    char err[256];
    rend = xcfa_renderer_open(argv[1], &o, err, sizeof err);
    if (!rend) { fprintf(stderr, "ic-face-view: %s\n", err); return 1; }
    fw = xcfa_renderer_width(rend); fh = xcfa_renderer_height(rend); fps = xcfa_renderer_fps(rend);
    silence = calloc((size_t)RATE * 3, sizeof(float));
    begin_idle();

    int ls = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    strncpy(a.sun_path, argv[2], sizeof a.sun_path - 1);
    unlink(argv[2]);
    if (bind(ls, (struct sockaddr *)&a, sizeof a) || listen(ls, 1)) { perror(argv[2]); return 1; }
    fprintf(stderr, "ic-face-view: %s, window %dx%d, face %dx%d at %d fps, control on %s\n", argv[1], win_w, win_h, fw, fh, fps, argv[2]);

    while (!quit && !bn_window_closed()) {
        int fds[2] = {ls, client};
        short rev[2] = {0, 0};
        int rc = bn_wait(fds, client >= 0 ? 2 : 1, rev, speech_on ? 8 : 15);
        if (rc > 0 && (rev[0] & POLLIN)) {
            int c = accept(ls, NULL, NULL);
            if (c >= 0) { if (client >= 0) close(client); client = c; inlen = 0; send_line("READY %d %d %d\n", fw, fh, fps); }
        }
        if (client >= 0 && rc > 0 && (rev[1] & (POLLIN | POLLHUP))) client_read();
        frame_tick();
    }
    audio_stop();
    unlink(argv[2]);
    return 0;
}
