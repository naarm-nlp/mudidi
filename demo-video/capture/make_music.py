"""Synthesise the demo's music bed: 100 BPM, 22 bars, A minor, no samples.

    python capture/make_music.py assets/music.wav
"""
import sys
import wave

import numpy as np

SR = 44100
BPM = 100
BEAT = 60 / BPM
BARS = 22
total = int(SR * BEAT * 4 * BARS)
mix = np.zeros(total + SR * 3)


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def add(signal, start):
    i = int(start * SR)
    mix[i : i + len(signal)] += signal[: len(mix) - i]


def keys(midi, length, gain):
    """Soft electric-piano tone: a few decaying partials with a slow tremolo."""
    t = np.arange(int(length * SR)) / SR
    f = hz(midi)
    tone = sum(a * np.sin(2 * np.pi * f * k * t) * np.exp(-t * d) for k, a, d in ((1, 1.0, 1.1), (2, 0.35, 2.2), (3, 0.12, 3.5), (4, 0.05, 5.0)))
    attack = np.minimum(1, t / 0.012)
    return gain * tone * attack * (1 + 0.06 * np.sin(2 * np.pi * 4.5 * t))


def pluck(midi, gain):
    t = np.arange(int(0.5 * SR)) / SR
    f = hz(midi)
    return gain * (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t)) * np.exp(-t * 9) * np.minimum(1, t / 0.004)


def bass(midi, length, gain):
    t = np.arange(int(length * SR)) / SR
    return gain * np.sin(2 * np.pi * hz(midi) * t) * np.minimum(1, t / 0.02) * np.exp(-t * 1.4)


def kick(gain):
    t = np.arange(int(0.28 * SR)) / SR
    return gain * np.sin(2 * np.pi * (48 * t + 60 * (1 - np.exp(-t * 28)) / 28)) * np.exp(-t * 13)


def hat(gain, rng):
    t = np.arange(int(0.05 * SR)) / SR
    noise = rng.standard_normal(len(t))
    return gain * np.diff(noise, prepend=0) * np.exp(-t * 90)


rng = np.random.default_rng(7)
# Am9 – Fmaj7 – Cmaj7 – Gadd9, one bar each.
chords = [((57, 60, 64, 71), 45), ((53, 57, 60, 64), 41), ((55, 60, 64, 71), 48), ((55, 59, 62, 69), 43)]
arps = [(69, 72, 76, 79), (69, 72, 76, 77), (67, 72, 76, 79), (67, 71, 74, 79)]
for bar in range(BARS):
    start = bar * 4 * BEAT
    notes, root = chords[bar % 4]
    ending = bar >= BARS - 2
    for note in notes:
        add(keys(note, 4 * BEAT + 0.6, 0.11), start)
    add(bass(root - 12 if root > 44 else root, 2.2 * BEAT, 0.34), start)
    if bar >= 2 and not ending:
        add(bass(root - 12 if root > 44 else root, 1.2 * BEAT, 0.26), start + 2.5 * BEAT)
        for beat in (0, 2):
            add(kick(0.5), start + beat * BEAT)
        for eighth in range(8):
            add(hat(0.05 if eighth % 2 else 0.025, rng), start + eighth * BEAT / 2)
    if bar >= 4 and not ending:
        for eighth in range(8):
            add(pluck(arps[bar % 4][(eighth * 3) % 4] + (12 if eighth == 5 else 0), 0.07), start + eighth * BEAT / 2)
    if bar == BARS - 2:
        for i, note in enumerate((57, 64, 69, 72, 76)):
            add(keys(note, 4.5, 0.12), start + i * 0.09)

mix = mix[: total]
# Gentle echo for space, then master fades and a soft limiter.
delay = int(0.75 * BEAT * SR)
mix[delay:] += 0.22 * mix[:-delay].copy()
fade_in, fade_out = int(0.4 * SR), int(3.2 * SR)
mix[:fade_in] *= np.linspace(0, 1, fade_in)
mix[-fade_out:] *= np.linspace(1, 0, fade_out) ** 1.5
mix = np.tanh(mix * 1.5) * 0.8
stereo = np.stack([mix, np.roll(mix, int(0.011 * SR))], axis=1)
with wave.open(sys.argv[1], "wb") as out:
    out.setnchannels(2)
    out.setsampwidth(2)
    out.setframerate(SR)
    out.writeframes((stereo * 32767).astype("<i2").tobytes())
print(f"{BARS} bars at {BPM} BPM = {total / SR:.1f}s")
