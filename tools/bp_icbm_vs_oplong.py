"""Was ICBM still pressing buttons while openpilot was authoring the ACC command?

His question, 2026-08-23: "Technically, when OP long is being used, ICBM doesn't, right? Or at least
when OP long is driving, we shouldn't affect its speed with ICBM?"

The gate that decides whether ICBM runs at all is `_op_long_drives()` in
`sunnypilot/selfdrive/car/interfaces.py`, and it is evaluated ONCE, at car init. Under the
passthrough it deliberately answers "Ford drives, so ICBM stays" -- which is right while Ford is
authoring. It has no way to notice that the passthrough later went `inert` and openpilot has been
authoring every frame since.

ICBM still emitting button presses, and did the dash set speed keep moving?

Prints per authority state: frames, how many carried an ICBM button, and how far the dash set speed
travelled. `speedCluster` is the DASH number the buttons move; `vCruiseCluster` is openpilot's own
MAX and is a different quantity -- see the note in CLAUDE.md about reading the wrong one.

    python tools/bp_icbm_vs_oplong.py 000003ae
"""
import os
import sys
from collections import defaultdict

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

  authority = "?"
  cluster = None
  frames = defaultdict(int)
  pressed = defaultdict(int)
  travel = defaultdict(float)
  buttons = defaultdict(lambda: defaultdict(int))

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
    # RESET AT EVERY SEGMENT. Carrying `cluster` across a boundary counts the discontinuity as real
    # dash movement, and the error only ever INFLATES -- which is the direction that supports the
    # conclusion this tool was written to reach.
    cluster = None
    for m in lr:
      try:
        w = m.which()
      except Exception:
        continue
      if w == "controllerStateBP":
        try:
          authority = "n/a"
        except Exception:
          pass
        continue
      if w == "carState":
        try:
          c = float(m.carState.cruiseState.speedCluster) * MS_TO_MPH
        except Exception:
          continue
        if cluster is not None and c > 0 and cluster > 0:
          travel[authority] += abs(c - cluster)
        cluster = c
        continue
      if w != "selfdriveStateSP":
        continue
      try:
        icbm = m.selfdriveStateSP.intelligentCruiseButtonManagement
        btn = str(icbm.sendButton).split(".")[-1]
      except Exception:
        continue
      frames[authority] += 1
      buttons[authority][btn] += 1
      if btn != "none":
        pressed[authority] += 1

  if not frames:
    print("no ICBM frames on this route")
    return

  print("authority     frames   ICBM pressing   dash travel (mph)   buttons")
  for a in sorted(frames, key=lambda k: -frames[k]):
    n = frames[a]
    b = ", ".join("{}={}".format(k, v) for k, v in sorted(buttons[a].items()) if k != "none")
    print("  {:<11} {:>6}   {:>6} {:>5.1f}%   {:>15.1f}   {}".format(
      a, n, pressed[a], 100.0 * pressed[a] / n, travel.get(a, 0.0), b or "-"))

  bad = pressed.get("inert", 0) + pressed.get("openpilot", 0)
  print()
  if bad:
    print("ICBM emitted {} button frames while OPENPILOT was authoring the ACC command.".format(bad))
    print("The set speed governs nothing in that state, so those presses moved a number that was")
    print("driving nothing -- until the authority flapped back, at which point Ford acted on")
    print("wherever ICBM had left it.")
  else:
    print("ICBM never pressed while openpilot was authoring.")


if __name__ == "__main__":
  main()
