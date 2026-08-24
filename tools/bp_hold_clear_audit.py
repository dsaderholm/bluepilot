"""Why did the hold not clear? Reconstructs the rule's own inputs from a route.

He reported it again on 2026-08-23, with a photo: HOLD 27, SPEED LIMIT 25, offset badge 2 -- so
SLA's target WAS 27 and the hold WAS 27 and it did not clear. The fix from 2026-08-22 is on the
car; it ran and did not fire, which is more informative than it being absent.

THE RULE COMPARES TWO VALUES THAT ARE NOW PUBLISHED. `v_sla_target` and `speed_limit_live` reach
the wire as `vSlaTarget` and `speedLimitLive` (2026-08-23), and this tool reads them in preference
to anything it can work out for itself: they are the terms the rule actually gated on, in the frame
that gated on them. The output states which source it used.

For drives recorded BEFORE that landed the fields are absent -- 0 on all 27,139 frames of 000003ae
against 61,967 of 000003b5 -- and both terms are reconstructable from
`longitudinalPlanSP.speedLimit.resolver`, which was always logged:

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
from openpilot.tools.lib.logreader import LogReader


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
  # THE EARLY RETURNS ABOVE THE RULE. Route 000003ae showed all four rule conditions passing while
  # the hold sat for 87 s, so whatever declined it is one of the returns higher up
  # `update_manual_override` -- the press path, the press-settle stand-down, `v_target_valid`, or
  # `cruise_enabled`. Only the last two are visible from a route; print them rather than guess.
  enabled = False
  raw = 0.0
  t0 = None
  rows = []
  prev_key = None
  # PREFER WHAT THE CONTROLLER USED over what this tool can re-derive. `vSlaTarget` and
  # `speedLimitLive` are published precisely because they are the two terms the clearing rule gates
  # on, evaluated inside the frame that ran the rule. Re-deriving them from the resolver here is a
  # second implementation of the same arithmetic, and a tool whose reconstruction drifts reports on
  # a rule the car never evaluated. The resolver path is kept only for routes recorded before those
  # fields existed -- on 000003ae they are absent on all 27,139 frames, on 000003b5 present on
  # 61,967 -- and which one was used is stated in the output rather than left to be assumed.
  published_seen = 0

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
          cs = m.carState.cruiseState
          enabled = bool(cs.available and cs.enabled)
        except Exception:
          pass
        continue
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
        raw = round(float(icbm.vTargetRaw))
      except Exception:
        continue

      try:
        pub_target = round(float(icbm.vSlaTarget))
        pub_live = bool(icbm.speedLimitLive)
      except Exception:
        pub_target, pub_live = 0, False
      if pub_target > 0:
        published_seen += 1
        sla_target, live = pub_target, pub_live
      # NEVER HIDE THE ZERO. The first version of this skipped `baseline <= 0` and so could not
      # tell 'the hold stuck' from 'the hold cleared one frame later' -- the single question it
      # was written to answer. That is this fork's oldest recorded bug shape, committed here by
      # the person who wrote the rule against it.
      if baseline <= 0:
        if prev_key is not None:
          rows.append((t - t0, 0, sla_target, live, last_valid, source, diverged, "CLEARED"))
        prev_key = None
        continue

      # WHY the rule did not clear this frame, in its own terms.
      if not live:
        why = "NOT LIVE" + (" (last-valid only)" if last_valid else " (no limit at all)")
      elif sla_target <= 0:
        why = "SLA TARGET 0"
      elif baseline != sla_target:
        why = f"differs by {baseline - sla_target:+d} -- arming, correct"
      elif source == "pinned":
        why = "PINNED -- exempt, correct"
      elif not enabled:
        why = "CRUISE OFF -- frozen by design, the rule returns above this"
      else:
        why = "*** SHOULD HAVE CLEARED ***"

      key = (baseline, sla_target, live, source, why, enabled)
      if key != prev_key:
        rows.append((t - t0, baseline, sla_target, live, enabled, source, raw, why))
        prev_key = key

  if not rows:
    print("no hold on this route")
    return

  if published_seen:
    print(f"source of `sla`/`live`: PUBLISHED by the controller ({published_seen} frames)")
  else:
    print("source of `sla`/`live`: RE-DERIVED from the resolver -- this route predates the")
    print("                        published fields, so these are this tool's arithmetic, not the")
    print("                        controller's. Treat a 'SHOULD HAVE CLEARED' here with suspicion.")
  print()
  print("     t+     hold  sla  live  cruise  source          raw  why")
  for t, b, s, lv, en, src, rw, why in rows:
    print("  {:7.1f}  {:>4} {:>4}  {:>5} {:>7}  {:<14} {:>4}  {}".format(
      t, b, s, str(lv), str(en), src.split(".")[-1][:14], rw, why))

  bad = [r for r in rows if "SHOULD HAVE" in r[7]]
  print()
  print(f"{len(rows)} state change(s); {len(bad)} where the rule should have cleared and did not")
  if not bad:
    # The interesting answer is usually this one: the rule behaved, and the REASON it never got to
    # act is the thing to fix.
    from collections import Counter
    c = Counter(r[7].split(" --")[0].split(" (")[0] for r in rows)
    print("reasons it declined, by frequency:")
    for k, v in c.most_common():
      print(f"   {k:<26} {v}")


if __name__ == "__main__":
  main()
