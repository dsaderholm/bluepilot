# Parked warning sounds

`intruder_ryan.wav` is the spoken warning played while the parked recorder's warning is active (mono, 48 kHz, 16-bit, 11.5 s).
It is a two-tone attention sound, then the message twice with a short gap:

> "Attention. Intruder detected. Your face has been recorded. Leave the vehicle immediately."

How it was made (D.J. picked it by ear on the comma, 2026-11-12): Piper text-to-speech, voice `en_US-ryan-high`, slightly slow (length_scale 0.95);
high-pass at 300 Hz, +6 dB at 2.2 kHz and +4 dB at 3.5 kHz, low-pass at 7 kHz; fast leveller toward -3 dB with a tanh soft clip (drive 2.5), then normalised to
-0.2 dBFS. The speech-level average is about -2.7 dBFS, the same as a full-scale siren, which is as loud as this speaker path goes (the amplifier
has its own limiter). `make_warning_clips.py` rebuilds it, and the other variants that were tried, from the wording in `TEXTS`.

The amplifier is shut down while the comma is parked, so anything that plays this must wake it first and put it back afterwards (see `warning_audio_bp.py`).
