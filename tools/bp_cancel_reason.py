"""WHY does the camera cancel? Ask it -- ACCDATA_3 carries its own message text.

CLAUDE.md says the only thing that would move the passthrough cancel problem is "finding what the
camera actually counts -- consecutive frames, a response-to-command test, something in ACCDATA_3 --
not another band". This is the ACCDATA_3 half, and the DBC turns out to answer it directly:

    VAL_ 394 AccMsgTxt_D2_Rq   4 "ACC_Overridden"   2 "ACC_Cancelled"   1 "ACC_Unavailable"
                               3 "Brake_Capacity_Warning"   10 "Press_Brake_To_Hold"  ...
    VAL_ 394 AccWarn_D_Dsply   1 "Cancel_Warning"   2 "Brake_Capacity_Warning"
                               3 "BrakeReleaseWarn_In_StopMd"
    VAL_ 394 AccStopStat_D_Dsply  1 "ResumeReady"   2 "Stopped"   3 "PressResume"

`ACC_Overridden` is what a camera watching the car decelerate harder than it asked would be
expected to display, and the stop override creates exactly that state. If it appears in the frames
before `AccCancl_B_Rq` goes high, the camera has told us what it thinks is happening -- and
"overridden" is a very different problem from "faulted", because a system that believes the DRIVER
is braking is waiting to be handed back rather than refusing to run.

`AccStopStat_D_Dsply` matters for the other half of the feature: it says whether Ford's own ACC
believes it is in a stop-and-hold, which is the state nobody has ever confirmed on this car.

Decoded from raw CAN rather than carState, because only `AccTGap_D_Dsply` is published off this
message today. Bus 2 is the camera side.

    python tools/bp_cancel_reason.py 000003ae
"""
import os
import sys

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402

ACCDATA = 390
ACCDATA_3 = 394

MSG_TXT = {0: "No_Text", 1: "ACC_Unavailable", 2: "ACC_Cancelled", 3: "Brake_Capacity_Warning",
           4: "ACC_Overridden", 5: "ACC_Selected", 6: "IACC_Unavailable", 7: "Shift_Down",
           8: "TJA_Unavailable", 9: "Only_Following_In_Low_Spd", 10: "Press_Brake_To_Hold",
           11: "IACC_Selected", 12: "ACC_TJA_Selected", 13: "IACC_TJA_Selected",
           14: "NCC_Enabled_Warning", 15: "NotUsed_1"}
WARN = {0: "No_Warning", 1: "Cancel_Warning", 2: "Brake_Capacity_Warning",
        3: "BrakeReleaseWarn_In_StopMd"}
STOP_STAT = {0: "NoDisplay", 1: "ResumeReady", 2: "Stopped", 3: "PressResume"}


def be(data: bytes, start: int, nbits: int) -> int:
  """Motorola/big-endian signal extraction, the same arithmetic the smoke test uses."""
  v = int.from_bytes(data, "big")
  total = len(data) * 8
  idx = (start // 8) * 8 + (7 - (start % 8))
  return (v >> (total - idx - nbits)) & ((1 << nbits) - 1)


def seg_index(name):
  try:
    return int(name.rsplit("--", 1)[1])
  except Exception:
    return -1


def main():
  route = sys.argv[1]
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)

  t0 = None
  rows = []          # (t, cancel, msg, warn, stopstat, accel)
  cancel = 0
  accel = 0.0
  msg = warn = stat = 0
  prev_cancel = 0
  authority = "?"
  edges = []

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
      # AUTHORITY IN THE SAME PASS, SAME TIME BASE. Comparing this tool's t+ against another's is
      # how two tools reported the same window 6.5 s apart earlier today.
      if w == "controllerStateBP":
        try:
          authority = str(m.controllerStateBP.accAuthority).split(".")[-1]
        except Exception:
          pass
        continue
      if w != "can":
        continue
      if t0 is None or t < t0:
        t0 = t
      for c in m.can:
        # Bus 2 is the camera. Its own transmissions, not our echo.
        if c.src != 2:
          continue
        if c.address == ACCDATA:
          d = bytes(c.dat)
          cancel = be(d, 39, 1)
          accel = be(d, 4, 13) * 0.0039 - 20.0
          if cancel and not prev_cancel:
            edges.append(t)
          prev_cancel = cancel
        elif c.address == ACCDATA_3:
          d = bytes(c.dat)
          msg = be(d, 31, 4)
          warn = be(d, 39, 2)
          stat = be(d, 41, 2)
      if not rows or t - rows[-1][0] >= 0.20:
        rows.append((t, cancel, msg, warn, stat, accel, authority))

  if not rows:
    print("no camera CAN on this route -- bus 2 not logged?")
    return

  rel = lambda x: x - t0  # noqa: E731

  # WHAT IT SAID WHILE WE HAD THE CAR. This is the question -- a camera that displays
  # ACC_Overridden during the override is telling us it thinks the driver is braking, which is a
  # handback problem; one that says ACC_Unavailable thinks it cannot run at all.
  during = [r for r in rows if r[6] == "opStop"]
  if during:
    from collections import Counter as _C
    print("WHILE THE OVERRIDE HAD THE CAR ({} samples):".format(len(during)))
    print("  AccMsgTxt_D2_Rq   {}".format(", ".join(
      "{}={}".format(MSG_TXT.get(k, k), v) for k, v in _C(r[2] for r in during).most_common())))
    print("  AccWarn_D_Dsply   {}".format(", ".join(
      "{}={}".format(WARN.get(k, k), v) for k, v in _C(r[3] for r in during).most_common())))
    print("  AccStopStat       {}".format(", ".join(
      "{}={}".format(STOP_STAT.get(k, k), v) for k, v in _C(r[4] for r in during).most_common())))
    print()
  else:
    print("the override never had the car on this route")
    print()

  # And where the non-default messages actually fall, by authority.
  from collections import Counter as _C2
  interesting = [r for r in rows if r[2] not in (0,) or r[3] != 0]
  if interesting:
    print("frames where the camera said ANYTHING, by authority:")
    for auth, n in _C2(r[6] for r in interesting).most_common():
      sub = [r for r in interesting if r[6] == auth]
      msgs = ", ".join("{}={}".format(MSG_TXT.get(k, k), v)
                       for k, v in _C2(r[2] for r in sub).most_common()[:3])
      warns = ", ".join("{}={}".format(WARN.get(k, k), v)
                        for k, v in _C2(r[3] for r in sub).most_common()[:2])
      print("  {:<10} {:>5}   {}   |   {}".format(auth, n, msgs, warns))
    print()

  print("what the camera DISPLAYED over the whole route:")
  from collections import Counter
  c_msg = Counter(MSG_TXT.get(r[2], r[2]) for r in rows)  # noqa
  c_warn = Counter(WARN.get(r[3], r[3]) for r in rows)
  c_stat = Counter(STOP_STAT.get(r[4], r[4]) for r in rows)
  for name, c in (("AccMsgTxt_D2_Rq", c_msg), ("AccWarn_D_Dsply", c_warn),
                  ("AccStopStat_D_Dsply", c_stat)):
    print("  {:<22} {}".format(name, ", ".join(
      "{}={}".format(k, v) for k, v in c.most_common())))
  print()

  if not edges:
    print("the camera never asserted AccCancl_B_Rq on this route")
    return

  print("{} cancel edge(s). What it was saying in the 6 s BEFORE each:".format(len(edges)))
  for e in edges[:6]:
    print("  --- cancel at t+{:.1f}".format(rel(e)))
    print("       t+      cancel  accel   AccMsgTxt              warn                 stopStat    authority")
    for r in rows:
      if not (e - 6.0 <= r[0] <= e + 1.5):
        continue
      print("    {:8.1f}  {:>6}  {:>6.2f}   {:<21} {:<20} {:<11} {}".format(
        rel(r[0]), r[1], r[5], MSG_TXT.get(r[2], r[2]), WARN.get(r[3], r[3]),
        STOP_STAT.get(r[4], r[4]), r[6]))


if __name__ == "__main__":
  main()
