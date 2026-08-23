"""Would the cancel recovery have run, if the accel ceiling had been clamped?

Routes 000003ae and 000003af, 2026-08-23. The override fired, the camera latched, the passthrough
went inert, and he lost Ford ACC for the drive. `INERT` logged four times; `RECOVERY` logged zero.

Attribution is not the reason -- `bp_cancel_attribution.py` measures a 4.99 s gap on both drives,
i.e. the cancel run began the frame the override ended, so `cancel_is_ours` was true. `inert` is
also unreachable unless `CC.longActive`, so that gate passed too.

WHAT IS LEFT IS THE BODY, NOT THE GATE. The recovery only acts if

    passthrough_admissible(CS.acc_stock_values, CC.longActive, allow_cancel=True)

comes back clean -- and until 2026-08-23 that refused `AccBrkTot_A_Rq` above 1.9949, which is
exactly where Ford sits pulling away from a stop. The recovery would have been silently declining
every frame for the whole inert window, with no log line of its own to say so.

This tests that against the real numbers. Both band fields are logged --
`carStateBP.brakeLightStatus.accAccelRequest` is `AccBrkTot_A_Rq` and `accPropulsionRequest` is
`AccPrpl_A_Rq` -- so for every frame of the inert window it asks: under the OLD rule was the frame
admissible, and under the NEW one?

Caveat stated rather than buried: the unpoliced bits (park brake, stop status, brake pulse, auto
resume) and `CmbbDeny_B_Actl` are not logged, so this can only prove the BAND was blocking. If the
band was clean the whole window, something else was, and that needs the wire.

    python tools/bp_recovery_blocked.py 000003ae
"""
import os
import sys

_PANDA_ACCEL_MIN = -3.4991
_PANDA_ACCEL_MAX = 1.9999
_PANDA_GAS_MIN = -0.5
_PANDA_GAS_MAX = 2.0
_PANDA_GAS_INACTIVE = -5.0
_PANDA_MARGIN = 0.005

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402


def seg_index(name):
  try:
    return int(name.rsplit("--", 1)[1])
  except Exception:
    return -1


def old_refusal(accel, gas):
  """The rule as it stood while these drives were recorded."""
  if not (_PANDA_ACCEL_MIN + _PANDA_MARGIN) <= accel <= (_PANDA_ACCEL_MAX - _PANDA_MARGIN):
    return "AccBrkTot_A_Rq %.3f outside band" % accel
  if abs(gas - _PANDA_GAS_INACTIVE) >= 0.005 and gas < (_PANDA_GAS_MIN + _PANDA_MARGIN):
    return "AccPrpl_A_Rq %.3f below band" % gas
  return ""


def new_refusal(accel, gas):
  """With the ceiling clamped instead of refused, 2026-08-23."""
  if accel < (_PANDA_ACCEL_MIN + _PANDA_MARGIN):
    return "AccBrkTot_A_Rq %.3f below band" % accel
  if abs(gas - _PANDA_GAS_INACTIVE) >= 0.005 and gas < (_PANDA_GAS_MIN + _PANDA_MARGIN):
    return "AccPrpl_A_Rq %.3f below band" % gas
  return ""


def main():
  route = sys.argv[1]
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)

  t0 = None
  authority = None
  windows = []       # (start, end) of inert
  cur_start = None
  samples = []       # (t, accel, gas)

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
      if w == "controllerStateBP":
        try:
          a = str(m.controllerStateBP.accAuthority).split(".")[-1]
        except Exception:
          continue
        if a == "inert" and authority != "inert":
          cur_start = t
        elif a != "inert" and authority == "inert" and cur_start is not None:
          windows.append((cur_start, t))
          cur_start = None
        authority = a
        continue
      if w == "carStateBP":
        try:
          b = m.carStateBP.brakeLightStatus
          if not b.accDataAvailable:
            continue
          samples.append((t, float(b.accAccelRequest), float(b.accPropulsionRequest)))
        except Exception:
          continue
  if cur_start is not None:
    windows.append((cur_start, samples[-1][0] if samples else cur_start))

  if not windows:
    print("never went inert on this route")
    return

  rel = lambda x: x - t0  # noqa: E731
  for ws, we in windows:
    inside = [(t, a, g) for t, a, g in samples if ws <= t <= we]
    if not inside:
      print("inert t+{:.1f}..{:.1f}: no camera ACCDATA logged in the window".format(rel(ws), rel(we)))
      continue
    old_blocked = [x for x in inside if old_refusal(x[1], x[2])]
    new_blocked = [x for x in inside if new_refusal(x[1], x[2])]
    print("inert t+{:.1f}..{:.1f}  {:.1f} s, {} camera frames".format(
      rel(ws), rel(we), we - ws, len(inside)))
    print("   OLD rule refused  {:>6} / {:<6} {:5.1f}%   <- recovery could not act on these".format(
      len(old_blocked), len(inside), 100.0 * len(old_blocked) / len(inside)))
    print("   NEW rule refuses  {:>6} / {:<6} {:5.1f}%".format(
      len(new_blocked), len(inside), 100.0 * len(new_blocked) / len(inside)))
    ceiling = [x for x in inside if x[1] > _PANDA_ACCEL_MAX - _PANDA_MARGIN]
    if ceiling:
      hi = max(x[1] for x in ceiling)
      print("   of those, {} were the ACCEL CEILING, worst {:.3f} m/s^2".format(len(ceiling), hi))
    clean_new = len(inside) - len(new_blocked)
    print("   => with the clamp, {} frames would have been forwardable with cancel cleared".format(clean_new))


if __name__ == "__main__":
  main()
