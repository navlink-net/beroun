// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* beroun_native - what a native part of OS Beroun asks of the system: a window with alpha over the screen, sound
 * with the position of the card, nothing else. A part calls this and never Wayland, ALSA or the NDK itself, so no `#ifdef ANDROID` stays in it.
 * Behind it: Wayland and ALSA on Linux (beroun_native_linux.c), `ANativeWindow` and AAudio on Android (stage 5).
 * The input (buttons, sticks) joins when a part needs it; the face takes none.
 */
#ifndef BEROUN_NATIVE_H
#define BEROUN_NATIVE_H

#include <stddef.h>
#include <stdint.h>

/* ---- the window: an overlay with alpha that takes no touches ---------------------------------------------------------------- */
/* Opens it as large as the system gives (*w, *h). 0 on success. */
int bn_window_open(const char *title, const char *app_id, int *w, int *h);
/* A free buffer of w*h premultiplied ARGB pixels (0xAARRGGBB) to draw into, or NULL when both are still held by the compositor. */
uint32_t *bn_window_acquire(void);
/* Shows the buffer got from bn_window_acquire(). */
void bn_window_present(uint32_t *px);
/* The system asked to close the window. */
int bn_window_closed(void);

/* Waits up to timeout_ms for the window's events and for `nfds` descriptors (POLLIN; revents are filled in as poll(2) does) and handles the window's events. Returns poll's result. */
int bn_wait(int *fds, int nfds, short *revents, int timeout_ms);

/* ---- the sound: mono 16-bit playback whose position is the clock of the animation --------------------------------------------- */
typedef struct bn_audio bn_audio;
bn_audio *bn_audio_open(int rate);                       /* NULL when there is no sound device */
/* Writes samples, blocking until the device takes them. Returns how many were taken, or -1 when the device failed for good. */
long bn_audio_write(bn_audio *a, const int16_t *pcm, size_t n);
/* How many samples written are still not heard (the device's buffer). */
long bn_audio_delay(bn_audio *a);
void bn_audio_drop(bn_audio *a);                         /* stop at once */
void bn_audio_close(bn_audio *a);

#endif
