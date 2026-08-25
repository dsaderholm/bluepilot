"""Is Available_FusionMode a real state, or the camera's power-on default?

Route 000003b9 is the only drive ever recorded in FusionMode with NoInformationAllOK -- and it did
it WITHOUT the synthesized GPS on the wire, which is the opposite of the hypothesis. It is also a
one-segment route with 10 TSR frames, i.e. about two seconds of TSR at the very start of an
ignition cycle.

So the question is whether every drive starts in FusionMode and degrades once the camera notices
nav data is missing. Print the FIRST N TSR frames of each route, in order, with the time since the
route's first message.

    python tools/bp_tsr_startup.py 000003b9 000003bb 000003bd
"""
import os
import sys

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402

TSR = 973
STAT = {0: "Null", 1: "TSR_Off", 2: "Available_FusionMode", 3: "Available_CameraOnly",
        4: "Available_NavigationOnly", 5: "TSR_Error", 6: "NoDataExists", 7: "NotUsed"}
MSG = {0: "Null", 1: "NoInformationAllOK", 2: "NoNavAvailableSwitchedOff", 3: "NoNavDataAvailable",
       4: "WrngNavDatIncompDatCarrier", 5: "CountryNotSupported", 6: "RegionNotSupported",
       7: "OffRoad", 8: "LimitedSystemPerformance", 9: "RecgnzdSignNotUsblForDsply"}

N = 25


def be(data, start, nbits):
  v = int.from_bytes(data, "big")
  idx = (start // 8) * 8 + (7 - (start % 8))
  return (v >> (len(data) * 8 - idx - nbits)) & ((1 << nbits) - 1)


def run(route):
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)],
                key=lambda n: int(n.rsplit("--", 1)[1]))
  if not segs:
    print(f"{route}: no segments"); return

  print(f"\n=== {route} === first {N} TSR frames")
  t0 = None
  shown = 0
  last = None
  for s in segs[:2]:
    p = os.path.join(REALDATA, s, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    for m in LogReader(p):
      t = m.logMonoTime / 1e9
      t0 = t if t0 is None else min(t0, t)
      if m.which() != "can":
        continue
      for c in m.can:
        if c.address == TSR and c.src == 2 and len(c.dat) == 8:
          st, ms = be(c.dat, 10, 3), be(c.dat, 7, 4)
          if shown < N:
            print(f"  t+{t - t0:6.2f}  {STAT.get(st, st):<26} {MSG.get(ms, ms)}")
            shown += 1
          elif (st, ms) != last:
            print(f"  t+{t - t0:6.2f}  CHANGED -> {STAT.get(st, st):<20} {MSG.get(ms, ms)}")
          last = (st, ms)
    if shown >= N and last is not None:
      break


for r in sys.argv[1:]:
  try:
    run(r)
  except Exception as ex:
    print(f"{r}: FAILED {type(ex).__name__}: {ex}")
