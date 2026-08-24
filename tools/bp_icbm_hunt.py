"""Was ICBM TRACKING a moving target, or OSCILLATING around a still one?

His report, 2026-08-23: "It keeps telling me set speed changed and the max speed is flashing fast",
and the measured shape of it on route 000003ae -- 378 button frames and 84 mph of dash travel in
one 60 s inert window, decrease=210 against increase=168.

THE TWO READINGS NEED OPPOSITE FIXES AND LOOK IDENTICAL IN A PRESS COUNT:

  TRACKING   ICBM's target is genuinely moving (a curve, a stop) and the set speed follows it. The
             presses are the price of a SAFE HANDBACK -- if Ford takes authority back it drives to
             the set speed, so the set speed should be the current plan target, not a stale aim.
             Nothing to fix but the noise.

  HUNTING    the target is essentially still and the cluster crosses it repeatedly. That is the
             documented set-speed hunt (tap = 1 mph, hold = 5 mph) and it is a real defect.

So this prints, over each `inert` window, how far ICBM's own TARGET travelled against how far the
DASH travelled, and how many times the dash crossed the target. A dash that moves far while the
target sits still, crossing it repeatedly, is hunting. Both moving together is tracking.

    python tools/bp_icbm_hunt.py 000003b5
"""
import os
import sys

MS_TO_MPH = 2.23694
REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402


def seg_index(name):
  try:
    return int(name.rsplit("--", 1)[1])
  except Exception:
    return -1


def main():
  route = sys.argv[1]
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)

  t0 = None
  authority = "?"
  cluster = None
  target = None
  windows = []       # list of dicts, one per inert run
  cur = None

  for s in segs:
    p = os.path.join(REALDATA, s, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception:
      continue
    cluster = None      # never carry the dash value across a segment boundary
    for m in lr:
      try:
        w = m.which()
      except Exception:
        continue
      t = m.logMonoTime / 1e9
      if t0 is None or t < t0:
        t0 = t

      if w == "controllerStateBP":
        try:
          a = str(m.controllerStateBP.accAuthority).split(".")[-1]
        except Exception:
          continue
        if a == "inert" and authority != "inert":
          cur = {"start": t, "dash": 0.0, "tgt": 0.0, "cross": 0, "n": 0,
                 "tmin": None, "tmax": None, "side": None}
        elif a != "inert" and authority == "inert" and cur is not None:
          cur["end"] = t
          windows.append(cur)
          cur = None
        authority = a
        continue

      if w == "carState":
        try:
          c = float(m.carState.cruiseState.speedCluster) * MS_TO_MPH
        except Exception:
          continue
        if cur is not None and cluster is not None and c > 0 and cluster > 0:
          cur["dash"] += abs(c - cluster)
        cluster = c
        continue

      if w != "selfdriveStateSP":
        continue
      try:
        icbm = m.selfdriveStateSP.intelligentCruiseButtonManagement
        tg = float(icbm.vTarget)
      except Exception:
        continue
      if cur is not None and tg > 0:
        if target is not None and target > 0:
          cur["tgt"] += abs(tg - target)
        cur["tmin"] = tg if cur["tmin"] is None else min(cur["tmin"], tg)
        cur["tmax"] = tg if cur["tmax"] is None else max(cur["tmax"], tg)
        # A CROSSING is the dash passing from one side of the target to the other. Hunting crosses
        # repeatedly; tracking approaches from one side and stays.
        if cluster is not None and cluster > 0:
          side = 1 if cluster > tg else (-1 if cluster < tg else 0)
          if side and cur["side"] and side != cur["side"]:
            cur["cross"] += 1
          if side:
            cur["side"] = side
        cur["n"] += 1
      target = tg

  if cur is not None:
    cur["end"] = cur["start"]
    windows.append(cur)

  if not windows:
    print("never went inert on this route")
    return

  rel = lambda x: x - t0  # noqa: E731
  print("ICBM DURING EACH INERT WINDOW ON {}".format(route))
  print("      window        secs   dash moved   target moved   target range   crossings")
  for w in windows:
    dur = w.get("end", w["start"]) - w["start"]
    rng = "{:.0f}-{:.0f}".format(w["tmin"], w["tmax"]) if w["tmin"] is not None else "n/a"
    print("  t+{:7.1f}      {:6.1f}   {:8.1f} mph   {:8.1f} mph   {:>12}   {:>7}".format(
      rel(w["start"]), dur, w["dash"], w["tgt"], rng, w["cross"]))

  print()
  tot_dash = sum(w["dash"] for w in windows)
  tot_tgt = sum(w["tgt"] for w in windows)
  tot_cross = sum(w["cross"] for w in windows)
  print("totals: dash {:.0f} mph, target {:.0f} mph, {} crossings".format(tot_dash, tot_tgt, tot_cross))
  if tot_tgt > 0 and tot_dash / max(tot_tgt, 1e-6) < 1.5 and tot_cross < 10:
    print("=> TRACKING. The dash moved about as far as the target did and rarely crossed it.")
    print("   The presses are the price of a safe handback, not a defect. Fix the NOISE, not the")
    print("   tracking -- taking the set speed off the plan target is what put him on an exit ramp.")
  else:
    print("=> HUNTING. The dash moved much further than the target, or crossed it repeatedly.")
    print("   That is the documented tap-vs-hold set-speed hunt and it is a real defect.")


if __name__ == "__main__":
  main()
