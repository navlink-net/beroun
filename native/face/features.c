// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* features.c - audio features of the face renderer. See features.h. */
#define _GNU_SOURCE
#include "features.h"

#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define N_MELS 128
#define N_MFCC 13
#define TOP_DB 80.0
#define AMIN 1e-10
#define EMA_OLD 0.45f
#define EMA_NEW 0.55f

struct xcfa_feat_stream {
    const float *audio;
    size_t n_sig;
    int fps, sr, hop, n_fft, bins, window_samples;
    long lead_samples;
    size_t n_frames, chunk_frames;
    float *shifted_owned; /* only for a negative lead (zeros in front) */
    const float *shifted;
    size_t n_shifted;
    double *energies;
    float *feats;       /* n_frames * 16 */
    size_t ready;
    int have_ema;
    float ema[XCFA_FEAT_DIM];
    double *window;     /* n_fft */
    double *mel;        /* N_MELS * bins */
    double *dct;        /* N_MFCC * N_MELS */
    double *freqs;      /* bins */
    double *re, *im;    /* fft scratch, n_fft */
    double *cos_t, *sin_t; /* fft twiddles, n_fft/2 */
};

size_t xcfa_frame_count(size_t n_samples, int fps) {
    double secs = (double)n_samples / XCFA_SAMPLE_RATE;
    long n = (long)(secs * fps);
    return n < 1 ? 1 : (size_t)n;
}

static int bit_length(unsigned long v) {
    int n = 0;
    while (v) { n++; v >>= 1; }
    return n;
}

/* ---- librosa pieces -------------------------------------------------------------------- */

static double hz_to_mel(double f) { /* Slaney */
    const double f_sp = 200.0 / 3.0, min_log_hz = 1000.0, min_log_mel = min_log_hz / f_sp, logstep = log(6.4) / 27.0;
    return f >= min_log_hz ? min_log_mel + log(f / min_log_hz) / logstep : f / f_sp;
}

static double mel_to_hz(double m) {
    const double f_sp = 200.0 / 3.0, min_log_hz = 1000.0, min_log_mel = min_log_hz / f_sp, logstep = log(6.4) / 27.0;
    return m >= min_log_mel ? min_log_hz * exp(logstep * (m - min_log_mel)) : f_sp * m;
}

static void build_mel(double *w, int n_fft, int sr, const double *freqs) {
    int bins = 1 + n_fft / 2;
    double mel_f[N_MELS + 2];
    double lo = hz_to_mel(0.0), hi = hz_to_mel(sr / 2.0);
    for (int i = 0; i < N_MELS + 2; i++) mel_f[i] = mel_to_hz(lo + (hi - lo) * i / (N_MELS + 1));
    for (int i = 0; i < N_MELS; i++) {
        double d0 = mel_f[i + 1] - mel_f[i], d1 = mel_f[i + 2] - mel_f[i + 1];
        double enorm = 2.0 / (mel_f[i + 2] - mel_f[i]);
        for (int k = 0; k < bins; k++) {
            double lower = -(mel_f[i] - freqs[k]) / d0;
            double upper = (mel_f[i + 2] - freqs[k]) / d1;
            double v = lower < upper ? lower : upper;
            w[i * bins + k] = (v > 0 ? v : 0) * enorm;
        }
    }
}

static void build_dct(double *c) { /* scipy.fft.dct type 2, norm='ortho', first N_MFCC rows */
    for (int k = 0; k < N_MFCC; k++) {
        double scale = k == 0 ? sqrt(1.0 / (4.0 * N_MELS)) : sqrt(1.0 / (2.0 * N_MELS));
        for (int n = 0; n < N_MELS; n++) c[k * N_MELS + n] = 2.0 * scale * cos(M_PI * k * (2 * n + 1) / (2.0 * N_MELS));
    }
}

static void fft(xcfa_feat_stream *s) { /* in place, radix 2, n = n_fft */
    int n = s->n_fft;
    double *re = s->re, *im = s->im;
    for (int i = 1, j = 0; i < n; i++) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { double t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (int len = 2; len <= n; len <<= 1) {
        int step = n / len;
        for (int i = 0; i < n; i += len)
            for (int j = 0; j < len / 2; j++) {
                double wr = s->cos_t[j * step], wi = -s->sin_t[j * step];
                int a = i + j, b = i + j + len / 2;
                double xr = re[b] * wr - im[b] * wi, xi = re[b] * wi + im[b] * wr;
                re[b] = re[a] - xr; im[b] = im[a] - xi;
                re[a] += xr; im[a] += xi;
            }
    }
}

/* ---- stream -------------------------------------------------------------------------------- */

xcfa_feat_stream *xcfa_feat_open(const float *audio, size_t n_samples, int fps, int sample_fps, int lead_ms) {
    xcfa_feat_stream *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->audio = audio;
    s->n_sig = n_samples;
    s->fps = fps;
    s->sr = XCFA_SAMPLE_RATE;
    s->hop = (int)(s->sr / fps);
    if (s->hop < 64) s->hop = 64;
    int ws = (int)(s->sr / sample_fps);
    s->window_samples = ws < 256 ? 256 : ws;
    int e = bit_length((unsigned long)(s->window_samples - 1)) - 1;
    if (e < 7) e = 7;
    s->n_fft = 1 << e;
    if (s->n_fft > 2048) s->n_fft = 2048;
    s->bins = 1 + s->n_fft / 2;
    s->lead_samples = (long)((double)s->sr * lead_ms / 1000.0);
    if (lead_ms < 0) s->lead_samples = -(long)((double)s->sr * (-lead_ms) / 1000.0); /* python int(): truncation toward zero */
    s->n_frames = xcfa_frame_count(n_samples, fps);
    long cf = (long)(fps * 5);
    s->chunk_frames = cf < 1 ? 1 : (size_t)cf;

    /* shifted = audio[lead:] when 0 <= lead < n, audio when lead >= n, zeros + audio when lead < 0 */
    if (s->lead_samples >= 0) {
        if ((size_t)s->lead_samples < n_samples) { s->shifted = audio + s->lead_samples; s->n_shifted = n_samples - (size_t)s->lead_samples; }
        else { s->shifted = audio; s->n_shifted = n_samples; }
    } else {
        size_t z = (size_t)(-s->lead_samples);
        s->shifted_owned = calloc(z + n_samples + 1, sizeof(float));
        if (!s->shifted_owned) { xcfa_feat_close(s); return NULL; }
        memcpy(s->shifted_owned + z, audio, n_samples * sizeof(float));
        s->shifted = s->shifted_owned;
        s->n_shifted = z + n_samples;
    }

    s->energies = malloc(s->n_frames * sizeof(double));
    s->feats = calloc(s->n_frames * XCFA_FEAT_DIM, sizeof(float));
    s->window = malloc(s->n_fft * sizeof(double));
    s->mel = malloc((size_t)N_MELS * s->bins * sizeof(double));
    s->dct = malloc((size_t)N_MFCC * N_MELS * sizeof(double));
    s->freqs = malloc(s->bins * sizeof(double));
    s->re = malloc(s->n_fft * sizeof(double));
    s->im = malloc(s->n_fft * sizeof(double));
    s->cos_t = malloc(s->n_fft / 2 * sizeof(double));
    s->sin_t = malloc(s->n_fft / 2 * sizeof(double));
    if (!s->energies || !s->feats || !s->window || !s->mel || !s->dct || !s->freqs || !s->re || !s->im || !s->cos_t || !s->sin_t) {
        xcfa_feat_close(s);
        return NULL;
    }
    for (int i = 0; i < s->n_fft; i++) s->window[i] = 0.5 - 0.5 * cos(2.0 * M_PI * i / s->n_fft); /* periodic Hann */
    for (int i = 0; i < s->n_fft / 2; i++) { s->cos_t[i] = cos(2.0 * M_PI * i / s->n_fft); s->sin_t[i] = sin(2.0 * M_PI * i / s->n_fft); }
    for (int k = 0; k < s->bins; k++) s->freqs[k] = (double)k * s->sr / s->n_fft;
    build_mel(s->mel, s->n_fft, s->sr, s->freqs);
    build_dct(s->dct);

    /* RMS energy of a window of window_samples around frame time + lead, via a running sum of squares in double */
    double *cum = malloc((n_samples + 1) * sizeof(double));
    if (!cum) { xcfa_feat_close(s); return NULL; }
    cum[0] = 0.0;
    for (size_t i = 0; i < n_samples; i++) cum[i + 1] = cum[i] + (double)audio[i] * (double)audio[i];
    long half = s->window_samples / 2;
    for (size_t f = 0; f < s->n_frames; f++) {
        long centre = (long)((double)f / fps * s->sr + (double)s->lead_samples);
        long start = centre - half;
        if (start < 0) start = 0;
        if (start > (long)n_samples) start = (long)n_samples;
        long end = start + s->window_samples;
        if (end > (long)n_samples) end = (long)n_samples;
        double sum = cum[end] - cum[start];
        long len = end - start;
        if (len < 1) len = 1;
        s->energies[f] = sqrt(sum / (double)len);
    }
    free(cum);
    return s;
}

void xcfa_feat_close(xcfa_feat_stream *s) {
    if (!s) return;
    free(s->shifted_owned); free(s->energies); free(s->feats); free(s->window); free(s->mel); free(s->dct);
    free(s->freqs); free(s->re); free(s->im); free(s->cos_t); free(s->sin_t);
    free(s);
}

size_t xcfa_feat_frames(const xcfa_feat_stream *s) { return s->n_frames; }
const double *xcfa_feat_energies(const xcfa_feat_stream *s) { return s->energies; }
size_t xcfa_feat_ready(const xcfa_feat_stream *s) { return s->ready; }
const float *xcfa_feat_vector(const xcfa_feat_stream *s, size_t i) { return s->feats + i * XCFA_FEAT_DIM; }

size_t xcfa_feat_next(xcfa_feat_stream *s) {
    if (s->ready >= s->n_frames) return 0;
    size_t chunk_start = s->ready;
    size_t chunk_end = chunk_start + s->chunk_frames;
    if (chunk_end > s->n_frames) chunk_end = s->n_frames;
    size_t n_chunk = chunk_end - chunk_start;

    /* the audio of this chunk: from a hop-aligned start, n_fft samples of context, zero padded at the end of the file */
    size_t s_start = chunk_start * (size_t)s->hop;
    size_t s_end = chunk_end * (size_t)s->hop + (size_t)s->n_fft;
    if (s_end > s->n_shifted) s_end = s->n_shifted;
    size_t len = s_end > s_start ? s_end - s_start : 0;
    float *buf = NULL;
    const float *y;
    if (len < (size_t)s->n_fft) {
        buf = calloc((size_t)s->n_fft, sizeof(float));
        if (!buf) return 0;
        if (len) memcpy(buf, s->shifted + s_start, len * sizeof(float));
        y = buf;
        len = (size_t)s->n_fft;
    } else y = s->shifted + s_start;
    size_t T = 1 + (len - (size_t)s->n_fft) / (size_t)s->hop; /* librosa frames, center=False */

    double *db = malloc((size_t)N_MELS * T * sizeof(double)); /* power in dB, [t][mel] */
    double *cent = malloc(T * sizeof(double)), *bw = malloc(T * sizeof(double));
    double *mag = malloc((size_t)s->bins * sizeof(double)), *pw = malloc((size_t)s->bins * sizeof(double));
    if (!db || !cent || !bw || !mag || !pw) { free(buf); free(db); free(cent); free(bw); free(mag); free(pw); return 0; }

    double max_db = -1e300;
    for (size_t t = 0; t < T; t++) {
        const float *fr = y + t * (size_t)s->hop;
        for (int i = 0; i < s->n_fft; i++) { s->re[i] = fr[i] * s->window[i]; s->im[i] = 0.0; }
        fft(s);
        double l1 = 0.0;
        for (int k = 0; k < s->bins; k++) {
            double m2 = s->re[k] * s->re[k] + s->im[k] * s->im[k];
            pw[k] = m2;
            mag[k] = sqrt(m2);
            l1 += mag[k];
        }
        /* spectral centroid / bandwidth on the L1-normalised magnitude (columns that sum to ~0 stay zero) */
        double c = 0.0, b = 0.0;
        if (l1 >= 1.1754943508222875e-38) {
            for (int k = 0; k < s->bins; k++) c += s->freqs[k] * (mag[k] / l1);
            for (int k = 0; k < s->bins; k++) { double d = fabs(s->freqs[k] - c); b += (mag[k] / l1) * d * d; }
            b = sqrt(b);
        }
        cent[t] = c;
        bw[t] = b;
        for (int m = 0; m < N_MELS; m++) {
            const double *w = s->mel + (size_t)m * s->bins;
            double acc = 0.0;
            for (int k = 0; k < s->bins; k++) acc += w[k] * pw[k];
            double v = 10.0 * log10(acc > AMIN ? acc : AMIN);
            db[t * N_MELS + m] = v;
            if (v > max_db) max_db = v;
        }
    }
    double floor_db = max_db - TOP_DB; /* power_to_db(top_db=80) is relative to the loudest cell of the whole chunk */

    for (size_t i = 0; i < n_chunk; i++) {
        size_t t = i < T ? i : T - 1; /* ti_idx = minimum(arange(n_chunk), T - 1) */
        float vec[XCFA_FEAT_DIM];
        for (int k = 0; k < N_MFCC; k++) {
            double acc = 0.0;
            for (int m = 0; m < N_MELS; m++) {
                double v = db[t * N_MELS + m];
                acc += s->dct[k * N_MELS + m] * (v > floor_db ? v : floor_db);
            }
            vec[k] = (float)acc;
        }
        vec[13] = (float)s->energies[chunk_start + i];
        vec[14] = (float)cent[t];
        vec[15] = (float)bw[t];
        float *dst = s->feats + (chunk_start + i) * XCFA_FEAT_DIM;
        if (!s->have_ema) { memcpy(s->ema, vec, sizeof vec); s->have_ema = 1; }
        else for (int k = 0; k < XCFA_FEAT_DIM; k++) s->ema[k] = EMA_OLD * s->ema[k] + EMA_NEW * vec[k];
        memcpy(dst, s->ema, sizeof s->ema);
    }
    free(buf); free(db); free(cent); free(bw); free(mag); free(pw);
    s->ready = chunk_end;
    return s->ready;
}

size_t xcfa_features_all(const float *audio, size_t n_samples, int fps, int sample_fps, int lead_ms,
                         float **feats, double **energies) {
    xcfa_feat_stream *s = xcfa_feat_open(audio, n_samples, fps, sample_fps, lead_ms);
    if (!s) return 0;
    while (xcfa_feat_next(s)) {}
    size_t n = s->n_frames;
    if (s->ready != n) { xcfa_feat_close(s); return 0; }
    *feats = s->feats; s->feats = NULL;
    *energies = s->energies; s->energies = NULL;
    xcfa_feat_close(s);
    return n;
}
