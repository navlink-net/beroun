# Copyright (c) 2026 Konstantin Khait, IT Partners Solutions. MIT License.
"""The sound of a group call (docs/usage.md): the voices of several people are summed into the one stream that goes to the speaker.

Every source (a remote participant, the ringing tone) pushes its samples under a key of its own; `pull()` takes the next 10 ms from each, adds them (16-bit, clipped, no wrapping)
and returns one frame. A source that has nothing ready contributes silence, so a late or silent participant never stalls the others; a source that falls behind more than `cap_ms`
loses its oldest samples (a call is never delayed more than that by a slow reader). Only numpy: the engine uses it, and so can a test.
"""
import numpy as np

RATE, FRAME = 48000, 480                      # 10 ms of mono at 48 kHz


class Mixer:
    def __init__(self, cap_ms=300):
        self.buf = {}                          # key -> int16 samples waiting
        self.cap = RATE * cap_ms // 1000

    def push(self, key, samples):
        s = np.asarray(samples, dtype=np.int16)
        old = self.buf.get(key)
        s = s if old is None or len(old) == 0 else np.concatenate([old, s])
        self.buf[key] = s[-self.cap:] if len(s) > self.cap else s

    def drop(self, key):
        self.buf.pop(key, None)

    def keys(self):
        return list(self.buf)

    def pull(self):
        """The next frame (FRAME samples, int16): the sum of what the sources have ready."""
        acc = np.zeros(FRAME, dtype=np.int32)
        for key, s in list(self.buf.items()):
            take = s[:FRAME]
            acc[:len(take)] += take
            self.buf[key] = s[FRAME:]
        return np.clip(acc, -32768, 32767).astype(np.int16)
