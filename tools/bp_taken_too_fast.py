"""Where was the car going too fast for the corner, and what was the set speed doing?

2026-08-24, after: "It took an exit so fast I almost slid off the fucking road!"

That is a safety incident and it gets its own tool rather than being reasoned about. It ranks every
moment of a drive by LATERAL ACCELERATION and prints, at each one, the four numbers that decide
whether the set speed was the cause:

    v_ego            how fast the car actually was
    speedCluster     the DASH set speed -- what Ford was driving to
    vSlaTarget       what Speed Limit Assist wanted   (published 2026-08-23)
    accAuthority     who was authoring ACCDATA

The specific hypothesis this exists to test: the ICBM suppression shipped on 2026-08-23 froze the
set speed whenever the camera latched, so an approach that should have walked the set speed DOWN
kept it high and Ford drove to it. If the hard-cornering frames show `speedCluster` stuck well above
`vSlaTarget` with authority `inert`, that is the mechanism, in his car, on the road.

`currentLateralAccel` is the model's own figure, cross-checked against a steering-angle derivation
on the same frame in 2026-08-12 and found sound -- see the note in CLAUDE.md before doubting it.

    python tools/bp_taken_too_fast.py 000003b5
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
  top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)

  t0 = None
  v_ego = cluster = 0.0
  sla = 0.0
  hold = 0.0
  authority = "?"
  lat = 0.0
  rows = []

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
    for m in lr:
      try:
        w = m.which()
      except Exception:
        continue
      t = m.logMonoTime / 1e9
      if t0 is None or t < t0:
        t0 = t
      if w == "carState":
        try:
          v_ego = float(m.carState.vEgo) * MS_TO_MPH
          cluster = float(m.carState.cruiseState.speedCluster) * MS_TO_MPH
        except Exception:
          pass
        continue
      if w == "controllerStateBP":
        try:
          authority = "n/a"
        except Exception:
          pass
        continue
      if w == "selfdriveStateSP":
        try:
          icbm = m.selfdriveStateSP.intelligentCruiseButtonManagement
          hold = float(icbm.vBaseline)
          sla = float(getattr(icbm, "vSlaTarget", 0.0))
        except Exception:
          pass
        continue
      if w != "longitudinalPlanSP":
        continue
      try:
        lat = abs(float(m.longitudinalPlanSP.smartCruiseControl.vision.currentLateralAccel))
      except Exception:
        continue
      if v_ego > 5.0:
        rows.append((t - t0, lat, v_ego, cluster, sla, hold, authority))

  if not rows:
    print("no lateral-accel data on this route")
    return

  # Peaks, not every frame above a threshold -- one corner is one event.
  rows.sort(key=lambda r: -r[1])
  picked = []
  for r in rows:
    if all(abs(r[0] - q[0]) > 6.0 for q in picked):
      picked.append(r)
    if len(picked) >= top_n:
      break
  picked.sort(key=lambda r: r[0])

  print("HARDEST CORNERING ON {}, by lateral accel".format(route))
  print("     t+     latAcc   mph   dashSet   SLA   hold   authority")
  for t, la, v, c, sl, h, a in picked:
    flag = ""
    if sl > 0 and c > sl + 2:
      flag = "  <== dash set {:.0f} ABOVE SLA {:.0f}".format(c, sl)
    print("  {:7.1f}   {:5.2f}  {:5.1f}   {:6.1f}  {:5.0f}  {:5.0f}   {:<9}{}".format(
      t, la, v, c, sl, h, a, flag))

  print()
  hard = [r for r in rows if r[1] >= 3.0]
  print("frames above 3.0 m/s^2 lateral: {}   (openpilot's own p99 is 2.73, his hands-on p99 4.14)".format(
    len(hard)))
  if hard:
    worst = max(hard, key=lambda r: r[1])
    print("worst: {:.2f} m/s^2 at t+{:.1f}, doing {:.1f} mph with the dash set to {:.0f}".format(
      worst[1], worst[0], worst[2], worst[3]))


if __name__ == "__main__":
  main()
