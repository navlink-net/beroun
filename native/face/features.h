// Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
/* features.h - audio features of the face renderer (C port of FaceAnimation/_render_worker.py:
 * _start_audio_feat_stream / _fill_feat_stream_chunked, which call librosa).
 *
 * Per output frame a 16-number vector: 13 MFCC, RMS energy, spectral centroid, spectral bandwidth
 * (librosa conventions: Slaney mel filterbank with 128 bands, power-to-dB with top_db 80, DCT-II ortho,
 * periodic Hann window, no centring), the audio looked at LEAD ms ahead of the frame, smoothed with
 * an exponential average `0.45 * previous + 0.55 * current`.
 *
 * The work is done in chunks of 5 s exactly like the Python code (the dB floor of power_to_db is
 * taken over a whole chunk, so the chunking is part of the result). A stream object lets the renderer
 * ask for the next chunk when it needs it.
 */
#ifndef XCFA_FEATURES_H
#define XCFA_FEATURES_H

#include <stddef.h>

#define XCFA_FEAT_DIM 16
#define XCFA_SAMPLE_RATE 16000
#define XCFA_LEAD_MS_DEFAULT 250 /* _FEATURE_LEAD_MS */
#define XCFA_SILENCE_THRESHOLD 0.005

/* Number of output frames for n samples at fps: max(1, int(seconds * fps)). */
size_t xcfa_frame_count(size_t n_samples, int fps);

typedef struct xcfa_feat_stream xcfa_feat_stream;

/* audio: mono float32 at 16 kHz, must stay valid (and unchanged) while the stream is used.
 * sample_fps: the model's frame rate (sets the energy window and n_fft). lead_ms < 0 is allowed.
 * The energies (RMS per frame, double, ready at once) are available with xcfa_feat_energies(). */
xcfa_feat_stream *xcfa_feat_open(const float *audio, size_t n_samples, int fps, int sample_fps, int lead_ms);
void xcfa_feat_close(xcfa_feat_stream *s);

size_t xcfa_feat_frames(const xcfa_feat_stream *s);          /* total output frames */
const double *xcfa_feat_energies(const xcfa_feat_stream *s); /* xcfa_feat_frames() values */
size_t xcfa_feat_ready(const xcfa_feat_stream *s);           /* frames whose features are computed */
/* Computes the next chunk (up to 5 s). Returns the number of frames ready, 0 when finished or on error. */
size_t xcfa_feat_next(xcfa_feat_stream *s);
/* Feature vector of frame i (XCFA_FEAT_DIM floats); valid once i < xcfa_feat_ready(). */
const float *xcfa_feat_vector(const xcfa_feat_stream *s, size_t i);

/* Convenience: everything at once. feats: n_frames * XCFA_FEAT_DIM floats. Returns frames or 0. */
size_t xcfa_features_all(const float *audio, size_t n_samples, int fps, int sample_fps, int lead_ms,
                         float **feats, double **energies);

#endif
