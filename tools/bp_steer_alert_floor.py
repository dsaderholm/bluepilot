"""What raising angle mode's saturation speed floor would cost, measured rather than argued.

`latcontrol_angle.py` sets `sat_check_min_speed = 5.` m/s (11.2 mph) where the base `LatControl`
uses 10. (22.4 mph). That extra 11 mph of exposure is why this car gets "Take Control -- Turn
Exceeds Steering Limit" during intersection turns at all: below ~22 mph a torque-controlled car
never sees the alert, and an angle-mode car sees every corner tight enough to saturate it.

It is the obvious lever on the alerts the lane-deviation gate CANNOT quiet -- 14 of the 61 episodes
on record are ones where the model had no lane lines to judge position from, so the gate fails open
and shows them, and 11 of those 14 are under 40 mph. **But a speed floor silences unconditionally,
on no evidence at all, which is the direction this fork is normally strict about.** So the question
is not "would it be quieter" -- of course it would -- it is what it costs, and this tool answers it.

    python tools/bp_steer_alert_floor.py <dir>...                    speed floors
    python tools/bp_steer_alert_floor.py <dir>... --limits 1,1.5,2   DWELL times, the other lever

THE DWELL TIME IS THE OTHER LEVER AND IT IS BETTER AIMED. `sat_limit` is `CP.steerLimitTimer`, and
grepping the whole tree it reaches exactly one line -- `latcontrol.py:9` -- with its own capnp
comment reading "time before steerLimitAlert is issued". **It does nothing but decide how long a
saturation must persist before he is told about it**, so raising it cannot change how the car
drives. Ford already carries the longest value of any brand (1.0 s against 0.4 for most), which is
worth knowing before treating it as untouched ground. A floor asks "is the car going fast enough for
this to matter"; a dwell asks "has this lasted long enough to matter", and the second is much closer
to the question.

HOW IT WORKS, AND WHY IT IS NOT JUST A FILTER. `sat_check_min_speed` gates an ACCUMULATOR, not the
raise: `sat_time` climbs by `dt` only while the car is above the floor, falls otherwise, and the
flag is true only once it has held for `steerLimitTimer` (1.0 s on Ford). So a higher floor does not
merely drop the frames below it -- it delays or prevents accumulation for the frames ABOVE it too,
in any episode that started slow. Filtering the logged flag by speed would understate the effect,
so `_check_saturation` is re-simulated in full at each candidate floor.

    angle_control_saturated   |steeringAngleDesiredDeg - steeringAngleDeg| > 2.5, from the logged
                              angleState -- the same expression latcontrol_angle.py evaluates,
                              since `use_steer_limited_by_safety` is False on Ford
    curvature_limited         recovered from the ISO clamp's signature: controlsState.curvature is
                              the CLIPPED value, so the clamp bound iff it sits on
                              (+-3.0 + roll*g) / v^2 to float32 precision
    reset                     controlsd calls LaC.reset() whenever latActive drops, so sat_time is
                              zeroed on every disengagement

**THE VALIDATION LINE IS THE POINT OF TRUSTING ANY OF THIS.** Re-simulating at the SHIPPED 5.0 m/s
has to reproduce the `saturated` flag the car actually published. That agreement is printed first,
and a low number means `curvature_limited` is being recovered badly and every floor column below it
is worthless. A counterfactual that cannot reproduce the factual is not a measurement.
"""
import argparse
import glob
import os
import statistics
import sys

import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))

# Reuses the schema load, the episode grouping and the deviation definition rather than restating
# them -- `worst()` in particular encodes "unmeasurable outranks any distance", which has to mean
# the same thing here as it does there or the two tools disagree about what an alert is.
from bp_steer_saturated import (  # noqa: E402
  GAP_S, MPH, log_capnp, route_of, segno, worst)
from bluepilot.selfdrive.selfdrived.steer_saturated_gate import (  # noqa: E402
  LANE_DEVIATION_M, lane_deviation)

DT = 0.01                               # DT_CTRL; controlsd runs _check_saturation once a frame
SAT_LIMIT = 1.0                         # CP.steerLimitTimer, opendbc/car/ford/interface.py:39
ANGLE_SAT_THRESHOLD = 2.5               # STEER_ANGLE_SATURATION_THRESHOLD, latcontrol_angle.py
MAX_LAT_ACCEL_NO_ROLL = 3.0             # drive_helpers.py
G = 9.81
MIN_SPEED = 1.0
MAX_CURVATURE = 0.2
SHIPPED_FLOOR = 5.0                     # latcontrol_angle.py; the base LatControl uses 10.
SHIPPED = (SHIPPED_FLOOR, SAT_LIMIT)    # the pair the car actually runs

# capnp stores curvature as float32, so "the clamp bound" is equality only to that precision.
CLAMP_EPS_REL = 2e-6


def curvature_limited(k_clipped: float, v_ego: float, roll: float) -> bool:
  """Did `clip_curvature`'s accel or max-curvature clamp bite on this frame?

  Recovered from the output rather than recomputed from the input, because the input the clamp saw
  (the previous frame's curvature, after its own jerk limit) is not logged. The clamp writes the
  bound exactly into its output, so sitting ON the bound is the signature.

  DELIBERATELY NOT THE JERK LIMIT. `clip_curvature` returns `limited_accel or limited_max_curv` and
  the jerk rate limit is excluded from that flag upstream -- so including it here would report a
  saturation the car never saw.
  """
  v = max(v_ego, MIN_SPEED)
  roll_comp = roll * G
  upper = (MAX_LAT_ACCEL_NO_ROLL + roll_comp) / v ** 2
  lower = (-MAX_LAT_ACCEL_NO_ROLL + roll_comp) / v ** 2
  for bound in (upper, lower, MAX_CURVATURE, -MAX_CURVATURE):
    if abs(k_clipped - bound) <= CLAMP_EPS_REL * max(abs(bound), 1e-9):
      return True
  return False


class SatSim:
  """`LatControl._check_saturation`, verbatim, at an arbitrary floor and dwell time."""

  def __init__(self, floor, limit):
    self.floor = floor
    self.limit = limit
    self.sat_time = 0.0

  def reset(self):
    self.sat_time = 0.0

  def update(self, raw_saturated, curv_limited, v_ego, steering_pressed) -> bool:
    if (raw_saturated or curv_limited) and v_ego > self.floor and not steering_pressed:
      self.sat_time += DT
    else:
      self.sat_time -= DT
    self.sat_time = min(self.limit, max(0.0, self.sat_time))
    return self.sat_time > (self.limit - 1e-3)


def scan(files, configs):
  """`configs` is a list of (floor m/s, dwell s). SHIPPED is the one validated against the log."""
  per_cfg = {c: [] for c in configs}       # config -> list of episodes
  agree = miss = 0                         # simulation vs the logged flag, at SHIPPED
  exposure = {c: 0 for c in configs}       # latActive frames above each config's floor

  for path in files:
    try:
      with open(path, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue

    sims = {c: SatSim(*c) for c in configs}
    open_ep = {c: None for c in configs}
    last_alert = {c: -1e9 for c in configs}

    v = 0.0
    pressed = False
    roll = 0.0
    dev = None
    desired_model = 0.0
    last_pressed_t = -1e9

    while True:
      try:
        m = next(evs)
      except StopIteration:
        break
      except Exception:
        break
      try:
        w = m.which()
        if w == "carState":
          v = float(m.carState.vEgo)
          pressed = bool(m.carState.steeringPressed)
          if pressed:
            last_pressed_t = m.logMonoTime / 1e9
          continue
        if w == "liveParameters":
          roll = float(m.liveParameters.roll)
          continue
        if w == "modelV2":
          desired_model = float(m.modelV2.action.desiredCurvature)
          dev = lane_deviation(m.modelV2.laneLines, m.modelV2.laneLineProbs)
          continue
        if w != "controlsState":
          continue

        t = m.logMonoTime / 1e9
        cs = m.controlsState
        which = cs.lateralControlState.which()
        lac = getattr(cs.lateralControlState, which)
        active = bool(lac.active)
        k_clipped = float(cs.curvature)

        if which != "angleState":
          # Nothing to say about a car that is not in angle mode; the floor being measured is
          # angle mode's alone.
          continue

        raw_sat = abs(float(lac.steeringAngleDesiredDeg)
                      - float(lac.steeringAngleDeg)) > ANGLE_SAT_THRESHOLD
        curv_lim = curvature_limited(float(cs.desiredCurvature), v, roll)

        for c, sim in sims.items():
          if not active:
            sim.reset()
            sat = False
          else:
            sat = sim.update(raw_sat, curv_lim, v, pressed)
            if v > c[0]:
              exposure[c] += 1

          if c == SHIPPED:
            if sat == bool(lac.saturated):
              agree += 1
            else:
              miss += 1

          # selfdrived's condition, verbatim, on the simulated flag.
          clipped_speed = max(v, 0.3)
          actual_la = k_clipped * clipped_speed ** 2
          desired_la = desired_model * clipped_speed ** 2
          undershooting = abs(desired_la) / abs(1e-3 + actual_la) > 1.2
          turning = abs(desired_la) > 1.0
          recent_press = (t - last_pressed_t) < 2.0
          if not (active and not recent_press and undershooting and turning and sat):
            continue

          ep = open_ep[c]
          if ep is None or t - last_alert[c] > GAP_S:
            ep = {"route": route_of(path), "seg": segno(path), "t": t, "end": t,
                  "devs": [], "unmeasurable": 0, "mph": []}
            per_cfg[c].append(ep)
            open_ep[c] = ep
          last_alert[c] = t
          ep["end"] = t
          ep["mph"].append(v * MPH)
          if dev is None:
            ep["unmeasurable"] += 1
          else:
            ep["devs"].append(dev)
      except Exception:
        continue

  return per_cfg, agree, miss, exposure


def shown(episodes, threshold=LANE_DEVIATION_M):
  """Episodes the shipped gate would still put on screen."""
  return [e for e in episodes if worst(e) >= threshold]


def survives(ep, others, tol=3.0):
  """Did this episode still happen under the other setting, allowing for it firing LATER?

  MATCHING ON START TIME ALONE IS WRONG AND THE FIRST VERSION DID IT. Raising either lever delays
  accumulation, so the same underlying saturation fires half a second or more later -- a rounded
  start time then reads as one episode LOST and one NEW, and the cost column doubles. Match on the
  windows being near each other instead: same segment, and the two [start, end] intervals within
  `tol` of overlapping.
  """
  for o in others:
    if o["route"] != ep["route"] or o["seg"] != ep["seg"]:
      continue
    if o["t"] - tol <= ep["end"] and ep["t"] - tol <= o["end"]:
      return True
  return False


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("directory", nargs="+")
  ap.add_argument("--floors", default="5,6,7,8,9,10",
                  help="m/s speed floors to simulate (5 is what ships)")
  ap.add_argument("--limits", default=None,
                  help="dwell times in seconds instead of floors (1.0 is what ships)")
  args = ap.parse_args()

  if args.limits:
    sweep = sorted({float(x) for x in args.limits.split(",")} | {SAT_LIMIT})
    configs = [(SHIPPED_FLOOR, x) for x in sweep]
    label, unit, shipped_val = "dwell s", "s", SAT_LIMIT
  else:
    sweep = sorted({float(x) for x in args.floors.split(",")} | {SHIPPED_FLOOR})
    configs = [(x, SAT_LIMIT) for x in sweep]
    label, unit, shipped_val = "floor m/s", "m/s", SHIPPED_FLOOR
  configs = sorted(set(configs))

  files = []
  for d in args.directory:
    files += glob.glob(os.path.join(d, "*.rlog.zst"))
  files.sort(key=segno)
  if not files:
    print("no matching segments")
    return

  per_cfg, agree, miss, exposure = scan(files, configs)
  total = agree + miss

  what = "DWELL TIME" if args.limits else "SPEED FLOOR"
  print(f"=== ANGLE-MODE SATURATION {what}: WHAT RAISING IT WOULD COST ===")
  print(f"   {len(files)} segments, {len(sorted({route_of(f) for f in files}))} routes")
  print()
  print(f"   VALIDATION -- re-simulating what SHIPS ({SHIPPED_FLOOR:.1f} m/s, {SAT_LIMIT:.1f} s)")
  print("   must reproduce the flag the car published. Below ~99% means curvature_limited is")
  print("   recovered badly and every row under it is worthless.")
  if not total:
    print("     NO ANGLE-MODE FRAMES -- nothing to validate against")
    return
  print(f"     agreement with controlsState.lateralControlState.angleState.saturated: "
        f"{100.0 * agree / total:.2f}%  ({miss} of {total} frames differ)")
  print()

  base = per_cfg[SHIPPED]
  base_shown = shown(base)

  print(f"  {label:>10}{'mph':>7}{'episodes':>10}{'shown':>7}"
        f"{'lost':>7}{'LOST+SHOWN':>12}{'worst lost':>12}")
  for c in configs:
    eps = per_cfg[c]
    val = c[1] if args.limits else c[0]
    lost = [e for e in base if not survives(e, eps)]
    lost_shown = [e for e in base_shown if not survives(e, eps)]
    worst_lost = max((worst(e) for e in lost_shown), default=0.0)
    worst_txt = "--" if not lost_shown else ("UNMEAS" if worst_lost == float("inf")
                                             else format(worst_lost, ".2f"))
    mph_txt = "" if args.limits else format(c[0] * MPH, ".1f")
    mark = "  <- SHIPS" if val == shipped_val else ""
    print(f"  {val:>10.1f}{mph_txt:>7}{len(eps):>10}{len(shown(eps)):>7}"
          f"{len(lost):>7}{len(lost_shown):>12}{worst_txt:>12}{mark}")

  print()
  print(f"   lost        episodes that stop happening at all, vs the shipped {shipped_val} {unit}")
  print("   shown       of the episodes that remain, how many the lane gate still puts on screen")
  print("   LOST+SHOWN  of the LOST ones, how many the lane gate WOULD have shown -- THE COST")
  print("   worst lost  the largest lane offset among those; UNMEAS means one was unmeasurable")
  print()

  # Every episode the most aggressive setting removes, so the cost reads case by case rather than
  # as a count. One 1.5 m episode outranks a dozen 0.1 m ones and a total cannot say so.
  top = configs[-1]
  removed = [e for e in base if not survives(e, per_cfg[top])]
  top_val = top[1] if args.limits else top[0]
  if removed:
    print(f"=== EVERY EPISODE THE {top_val:g} {unit} SETTING WOULD REMOVE ===")
    print(f"  {'route':>10}{'seg':>5}{'dur s':>8}{'mph':>7}{'worst m':>10}{'gate':>8}")
    for e in sorted(removed, key=worst, reverse=True):
      wv = worst(e)
      print(f"  {e['route']:>10}{e['seg']:>5}{e['end'] - e['t']:>8.1f}"
            f"{statistics.median(e['mph']):>7.1f}"
            f"{('UNMEAS' if wv == float('inf') else format(wv, '.2f')):>10}"
            f"{('SHOWN' if wv >= LANE_DEVIATION_M else 'silent'):>8}")
  else:
    print(f"=== THE {top_val:g} {unit} SETTING REMOVES NOTHING ON THIS SAMPLE ===")

  if not args.limits:
    print()
    print("=== HOW MUCH DRIVING EACH FLOOR STOPS WATCHING ===")
    base_exp = exposure[SHIPPED]
    for c in configs:
      lost_frames = base_exp - exposure[c]
      print(f"  {c[0]:>10.1f} m/s  {exposure[c]:>10} latActive frames watched"
            f"  ({100.0 * lost_frames / max(base_exp, 1):>5.1f}% less than what ships)")
    print()
    print("  A floor removes SUPERVISION, not just alerts: below it the car can saturate for any")
    print("  length of time and nothing accumulates. Read the percentage as the share of engaged")
    print("  driving that stops being able to raise this alert at all.")
  else:
    print()
    print("  A dwell time removes no supervision at any speed -- it only requires the saturation")
    print("  to persist longer before he is told. That is why its cost column is the one to read.")


if __name__ == "__main__":
  main()
