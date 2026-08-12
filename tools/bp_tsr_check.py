#!/usr/bin/env python3
"""FusionPilot: what the IPMA camera actually reports for speed limit signs.

Run this before and after any FORScan change to the camera, so "did that help" is a measurement
instead of an impression.

2026-08-11: "CAMERA + APIM" CLEARED IT, AND THE WRITE DID NOT PERSIST.

The sequence, and the order matters because two wrong conclusions were recorded before it was
complete:

  1. His IPMA was ALREADY "TSR data source: Camera Only" -- the state this file spent two days
     arguing he should move TO -- and it reported NoNavDataAvailable.
  2. He set it to "Camera + APIM". NoNavDataAvailable cleared immediately.
  3. After an ignition cycle the message came back AND THE SETTING HAD REVERTED to Camera Only.

So Camera + APIM was never really under test. While it was actually applied the message cleared,
which is evidence the setting DOES do something; it reverted because the write did not stick.

THE PROBLEM IS THEREFORE THE WRITE, NOT THE VALUE. An as-built change that reverts across an ignition
cycle was not committed to the module. That is FORScan procedure, outside anything measurable from
here, and the usual causes are writing without the full block, a rejected checksum, or insufficient
voltage during the write.

DO NOT record a FORScan result until after an ignition cycle, and check the SETTING as well as the
symptom. Two conclusions were committed here from partial observations of this one change -- "the fix
was Camera + APIM", then "neither value matters" -- and both were wrong for the same reason: reported
before the state had settled.

What is still ruled out: the camera reports NoNavDataAvailable (3), never CountryNotSupported (5) or
RegionNotSupported (6), so it believes its region setup is fine.

STATE AS OF 2026-08-09, region UNSPECIFIED, no FORScan TSR change made:

  - Traffic_RecognitnData (0x3CD) IS on the bus: 366 frames on bus 2, forwarded to bus 0. The
    routing works, including on classic CAN -- see the registration in ford/carstate.py, whose
    second clause (`or not CANFD`) is what makes a non-CAN-FD Fusion register the message at all.
  - vLimit1 reads 255 ("NoLimit", the sentinel) for most of a drive and a real value for roughly
    10% of it, so the camera does detect signs, intermittently.
  - tsrMsg is pinned at 3 = NoNavDataAvailable across every frame of every drive. The camera is in
    fusion mode, waiting on navigation data.

WHY THE NAV DATA NEVER ARRIVES: he has SYNC 3 with Ford navigation but always navigates with Waze
in Android Auto. The APIM only supplies nav speed limits while ITS OWN route guidance is running,
and Android Auto does not write to the APIM's nav database. So the second opinion the camera wants
cannot arrive as the car is used.

TESTED 2026-08-11 AND DEAD: USING FORD'S OWN NAVIGATION DOES NOT HELP. He ran a route in SYNC 3's
navigation -- the exact condition the theory below says should supply the missing nav data -- and
tsrMsg still read NoNavDataAvailable. So "stop using Waze and use Ford nav" is not a workaround, and
nobody should suggest it again.

AND THE FUSION PATH CANNOT BE DEBUGGED FROM HERE. The only navigation messages in the Ford DBC are
APIMGPS_Data_Nav_1/2/3_FD1 (0x462-0x464), and all three are pure GPS telemetry -- latitude,
longitude, UTC, satellites, heading, speed. No speed limit, no route guidance. Whatever the camera
wants travels on a bus openpilot does not tap, which fits this car having no MS-CAN access. We cannot
observe the APIM sending it, the camera receiving it, or the camera rejecting it.

Which makes CAMERA-ONLY MODE the only remaining route to TSR on this car, rather than one of two
options. See below.

TWO CANDIDATE CAUSES, needing opposite fixes, which is why neither has been acted on:

  1. TSR MODE. The IPMA supports camera-only (SLIF) as well as fusion. In camera-only it should
     stop waiting for nav. IPMA 706-01-01, per Maverick/Focus/F-150 threads -- byte positions are
     NOT documented for an Edge MK2 IPMA, and the one forum thread asking got no answers.
  2. REGION. Region also tells the camera which sign FORMAT to match, US rectangular versus
     European circular. Unspecified may mean it is matching the wrong sign set, which would also
     produce intermittent detection.

Both can be true at once. Change ONE field at a time, or a DTC tells you nothing about which.

A SAME-SPEC DONOR NOW EXISTS, which is what was missing. As of 2026-08-11 the owner is in contact
with someone running a Ford Fusion Sport whose IPMA reports strategy KT4T-14F397-AE -- character for
character the fwdCamera entry in our fingerprint, so the same camera software -- and who HAS sign
recognition working. That is the car the note below says does not exist.

So the byte positions no longer have to be guessed. Get his full 706-01-01 as-built block and diff it
against this car's; the differing bytes are the answer. Ask 706-01-02 and -03 too while he is in
there, and ask whether he uses Ford nav or Android Auto -- if he is on Android Auto and TSR still
works, he is in camera-only mode and the diff will show exactly which byte says so.

AND NOTE: setting the region in FORScan previously produced a lot of DTCs (U2100 / U2101 class,
"configuration not complete" / "incompatible"). The usual remedy is diffing against a same-spec
donor car, and this car has no same-spec donor -- a Fusion with Edge ADAS parts does not exist. So
region changes are unusually likely to misfire here.

USAGE, on the device:

    cd /data/openpilot && python tools/bp_tsr_check.py
"""
import os
from collections import Counter

from openpilot.tools.lib.logreader import LogReader

REALDATA = "/data/media/0/realdata"
NO_SIGN = 255
MS_TO_MPH = 2.23694


def segs_for(route, limit):
  return [d for d in sorted(os.listdir(REALDATA)) if d.startswith(route + "--")][:limit]


def rlog(seg):
  for name in ("rlog", "rlog.zst", "rlog.bz2"):
    p = os.path.join(REALDATA, seg, name)
    if os.path.exists(p):
      return p
  return None


def scan(route, seg_limit, timeline=False):
  seen, changes, v_ego, t0, last = Counter(), [], 0.0, None, None
  units = Counter()
  for seg in segs_for(route, seg_limit):
    path = rlog(seg)
    if path is None:
      continue
    for msg in LogReader(path):
      w = msg.which()
      t = msg.logMonoTime / 1e9
      if t0 is None:
        t0 = t
      try:
        if w == "carState":
          v_ego = msg.carState.vEgo * MS_TO_MPH
        elif w == "carStateBP":
          tsd = msg.carStateBP.trafficSignData
          v = int(tsd.vLimit1)
          units[int(tsd.vLimitUnit)] += 1
          seen[v] += 1
          if timeline and v != last:
            changes.append((t - t0, last, v, v_ego))
            last = v
      except Exception:  # noqa: BLE001
        continue
  return seen, changes, units


routes = sorted({d.rsplit("--", 1)[0] for d in os.listdir(REALDATA) if "--" in d})
recent = routes[-6:]

print("=== 1. across the last few drives: what the camera has reported ===")
print(f"  {'route':26s} {'frames':>8s}   limits seen (255 = no sign)")
for r in recent:
  seen, _, _ = scan(r, 4)
  total = sum(seen.values())
  if not total:
    print(f"  {r:26s} {'0':>8s}   (no carStateBP in this route)")
    continue
  real = {k: n for k, n in seen.items() if k != NO_SIGN}
  desc = ", ".join(f"{k} ({100.0 * n / total:.0f}%)" for k, n in sorted(real.items(), key=lambda x: -x[1])) or "none"
  print(f"  {r:26s} {total:>8d}   {desc}")

newest = routes[-1]
print(f"\n=== 2. the most recent drive: {newest} ===")
seen, changes, units = scan(newest, 12, timeline=True)
if not changes:
  print("  no carStateBP frames -- nothing to show")
else:
  print(f"  unit enumerant seen: {dict(units)}  (raw; nothing here establishes what it means)\n")
  print(f"  {'time':>9s}  {'from':>5s} -> {'to':>5s}   {'mph at change':>13s}")
  for ts, a, b, v in changes:
    a_s = "-" if a is None else ("none" if a == NO_SIGN else str(a))
    b_s = "none" if b == NO_SIGN else str(b)
    mark = "   <- READ A SIGN" if b != NO_SIGN else ""
    print(f"  t+{ts:7.1f}  {a_s:>5s} -> {b_s:>5s}   {v:13.0f}{mark}")
