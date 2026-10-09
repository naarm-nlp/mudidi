"""Synthesise the demo's music bed: 150 BPM, C major, no samples.

    python capture/make_music.py out.wav assets/timing.json

The length and the point where the drums drop out come from the timing file
that capture/build.mjs writes.

One beat is 0.4s and every scene cut in the video is snapped to
a beat. The drums enter at 7.2s, where the opening hands over to the dashboard,
and drop out where the outro starts.
"""
import json
import math
import sys
import wave

import numpy as np

SR = 44100
BPM = 150
BEAT = 60 / BPM
with open(sys.argv[2]) as handle:
    timing = json.load(handle)
DROP_BEAT = round(timing["drop"] / BEAT)
OUTRO_BEAT = round(timing["outroStart"] / BEAT)
total = int(SR * timing["duration"])
BARS = math.ceil(timing["duration"] / (4 * BEAT))
tail = SR * 3
drums = np.zeros(total + tail)
ducked = np.zeros(total + tail)  # pad and bass, pumped by the kick
lead = np.zeros(total + tail)
rng = np.random.default_rng(11)


def hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def add(bus, signal, start):
    i = int(start * SR)
    bus[i : i + len(signal)] += signal[: len(bus) - i]


def lowpass(signal, cutoff):
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(len(signal), 1 / SR)
    return np.fft.irfft(spectrum / (1 + (freqs / cutoff) ** 4), len(signal))


def saw(freq, t, partials=14):
    return sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, partials + 1) if freq * k < 9000)


def pad(notes, length, gain):
    t = np.arange(int(length * SR)) / SR
    tone = sum(saw(hz(n) * d, t) for n in notes for d in (0.994, 1.0, 1.006))
    env = np.minimum(1, t / 0.03) * np.minimum(1, (length - t) / 0.08).clip(0)
    return gain * lowpass(tone, 2200) * env


def pluck(midi, gain, decay=14):
    t = np.arange(int(0.35 * SR)) / SR
    return gain * saw(hz(midi), t, 8) * np.exp(-t * decay) * np.minimum(1, t / 0.003)


def bass(midi, length, gain):
    t = np.arange(int(length * SR)) / SR
    f = hz(midi)
    tone = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.15 * np.sin(2 * np.pi * 3 * f * t)
    return gain * tone * np.minimum(1, t / 0.008) * np.minimum(1, (length - t) / 0.03).clip(0)


def kick(gain):
    t = np.arange(int(0.26 * SR)) / SR
    body = np.sin(2 * np.pi * (50 * t + 110 * (1 - np.exp(-t * 32)) / 32))
    return gain * (body * np.exp(-t * 11) + 0.25 * np.exp(-t * 300) * np.sign(body))


def clap(gain):
    t = np.arange(int(0.16 * SR)) / SR
    noise = lowpass(rng.standard_normal(len(t)), 5000) - lowpass(rng.standard_normal(len(t)), 900)
    bursts = sum(np.exp(-np.maximum(0, t - d) * 60) * (t >= d) for d in (0.0, 0.011, 0.022))
    return gain * noise * bursts / 3


def hat(gain, length=0.05):
    t = np.arange(int(length * SR)) / SR
    return gain * np.diff(rng.standard_normal(len(t)), prepend=0) * np.exp(-t * (3.5 / length))


# F – G – Am – C, one bar each: bright and forward-moving.
chords = [((53, 57, 60, 65), 41), ((55, 59, 62, 67), 43), ((57, 60, 64, 69), 45), ((60, 64, 67, 72), 48)]
arp_order = (0, 2, 1, 3, 2, 1, 3, 2)

for bar in range(BARS):
    notes, root = chords[bar % 4]
    for step in range(4):
        beat = bar * 4 + step
        start = beat * BEAT
        main = DROP_BEAT <= beat < OUTRO_BEAT
        # Pad stabs: sustained in the intro and outro, rhythmic in the main section.
        if step == 0 or (bar * 4 + step) == DROP_BEAT:
            length = 4 * BEAT - 0.02 if not main else 1.5 * BEAT
            add(ducked, pad(notes, length, 0.022 if main else 0.018), start)
        if main and step == 2:
            add(ducked, pad(notes, 1.0 * BEAT, 0.02), start + 0.5 * BEAT)
        # Arpeggio: eighth notes throughout, an octave higher after the drop.
        for half in range(2):
            index = arp_order[(step * 2 + half) % 8]
            note = notes[index] + (24 if main else 12)
            add(lead, pluck(note, 0.055 if main else 0.04), start + half * BEAT / 2)
        if main:
            add(drums, kick(0.62), start)
            add(drums, hat(0.07, 0.09), start + BEAT / 2)
            for quarter in (0.25, 0.75):
                add(drums, hat(0.025), start + quarter * BEAT)
            if step in (1, 3):
                add(drums, clap(0.34), start)
            # Offbeat bass, answering the kick.
            add(ducked, bass(root - 12, 0.42 * BEAT, 0.3), start + BEAT / 2)
        elif beat < DROP_BEAT and step == 0:
            add(ducked, bass(root - 12, 3.5 * BEAT, 0.2), start)

# Riser into the drop: noise sweep plus a snare roll over the last four beats.
rise_len = 4 * BEAT
t = np.arange(int(rise_len * SR)) / SR
noise = rng.standard_normal(len(t))
sweep = np.concatenate([lowpass(chunk, 400 + 7000 * i / 15) for i, chunk in enumerate(np.array_split(noise, 16))])
add(lead, 0.09 * sweep * (t / rise_len) ** 2, DROP_BEAT * BEAT - rise_len)
for i in range(16):
    add(drums, clap(0.1 + 0.014 * i), DROP_BEAT * BEAT - rise_len + i * BEAT / 4)
# Outro: one long chord with the arpeggio ringing out.
add(ducked, pad((48, 55, 60, 64, 67), 6.0, 0.02), OUTRO_BEAT * BEAT)
add(drums, kick(0.6), OUTRO_BEAT * BEAT)

# Sidechain: duck the pad and bass on every kick of the main section.
time = np.arange(len(ducked)) / SR
pump = 1 - 0.65 * np.exp(-(time % BEAT) / 0.1)
in_main = (time >= DROP_BEAT * BEAT) & (time < OUTRO_BEAT * BEAT)
ducked *= np.where(in_main, pump, 1.0)

# Dotted-eighth echo on the lead for width.
delay = int(0.75 * BEAT * SR)
lead[delay:] += 0.3 * lead[:-delay].copy()

mix = (drums + ducked + lead)[:total]
fade_in, fade_out = int(0.3 * SR), int(3.6 * SR)
mix[:fade_in] *= np.linspace(0, 1, fade_in)
mix[-fade_out:] *= np.linspace(1, 0, fade_out) ** 1.5
mix = np.tanh(mix * 1.6) * 0.82
stereo = np.stack([mix, np.roll(mix, int(0.009 * SR))], axis=1)
with wave.open(sys.argv[1], "wb") as out:
    out.setnchannels(2)
    out.setsampwidth(2)
    out.setframerate(SR)
    out.writeframes((stereo * 32767).astype("<i2").tobytes())
print(f"{BPM} BPM, {total / SR:.1f}s, drums {DROP_BEAT * BEAT:.1f}s to {OUTRO_BEAT * BEAT:.1f}s")
