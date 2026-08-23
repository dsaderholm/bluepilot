"""Why did the hold not clear? Reconstructs the rule's own inputs from a route.

He reported it again on 2026-08-23, with a photo: HOLD 27, SPEED LIMIT 25, offset badge 2 -- so
SLA's target WAS 27 and the hold WAS 27 and it did not clear. The fix from 2026-08-22 is on the
car; it ran and did not fire, which is more informative than it being absent.

THE RULE COMPARES TWO VALUES THAT ARE NOT PUBLISHED. `v_sla_target` and `speed_limit_live` are
computed in `update_calculations` and never reach the wire, so a route cannot say which term
failed -- the exact defect the capnp comment above `vTargetRaw` describes, reintroduced when the
rule changed to compare different values. Publishing them is the real fix; this tool exists because
the drives that need explaining were recorded before that lands.

Both terms are reconstructable from `longitudinalPlanSP.speedLimit.resolver`, which IS logged:

    speed_limit_live = resolver.speedLimitValid
    v_sla_target     = round(resolver.speedLimitFinalLast * MS_TO_MPH)   when live, else 0

and the rule is:

    if speed_limit_live and v_sla_target > 0:
      if v_baseline != v_sla_target:  arm
      elif source != pinned:          CLEAR

So a hold that sits at SLA's number and does not clear fails on exactly one of: not live, target
zero, values unequal after rounding, or pinned. This prints which, per frame.

    python tools/bp_hold_clear_audit.py 000003b1
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

  live = False
  last_valid = False
  sla_target = 0
  t0 = None
  rows = []
  prev_key = None

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
      if w == "longitudinalPlanSP":
        try:
          r = m.longitudinalPlanSP.speedLimit.resolver
          live = bool(r.speedLimitValid)
          last_valid = bool(r.speedLimitLastValid)
          sla_target = round(float(r.speedLimitFinalLast) * MS_TO_MPH) if live else 0
        except Exception:
          pass
        continue
      if w != "selfdriveStateSP":
        continue
      try:
        icbm = m.selfdriveStateSP.intelligentCruiseButtonManagement
        baseline = round(float(icbm.vBaseline))
        source = str(icbm.baselineSource)
        diverged = bool(icbm.baselineDiverged)
      except Exception:
        continue
      if baseline <= 0:
        prev_key = None
        continue

      # WHY the rule did not clear this frame, in its own terms.
      if not live:
        why = "NOT LIVE" + (" (last-valid only)" if last_valid else " (no limit at all)")
      elif sla_target <= 0:
        why = "SLA TARGET 0"
      elif baseline != sla_target:
        why = "differs by {:+d} -- arming, correct".format(baseline - sla_target)
      elif source == "pinned":
        why = "PINNED -- exempt, correct"
      else:
        why = "*** SHOULD HAVE CLEARED ***"

      key = (baseline, sla_target, live, source, why)
      if key != prev_key:
        rows.append((t - t0, baseline, sla_target, live, last_valid, source, diverged, why))
        prev_key = key

  if not rows:
    print("no hold on this route")
    return

  print("     t+     hold  sla  live  lastv  source          div  why")
  for t, b, s, lv, la, src, dv, why in rows:
    print("  {:7.1f}  {:>4} {:>4}  {:>5} {:>6}  {:<14} {:>4}  {}".format(
      t, b, s, str(lv), str(la), src.split(".")[-1][:14], str(dv), why))

  bad = [r for r in rows if "SHOULD HAVE" in r[7]]
  print()
  print("{} state change(s); {} where the rule should have cleared and did not".format(len(rows), len(bad)))
  if not bad:
    # The interesting answer is usually this one: the rule behaved, and the REASON it never got to
    # act is the thing to fix.
    from collections import Counter
    c = Counter(r[7].split(" --")[0].split(" (")[0] for r in rows)
    print("reasons it declined, by frequency:")
    for k, v in c.most_common():
      print("   {:<26} {}".format(k, v))


if __name__ == "__main__":
  main()
