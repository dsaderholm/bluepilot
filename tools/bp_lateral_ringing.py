"""Characterise the lateral ping-pong on GRADUAL turns. Read-only, runs off-device.

HIS REPORT, 2026-08-27: *"we have a weird thing where it oversteers and then corrects itself on
more gradual turns and ping pongs while doing it"*, and when it was written up as if the Yosemite
trip caused it: *"No, it's been like this for a bit."* Then, unprompted: *"It almost seems like a
latency thing, too."*

This does NOT tune anything. It answers which of three mechanisms is consistent with the data,
because all three are plausible from reading and only one set of numbers can be true.

  1. THE GAIN BAND. `curvature_factor` interpolates the gain across `abs(kappa_cmd)` in
     [0.0007, 0.001] -- a 1429 m radius to a 1000 m radius -- and it MULTIPLIES the command
     (`path_angle = kappa_cmd * v_ego * curvature_factor`). Sharp corners are pinned above the band
     and stable; a gradual turn sits IN the transition, where curvature noise swings the gain.
     -> if true: ringing concentrates while |desiredCurvature| is inside the band.

  2. UNDER-COMPENSATED LAG -- HIS READ. The VLT lookahead floor is
     `clip(liveDelay.lateralDelay, 0.1, 0.15)` while `interfaces_ext` declares
     `steerActuatorDelay = 0.22`. If liveDelay learns above 0.15 the clip truncates it silently and
     the controller is permanently behind.
     -> if true: `lateralDelay` sits at or above the 0.15 rail.

  3. THE RATE LIMITER. `carStateBP.angleRateLimited` says the path_angle soft-ROC clip bit that
     frame. A rate-limited command lags the desired one, the error grows, then it catches up and
     overshoots -- latency-shaped ringing from a limiter rather than a gain.
     -> if true: ringing episodes are dense in rate-limited frames.

RINGING is measured as SIGN CHANGES of the tracking error (desiredCurvature - curvature) while
lateral is active and the driver's hands are OFF. A controller that merely LAGS has a steady
one-signed error; one that RINGS crosses zero repeatedly. That distinction is the whole point.

Speed is split across the 13.5-26.82 m/s (30-60 mph) gain blend, because if the ringing lives on
one side of it the blend is implicated and the other side is a control.

    python tools/bp_lateral_ringing.py <dir-of-rlog.zst>
"""
import collections
import glob
import math
import os
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694

# The gain-schedule band, straight out of lateral_angle_ext._update_calculations.
KAPPA_LO, KAPPA_HI = 0.0007, 0.001
# The VLT lookahead clip, from the same file.
DELAY_CLIP_LO, DELAY_CLIP_HI = 0.10, 0.15
# What interfaces_ext declares the actuator delay to be.
DECLARED_ACTUATOR_DELAY = 0.22

GRADUAL_R = (300.0, 3000.0)     # radius band we call "gradual"
MIN_RUN = 50                    # frames of continuous qualifying curve
SPEED_BINS = ((0, 30), (30, 45), (45, 60), (60, 95))
MIN_ERR = 2e-5                  # rad/m; ignore sign flips in pure noise near zero


def say(s=""):
  print(s)
  sys.stdout.flush()


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def radius(k):
  return (1.0 / abs(k)) if abs(k) > 1e-9 else float("inf")


PARAM_KEYS = ("FordLowSpeedFactor_ang", "FordHighSpeedFactor_ang", "FordHighSpeedDampening_ang",
              "FordVLTExtraMax", "FordPathAngleBlendRatio")


def route_config(paths):
  """The params as they stood when this route started.

  HE CHANGED THE ANGLE PARAMETERS AND THE MODEL PARTWAY THROUGH THE TRIP (2026-08-27, his words:
  "I did try changing my models about half way to California today. I also did change the angle
  parameters"). Pooling ringing across the whole trip would therefore average two or three
  different configurations, which is the population error this fork keeps making. initData carries
  a full params snapshot per segment, so the configuration is recoverable per route.
  """
  cfg = {}
  for p in paths[:1]:
    try:
      with open(p, "rb") as f:
        raw = zstandard.ZstdDecompressor().stream_reader(f).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      return cfg
    while True:
      try:
        m = next(evs)
      except (StopIteration, Exception):
        break
      if m.which() != "initData":
        continue
      try:
        params = {e.key: e.value for e in m.initData.params.entries}
      except Exception:
        return cfg
      for k in PARAM_KEYS:
        v = params.get(k)
        if v is not None:
          try:
            cfg[k] = v.decode(errors="replace").strip()
          except Exception:
            cfg[k] = repr(v)
      for k, v in params.items():
        if "Model" in k and v and len(v) < 120:
          try:
            t = v.decode(errors="replace").strip()
          except Exception:
            continue
          if t and not t.isdigit():
            cfg.setdefault("model", t[:48])
      return cfg
  return cfg


def scan(paths):
  """Yield per-frame lateral state, carrying the most recent value of each stream."""
  cur = dict(lat_active=False, delay=float("nan"), rate_limited=False)
  for p in paths:
    try:
      with open(p, "rb") as f:
        raw = zstandard.ZstdDecompressor().stream_reader(f).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue
    while True:
      try:
        m = next(evs)
      except (StopIteration, Exception):
        break
      w = m.which()
      try:
        if w == "carControl":
          cur["lat_active"] = bool(m.carControl.latActive)
        elif w == "liveDelay":
          cur["delay"] = float(m.liveDelay.lateralDelay)
        elif w == "carStateBP":
          cur["rate_limited"] = bool(getattr(m.carStateBP, "angleRateLimited", False))
        elif w == "carState":
          cur["v"] = m.carState.vEgo
          cur["hands"] = bool(m.carState.steeringPressed)
        elif w == "controlsState":
          cs = m.controlsState
          yield (float(cs.desiredCurvature), float(cs.curvature),
                 cur.get("v", 0.0), cur.get("hands", False), cur["lat_active"],
                 cur["delay"], cur["rate_limited"])
      except Exception:
        continue


def main():
  d = sys.argv[1]
  files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")))
  by_route = collections.OrderedDict()
  for f in files:
    by_route.setdefault(os.path.basename(f).split("--")[0], []).append(f)
  for r in by_route:
    by_route[r].sort(key=seg_index)

  bins = {b: dict(n=0, rev=0, amp=0.0, worst=0.0, runs=0, in_band=0, rl=0) for b in SPEED_BINS}
  delays = []
  rl_ring = dict(rl_frames=0, rl_rev=0, clean_frames=0, clean_rev=0)
  band_ring = dict(in_frames=0, in_rev=0, out_frames=0, out_rev=0)
  worst_runs = []

  per_route = collections.OrderedDict()
  for route, paths in by_route.items():
    cfg = route_config(paths)
    before = dict(n=0, rev=0, delay_sum=0.0, delay_n=0, rl=0)
    per_route[route] = (cfg, before)
    run = []
    for des, act, v, hands, lat_active, delay, rate_limited in scan(paths):
      if not math.isnan(delay):
        delays.append(delay)
        before["delay_sum"] += delay
        before["delay_n"] += 1
      if lat_active and not hands and v >= 8.0 and GRADUAL_R[0] <= radius(des) <= GRADUAL_R[1]:
        before["n"] += 1
        before["rl"] += 1 if rate_limited else 0
      qualifies = (lat_active and not hands and v >= 8.0
                   and GRADUAL_R[0] <= radius(des) <= GRADUAL_R[1])
      if qualifies:
        run.append((des - act, v, des, rate_limited))
      else:
        if len(run) >= MIN_RUN:
          before["rev"] += _score(run, bins, worst_runs, route, rl_ring, band_ring)
        run = []
    if len(run) >= MIN_RUN:
      before["rev"] += _score(run, bins, worst_runs, route, rl_ring, band_ring)

  say("=== PER ROUTE, WITH THE CONFIGURATION IT RAN === ")
  say("  he changed the angle params AND the model partway through this trip, so a pooled")
  say("  number across all of it would average different cars. This is the split.")
  say()
  say("  %-9s %8s %8s %7s %7s %6s %6s %7s  %s" % (
    "route", "frames", "revs/s", "low", "high", "damp", "RL%", "delay", "model"))
  for route, (cfg, x) in per_route.items():
    if not x["n"]:
      continue
    secs = x["n"] / 100.0
    say("  %-9s %8d %8.2f %7s %7s %6s %5.0f%% %7.3f  %s" % (
      route, x["n"], x["rev"] / max(secs, 1e-9),
      cfg.get("FordLowSpeedFactor_ang", "?"), cfg.get("FordHighSpeedFactor_ang", "?"),
      cfg.get("FordHighSpeedDampening_ang", "?"), 100.0 * x["rl"] / x["n"],
      x["delay_sum"] / max(x["delay_n"], 1), cfg.get("model", "?")))
  say()
  say("=== LATERAL RINGING ON GRADUAL TURNS (radius %.0f-%.0f m, hands OFF, latActive) ===" % GRADUAL_R)
  say()
  say("  %-12s %9s %6s %10s %13s %13s %9s" % (
    "speed", "frames", "runs", "revs/s", "mean |err|", "worst |err|", "in band"))
  for b in SPEED_BINS:
    x = bins[b]
    if not x["n"]:
      continue
    secs = x["n"] / 100.0
    say("  %3d-%-3d mph %9d %6d %10.2f %10.6f    %10.6f %7.0f%%" % (
      b[0], b[1], x["n"], x["runs"], x["rev"] / max(secs, 1e-9),
      x["amp"] / x["n"], x["worst"], 100.0 * x["in_band"] / x["n"]))
  say()
  say("  revs/s = sign changes of (desiredCurvature - curvature) per second. A controller that")
  say("  merely LAGS holds one sign; one that RINGS crosses zero over and over.")

  say()
  say("=== 1. THE GAIN BAND: does ringing concentrate inside [%.4f, %.3f] 1/m? ===" % (KAPPA_LO, KAPPA_HI))
  for name, fr, rv in (("INSIDE the band ", band_ring["in_frames"], band_ring["in_rev"]),
                       ("outside the band", band_ring["out_frames"], band_ring["out_rev"])):
    if fr:
      say("  %s  %8d frames  %7.2f revs/s" % (name, fr, rv / (fr / 100.0)))
  say("  (band = radius %.0f m to %.0f m)" % (1 / KAPPA_HI, 1 / KAPPA_LO))

  say()
  say("=== 2. HIS LATENCY READ: is liveDelay.lateralDelay railing at the %.2f s clip? ===" % DELAY_CLIP_HI)
  if delays:
    delays.sort()
    n = len(delays)
    def pct(q):
      return delays[min(int(n * q), n - 1)]
    at_rail = sum(1 for x in delays if x >= DELAY_CLIP_HI - 1e-6)
    say("  samples %d   min %.3f  p50 %.3f  p90 %.3f  p99 %.3f  max %.3f" % (
      n, delays[0], pct(0.50), pct(0.90), pct(0.99), delays[-1]))
    say("  AT OR ABOVE THE %.2f s CLIP: %d  (%.1f%%)" % (DELAY_CLIP_HI, at_rail, 100.0 * at_rail / n))
    say("  declared steerActuatorDelay = %.2f s" % DECLARED_ACTUATOR_DELAY)
    if pct(0.50) >= DELAY_CLIP_HI - 1e-6:
      say("  >>> the clip is BINDING on the median frame: the lookahead is truncated below the")
      say("  >>> delay the car is learning, and the controller is permanently behind.")
  else:
    say("  no liveDelay samples -- the service may not be in these logs")

  say()
  say("=== 3. THE RATE LIMITER: is ringing denser while the soft-ROC clip is biting? ===")
  for name, fr, rv in (("rate-limited ", rl_ring["rl_frames"], rl_ring["rl_rev"]),
                       ("not limited  ", rl_ring["clean_frames"], rl_ring["clean_rev"])):
    if fr:
      say("  %s  %8d frames  %7.2f revs/s" % (name, fr, rv / (fr / 100.0)))

  if worst_runs:
    say()
    say("=== WORST RUNS (for a frame-level dump) ===")
    for route, rev, amp, v, inband in sorted(worst_runs, key=lambda x: -x[1])[:10]:
      say("  %s  %3d sign changes  mean |err| %.6f 1/m  %.0f mph  %.0f%% in band" % (
        route, rev, amp, v * MS_TO_MPH, 100.0 * inband))


def _score(run, bins, worst_runs, route, rl_ring, band_ring):
  errs = [e for e, _, _, _ in run]
  vs = [v for _, v, _, _ in run]
  v_mean = sum(vs) / len(vs)
  mph = v_mean * MS_TO_MPH
  b = next((b for b in SPEED_BINS if b[0] <= mph < b[1]), None)
  if b is None:
    return 0

  rev = 0
  prev = None
  for e in errs:
    if abs(e) < MIN_ERR:
      continue
    if prev is not None and (e > 0) != (prev > 0):
      rev += 1
    prev = e

  in_band = sum(1 for _, _, des, _ in run if KAPPA_LO <= abs(des) <= KAPPA_HI)

  # per-frame attribution: count a sign change against the condition holding at that frame
  prev = None
  for e, _, des, rl in run:
    if abs(e) < MIN_ERR:
      continue
    flip = prev is not None and (e > 0) != (prev > 0)
    prev = e
    if KAPPA_LO <= abs(des) <= KAPPA_HI:
      band_ring["in_frames"] += 1
      band_ring["in_rev"] += flip
    else:
      band_ring["out_frames"] += 1
      band_ring["out_rev"] += flip
    if rl:
      rl_ring["rl_frames"] += 1
      rl_ring["rl_rev"] += flip
    else:
      rl_ring["clean_frames"] += 1
      rl_ring["clean_rev"] += flip

  x = bins[b]
  x["n"] += len(errs)
  x["rev"] += rev
  x["amp"] += sum(abs(e) for e in errs)
  x["worst"] = max(x["worst"], max(abs(e) for e in errs))
  x["runs"] += 1
  x["in_band"] += in_band
  if rev >= 8:
    worst_runs.append((route, rev, sum(abs(e) for e in errs) / len(errs), v_mean, in_band / len(run)))
  return rev


if __name__ == "__main__":
  main()
