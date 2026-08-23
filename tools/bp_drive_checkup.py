#!/usr/bin/env python3
"""FusionPilot: did today's fixes actually work? One pass, every question, several routes.

2026-08-19. A single day shipped fixes to eight separate complaints, and checking them one tool at a
time is six SSH round trips and a lot of reading. This asks all of them at once, over the last few
routes, and prints a verdict per question rather than a wall of numbers.

WHAT IT CHECKS, and which complaint each one answers:

  1. ICBM ALIVE            "when I turn ICBM on, all its settings are grayed out", and the param
                           being deleted on every UI start by the fifth gate. Detected from the
                           wire, not from params: with ICBM running `pcmCruiseSpeed` is False, so
                           vCruiseCluster (openpilot's MAX) and speedCluster (the dash) are free to
                           DIVERGE. Locked together for a whole drive means ICBM was off.
  2. SLA REACHED ACTIVE    "SLA is telling me to set my speed to 70". A state histogram plus what it
                           published. On the drive that prompted the fix, `active` was reached ZERO
                           times across 5,207 frames.
  3. +/- ROUTING           "it changes the ICBM little speed number, not the max". For every driver
                           press, which of the two numbers moved in the second after it.
  4. ACC AUTHORITY         Who drove. ford / opStop / fallback / inert / openpilot, as a share of
                           longitudinal frames, from the field published for exactly this.
  5. CAMERA CANCEL BRICK   CLOSED 2026-08-19 -- six consecutive drives with zero inert frames and
                           zero accFaulted. Kept as a WATCH, not an open question: it is what would
                           catch a third mechanism. First assert, transition count, and
                           whether it EVER cleared -- on route 0000038d it asserted at t+0.0 and
                           never cleared for the whole drive.
  6. GAP BUTTON            "when I adjusted my gap, it said personality on the screen".
                           personalityChanged should now be zero under the passthrough.
  7. PROCESS HEALTH        plannerd died on the first frame of a drive two days ago from a numpy
                           bool at the capnp boundary. Any process not running, and any exception.
  8. THERMAL               "my fan sounded like it was pinned at 100%" -- two map daemons running.
  9. SCC AT CORNERS        Did either curve controller ask for anything, and what.
 10. STOP OVERRIDE         Did it arm, and did `hasSlowDown` ever go true -- the signal that only
                           started being published two days ago.

NO DATA IS NOT ZERO, AND THIS PRINTS THEM DIFFERENTLY. A tool that shows `--` for both "the feature
did nothing" and "the message was never on the bus" hides the only distinction that matters; two
tools in this repo were written that way and both pointed at the wrong controller. Every check here
reports `no data` explicitly when the message it needs never arrived.

SAMPLING: reads ALL segments by default. `--max-segments` exists for speed and it is a TRAP for any
whole-drive percentage -- capping at the front of a route measures the car sitting parked, which is
how "1.7% of frames had a speed limit" was published when the real figure was 50.9%. When the cap is
hit this says so, loudly, next to the percentages it invalidates.

Percentages that describe DRIVING are taken over moving frames only (> 5 mph), for the same reason.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 tools/bp_drive_checkup.py
    ... tools/bp_drive_checkup.py --routes 3
    ... tools/bp_drive_checkup.py --route 00000390--abcdef0123
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

# Overridable so the tool can be validated against routes pulled off the device, rather than only
# ever running where its answers cannot be checked against known data. Added 2026-08-22 after the
# new checks below were written -- shipping a diagnostic nobody has seen produce a correct answer
# is how this fork got two tools that pointed at the wrong controller.
REALDATA = os.environ.get("BP_REALDATA", "/data/media/0/realdata")
MS_TO_MPH = 2.23694
KPH_TO_MPH = 0.621371
MOVING_MPH = 5.0

# A press moves the dash within about a second; sample a little past that so a held button settles.
PRESS_WINDOW_S = 1.5
PRESS_EPS_MPH = 0.4

DRIVER_SET_BUTTONS = ("accelCruise", "decelCruise")


def seg_index(name: str) -> int:
  tail = name.rsplit("--", 1)[-1]
  return int(tail) if tail.isdigit() else -1


def routes_by_recency() -> list[str]:
  groups: dict[str, list[str]] = defaultdict(list)
  for d in os.listdir(REALDATA):
    if "--" in d and seg_index(d) >= 0:
      groups[d.rsplit("--", 1)[0]].append(d)
  if not groups:
    sys.exit("no route segments under " + REALDATA)

  def when(r: str) -> float:
    return max(os.path.getmtime(os.path.join(REALDATA, d)) for d in groups[r])

  return sorted(groups, key=when, reverse=True)


def pct(n: int, d: int) -> str:
  return "  n/a" if d <= 0 else "{:5.1f}%".format(100.0 * n / d)


def verdict(ok: bool | None, yes: str, no: str, unknown: str = "no data") -> str:
  if ok is None:
    return "?  " + unknown
  return ("OK   " + yes) if ok else ("BAD  " + no)


class Checkup:
  """One route's worth of counters. Every field starts empty so 'never seen' stays distinguishable."""

  def __init__(self, route: str) -> None:
    self.route = route
    self.t0: float | None = None
    self.tmax: float = 0.0

    # 1. ICBM alive
    self.cs_frames = 0
    self.moving_frames = 0
    self.cluster_pairs = 0
    self.cluster_diverged = 0

    # 2. SLA
    self.sla_states: Counter[str] = Counter()
    self.sla_target_frames = 0
    self.sla_sources: Counter[str] = Counter()
    self.limits_seen: Counter[float] = Counter()

    # 3. +/- routing
    self.presses: list[dict] = []
    self._pending: list[dict] = []

    # 4. authority
    self.authority: Counter[str] = Counter()

    # 5. passthrough death / camera brick
    self.acc_faults = 0

    # 6. gap
    self.personality_changed = 0
    self.gap_button_presses = 0

    # 7. health
    self.not_running: Counter[str] = Counter()
    self.exceptions: list[str] = []
    self.network_noise = 0

    # 8. thermal
    self.max_temp: float | None = None
    self.max_ambient: float | None = None  # intakeTempC -- fan intake air, the ambient proxy
    self.max_fan: float | None = None

    # 10. the 2026-08-22 fixes. Three things shipped that day whose whole verification is a drive,
    # and every one of them was found with a throwaway script. They belong here instead.
    self.hold_births: list[dict] = []   # every 0 -> nonzero baseline, with what created it
    self.hold_deaths: list[dict] = []   # every nonzero -> 0, with whether it looked like a clear
    self.resume_t: float | None = None  # last resumeCruise press, for the phantom test
    self._prev_baseline = 0.0
    self._prev_raw = 0.0
    self._last_btn = ("none", -99.0)
    self._enabled = False
    # `_enabled` is meaningless until a carState has actually been read, and messages are NOT
    # ordered by type -- a hold event in the opening frames would otherwise be attributed to
    # cruise-off regardless of the truth, in the very check built to tell those apart.
    self._cs_seen = False

    # 9. SCC
    self.scc_map_active = 0
    self.scc_vision_active = 0
    self.scc_map_min: float | None = None
    self.scc_vision_min: float | None = None
    self.plan_sources: Counter[str] = Counter()

    # 10. stop override / model stop
    self.has_slow_down_frames = 0
    self.dec_frames = 0
    self.standstill_frames = 0

  # -- ingest -------------------------------------------------------------------------------

  def note_time(self, t: float) -> None:
    # Anchor on the RUNNING MINIMUM. Header replay means the first monotime is not the smallest,
    # and compensating for the backward step at a segment boundary is what inflated every t+NNNN
    # printed before 2026-08-12 by about 4x.
    if self.t0 is None or t < self.t0:
      self.t0 = t
    if t > self.tmax:
      self.tmax = t

  def rel(self, t: float) -> float:
    return t - self.t0 if self.t0 is not None else 0.0

  def car_state(self, cs, t: float) -> None:
    self.cs_frames += 1
    v_mph = float(cs.vEgo) * MS_TO_MPH
    moving = v_mph > MOVING_MPH
    if moving:
      self.moving_frames += 1

    dash = float(getattr(cs.cruiseState, "speedCluster", 0.0)) * MS_TO_MPH
    max_sp = float(getattr(cs, "vCruiseCluster", 0.0)) * KPH_TO_MPH
    if dash > 0.0 and max_sp > 0.0:
      self.cluster_pairs += 1
      if abs(dash - max_sp) > PRESS_EPS_MPH:
        self.cluster_diverged += 1

    if bool(getattr(cs.cruiseState, "standstill", False)):
      self.standstill_frames += 1

    for be in getattr(cs, "buttonEvents", []) or []:
      bt = str(be.type)
      if not be.pressed:
        continue
      if bt == "gapAdjustCruise":
        self.gap_button_presses += 1
      elif bt in DRIVER_SET_BUTTONS:
        self._pending.append({"t": t, "type": bt, "dash0": dash, "max0": max_sp,
                              "dash1": None, "max1": None})

    # Resolve any press whose window has closed.
    still = []
    for p in self._pending:
      if t - p["t"] >= PRESS_WINDOW_S:
        p["dash1"], p["max1"] = dash, max_sp
        self.presses.append(p)
      else:
        still.append(p)
    self._pending = still

  def plan_sp(self, lp) -> None:
    try:
      a = lp.speedLimit.assist
      self.sla_states[str(a.state)] += 1
      if float(a.vTarget) > 0.0:
        self.sla_target_frames += 1
    except Exception:
      pass
    try:
      r = lp.speedLimit.resolver
      src = str(r.source)
      self.sla_sources[src] += 1
      lim = float(r.speedLimit) * MS_TO_MPH
      if lim > 0.0:
        self.limits_seen[round(lim)] += 1
    except Exception:
      pass
    try:
      d = lp.dec
      self.dec_frames += 1
      if bool(d.hasSlowDown):
        self.has_slow_down_frames += 1
    except Exception:
      pass
    for name in ("map", "vision"):
      try:
        scc = getattr(lp.smartCruiseControl, name)
        if not bool(scc.active):
          continue
        v = float(scc.vTarget) * MS_TO_MPH
        if name == "map":
          self.scc_map_active += 1
          if v > 0.0 and (self.scc_map_min is None or v < self.scc_map_min):
            self.scc_map_min = v
        else:
          self.scc_vision_active += 1
          if v > 0.0 and (self.scc_vision_min is None or v < self.scc_vision_min):
            self.scc_vision_min = v
      except Exception:
        pass

  def controller_bp(self, cbp) -> None:
    try:
      self.authority[str(cbp.accAuthority)] += 1
    except Exception:
      pass

  def acc_faulted(self) -> None:
    self.acc_faults += 1

  def segment_gap(self) -> None:
    """A segment could not be read, so the baseline either side of it is not continuous.

    Without this the reader carries `_prev_baseline` across the hole: a hold that ENDED inside the
    missing segment is never recorded as a death, and one that STARTED there is reported as a birth
    at the next segment's first frame, with whatever button happened to be last seen attached to it.
    Checks 12 and 13 are the ones verifying today's fixes, so a fabricated birth reads as a phantom
    hold that never happened."""
    self._prev_baseline = 0.0
    self._prev_raw = 0.0
    self._last_btn = ("none", -99.0)
    self.resume_t = None
    self._cs_seen = False

  def buttons(self, cs, t: float) -> None:
    """Remember the last press, and specifically the last RESUME. Both feed check 10."""
    try:
      # Cruise state, for the hold-death test below. Route 000003a8 is exactly why it is needed:
      # the hold there died while SITTING ON SLA's number, which looks like the clear firing and
      # was not -- he had switched cruise off. Without this the check reports a false success on
      # the very drive that proved the bug.
      self._enabled = bool(cs.cruiseState.enabled)
      self._cs_seen = True
    except Exception:
      pass
    try:
      for b in cs.buttonEvents:
        if not b.pressed:
          continue
        name = str(b.type).split(".")[-1]
        self._last_btn = (name, t)
        if name == "resumeCruise":
          self.resume_t = t
    except Exception:
      pass

  def icbm_hold(self, icbm, t: float) -> None:
    """Every birth and death of a hold, which is what all three of the day's fixes turn on.

    Births carry the button that made them and how long since the last RESUME -- a birth within a
    fraction of a second of one is the phantom (route 000003aa, 0.02 s).

    Deaths carry whether the baseline had ARRIVED at SLA's own number first. A hold walked back to
    that number and then vanishing is the clear working; vanishing from somewhere else is cruise
    being switched off, which is what the old measurement mistook for a late clear.
    """
    if not self._cs_seen:
      return          # cannot say whether cruise was engaged, so do not pretend
    try:
      base = float(icbm.vBaseline)
      raw = float(getattr(icbm, "vTargetRaw", 0.0))
      if self._prev_baseline == 0.0 and base > 0.0:
        btn, bt = self._last_btn
        self.hold_births.append({
          "t": t, "speed": round(base), "button": btn,
          "since_press": t - bt,
          "since_resume": (t - self.resume_t) if self.resume_t is not None else 1e9,
        })
      elif self._prev_baseline > 0.0 and base == 0.0:
        self.hold_deaths.append({
          "t": t, "was": round(self._prev_baseline),
          # Was it sitting on SLA's number when it went? That is a clear; anything else is not.
          "at_sla": abs(self._prev_baseline - self._prev_raw) < 0.51 and self._prev_raw > 0,
          "engaged": self._enabled,
        })
      self._prev_baseline, self._prev_raw = base, raw
    except Exception:
      pass

def render(c: Checkup, capped: bool) -> None:
  dur = c.tmax - c.t0 if c.t0 is not None else 0.0
  print("")
  print("=" * 78)
  print("route {}   {:.0f} s   {} carState frames".format(c.route, dur, c.cs_frames))
  if capped:
    print("  !! SEGMENT CAP HIT -- every percentage below is over the FRONT of the drive only,")
    print("     which is where the car is parked. Re-run without --max-segments before believing one.")
  print("=" * 78)

  # 1 -----------------------------------------------------------------------------------
  if c.cluster_pairs == 0:
    icbm = None
  else:
    icbm = c.cluster_diverged > 0
  print("1. ICBM alive          " + verdict(
    icbm,
    "MAX and dash diverged on {} of {} frames".format(c.cluster_diverged, c.cluster_pairs),
    "MAX and dash were LOCKED all drive -- ICBM was off, so pcmCruiseSpeed stayed True",
    "cruise never engaged, so the two numbers were never both live"))

  # 2 -----------------------------------------------------------------------------------
  total_sla = sum(c.sla_states.values())
  if total_sla == 0:
    sla = None
  else:
    sla = c.sla_states.get("active", 0) > 0
  print("2. SLA reached active  " + verdict(
    sla,
    "active on {} frames ({} of plan)".format(c.sla_states.get("active", 0),
                                              pct(c.sla_states.get("active", 0), total_sla).strip()),
    "NEVER reached active across {} plan frames".format(total_sla),
    "longitudinalPlanSP never arrived"))
  if total_sla:
    order = ["disabled", "inactive", "preActive", "pending", "adapting", "active"]
    row = "   states: " + "  ".join(
      "{}={}".format(s, c.sla_states.get(s, 0)) for s in order if c.sla_states.get(s, 0))
    print(row)
    if c.sla_sources:
      print("   source: " + "  ".join("{}={}".format(k, v) for k, v in c.sla_sources.most_common()))
    if c.limits_seen:
      top = "  ".join("{} mph x{}".format(k, v) for k, v in c.limits_seen.most_common(6))
      print("   limits: " + top)
      # The stuck-80 signature: TSR reporting one constant value forever.
      if len(c.limits_seen) == 1 and c.sla_sources.get("car", 0) > c.sla_sources.get("map", 0):
        print("   !! ONE constant limit, sourced from the CAR -- this is the stuck TSR value again.")

  # 3 -----------------------------------------------------------------------------------
  if not c.presses:
    print("3. +/- routing         ?  no driver set-speed presses in this drive")
  else:
    moved_max = moved_dash = moved_both = moved_neither = 0
    for p in c.presses:
      dm = abs((p["max1"] or 0) - p["max0"]) > PRESS_EPS_MPH
      dd = abs((p["dash1"] or 0) - p["dash0"]) > PRESS_EPS_MPH
      if dm and dd:
        moved_both += 1
      elif dm:
        moved_max += 1
      elif dd:
        moved_dash += 1
      else:
        moved_neither += 1
    ok = moved_max + moved_both > 0 and moved_dash == 0
    print("3. +/- routing         " + verdict(
      ok,
      "every press moved the MAX ({} max, {} both)".format(moved_max, moved_both),
      "{} press(es) moved ONLY the ICBM number, which is the complaint".format(moved_dash)))
    print("   {} presses: max-only {}  dash-only {}  both {}  neither {}".format(
      len(c.presses), moved_max, moved_dash, moved_both, moved_neither))

  # 4 -----------------------------------------------------------------------------------
  tot_auth = sum(c.authority.values())
  if tot_auth == 0:
    print("4. ACC authority       ?  controllerStateBP never arrived")
  else:
    ford = c.authority.get("ford", 0)
    inert = c.authority.get("inert", 0)
    # `stock` is CC.longActive False -- cruise not engaged, nobody driving. Counting it in the
    # denominator made 23% of drive 389 read as openpilot substituting for Ford when nothing was
    # asked of either, and the same shape produced the 70.6% error on drive A. Engaged frames only.
    engaged = tot_auth - c.authority.get("stock", 0)
    print("4. ACC authority       " + verdict(
      inert == 0,
      "never inert; Ford authored {} of ENGAGED frames".format(pct(ford, engaged).strip()),
      "WENT INERT for {} frames -- the passthrough died and openpilot drove".format(inert)))
    print("   engaged {} of {} frames; disengaged (stock) {}".format(
      engaged, tot_auth, c.authority.get("stock", 0)))
    print("   " + "  ".join("{}={} ({})".format(k, v, pct(v, tot_auth).strip())
                            for k, v in c.authority.most_common()))

  # 5 -----------------------------------------------------------------------------------
  inert_n = c.authority.get("inert", 0)
  if tot_auth == 0:
    print("5. Passthrough brick   ?  controllerStateBP never arrived")
  else:
    print("5. Passthrough brick   " + verdict(
      inert_n == 0,
      "never went inert; {} accFaulted events".format(c.acc_faults),
      "INERT for {} frames -- the camera latched cancel and openpilot drove the rest".format(inert_n)))
    if c.acc_faults:
      print("   accFaulted events: {}".format(c.acc_faults))

  # 6 -----------------------------------------------------------------------------------
  if c.gap_button_presses == 0:
    print("6. Gap button          ?  no gap presses in this drive")
  else:
    print("6. Gap button          " + verdict(
      c.personality_changed == 0,
      "{} presses, no personality change".format(c.gap_button_presses),
      "{} presses changed openpilot PERSONALITY instead of Ford's gap".format(c.personality_changed)))

  # 7 -----------------------------------------------------------------------------------
  bad = sum(c.not_running.values()) + len(c.exceptions)
  print("7. Process health      " + verdict(
    bad == 0,
    "nothing died ({} network-noise exceptions ignored)".format(c.network_noise),
    "{} process/exception events".format(bad)))
  for name, n in c.not_running.most_common(5):
    print("   not running: {} x{}".format(name, n))
  for e in c.exceptions[:3]:
    print("   exception: " + e[:110])

  # 8 -----------------------------------------------------------------------------------
  if c.max_temp is None:
    print("8. Thermal             ?  deviceState never arrived")
  else:
    fan = "?" if c.max_fan is None else "{:.0f}%".format(c.max_fan)
    amb = "?" if c.max_ambient is None else "{:.0f} C".format(c.max_ambient)
    print("8. Thermal             .    peak {:.0f} C, intake air {}, fan peak {}".format(
      c.max_temp, amb, fan))
    print("   NOT a verdict. This device idles at 75 C parked with the engine off in August, so a")
    print("   peak near 80 proves nothing on its own -- check `ps` for a process actually spinning.")

  # 9 -----------------------------------------------------------------------------------
  if c.scc_map_active == 0 and c.scc_vision_active == 0:
    print("9. SCC at corners      ?  neither curve controller was active at any point")
  else:
    mm = "--" if c.scc_map_min is None else "{:.0f} mph".format(c.scc_map_min)
    vv = "--" if c.scc_vision_min is None else "{:.0f} mph".format(c.scc_vision_min)
    print("9. SCC at corners      OK   map active {} frames (min {}), vision {} frames (min {})".format(
      c.scc_map_active, mm, c.scc_vision_active, vv))

  # 10 ----------------------------------------------------------------------------------
  if c.dec_frames == 0:
    print("10. Model stop         ?  dec was never published -- plannerd may not have run")
  else:
    print("10. Model stop         " + verdict(
      c.has_slow_down_frames > 0,
      "hasSlowDown true on {} frames ({})".format(
        c.has_slow_down_frames, pct(c.has_slow_down_frames, c.dec_frames).strip()),
      "hasSlowDown NEVER true -- the stop override could not have armed"))
    op_stop = c.authority.get("opStop", 0)
    print("   stop override: {}".format(
      "fired on {} frames".format(op_stop) if op_stop else
      "NEVER FIRED -- needs hasSlowDown + plan committed to stopping + <=20 mph + no lead in 60 m"))
    print("   Ford standstill (its own hold): {} frames".format(c.standstill_frames))

  # 11 -- the 2026-08-22 fixes, which all three needed a drive to verify -------------------
  rec = c.authority.get("recovery", 0)
  print("11. Cancel recovery    " + verdict(
    None if c.authority.get("opStop", 0) == 0 else rec > 0,
    "ran on {} frames -- grep swaglog for RECOVERY WORKED to see if the camera let go".format(rec),
    "the override fired and the recovery NEVER ran: either no cancel followed it (good) or "
    "attribution refused it -- check the cancel timing against RESUME of Ford authority",
    "the override never fired, so there was no cancel of ours to recover from"))
  if rec:
    print("   Ford authored {} frames after it -- a non-zero count here IS the recovery working"
          .format(c.authority.get("ford", 0)))

  # 12 ------------------------------------------------------------------------------------
  phantoms = [b for b in c.hold_births if b["since_resume"] < 0.4]
  print("12. Phantom holds      " + verdict(
    not phantoms,
    "none -- every hold was asked for",
    "{} hold(s) born within 0.4 s of a RESUME press: {} -- the resume-tail guard is not holding"
    .format(len(phantoms),
            ", ".join("{:.0f} mph at t+{:.0f}".format(b["speed"], b["t"]) for b in phantoms))))
  for b in c.hold_births:
    print("   born t+{:<7.1f} {:>3} mph  from {:<14} ({:.2f} s after that press)".format(
      b["t"], b["speed"], b["button"], b["since_press"]))

  # 13 ------------------------------------------------------------------------------------
  # A hold walked back to SLA's number must CLEAR. Before 2026-08-22 it never could, and the tell
  # was a death that did not happen at SLA's number -- cruise being switched off instead.
  # BOTH terms. Sitting on SLA's number is not enough -- see the note in `buttons`.
  clean = [d for d in c.hold_deaths if d["at_sla"] and d["engaged"]]
  off = [d for d in c.hold_deaths if not d["engaged"]]
  print("13. Hold clears at SLA " + verdict(
    None if not c.hold_deaths else bool(clean),
    "{} of {} hold(s) ended sitting on SLA's own number -- the clear is firing".format(
      len(clean), len(c.hold_deaths)),
    "{} hold(s) ended and NOT ONE ended cleanly while engaged at SLA's number -- {} went when "
    "cruise was switched off. That is the exact shape of the bug fixed on 2026-08-22.".format(
      len(c.hold_deaths), len(off)),
    "no hold ended this drive"))


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--route", default=None, help="one route; default is the most recent --routes")
  ap.add_argument("--routes", type=int, default=3, help="how many recent routes to check")
  ap.add_argument("--max-segments", type=int, default=0, help="0 = all; a cap invalidates percentages")
  args = ap.parse_args()

  if not os.path.isdir(REALDATA):
    sys.exit("no {} -- run this on the device".format(REALDATA))
  try:
    from openpilot.tools.lib.logreader import LogReader
  except ImportError as e:
    sys.exit("no LogReader ({}); see the docstring for the interpreter to use".format(e))

  wanted = [args.route] if args.route else routes_by_recency()[:args.routes]

  for route in wanted:
    segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
    capped = bool(args.max_segments) and len(segs) > args.max_segments
    if args.max_segments:
      segs = segs[:args.max_segments]

    c = Checkup(route)
    for seg in segs:
      p = os.path.join(REALDATA, seg, "rlog")
      if not os.path.exists(p):
        p += ".zst"
      if not os.path.exists(p):
        c.segment_gap()
        continue
      try:
        lr = LogReader(p)
      except Exception:
        c.segment_gap()
        continue

      for m in lr:
        try:
          w = m.which()
        except Exception:
          continue
        t = m.logMonoTime / 1e9
        c.note_time(t)

        try:
          if w == "carState":
            c.car_state(m.carState, t)
            c.buttons(m.carState, t)
          elif w == "selfdriveStateSP":
            c.icbm_hold(m.selfdriveStateSP.intelligentCruiseButtonManagement, t)
          elif w == "longitudinalPlanSP":
            c.plan_sp(m.longitudinalPlanSP)
          elif w == "controllerStateBP":
            c.controller_bp(m.controllerStateBP)
          elif w == "deviceState":
            d = m.deviceState
            v = getattr(d, "maxTempC", None)
            if v is not None:
              v = float(v)
              if c.max_temp is None or v > c.max_temp:
                c.max_temp = v
            a = getattr(d, "intakeTempC", None)
            if a is not None:
              a = float(a)
              if c.max_ambient is None or a > c.max_ambient:
                c.max_ambient = a
            f = getattr(d, "fanSpeedPercentDesired", None)
            if f is not None:
              f = float(f)
              if c.max_fan is None or f > c.max_fan:
                c.max_fan = f
          elif w == "managerState":
            for pr in m.managerState.processes:
              if pr.shouldBeRunning and not pr.running:
                c.not_running[pr.name] += 1
          elif w == "selfdriveState":
            pass
          elif w == "onroadEvents":
            for e in m.onroadEvents:
              n = str(e.name)
              if n == "personalityChanged":
                c.personality_changed += 1
              elif n == "accFaulted":
                c.acc_faulted()
          elif w == "logMessage":
            s = str(m.logMessage)
            if "Traceback" in s or "Exception" in s:
              if any(n in s for n in ("athenad", "sunnylinkd", "upload_failed", "ws_recv")):
                c.network_noise += 1
              else:
                c.exceptions.append(s.replace(chr(10), " | ")[:200])
        except Exception:
          continue

    render(c, capped)

  print("")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
