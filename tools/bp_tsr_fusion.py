"""FusionPilot: did synthesizing the APIM GPS move TSR out of camera-only mode?

Ford's TSR is a FUSION system. This car has run `Available_CameraOnly` on every drive ever
measured, because the IPMA is a listed receiver of three APIM GPS messages and only one arrives:

    0x462  APIMGPS_Data_Nav_1   lat/lon                       ~600-3500 frames a drive
    0x463  APIMGPS_Data_Nav_2   UTC date+time, PDOP, compass   ZERO
    0x464  APIMGPS_Data_Nav_3   heading, HDOP, VDOP, altitude  ZERO

`FordSynthesizeApimGps` sends the two missing ones from the comma's own GPS. It was written to 1
at 19:21 on 2026-08-24, and routes 3b9 onward all start after that -- so this is a real before /
after rather than a prediction.

TWO QUESTIONS, IN THIS ORDER, because the second is meaningless without the first:

  1. Did 0x463/0x464 actually reach the wire? A stale device build makes this a null test and it
     would read exactly like "the feature did not help".
  2. Did TsrStatMsgTxt move off Available_CameraOnly, and TsrMsgTxt off NoNavDataAvailable?

Read counts are reported too, but DO NOT compare them against the old baseline if question 2
changed: a different mode is a different system and the earlier numbers stop being comparable.

    python tools/bp_tsr_fusion.py 000003b5 000003b7 000003b9 000003bb 000003bd
"""
import os
import sys
from collections import Counter

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402

TSR = 973                       # Traffic_RecognitnData
GPS = {1122: "0x462 lat/lon", 1123: "0x463 time/PDOP", 1124: "0x464 heading/HDOP"}

STAT = {0: "Null", 1: "TSR_Off", 2: "Available_FusionMode", 3: "Available_CameraOnly",
        4: "Available_NavigationOnly", 5: "TSR_Error", 6: "NoDataExists", 7: "NotUsed"}
MSG = {0: "Null", 1: "NoInformationAllOK", 2: "NoNavAvailableSwitchedOff", 3: "NoNavDataAvailable",
       4: "WrngNavDatIncompDatCarrier", 5: "CountryNotSupported", 6: "RegionNotSupported",
       7: "OffRoad", 8: "LimitedSystemPerformance", 9: "RecgnzdSignNotUsblForDsply"}
VSTAT = {0: "Null", 1: "LimitChanged", 2: "LimitReliable", 3: "LimitOutdated"}


def be(data, start, nbits):
  v = int.from_bytes(data, "big")
  idx = (start // 8) * 8 + (7 - (start % 8))
  return (v >> (len(data) * 8 - idx - nbits)) & ((1 << nbits) - 1)


def seg_index(n):
  try:
    return int(n.rsplit("--", 1)[1])
  except Exception:
    return -1


def run(route):
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
  if not segs:
    print(f"{route}: no segments"); return

  gps_seen = Counter()          # (addr, src) -> frames
  stat = Counter()
  msg = Counter()
  vstat = Counter()
  frames = 0
  reads = []                    # (value, mph)
  prev = None
  v_ego = 0.0

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
      w = m.which()
      if w == "carState":
        v_ego = m.carState.vEgo
      elif w == "can":
        for c in m.can:
          if c.address in GPS:
            gps_seen[(c.address, c.src)] += 1
          elif c.address == TSR and c.src == 2 and len(c.dat) == 8:
            frames += 1
            stat[be(c.dat, 10, 3)] += 1
            msg[be(c.dat, 7, 4)] += 1
            vstat[be(c.dat, 21, 2)] += 1
            v = be(c.dat, 31, 8)
            live = v not in (0, 255)
            if live and prev is not True:
              reads.append((v, v_ego * 2.237))
            prev = live

  print(f"\n=== {route} ===  {len(segs)} segs, {frames} TSR frames")

  print("  APIM GPS on the wire:")
  if not gps_seen:
    print("    NOTHING -- neither the real APIM nor our synthesis")
  for (addr, src), n in sorted(gps_seen.items()):
    who = "OURS (TX echo)" if src in (128, 130) else f"src {src}"
    print(f"    {GPS[addr]:<22} {n:>6} frames   {who}")
  for addr in (1123, 1124):
    if not any(a == addr for a, _ in gps_seen):
      print(f"    {GPS[addr]:<22}      0 frames   *** STILL ABSENT ***")

  def pct(counter, names):
    tot = max(1, sum(counter.values()))
    return "  ".join(f"{names.get(k, k)} {100.0 * n / tot:.1f}%"
                     for k, n in counter.most_common(3))

  print(f"  TsrStatMsgTxt : {pct(stat, STAT)}")
  print(f"  TsrMsgTxt     : {pct(msg, MSG)}")
  print(f"  TsrVl1Stat    : {pct(vstat, VSTAT)}")
  print(f"  reads         : {len(reads)}  " +
        ("  ".join(f"{v} mph @ {s:.0f} mph" for v, s in reads) if reads else "(none)"))


for r in sys.argv[1:]:
  try:
    run(r)
  except Exception as ex:
    print(f"{r}: FAILED {type(ex).__name__}: {ex}")
