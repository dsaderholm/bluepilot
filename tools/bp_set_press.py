"""FusionPilot: what happens when he presses SET while cruise is OFF?

Reported 2026-08-25: cruise disengaged while accelerating to highway speed, pressed SET, and it
KEPT the ~22 mph hold from the surface street instead of handing the speed to SLA, which had 75.

The contract says SET-while-off clears the hold and SLA takes the speed. Two candidate mechanisms
and this tool separates them, because they need different fixes:

  A. the `setCruise` BUTTON EVENT never arrives, so the decision falls through to the behavioural
     detector -- there is a `resume_press_frames` short-circuit but no SET equivalent
  B. the event arrives, but `looks_like_set` compares the DASH against vEgo and the dash had not
     landed on the new value yet when the decision fired

Prints every cruise engage edge with the button events and both speeds around it.

    python tools/bp_set_press.py 000003be
"""
import os
import sys

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402

MPH = 2.23694
WINDOW = 6.0     # seconds either side of an engage edge


def seg_index(n):
  try:
    return int(n.rsplit("--", 1)[1])
  except Exception:
    return -1


def run(route):
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
  if not segs:
    print(f"{route}: no segments"); return

  rows = []          # (t, enabled, dash_mph, vego_mph, buttons, baseline, src, sla, live, maxsp)
  t0 = None
  enabled = False
  dash = vego = 0.0
  baseline = sla = maxsp = 0.0
  src = "?"
  live = False
  pending_btns = []

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
      t = m.logMonoTime / 1e9
      t0 = t if t0 is None else min(t0, t)
      w = m.which()

      if w == "carState":
        cs = m.carState
        was, enabled = enabled, cs.cruiseState.enabled
        dash = cs.cruiseState.speedCluster * MPH
        vego = cs.vEgo * MPH
        maxsp = cs.vCruiseCluster / 1.609344      # kph field -> mph
        for b in cs.buttonEvents:
          if b.pressed:
            pending_btns.append(str(b.type).split(".")[-1])
        rows.append((t, enabled, dash, vego, list(pending_btns), baseline, src, sla, live, maxsp,
                     was != enabled))
        pending_btns = []
      elif w == "selfdriveStateSP":
        try:
          icbm = m.selfdriveStateSP.intelligentCruiseButtonManagement
          baseline = icbm.vBaseline
          src = str(icbm.baselineSource).split(".")[-1]
          sla = getattr(icbm, "vSlaTarget", 0)
          live = bool(getattr(icbm, "speedLimitLive", False))
        except Exception:
          pass

  edges = [i for i, r in enumerate(rows) if r[10] and r[1]]
  print(f"\n=== {route} ===  {len(segs)} segs, {len(rows)} carState frames, "
        f"{len(edges)} cruise ENGAGE edges")

  for ei in edges:
    te = rows[ei][0]
    print(f"\n--- engage at t+{te - t0:.1f} s ---")
    print(f"      {'t':>8}  {'en':<3} {'dash':>6} {'vEgo':>6} {'MAX':>6} "
          f"{'hold':>6} {'src':<12} {'sla':>5} {'live':<5} buttons")
    for r in rows:
      if abs(r[0] - te) > WINDOW:
        continue
      if r[4] or r[10] or abs(r[0] - te) < 0.3:
        print(f"      {r[0] - t0:8.2f}  {'ON ' if r[1] else 'off':<3} {r[2]:6.1f} {r[3]:6.1f} "
              f"{r[9]:6.1f} {r[5]:6.1f} {r[6]:<12} {r[7]:5.1f} {str(r[8]):<5} "
              f"{','.join(r[4]) if r[4] else ''}")


for r in sys.argv[1:]:
  try:
    run(r)
  except Exception as ex:
    print(f"{r}: FAILED {type(ex).__name__}: {ex}")
