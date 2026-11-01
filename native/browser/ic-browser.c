// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* ic-browser: the IntelliCat browser host.
 *
 * One fullscreen Wayland window. Inside it two kinds of WPE WebKit views are composited into a single buffer:
 *   - the content of the active tab (up to MAX_TABS tabs), below a strip_h high strip (40; IC_BROWSER_STRIP=0 for the window on the external screen, which is the page alone);
 *   - the browser's own UI, an HTML page (chrome.html) with a transparent background, drawn over everything:
 *     the strip (address, kebab menu), the menu panel and the on-screen keyboard.
 * The UI page talks to this program with window.webkit.messageHandlers.host.postMessage("command") and gets the state back
 * through window.ic.state(json). Input goes to the UI page while it asks for it (menu or keyboard open) or when it is
 * over the strip, otherwise to the active tab. The browser assigns no buttons of the device to anything.
 *
 * Frames come out of WebKit as SHM buffers (wpebackend-fdo, software path), so nothing here needs a GPU.
 *
 * Usage: ic-browser <ui-url> <web-shell-dir>   (the directory holds osk.js and inject.js, injected into every page)
 *
 * Control channel (for Caroline's browser tool, which cannot use CDP here): when the environment names a socket,
 * IC_BROWSER_CONTROL=<path>, a program may connect to that Unix socket. One request per line, one reply per line:
 *   request  "<id> <op> <label> <base64 payload>\n"        reply  "<id> ok|err <base64 payload>\n"
 *   list                     -> JSON [{"label","title","uri","loading","active","scroll"}...] (every tab, labelled or not; scroll: where the page is scrolled to, polled)
 *   (a tab with no label is reached as "@active" or "@<n>", n from 0, in every op below)
 *   open    (label, url)     -> opens a tab (or reuses the label), makes it the active one
 *   navigate(label, url)  close(label)  activate(label)
 *   eval    (label, js)      -> the string value of the script (the script returns a string)
 *   shot    (label)          -> "<w> <h>\n" + the tab's last frame, raw BGRA (the tab is made active first)
 *   click   (label, "x y")   -> a real pointer click at the page coordinates
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <linux/input-event-codes.h>

#include <glib.h>
#include <gio/gio.h>
#include <gio/gunixsocketaddress.h>
#include <wayland-client.h>
#include <wayland-server-core.h>
#include <xkbcommon/xkbcommon.h>
#include <wpe/webkit.h>
#include <wpe/fdo.h>
#include <wpe/unstable/fdo-shm.h>

#include "xdg-shell-client-protocol.h"

static int strip_h = 40;      /* the height of the UI strip above the page; IC_BROWSER_STRIP=0: none (the external screen) */
#define MAX_TABS 5
#define DEFAULT_W 640
#define DEFAULT_H 480

typedef struct {
    WebKitWebView *wv;
    struct wpe_view_backend_exportable_fdo *exp;
    struct wpe_view_backend *be;
    uint8_t *pix;         /* the last frame, w * h * 4 (ARGB, premultiplied) */
    int w, h;
    gboolean has_frame;
    gboolean is_ui;
    char *label;          /* the name a controlling program gave the tab (control channel); NULL for tabs of the user */
    int scroll_y;         /* where the page is scrolled to (polled), saved with the session */
    int pending_scroll;   /* a restored tab: scroll here when the page has loaded ... */
    int scroll_tries;
    char *pending_uri;    /* ... the page of this address (not the start page that was loading before it) */
} View;

/* ---- state ------------------------------------------------------------------------------------------------ */
static struct wl_display *dpy;
static struct wl_registry *registry;
static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct wl_seat *seat;
static struct xdg_wm_base *wm_base;
static struct wl_surface *surface;
static struct xdg_surface *xsurface;
static struct xdg_toplevel *toplevel;
static struct wl_pointer *pointer;
static struct wl_keyboard *keyboard;
static struct wl_touch *touch;

static int W = DEFAULT_W, H = DEFAULT_H;
static gboolean configured;
static GMainLoop *loop;

static View ui;
static View *tabs[MAX_TABS];
static int ntabs, active;
static gboolean ui_capture;  /* the UI page wants all input (menu or keyboard is open) */

static WebKitWebContext *web_ctx;
static WebKitSettings *content_settings, *ui_settings;
static WebKitUserContentManager *content_ucm, *ui_ucm;
static char *assets_dir;
static const char *ui_url;
static const char *home_url = "about:blank";  /* the start page: new tabs and the first tab */
static gboolean restored;

/* output buffers */
typedef struct { struct wl_buffer *buf; uint32_t *data; gboolean busy; } OutBuf;
static OutBuf out[2];
static void *pool_mem;
static size_t pool_size;
static struct wl_shm_pool *pool;
static gboolean frame_pending, dirty;
static guint compose_idle;

static void schedule_compose(void);
static void push_state(void);
static gboolean scroll_poll(gpointer d);
static void set_content_scripts(const char *osk_src);

/* ---- helpers ---------------------------------------------------------------------------------------------- */
static char *json_escape(const char *s)
{
    GString *o = g_string_new(NULL);
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') g_string_append_printf(o, "\\%c", c);
        else if (c == '\n') g_string_append(o, "\\n");
        else if (c == '\r') g_string_append(o, "\\r");
        else if (c == '\t') g_string_append(o, "\\t");
        else if (c < 0x20) g_string_append_printf(o, "\\u%04x", c);
        else g_string_append_c(o, c);
    }
    return g_string_free(o, FALSE);
}

static View *cur(void) { return ntabs ? tabs[active] : NULL; }

/* ---- output: the composed buffer ------------------------------------------------------------------------------- */
static void buffer_release(void *data, struct wl_buffer *b)
{
    OutBuf *o = data;
    o->busy = FALSE;
    if (dirty) schedule_compose();
}
static const struct wl_buffer_listener buffer_listener = { .release = buffer_release };

static void free_out(void)
{
    for (int i = 0; i < 2; i++) { if (out[i].buf) wl_buffer_destroy(out[i].buf); out[i].buf = NULL; }
    if (pool) wl_shm_pool_destroy(pool);
    pool = NULL;
    if (pool_mem) munmap(pool_mem, pool_size);
    pool_mem = NULL;
}

static void alloc_out(void)
{
    free_out();
    size_t one = (size_t)W * H * 4;
    pool_size = one * 2;
    int fd = memfd_create("ic-browser", MFD_CLOEXEC);
    if (fd < 0 || ftruncate(fd, pool_size) < 0) { perror("memfd"); exit(1); }
    pool_mem = mmap(NULL, pool_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    pool = wl_shm_create_pool(shm, fd, pool_size);
    close(fd);
    for (int i = 0; i < 2; i++) {
        out[i].data = (uint32_t *)((uint8_t *)pool_mem + one * i);
        out[i].buf = wl_shm_pool_create_buffer(pool, one * i, W, H, W * 4, WL_SHM_FORMAT_XRGB8888);
        out[i].busy = FALSE;
        wl_buffer_add_listener(out[i].buf, &buffer_listener, &out[i]);
    }
}

static void frame_done(void *data, struct wl_callback *cb, uint32_t t)
{
    wl_callback_destroy(cb);
    frame_pending = FALSE;
    if (dirty) schedule_compose();
}
static const struct wl_callback_listener frame_listener = { .done = frame_done };

static void compose_now(void)
{
    OutBuf *o = !out[0].busy ? &out[0] : !out[1].busy ? &out[1] : NULL;
    if (!o || frame_pending || !configured) return;
    dirty = FALSE;
    uint32_t *d = o->data;
    View *c = cur();
    /* below the strip: the active tab (white until its first frame) */
    for (int y = 0; y < H; y++) {
        uint32_t *row = d + (size_t)y * W;
        if (y < strip_h) memset(row, 0, W * 4);
        else if (c && c->has_frame && (y - strip_h) < c->h) memcpy(row, c->pix + (size_t)(y - strip_h) * c->w * 4, (size_t)MIN(W, c->w) * 4);
        else for (int x = 0; x < W; x++) row[x] = 0xffffffffu;
    }
    /* the UI over it (premultiplied alpha) */
    if (ui.has_frame) {
        const uint32_t *s = (const uint32_t *)ui.pix;
        int n = MIN(W, ui.w), rows = MIN(H, ui.h);
        for (int y = 0; y < rows; y++) {
            const uint32_t *sr = s + (size_t)y * ui.w;
            uint32_t *dr = d + (size_t)y * W;
            for (int x = 0; x < n; x++) {
                uint32_t p = sr[x], a = p >> 24;
                if (a == 255) dr[x] = p;
                else if (a) {
                    uint32_t q = dr[x], ia = 255 - a;
                    uint32_t r = ((p >> 16) & 255) + ((q >> 16) & 255) * ia / 255;
                    uint32_t g = ((p >> 8) & 255) + ((q >> 8) & 255) * ia / 255;
                    uint32_t b = (p & 255) + (q & 255) * ia / 255;
                    dr[x] = (MIN(r, 255u) << 16) | (MIN(g, 255u) << 8) | MIN(b, 255u);
                }
            }
        }
    }
    o->busy = TRUE;
    wl_surface_attach(surface, o->buf, 0, 0);
    wl_surface_damage_buffer(surface, 0, 0, W, H);
    struct wl_callback *cb = wl_surface_frame(surface);
    wl_callback_add_listener(cb, &frame_listener, NULL);
    frame_pending = TRUE;
    wl_surface_commit(surface);
}

static gboolean compose_idle_cb(gpointer d) { compose_idle = 0; compose_now(); return G_SOURCE_REMOVE; }
static void schedule_compose(void)
{
    dirty = TRUE;
    if (!compose_idle) compose_idle = g_idle_add(compose_idle_cb, NULL);
}

/* ---- WebKit views ------------------------------------------------------------------------------------------- */
static void on_export_shm(void *data, struct wpe_fdo_shm_exported_buffer *eb)
{
    View *v = data;
    struct wl_shm_buffer *sb = wpe_fdo_shm_exported_buffer_get_shm_buffer(eb);
    int bw = wl_shm_buffer_get_width(sb), bh = wl_shm_buffer_get_height(sb), stride = wl_shm_buffer_get_stride(sb);
    wl_shm_buffer_begin_access(sb);
    const uint8_t *src = wl_shm_buffer_get_data(sb);
    int cw = MIN(bw, v->w), ch = MIN(bh, v->h);
    for (int y = 0; y < ch; y++) memcpy(v->pix + (size_t)y * v->w * 4, src + (size_t)y * stride, (size_t)cw * 4);
    wl_shm_buffer_end_access(sb);
    wpe_view_backend_exportable_fdo_dispatch_release_shm_exported_buffer(v->exp, eb);
    wpe_view_backend_exportable_fdo_dispatch_frame_complete(v->exp);
    v->has_frame = TRUE;
    if (v == &ui || v == cur()) schedule_compose();
}

static const struct wpe_view_backend_exportable_fdo_client exportable_client = {
    .export_buffer_resource = NULL, .export_dmabuf_resource = NULL, .export_shm_buffer = on_export_shm,
};

static void view_resize(View *v, int w, int h)
{
    g_free(v->pix);
    v->w = w; v->h = h;
    v->pix = g_malloc0((size_t)w * h * 4);
    v->has_frame = FALSE;
    if (v->be) wpe_view_backend_dispatch_set_size(v->be, w, h);
}

static void view_init(View *v, int w, int h, WebKitUserContentManager *ucm, WebKitSettings *settings, gboolean is_ui)
{
    memset(v, 0, sizeof *v);
    v->is_ui = is_ui;
    v->w = w; v->h = h;
    v->pix = g_malloc0((size_t)w * h * 4);
    v->exp = wpe_view_backend_exportable_fdo_create(&exportable_client, v, w, h);
    v->be = wpe_view_backend_exportable_fdo_get_view_backend(v->exp);
    WebKitWebViewBackend *bk = webkit_web_view_backend_new(v->be, (GDestroyNotify)wpe_view_backend_exportable_fdo_destroy, v->exp);
    v->wv = g_object_new(WEBKIT_TYPE_WEB_VIEW, "backend", bk, "web-context", web_ctx, "settings", settings,
                         "user-content-manager", ucm, NULL);
    wpe_view_backend_add_activity_state(v->be, wpe_view_activity_state_visible | wpe_view_activity_state_in_window);
}

static void tab_changed(WebKitWebView *wv, GParamSpec *p, gpointer d) { push_state(); }
static gboolean tab_known(View *v)
{
    for (int i = 0; i < ntabs; i++) if (tabs[i] == v) return TRUE;
    return FALSE;
}

/* A restored page goes back to where it was scrolled to. The load events cannot be trusted for this (the end of the load that the restore cut short comes with the new address),
 * so the script itself asks whether the document is complete, scrolls, and says where the page is now; it is tried again until the page is there (or the page is too short). */
static void scroll_try(View *v);
static gboolean scroll_retry(gpointer d)
{
    if (tab_known(d)) scroll_try(d);
    return G_SOURCE_REMOVE;
}
static void scroll_applied(GObject *src, GAsyncResult *res, gpointer d)
{
    View *v = d;
    GError *err = NULL;
    JSCValue *val = webkit_web_view_evaluate_javascript_finish(WEBKIT_WEB_VIEW(src), res, &err);
    g_clear_error(&err);
    int got = -1;
    if (val) { got = (int)jsc_value_to_double(val); g_object_unref(val); }
    if (!tab_known(v) || v->pending_scroll <= 0) return;
    if ((got >= 0 && abs(got - v->pending_scroll) <= 4) || ++v->scroll_tries > 12) {
        v->scroll_y = got >= 0 ? got : v->pending_scroll;
        v->pending_scroll = v->scroll_tries = 0;
        g_clear_pointer(&v->pending_uri, g_free);
        push_state();
    } else g_timeout_add(300, scroll_retry, v);
}
static void scroll_try(View *v)
{
    if (v->pending_scroll <= 0) return;
    char *js = g_strdup_printf("(function (y) { if (document.readyState !== 'complete') return -1; window.scrollTo(0, y); return Math.round(window.scrollY); })(%d)", v->pending_scroll);
    webkit_web_view_evaluate_javascript(v->wv, js, -1, NULL, NULL, NULL, scroll_applied, v);
    g_free(js);
}

static void tab_load_changed(WebKitWebView *wv, WebKitLoadEvent e, gpointer d)
{
    View *v = d;
    if (e == WEBKIT_LOAD_FINISHED && v && v->pending_scroll > 0 && v->pending_uri && webkit_web_view_get_uri(wv) && !strcmp(webkit_web_view_get_uri(wv), v->pending_uri))
        scroll_try(v);
    push_state();
}

/* every moment or so: where each page is scrolled to, so that the session can say it */
static void scroll_polled(GObject *src, GAsyncResult *res, gpointer d)
{
    View *v = d, *known = NULL;
    for (int i = 0; i < ntabs; i++) if (tabs[i] == v) known = v;
    GError *err = NULL;
    JSCValue *val = webkit_web_view_evaluate_javascript_finish(WEBKIT_WEB_VIEW(src), res, &err);
    g_clear_error(&err);
    if (!val) return;
    if (known && !v->pending_scroll) {
        int y = (int)jsc_value_to_double(val);
        if (y != v->scroll_y) { v->scroll_y = y; push_state(); }
    }
    g_object_unref(val);
}
static gboolean scroll_poll(gpointer d)
{
    for (int i = 0; i < ntabs; i++)
        if (!webkit_web_view_is_loading(tabs[i]->wv))
            webkit_web_view_evaluate_javascript(tabs[i]->wv, "Math.round(window.scrollY || 0)", -1, NULL, NULL, NULL, scroll_polled, tabs[i]);
    return G_SOURCE_CONTINUE;
}

static gboolean tab_decide_policy(WebKitWebView *wv, WebKitPolicyDecision *dec, WebKitPolicyDecisionType type, gpointer d)
{
    /* the online simulator (INTELLICAT_ONLINE=1: the hosted one, every visitor a stranger): the files of the machine are not the visitor's to read (its keys are there) */
    if (type != WEBKIT_POLICY_DECISION_TYPE_RESPONSE && !g_strcmp0(g_getenv("INTELLICAT_ONLINE"), "1")) {
        WebKitURIRequest *rq = webkit_navigation_action_get_request(webkit_navigation_policy_decision_get_navigation_action(WEBKIT_NAVIGATION_POLICY_DECISION(dec)));
        const char *uri = rq ? webkit_uri_request_get_uri(rq) : NULL;
        if (uri && !g_ascii_strncasecmp(uri, "file:", 5)) {
            fprintf(stderr, "ic-browser: refused (online): %s\n", uri);
            webkit_policy_decision_ignore(dec);
            return TRUE;
        }
    }
    return FALSE;  /* the default: pages open in the tab they are in */
}

static View *tab_new(const char *uri)
{
    if (ntabs >= MAX_TABS) return NULL;
    View *v = g_new0(View, 1);
    view_init(v, W, H - strip_h, content_ucm, content_settings, FALSE);
    g_signal_connect(v->wv, "notify::uri", G_CALLBACK(tab_changed), NULL);
    g_signal_connect(v->wv, "notify::title", G_CALLBACK(tab_changed), NULL);
    g_signal_connect(v->wv, "load-changed", G_CALLBACK(tab_load_changed), v);
    g_signal_connect(v->wv, "decide-policy", G_CALLBACK(tab_decide_policy), NULL);
    tabs[ntabs++] = v;
    webkit_web_view_load_uri(v->wv, uri && *uri ? uri : home_url);
    return v;
}

static void tab_activate(int i)
{
    if (i < 0 || i >= ntabs) return;
    for (int k = 0; k < ntabs; k++) {
        if (k == i) wpe_view_backend_add_activity_state(tabs[k]->be, wpe_view_activity_state_visible | wpe_view_activity_state_in_window);
        else wpe_view_backend_remove_activity_state(tabs[k]->be, wpe_view_activity_state_visible);
    }
    active = i;
    schedule_compose();
    push_state();
}

static void tab_close(int i)
{
    if (i < 0 || i >= ntabs) return;
    View *v = tabs[i];
    for (int k = i; k < ntabs - 1; k++) tabs[k] = tabs[k + 1];
    ntabs--;
    g_object_unref(v->wv);
    g_free(v->pix);
    g_free(v->label);
    g_free(v->pending_uri);
    g_free(v);
    if (ntabs == 0) { tab_new(NULL); active = 0; }
    else if (active >= ntabs) active = ntabs - 1;
    else if (i < active) active--;
    tab_activate(active);
}

/* An address typed by the user: with a scheme as it is; a host (no spaces, with a dot or a port, or localhost) gets https://;
   anything else is a search. */
static char *normalize_url(const char *in)
{
    char *s = g_strstrip(g_strdup(in));
    if (!*s) { g_free(s); return g_strdup("about:blank"); }
    if (strstr(s, "://") || g_str_has_prefix(s, "about:")) return s;
    gboolean host = !strpbrk(s, " 	") && (strchr(s, '.') || strchr(s, ':') || g_str_has_prefix(s, "localhost") || *s == '[');
    char *r;
    if (host) r = g_strconcat("https://", s, NULL);
    else { char *q = g_uri_escape_string(s, NULL, FALSE); r = g_strconcat("https://duckduckgo.com/?q=", q, NULL); g_free(q); }
    g_free(s);
    return r;
}

/* ---- the UI page <-> host ------------------------------------------------------------------------------------- */
static gboolean push_idle_id;
static gboolean push_state_now(gpointer d)
{
    push_idle_id = FALSE;
    if (!ui.wv) return G_SOURCE_REMOVE;
    GString *s = g_string_new("{\"tabs\":[");
    for (int i = 0; i < ntabs; i++) {
        char *t = json_escape(webkit_web_view_get_title(tabs[i]->wv)), *u = json_escape(webkit_web_view_get_uri(tabs[i]->wv));
        g_string_append_printf(s, "%s{\"title\":\"%s\",\"uri\":\"%s\",\"scroll\":%d}", i ? "," : "", t, u, tabs[i]->scroll_y);
        g_free(t); g_free(u);
    }
    View *c = cur();
    char *home_js = json_escape(home_url);
    g_string_append_printf(s, "],\"home\":\"%s\",\"active\":%d,\"max\":%d,\"back\":%s,\"fwd\":%s,\"loading\":%s}", home_js, active, MAX_TABS,
                           c && webkit_web_view_can_go_back(c->wv) ? "true" : "false",
                           c && webkit_web_view_can_go_forward(c->wv) ? "true" : "false",
                           c && webkit_web_view_is_loading(c->wv) ? "true" : "false");
    g_free(home_js);
    char *js = g_strdup_printf("window.ic && window.ic.state(%s);", s->str);
    webkit_web_view_evaluate_javascript(ui.wv, js, -1, NULL, NULL, NULL, NULL, NULL);
    g_free(js);
    g_string_free(s, TRUE);
    return G_SOURCE_REMOVE;
}
static void push_state(void)
{
    if (!push_idle_id) push_idle_id = g_idle_add(push_state_now, NULL);
}

static void on_host_message(WebKitUserContentManager *m, JSCValue *value, gpointer d)
{
    char *msg = jsc_value_to_string(value);
    if (!msg) return;
    View *c = cur();
    if (g_str_has_prefix(msg, "nav:") && c) {
        char *u = normalize_url(msg + 4);
        webkit_web_view_load_uri(c->wv, u);
        g_free(u);
    } else if (!strcmp(msg, "back") && c) webkit_web_view_go_back(c->wv);
    else if (!strcmp(msg, "fwd") && c) webkit_web_view_go_forward(c->wv);
    else if (!strcmp(msg, "reload") && c) webkit_web_view_reload(c->wv);
    else if (!strcmp(msg, "sync")) scroll_poll(NULL);   /* about to be saved: where the pages are scrolled to, now, not at the next poll */
    else if (!strcmp(msg, "detach")) {
        /* the tabs moved to the other screen: keep one blank tab, and be ready to take a session again (restore:) */
        while (ntabs > 1) tab_close(ntabs - 1);
        if (ntabs == 1) { tabs[0]->scroll_y = tabs[0]->pending_scroll = 0; webkit_web_view_load_uri(tabs[0]->wv, home_url); }
        active = 0;
        restored = FALSE;
        tab_activate(0);
    }
    else if (!strcmp(msg, "newtab")) { if (tab_new(NULL)) tab_activate(ntabs - 1); }
    else if (g_str_has_prefix(msg, "close:")) tab_close(atoi(msg + 6));
    else if (g_str_has_prefix(msg, "switch:")) tab_activate(atoi(msg + 7));
    else if (g_str_has_prefix(msg, "capture:")) ui_capture = msg[8] == '1';
    else if (!strcmp(msg, "ready")) push_state();
    else if (g_str_has_prefix(msg, "osk:")) set_content_scripts(msg + 4);
    else if (g_str_has_prefix(msg, "restore:") && !restored) {
        /* restore:<active>|<url>\n<url>... : the tabs of the last session, once, at start */
        restored = TRUE;
        const char *p = msg + 8, *bar = strchr(p, '|');
        int act = atoi(p), n = 0;
        if (bar) {
            gchar **urls = g_strsplit(bar + 1, "\n", -1);
            for (int i = 0; urls[i] && n < MAX_TABS; i++) {
                if (!*urls[i]) continue;
                char *tab = strchr(urls[i], '\t');   /* <url>\t<scroll> */
                int sy = tab ? atoi(tab + 1) : 0;
                if (tab) *tab = 0;
                View *v = NULL;
                if (n == 0 && ntabs > 0) { v = tabs[0]; webkit_web_view_load_uri(v->wv, urls[i]); }
                else if (!(v = tab_new(urls[i]))) break;
                v->pending_scroll = sy;
                v->scroll_y = sy;
                g_free(v->pending_uri);
                v->pending_uri = sy > 0 ? g_strdup(urls[i]) : NULL;
                n++;
            }
            g_strfreev(urls);
        }
        if (n > 0) tab_activate(act >= 0 && act < ntabs ? act : 0);
    }
    g_free(msg);
}

/* ---- input ----------------------------------------------------------------------------------------------------- */
static View *ptr_grab;          /* the view a pressed button belongs to until it is released */
static int ptr_x, ptr_y;
static uint32_t ptr_button, ptr_state;

static View *view_at(int y, int *vy)
{
    if (ui_capture || y < strip_h) { *vy = y; return &ui; }
    *vy = y - strip_h;
    return cur();
}

static void pointer_enter(void *d, struct wl_pointer *p, uint32_t serial, struct wl_surface *s, wl_fixed_t x, wl_fixed_t y)
{
    ptr_x = wl_fixed_to_int(x); ptr_y = wl_fixed_to_int(y);
    /* no cursor sprite of the compositor: the console is a touch screen (a sprite would hang where the last click was, also after the external screen is gone), and the
     * pointer of the external screen is drawn by the page itself (web-shell/inject.js) */
    wl_pointer_set_cursor(p, serial, NULL, 0, 0);
}
static void pointer_leave(void *d, struct wl_pointer *p, uint32_t serial, struct wl_surface *s) {}
static void pointer_motion(void *d, struct wl_pointer *p, uint32_t time, wl_fixed_t x, wl_fixed_t y)
{
    ptr_x = wl_fixed_to_int(x); ptr_y = wl_fixed_to_int(y);
    int vy; View *v = ptr_grab ? ptr_grab : view_at(ptr_y, &vy);
    if (!v) return;
    vy = ptr_grab ? (ptr_grab == &ui ? ptr_y : ptr_y - strip_h) : vy;
    struct wpe_input_pointer_event e = { wpe_input_pointer_event_type_motion, time, ptr_x, vy, ptr_button, ptr_state, 0 };
    wpe_view_backend_dispatch_pointer_event(v->be, &e);
}
static void pointer_button(void *d, struct wl_pointer *p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state)
{
    ptr_button = button >= BTN_MOUSE ? button - BTN_MOUSE + 1 : 0;
    ptr_state = state;
    int vy; View *v = ptr_grab ? ptr_grab : view_at(ptr_y, &vy);
    if (!v) return;
    if (state && !ptr_grab) ptr_grab = v;
    vy = v == &ui ? ptr_y : ptr_y - strip_h;
    struct wpe_input_pointer_event e = { wpe_input_pointer_event_type_button, time, ptr_x, vy, ptr_button, ptr_state, 0 };
    wpe_view_backend_dispatch_pointer_event(v->be, &e);
    if (!state) ptr_grab = NULL;
}
static void pointer_axis(void *d, struct wl_pointer *p, uint32_t time, uint32_t axis, wl_fixed_t value)
{
    int vy; View *v = view_at(ptr_y, &vy);
    if (!v) return;
    struct wpe_input_axis_2d_event e = { 0 };
    e.base.type = wpe_input_axis_event_type_mask_2d | wpe_input_axis_event_type_motion_smooth;
    e.base.time = time; e.base.x = ptr_x; e.base.y = vy;
    double dv = wl_fixed_to_double(value) * 8;  /* wheel steps -> pixels */
    if (axis == WL_POINTER_AXIS_VERTICAL_SCROLL) e.y_axis = -dv; else e.x_axis = dv;
    wpe_view_backend_dispatch_axis_event(v->be, &e.base);
}
static void noop(void) {}
#define NOOP ((void *)noop)
static const struct wl_pointer_listener pointer_listener = {
    .enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion, .button = pointer_button, .axis = pointer_axis,
    .frame = NOOP, .axis_source = NOOP, .axis_stop = NOOP, .axis_discrete = NOOP,
};

/* touch: a finger belongs to the view it went down in */
static struct wpe_input_touch_event_raw touch_pts[10];
static View *touch_view;
static void touch_send(View *v, enum wpe_input_touch_event_type type, uint32_t time, int id)
{
    struct wpe_input_touch_event e = { touch_pts, 10, type, id, time };
    wpe_view_backend_dispatch_touch_event(v->be, &e);
}
static void touch_down(void *d, struct wl_touch *t, uint32_t serial, uint32_t time, struct wl_surface *s, int32_t id, wl_fixed_t x, wl_fixed_t y)
{
    if (id < 0 || id >= 10) return;
    int px = wl_fixed_to_int(x), py = wl_fixed_to_int(y), vy;
    if (!touch_view) touch_view = view_at(py, &vy);
    if (!touch_view) return;
    vy = touch_view == &ui ? py : py - strip_h;
    touch_pts[id] = (struct wpe_input_touch_event_raw){ wpe_input_touch_event_type_down, time, id, px, vy };
    touch_send(touch_view, wpe_input_touch_event_type_down, time, id);
}
static void touch_up(void *d, struct wl_touch *t, uint32_t serial, uint32_t time, int32_t id)
{
    if (id < 0 || id >= 10 || !touch_view) return;
    touch_pts[id].type = wpe_input_touch_event_type_up; touch_pts[id].time = time;
    touch_send(touch_view, wpe_input_touch_event_type_up, time, id);
    touch_pts[id].type = wpe_input_touch_event_type_null;
    gboolean any = FALSE;
    for (int i = 0; i < 10; i++) if (touch_pts[i].type != wpe_input_touch_event_type_null) any = TRUE;
    if (!any) touch_view = NULL;
}
static void touch_motion(void *d, struct wl_touch *t, uint32_t time, int32_t id, wl_fixed_t x, wl_fixed_t y)
{
    if (id < 0 || id >= 10 || !touch_view) return;
    touch_pts[id].type = wpe_input_touch_event_type_motion; touch_pts[id].time = time;
    touch_pts[id].x = wl_fixed_to_int(x);
    touch_pts[id].y = wl_fixed_to_int(y) - (touch_view == &ui ? 0 : strip_h);
    touch_send(touch_view, wpe_input_touch_event_type_motion, time, id);
}
static const struct wl_touch_listener touch_listener = {
    .down = touch_down, .up = touch_up, .motion = touch_motion, .frame = NOOP, .cancel = NOOP,
};

/* keyboard */
static struct xkb_context *xkb_ctx;
static struct xkb_keymap *xkb_map;
static struct xkb_state *xkb_st;
static uint32_t mods;
static struct { int32_t rate, delay; guint src; uint32_t key, time; } rep;

static void key_dispatch(uint32_t key, uint32_t state, uint32_t time)
{
    if (!xkb_st) return;
    uint32_t keysym = xkb_state_key_get_one_sym(xkb_st, key);
    struct wpe_input_keyboard_event e = { time, keysym, key, state == WL_KEYBOARD_KEY_STATE_PRESSED, mods };
    View *v = ui_capture ? &ui : cur();
    if (v) wpe_view_backend_dispatch_keyboard_event(v->be, &e);
}
static gboolean rep_fire(gpointer d)
{
    key_dispatch(rep.key, WL_KEYBOARD_KEY_STATE_RELEASED, rep.time);
    key_dispatch(rep.key, WL_KEYBOARD_KEY_STATE_PRESSED, rep.time);
    g_source_remove(rep.src);
    rep.src = g_timeout_add(1000 / (rep.rate > 0 ? rep.rate : 25), rep_fire, NULL);
    return G_SOURCE_REMOVE;
}
static void kb_keymap(void *d, struct wl_keyboard *k, uint32_t fmt, int32_t fd, uint32_t size)
{
    if (fmt != WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1) { close(fd); return; }
    void *m = mmap(NULL, size, PROT_READ, MAP_PRIVATE, fd, 0);
    if (m == MAP_FAILED) { close(fd); return; }
    if (xkb_map) xkb_keymap_unref(xkb_map);
    if (xkb_st) xkb_state_unref(xkb_st);
    xkb_map = xkb_keymap_new_from_string(xkb_ctx, m, XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
    munmap(m, size); close(fd);
    xkb_st = xkb_map ? xkb_state_new(xkb_map) : NULL;
}
static void kb_key(void *d, struct wl_keyboard *k, uint32_t serial, uint32_t time, uint32_t key, uint32_t state)
{
    key += 8;
    key_dispatch(key, state, time);
    if (rep.src && rep.key == key && state == WL_KEYBOARD_KEY_STATE_RELEASED) { g_source_remove(rep.src); rep.src = 0; }
    else if (state == WL_KEYBOARD_KEY_STATE_PRESSED && xkb_map && xkb_keymap_key_repeats(xkb_map, key) && rep.rate != 0) {
        if (rep.src) g_source_remove(rep.src);
        rep.key = key; rep.time = time;
        rep.src = g_timeout_add(rep.delay > 0 ? rep.delay : 400, rep_fire, NULL);
    }
}
static void kb_modifiers(void *d, struct wl_keyboard *k, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t lock, uint32_t grp)
{
    if (!xkb_st) return;
    xkb_state_update_mask(xkb_st, dep, lat, lock, 0, 0, grp);
    mods = 0;
    if (xkb_state_mod_name_is_active(xkb_st, XKB_MOD_NAME_CTRL, XKB_STATE_MODS_EFFECTIVE)) mods |= wpe_input_keyboard_modifier_control;
    if (xkb_state_mod_name_is_active(xkb_st, XKB_MOD_NAME_ALT, XKB_STATE_MODS_EFFECTIVE)) mods |= wpe_input_keyboard_modifier_alt;
    if (xkb_state_mod_name_is_active(xkb_st, XKB_MOD_NAME_SHIFT, XKB_STATE_MODS_EFFECTIVE)) mods |= wpe_input_keyboard_modifier_shift;
}
static void kb_repeat_info(void *d, struct wl_keyboard *k, int32_t rate, int32_t delay) { rep.rate = rate; rep.delay = delay; }
static const struct wl_keyboard_listener keyboard_listener = {
    .keymap = kb_keymap, .enter = NOOP, .leave = NOOP, .key = kb_key, .modifiers = kb_modifiers, .repeat_info = kb_repeat_info,
};

static void seat_caps(void *d, struct wl_seat *s, uint32_t caps)
{
    if ((caps & WL_SEAT_CAPABILITY_POINTER) && !pointer) { pointer = wl_seat_get_pointer(s); wl_pointer_add_listener(pointer, &pointer_listener, NULL); }
    if ((caps & WL_SEAT_CAPABILITY_KEYBOARD) && !keyboard) { keyboard = wl_seat_get_keyboard(s); wl_keyboard_add_listener(keyboard, &keyboard_listener, NULL); }
    if ((caps & WL_SEAT_CAPABILITY_TOUCH) && !touch) { touch = wl_seat_get_touch(s); wl_touch_add_listener(touch, &touch_listener, NULL); }
}
static const struct wl_seat_listener seat_listener = { .capabilities = seat_caps, .name = NOOP };

/* ---- window ---------------------------------------------------------------------------------------------------- */
static void wm_ping(void *d, struct xdg_wm_base *b, uint32_t serial) { xdg_wm_base_pong(b, serial); }
static const struct xdg_wm_base_listener wm_listener = { .ping = wm_ping };

static void xs_configure(void *d, struct xdg_surface *s, uint32_t serial)
{
    xdg_surface_ack_configure(s, serial);
    configured = TRUE;
    if (dirty) schedule_compose();
}
static const struct xdg_surface_listener xs_listener = { .configure = xs_configure };

static void apply_size(int w, int h)
{
    if (w <= 0 || h <= 0 || (w == W && h == H && out[0].buf)) return;
    W = w; H = h;
    alloc_out();
    if (ui.wv) {
        view_resize(&ui, W, H);
        for (int i = 0; i < ntabs; i++) view_resize(tabs[i], W, H - strip_h);
    }
}
static void tl_configure(void *d, struct xdg_toplevel *t, int32_t w, int32_t h, struct wl_array *states) { if (w > 0 && h > 0) apply_size(w, h); }
static void tl_close(void *d, struct xdg_toplevel *t) { g_main_loop_quit(loop); }
static const struct xdg_toplevel_listener tl_listener = { .configure = tl_configure, .close = tl_close };

static void reg_global(void *d, struct wl_registry *r, uint32_t name, const char *iface, uint32_t ver)
{
    if (!strcmp(iface, wl_compositor_interface.name)) compositor = wl_registry_bind(r, name, &wl_compositor_interface, 4);
    else if (!strcmp(iface, wl_shm_interface.name)) shm = wl_registry_bind(r, name, &wl_shm_interface, 1);
    else if (!strcmp(iface, wl_seat_interface.name)) { seat = wl_registry_bind(r, name, &wl_seat_interface, MIN(ver, 5u)); wl_seat_add_listener(seat, &seat_listener, NULL); }
    else if (!strcmp(iface, xdg_wm_base_interface.name)) { wm_base = wl_registry_bind(r, name, &xdg_wm_base_interface, 1); xdg_wm_base_add_listener(wm_base, &wm_listener, NULL); }
}
static const struct wl_registry_listener reg_listener = { .global = reg_global, .global_remove = NOOP };

/* the Wayland socket in the GLib main loop */
typedef struct { GSource src; GPollFD pfd; } WlSource;
static gboolean wl_prepare(GSource *s, gint *t)
{
    *t = -1;
    while (wl_display_prepare_read(dpy) != 0) wl_display_dispatch_pending(dpy);
    wl_display_flush(dpy);
    return FALSE;
}
static gboolean wl_check(GSource *s)
{
    WlSource *w = (WlSource *)s;
    if (w->pfd.revents & G_IO_IN) wl_display_read_events(dpy); else wl_display_cancel_read(dpy);
    return w->pfd.revents != 0;
}
static gboolean wl_dispatch(GSource *s, GSourceFunc cb, gpointer d)
{
    WlSource *w = (WlSource *)s;
    if (w->pfd.revents & (G_IO_ERR | G_IO_HUP)) { g_main_loop_quit(loop); return G_SOURCE_REMOVE; }
    wl_display_dispatch_pending(dpy);
    return G_SOURCE_CONTINUE;
}
static GSourceFuncs wl_funcs = { wl_prepare, wl_check, wl_dispatch, NULL };

/* ---- control channel ------------------------------------------------------------------------------------------ */
static GOutputStream *ctl_out;      /* the one connected client, NULL when none */
static GDataInputStream *ctl_in;
static GSocketConnection *ctl_conn;

static void ctl_send(const char *id, const char *status, const char *data, gsize len)
{
    if (!ctl_out) return;
    char *b64 = g_base64_encode((const guchar *)data, len);
    char *line = g_strdup_printf("%s %s %s\n", id, status, b64);
    g_output_stream_write_all(ctl_out, line, strlen(line), NULL, NULL, NULL);
    g_free(line); g_free(b64);
}
static void ctl_send_str(const char *id, const char *status, const char *s) { ctl_send(id, status, s, strlen(s)); }

static int tab_by_label(const char *label)
{
    /* a tab with no name of its own (the user's, a restored one) is reached by place: "@active", or "@<n>" for the n-th (from 0) */
    if (!strcmp(label, "@active")) return ntabs ? active : -1;
    if (label[0] == '@' && label[1] >= '0' && label[1] <= '9') { int k = atoi(label + 1); return k < ntabs ? k : -1; }
    for (int i = 0; i < ntabs; i++) if (tabs[i]->label && !strcmp(tabs[i]->label, label)) return i;
    return -1;
}

typedef struct { char id[32]; } EvalReq;
static void ctl_eval_done(GObject *src, GAsyncResult *res, gpointer d)
{
    EvalReq *r = d;
    GError *err = NULL;
    JSCValue *v = webkit_web_view_evaluate_javascript_finish(WEBKIT_WEB_VIEW(src), res, &err);
    if (!v) { ctl_send_str(r->id, "err", err ? err->message : "script error"); g_clear_error(&err); }
    else { char *s = jsc_value_to_string(v); ctl_send_str(r->id, "ok", s ? s : ""); g_free(s); g_object_unref(v); }
    g_free(r);
}

static void ctl_handle(const char *line)
{
    gchar **f = g_strsplit(line, " ", 4);
    if (g_strv_length(f) < 3) { g_strfreev(f); return; }
    const char *id = f[0], *op = f[1], *label = f[2];
    gsize plen = 0;
    char *payload = f[3] ? (char *)g_base64_decode(f[3], &plen) : g_strdup("");
    payload = g_realloc(payload, plen + 1); payload[plen] = 0;
    int i = tab_by_label(label);

    if (!strcmp(op, "list")) {
        GString *s = g_string_new("[");
        for (int k = 0; k < ntabs; k++) {
            char *t = json_escape(webkit_web_view_get_title(tabs[k]->wv)), *u = json_escape(webkit_web_view_get_uri(tabs[k]->wv));
            char *l = json_escape(tabs[k]->label ? tabs[k]->label : "");
            g_string_append_printf(s, "%s{\"label\":\"%s\",\"title\":\"%s\",\"uri\":\"%s\",\"loading\":%s,\"active\":%s,\"scroll\":%d}", k ? "," : "", l, t, u,
                                   webkit_web_view_is_loading(tabs[k]->wv) ? "true" : "false", k == active ? "true" : "false", tabs[k]->scroll_y);
            g_free(t); g_free(u); g_free(l);
        }
        g_string_append(s, "]");
        ctl_send_str(id, "ok", s->str);
        g_string_free(s, TRUE);
    } else if (!strcmp(op, "open")) {
        char *u = normalize_url(payload);
        const char *cur_uri = ntabs == 1 ? webkit_web_view_get_uri(tabs[0]->wv) : NULL;
        if (i < 0 && ntabs == 1 && !tabs[0]->label && cur_uri && (!strcmp(cur_uri, home_url) || !strcmp(cur_uri, "about:blank"))) {
            /* the browser's untouched start tab: the first window of the program takes it, so five windows fit into five tabs */
            tabs[0]->label = g_strdup(label);
            webkit_web_view_load_uri(tabs[0]->wv, u);
            tab_activate(0);
            ctl_send_str(id, "ok", "took the start tab");
        } else if (i < 0) {
            View *v = tab_new(u);
            if (!v) ctl_send_str(id, "err", "too many tabs");
            else { v->label = g_strdup(label); tab_activate(ntabs - 1); ctl_send_str(id, "ok", "opened"); }
        } else { webkit_web_view_load_uri(tabs[i]->wv, u); tab_activate(i); ctl_send_str(id, "ok", "reused"); }
        g_free(u);
    } else if (i < 0) {
        ctl_send_str(id, "err", "no such label");
    } else if (!strcmp(op, "navigate")) {
        char *u = normalize_url(payload);
        webkit_web_view_load_uri(tabs[i]->wv, u);
        g_free(u);
        ctl_send_str(id, "ok", "");
    } else if (!strcmp(op, "close")) {
        tab_close(i);
        ctl_send_str(id, "ok", "");
    } else if (!strcmp(op, "activate")) {
        tab_activate(i);
        ctl_send_str(id, "ok", "");
    } else if (!strcmp(op, "eval")) {
        EvalReq *r = g_new0(EvalReq, 1);
        g_strlcpy(r->id, id, sizeof r->id);
        webkit_web_view_evaluate_javascript(tabs[i]->wv, payload, -1, NULL, NULL, NULL, ctl_eval_done, r);
    } else if (!strcmp(op, "shot")) {
        View *v = tabs[i];
        tab_activate(i);
        gsize n = (gsize)v->w * v->h * 4;
        char *hdr = g_strdup_printf("%d %d\n", v->w, v->h);
        gsize hl = strlen(hdr);
        char *buf = g_malloc(hl + n);
        memcpy(buf, hdr, hl); memcpy(buf + hl, v->pix, n);
        ctl_send(id, "ok", buf, hl + n);
        g_free(hdr); g_free(buf);
    } else if (!strcmp(op, "click")) {
        int x = 0, y = 0;
        sscanf(payload, "%d %d", &x, &y);
        View *v = tabs[i];
        uint32_t t = (uint32_t)(g_get_monotonic_time() / 1000);
        struct wpe_input_pointer_event m = { wpe_input_pointer_event_type_motion, t, x, y, 0, 0, 0 };
        wpe_view_backend_dispatch_pointer_event(v->be, &m);
        struct wpe_input_pointer_event d = { wpe_input_pointer_event_type_button, t + 1, x, y, 1, 1, 0 };
        wpe_view_backend_dispatch_pointer_event(v->be, &d);
        struct wpe_input_pointer_event u = { wpe_input_pointer_event_type_button, t + 2, x, y, 1, 0, 0 };
        wpe_view_backend_dispatch_pointer_event(v->be, &u);
        ctl_send_str(id, "ok", "");
    } else ctl_send_str(id, "err", "unknown op");
    g_free(payload);
    g_strfreev(f);
}

static void ctl_read_line(GObject *src, GAsyncResult *res, gpointer d)
{
    GError *err = NULL;
    gsize len = 0;
    char *line = g_data_input_stream_read_line_finish(G_DATA_INPUT_STREAM(src), res, &len, &err);
    if (!line) {  /* the client went away */
        g_clear_error(&err);
        g_clear_object(&ctl_in); g_clear_object(&ctl_conn);
        ctl_out = NULL;
        return;
    }
    ctl_handle(line);
    g_free(line);
    g_data_input_stream_read_line_async(ctl_in, G_PRIORITY_DEFAULT, NULL, ctl_read_line, NULL);
}

static gboolean ctl_incoming(GSocketService *svc, GSocketConnection *conn, GObject *src, gpointer d)
{
    g_clear_object(&ctl_in); g_clear_object(&ctl_conn);   /* a newer client replaces the older one */
    ctl_conn = g_object_ref(conn);
    ctl_out = g_io_stream_get_output_stream(G_IO_STREAM(conn));
    ctl_in = g_data_input_stream_new(g_io_stream_get_input_stream(G_IO_STREAM(conn)));
    g_data_input_stream_set_newline_type(ctl_in, G_DATA_STREAM_NEWLINE_TYPE_LF);
    g_data_input_stream_read_line_async(ctl_in, G_PRIORITY_DEFAULT, NULL, ctl_read_line, NULL);
    return TRUE;
}

static void ctl_init(const char *path)
{
    if (!path || !*path) return;
    unlink(path);
    GSocketService *svc = g_socket_service_new();
    GError *err = NULL;
    GSocketAddress *addr = g_unix_socket_address_new(path);
    if (!g_socket_listener_add_address(G_SOCKET_LISTENER(svc), addr, G_SOCKET_TYPE_STREAM, G_SOCKET_PROTOCOL_DEFAULT, NULL, NULL, &err)) {
        g_printerr("ic-browser: control socket %s: %s\n", path, err ? err->message : "?");
        g_clear_error(&err);
    } else {
        g_signal_connect(svc, "incoming", G_CALLBACK(ctl_incoming), NULL);
        g_socket_service_start(svc);
    }
    g_object_unref(addr);
}

/* ---- main ------------------------------------------------------------------------------------------------------ */
static char *read_file(const char *dir, const char *name)
{
    char *p = g_build_filename(dir, name, NULL), *c = NULL;
    if (!g_file_get_contents(p, &c, NULL, NULL)) g_printerr("ic-browser: cannot read %s\n", p);
    g_free(p);
    return c;
}

static void add_script(const char *src)
{
    if (!src) return;
    WebKitUserScript *us = webkit_user_script_new(src, WEBKIT_USER_CONTENT_INJECT_TOP_FRAME,
                                                  WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_END, NULL, NULL);
    webkit_user_content_manager_add_script(content_ucm, us);
    webkit_user_script_unref(us);
}

/* The scripts every page gets: the keyboard (osk_src: the bundle with the layouts and the interface language; without it the
 * plain osk.js from disk) and inject.js (drag scrolling, no scrollbars). Pages loaded from now on get the new ones. */
static void set_content_scripts(const char *osk_src)
{
    webkit_user_content_manager_remove_all_scripts(content_ucm);
    /* the keyboard's clipboard menu (osk.js) talks to the shell, which is at the origin of the UI page, from a page of any site */
    {
        const char *p = strstr(ui_url, "://");
        const char *slash = p ? strchr(p + 3, '/') : NULL;
        char *origin = slash ? g_strndup(ui_url, slash - ui_url) : g_strdup(ui_url);
        char *flag = g_strdup_printf("window.__icClipShell = '%s';", origin);
        add_script(flag);
        g_free(flag); g_free(origin);
    }
    /* IC_BROWSER_SCREEN=external (the window on the HDMI screen): the keyboard is not drawn in these pages but on the console's; osk.js reports the focus of a field to the
     * shell, which is at the origin of the UI page */
    if (!g_strcmp0(g_getenv("IC_BROWSER_SCREEN"), "external")) {
        const char *p = strstr(ui_url, "://");
        const char *slash = p ? strchr(p + 3, '/') : NULL;
        char *origin = slash ? g_strndup(ui_url, slash - ui_url) : g_strdup(ui_url);
        char *flag = g_strdup_printf("window.__icExternal = true; window.__icShell = '%s';", origin);
        add_script(flag);
        g_free(flag); g_free(origin);
    }
    char *own = NULL;
    if (!osk_src) osk_src = own = read_file(assets_dir, "osk.js");
    add_script(osk_src);
    g_free(own);
    char *inj = read_file(assets_dir, "inject.js");
    add_script(inj);
    g_free(inj);
}

int main(int argc, char **argv)
{
    if (argc < 3) { g_printerr("usage: ic-browser <ui-url> <web-shell-dir> [<start-page-url>]\n"); return 2; }
    ui_url = argv[1];
    if (g_getenv("IC_BROWSER_STRIP")) strip_h = MAX(0, atoi(g_getenv("IC_BROWSER_STRIP")));
    if (argc > 3 && *argv[3]) home_url = argv[3];
    assets_dir = argv[2];

    if (!wpe_loader_init("libWPEBackend-fdo-1.0.so")) { g_printerr("ic-browser: no WPE backend\n"); return 1; }
    dpy = wl_display_connect(NULL);
    if (!dpy) { g_printerr("ic-browser: cannot connect to Wayland\n"); return 1; }
    xkb_ctx = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    registry = wl_display_get_registry(dpy);
    wl_registry_add_listener(registry, &reg_listener, NULL);
    wl_display_roundtrip(dpy);
    wl_display_roundtrip(dpy);
    if (!compositor || !shm || !wm_base) { g_printerr("ic-browser: missing Wayland globals\n"); return 1; }

    surface = wl_compositor_create_surface(compositor);
    xsurface = xdg_wm_base_get_xdg_surface(wm_base, surface);
    xdg_surface_add_listener(xsurface, &xs_listener, NULL);
    toplevel = xdg_surface_get_toplevel(xsurface);
    xdg_toplevel_add_listener(toplevel, &tl_listener, NULL);
    xdg_toplevel_set_title(toplevel, "Browser");
    xdg_toplevel_set_app_id(toplevel, "com.intellicat.app.browser");
    xdg_toplevel_set_fullscreen(toplevel, NULL);
    wl_surface_commit(surface);
    alloc_out();
    while (!configured) if (wl_display_dispatch(dpy) < 0) return 1;

    loop = g_main_loop_new(NULL, FALSE);
    WlSource *ws = (WlSource *)g_source_new(&wl_funcs, sizeof(WlSource));
    ws->pfd.fd = wl_display_get_fd(dpy);
    ws->pfd.events = G_IO_IN | G_IO_ERR | G_IO_HUP;
    g_source_add_poll(&ws->src, &ws->pfd);
    g_source_attach(&ws->src, NULL);

    if (!wpe_fdo_initialize_shm()) { g_printerr("ic-browser: cannot initialize the SHM backend\n"); return 1; }
    web_ctx = g_object_new(WEBKIT_TYPE_WEB_CONTEXT, NULL);

    /* the content: its settings, and the scripts injected into every page (the keyboard, drag scrolling for a mouse) */
    content_settings = webkit_settings_new();
    content_ucm = webkit_user_content_manager_new();
    set_content_scripts(NULL);   /* the UI page sends the keyboard bundle (layouts, language) soon after it starts */

    /* the UI page */
    ui_settings = webkit_settings_new();
    ui_ucm = webkit_user_content_manager_new();
    webkit_user_content_manager_register_script_message_handler(ui_ucm, "host", NULL);
    g_signal_connect(ui_ucm, "script-message-received::host", G_CALLBACK(on_host_message), NULL);
    view_init(&ui, W, H, ui_ucm, ui_settings, TRUE);
    WebKitColor clear = { 0, 0, 0, 0 };
    webkit_web_view_set_background_color(ui.wv, &clear);
    webkit_web_view_load_uri(ui.wv, ui_url);

    tab_new(NULL);
    tab_activate(0);
    ctl_init(g_getenv("IC_BROWSER_CONTROL"));
    g_timeout_add(1500, scroll_poll, NULL);

    g_main_loop_run(loop);
    return 0;
}
