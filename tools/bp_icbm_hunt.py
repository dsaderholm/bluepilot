"""Is the PLAN'S OWN target steady, or is ICBM being handed a shaking number?

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
  by_auth = {}       # authority -> jitter accumulators
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
        authority = a
        continue

      if w == "carState":
        try:
          c = float(m.carState.cruiseState.speedCluster) * MS_TO_MPH
        except Exception:
          continue
        b = by_auth.setdefault(authority, {"dash": 0.0, "tgt": 0.0, "cross": 0, "n": 0,
                                           "tmin": None, "tmax": None, "side": None, "secs": 0.0})
        if cluster is not None and c > 0 and cluster > 0:
          b["dash"] += abs(c - cluster)
        cluster = c
        continue

      if w != "selfdriveStateSP":
        continue
      try:
        icbm = m.selfdriveStateSP.intelligentCruiseButtonManagement
        # `vTargetRaw`, NOT `vTarget`. vTarget is POST-baseline, so while a hold is active it EQUALS
        # the hold by construction -- measuring its travel measures the DRIVER'S THUMB, not
        # controller noise. The capnp comment above vTargetRaw says exactly this, and the first
        # version of this tool read vTarget anyway and reported 27x "jitter" that was him pressing
        # buttons while the plan sat steady at 22.
        tg = float(icbm.vTargetRaw)
      except Exception:
        continue
      if tg > 0:
        cur = by_auth.setdefault(authority, {"dash": 0.0, "tgt": 0.0, "cross": 0, "n": 0,
                                            "tmin": None, "tmax": None, "side": None, "secs": 0.0})
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

  if not by_auth:
    print("no ICBM target data on this route")
    return

  print("TARGET JITTER BY AUTHORITY ON {}".format(route))
  print("  authority   samples   dash moved   target moved   target range   crossings   jitter ratio")
  for a in sorted(by_auth, key=lambda k: -by_auth[k]["n"]):
    b = by_auth[a]
    if not b["n"]:
      continue
    span = (b["tmax"] - b["tmin"]) if b["tmin"] is not None else 0.0
    ratio = b["tgt"] / max(span, 1.0)
    rng = "{:.0f}-{:.0f}".format(b["tmin"], b["tmax"]) if b["tmin"] is not None else "n/a"
    print("  {:<10} {:>7}   {:8.1f} mph   {:8.1f} mph   {:>12}   {:>9}   {:>7.1f}x".format(
      a, b["n"], b["dash"], b["tgt"], rng, b["cross"], ratio))

  print()
  print("JITTER RATIO is target travel divided by the band it stayed inside. A target that moves")
  print("from 30 to 25 and stops reads ~1x. One that shakes between 27 and 30 for a minute reads")
  print("in the tens -- same band, enormously more travel, and every reversal is a button press.")
  print()
  print("`ford` is the row that matters most: there the set speed IS driving the car, so a jittering")
  print("target is button wear and 'set speed changed' spam while Ford chases a number that never")
  print("settles. `inert` jitter costs only noise, because nothing is listening.")


if __name__ == "__main__":
  main()
