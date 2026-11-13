"""Run with the piper-tts and scipy packages and a downloaded voice model (this is a build tool, not part of the car software).
Usage: python make_warning_clips.py <dir with en_US-ryan-high.onnx> <output dir>

Builds loud warning clips: text-to-speech -> band-limit and presence boost -> heavy compression -> soft clip to full scale.

Goal is loudness and clarity on the comma's small speaker, not effects. Output: mono 48 kHz 16-bit WAV.
"""
import sys
import wave

import numpy as np
from piper import PiperVoice
from scipy import signal

SR = 48000
HERE = sys.argv[1]
OUT = sys.argv[2]


def synth(voice_name: str, text: str, speed: float = 0.95) -> np.ndarray:
  voice = PiperVoice.load(f"{HERE}/{voice_name}.onnx")
  chunks = []
  from piper import SynthesisConfig
  cfg = SynthesisConfig(length_scale=speed)       # slightly slower than normal = clearer
  for ch in voice.synthesize(text, syn_config=cfg):
    chunks.append(ch.audio_float_array)
  x = np.concatenate(chunks).astype(np.float64)
  return signal.resample_poly(x, SR, voice.config.sample_rate)


def eq(x: np.ndarray) -> np.ndarray:
  # the speaker is tiny: drop everything under 300 Hz (it only eats headroom), lift 1.5-4 kHz where speech carries and the speaker is strongest
  sos = signal.butter(4, 300, "highpass", fs=SR, output="sos")
  x = signal.sosfilt(sos, x)
  for f0, gain_db, q in ((2200, 6.0, 0.9), (3500, 4.0, 1.2)):
    w0 = 2 * np.pi * f0 / SR
    a_ = 10 ** (gain_db / 40)
    alpha = np.sin(w0) / (2 * q)
    b = [1 + alpha * a_, -2 * np.cos(w0), 1 - alpha * a_]
    a = [1 + alpha / a_, -2 * np.cos(w0), 1 - alpha / a_]
    x = signal.lfilter(b, a, x)
  sos = signal.butter(4, 7000, "lowpass", fs=SR, output="sos")
  return signal.sosfilt(sos, x)


def compress(x: np.ndarray, target_db: float, drive: float) -> np.ndarray:
  """Fast leveller: every syllable is pulled to the same level, then tanh soft-clips the peaks so the average gets very high."""
  env = np.abs(x)
  win = int(0.012 * SR)
  env = np.convolve(env, np.ones(win) / win, mode="same")                  # ~12 ms envelope
  slow = signal.lfilter([0.002], [1, -0.998], env)                          # ~0.5 s average, so pauses are not boosted into noise
  gain = (10 ** (target_db / 20)) / np.maximum(env * 0.7 + slow * 0.3, 1e-4)
  gain = np.minimum(gain, 30.0)
  y = x * gain
  y = np.tanh(drive * y) / np.tanh(drive)                                   # soft clip
  return y


def finish(x: np.ndarray) -> np.ndarray:
  return x / max(1e-9, np.max(np.abs(x))) * 0.98


def chirp(secs=0.6) -> np.ndarray:
  """Two-tone attention sound ahead of the voice."""
  t = np.arange(int(SR * 0.3)) / SR
  a = np.sin(2 * np.pi * 1700 * t)
  b = np.sin(2 * np.pi * 1100 * t)
  seg = np.concatenate([a, b, a, b])
  fade = np.minimum(1, np.minimum(np.arange(seg.size), np.arange(seg.size)[::-1]) / 200)
  return seg * fade * 0.9


def stats(name: str, x: np.ndarray) -> str:
  peak = 20 * np.log10(np.max(np.abs(x)))
  active = x[np.abs(x) > 0.02]
  rms = 20 * np.log10(np.sqrt(np.mean(active ** 2)))
  return f"{name:34s} peak {peak:5.1f} dBFS   speech-level RMS {rms:5.1f} dBFS   {x.size / SR:4.1f} s"


def write(path: str, x: np.ndarray) -> None:
  with wave.open(path, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


TEXTS = {
  "recorded": "Warning. You are being recorded. Leave the vehicle now.",
  "intruder": "Attention. Intruder detected. Your face has been recorded. Leave the vehicle immediately.",
}
for tag, text in TEXTS.items():
  for voice in ("en_US-ryan-high", "en_US-lessac-high"):
    raw = synth(voice, text)
    loud = finish(compress(eq(raw), target_db=-3.0, drive=2.5))
    gap = np.zeros(int(0.7 * SR))
    clip = np.concatenate([chirp(), np.zeros(int(0.25 * SR)), loud, gap, loud])
    clip = finish(clip)
    name = f"{tag}_{voice.split('-')[1]}"
    write(f"{OUT}/{name}.wav", clip)
    print(stats(name, clip))
print(stats("(reference) full-scale siren", np.sin(2 * np.pi * np.cumsum(600 + 900 * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * np.arange(SR * 4) / SR))) / SR)))

# milder variant for comparison: less clipping, so words stay crisp; still compressed and boosted
raw = synth("en_US-ryan-high", TEXTS["recorded"])
for name, drive, tgt in (("recorded_ryan_clear", 1.1, -6.0),):
  loud = finish(compress(eq(raw), target_db=tgt, drive=drive))
  clip = finish(np.concatenate([chirp(), np.zeros(int(0.25 * SR)), loud, np.zeros(int(0.7 * SR)), loud]))
  write(f"{OUT}/{name}.wav", clip)
  print(stats(name, clip))
