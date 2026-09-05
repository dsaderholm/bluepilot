"""Every "Take Control -- Turn Exceeds Steering Limit" alert, with how far off center the car was.

**THE THRESHOLD IN `steer_saturated_gate.py` IS SET FROM THIS TOOL'S `--sweep` OUTPUT.** Re-run it
before moving that number, and re-run it after any drive that changes lateral behavior -- a gate
whose threshold was argued from memory is the guessed-bound failure this repo already paid for once
(`MAPD_V2_STALL_S`, where an invented 60 s would have bounced a healthy mapd on two of three drives).

    python tools/bp_steer_saturated.py <dir>            per-episode table
    python tools/bp_steer_saturated.py <dir> --sweep    surviving episodes at each threshold

WHY LANE POSITION AND NOT SATURATION. His own rule, from the road: *"I just ignore most steering
saturated errors until it starts to stray enough from my lane."* Saturation means the controller
asked for more than it got; that is a fact about the command, not about where the car ended up. The
alert exists to tell him to take the wheel, and what justifies taking the wheel is the car being
somewhere it should not be.

IT IMPORTS THE SHIPPED GATE RATHER THAN RE-IMPLEMENTING IT. `lane_deviation` here is the same
function the car runs, so a threshold scored here is scored on exactly the arithmetic that will
decide it on the road. The gate module imports nothing but `math` -- deliberately, because
**importing `opendbc.car.*` into a process that has called `capnp.load()` aborts the interpreter
with exit 127 and no traceback**, and every rlog tool loads `log.capnp`.

EPISODES, NOT FRAMES. One saturation runs for seconds and raises the event on every frame of it, so
counting frames counts how long he was in a corner. He hears one chime cycle per episode, so
episodes are the unit. Frames separated by more than `GAP_S` are different episodes.

**THE ALERT CONDITION IS RECONSTRUCTED AT 100 Hz, NOT READ OUT OF `onroadEvents`.** That was the
first version and it undercounts by a factor of thirty: selfdrived logs `onroadEvents` "every second
or on change" (selfdrived.py:666), so a three-second alert leaves about three samples in the route
and a `max()` over them is a max over three arbitrary instants. The gate runs on EVERY frame the
alert would fire, so scoring it needs every one of those frames. The raw `onroadEvents` count is
printed beside the reconstruction as a cross-check that the reconstruction is not inventing
episodes -- it should be far smaller and roughly one per alerting second.

DEFINITIONS, so a number quoted from here can be traced:

    alert frame   selfdrived.py's own condition, verbatim: `lac.active`, no steering press within
                  2 s, `undershooting` (|desired| / |1e-3 + actual| > 1.2), `turning`
                  (|desired lateral accel| > 1.0) and `lac.saturated`
    deviation     |(laneLines[1].y[0] + laneLines[2].y[0]) / 2|, meters, both inner probs >= 0.30;
                  UNMEASURABLE otherwise, which the gate treats as "show the alert"
    worst         the largest deviation anywhere in the episode -- the gate latches, so an episode
                  that ever goes wide keeps its alert from that point on
    baseline      deviation on engaged hands-off frames with NO alert, for scale
"""
import argparse
import glob
import os
import statistics
import sys

import capnp
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from bluepilot.selfdrive.selfdrived.steer_saturated_gate import (  # noqa: E402
  LANE_DEVIATION_M, lane_deviation)

capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MPH = 2.23694
GAP_S = 1.0               # 1 s, the same silence RESET_FRAMES calls the end of an episode
SWEEP = (0.20, 0.30, 0.40, 0.50, 0.60, 0.75, 1.00)


def segno(path):
  try:
    return int(os.path.basename(path).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def route_of(path):
  return os.path.basename(path).split("--")[0]


def scan(files):
  episodes = []            # one dict per alert episode
  baseline = []            # deviation on engaged, hands-off, un-alerted frames
  alert_frames = 0
  logged_events = 0        # raw `onroadEvents` occurrences, as a cross-check only

  for path in files:
    try:
      with open(path, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue

    lat_active = False       # controlsState.lateralControlState.<x>.active
    saturated = False        # ...saturated
    curvature = 0.0          # controlsState.curvature, the ACTUAL curvature
    desired = 0.0            # modelV2.action.desiredCurvature -- selfdrived reads the MODEL, not
                             # controlsState, so read the same field it does
    dev = None
    last_pressed_t = -1e9
    cur = None               # the episode being accumulated, if any
    last_alert_t = -1e9

    while True:
      try:
        m = next(evs)
      except StopIteration:
        break
      except Exception:
        break
      try:
        w = m.which()
        t = m.logMonoTime / 1e9

        if w == "controlsState":
          cs = m.controlsState
          lac = getattr(cs.lateralControlState, cs.lateralControlState.which())
          lat_active = bool(lac.active)
          saturated = bool(lac.saturated)
          curvature = float(cs.curvature)
        elif w == "modelV2":
          desired = float(m.modelV2.action.desiredCurvature)
          dev = lane_deviation(m.modelV2.laneLines, m.modelV2.laneLineProbs)
        elif w == "onroadEvents":
          logged_events += sum(1 for e in m.onroadEvents if e.name == "steerSaturated")
        elif w == "carState":
          v = float(m.carState.vEgo)
          if bool(m.carState.steeringPressed):
            last_pressed_t = t

          # selfdrived.py's condition, verbatim. `1e-3 + actual` is signed there and is kept
          # signed here -- reproducing it, not improving it, is the whole point of this block.
          clipped_speed = max(v, 0.3)
          actual_la = curvature * clipped_speed ** 2
          desired_la = desired * clipped_speed ** 2
          undershooting = abs(desired_la) / abs(1e-3 + actual_la) > 1.2
          turning = abs(desired_la) > 1.0
          recent_press = (t - last_pressed_t) < 2.0
          alerting = lat_active and not recent_press and undershooting and turning and saturated

          if not alerting:
            if lat_active and not recent_press and dev is not None and v * MPH > 5:
              baseline.append(dev)
            continue

          alert_frames += 1
          if cur is None or t - last_alert_t > GAP_S:
            cur = {"route": route_of(path), "seg": segno(path), "t": t,
                   "devs": [], "unmeasurable": 0, "mph": [], "end": t}
            episodes.append(cur)
          last_alert_t = t
          cur["end"] = t
          cur["mph"].append(v * MPH)
          if dev is None:
            cur["unmeasurable"] += 1
          else:
            cur["devs"].append(dev)
      except Exception:
        continue

  return episodes, baseline, alert_frames, logged_events


def pct(xs, p):
  s = sorted(xs)
  return s[min(len(s) - 1, int(len(s) * p))] if s else float("nan")


def worst(ep):
  """The gate's own view: an unmeasurable frame forces the alert, so it outranks any distance."""
  if ep["unmeasurable"]:
    return float("inf")
  return max(ep["devs"]) if ep["devs"] else float("inf")


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("directory", nargs="+",
                  help="one or more pull directories; combine them to widen the sample")
  ap.add_argument("--route", action="append", help="limit to route(s)")
  ap.add_argument("--sweep", action="store_true", help="surviving episodes per threshold")
  ap.add_argument("--top", type=int, default=40, help="episodes to print (0 for all)")
  args = ap.parse_args()

  files = []
  for d in args.directory:
    files += glob.glob(os.path.join(d, "*.rlog.zst"))
  files.sort(key=segno)
  if args.route:
    files = [f for f in files if route_of(f) in set(args.route)]
  if not files:
    print("no matching segments")
    return

  episodes, baseline, alert_frames, logged_events = scan(files)
  routes = sorted({route_of(f) for f in files})

  print("=== STEERING-EXHAUSTED ALERTS, BY LANE POSITION ===")
  print(f"   {len(files)} segments, routes {', '.join(routes)}")
  print(f"   {len(episodes)} episodes over {alert_frames} alerting frames "
        f"(reconstructed at 100 Hz)")
  print(f"   {logged_events} raw onroadEvents samples -- LOWER BY DESIGN, that stream is logged "
        f"once a second or on change")
  print()
  if not episodes:
    print("   no steerSaturated events -- nothing to gate")
    return

  ranked = sorted(episodes, key=worst, reverse=True)
  shown_rows = ranked if args.top == 0 else ranked[:args.top]
  header = ("route", "seg", "dur s", "mph", "worst m", "med m", "unmeas")
  print(f"  {header[0]:>10}{header[1]:>5}{header[2]:>8}{header[3]:>7}"
        f"{header[4]:>10}{header[5]:>8}{header[6]:>8}")
  for ep in shown_rows:
    w = worst(ep)
    w_txt = "UNMEAS" if w == float("inf") else format(w, ".2f")
    med_txt = format(statistics.median(ep["devs"]), ".2f") if ep["devs"] else "--"
    print(f"  {ep['route']:>10}{ep['seg']:>5}{ep['end'] - ep['t']:>8.1f}"
          f"{statistics.median(ep['mph']):>7.1f}{w_txt:>10}{med_txt:>8}"
          f"{ep['unmeasurable']:>8}")
  if len(ranked) > len(shown_rows):
    print(f"  ... {len(ranked) - len(shown_rows)} more, all below "
          f"{worst(shown_rows[-1]):.2f} m")

  alerting = [d for ep in episodes for d in ep["devs"]]
  if alerting:
    print()
    print(f"  WHILE ALERTING                           n={len(alerting)}"
          f"  p50 {statistics.median(alerting):.2f}  p90 {pct(alerting, 0.90):.2f}"
          f"  p99 {pct(alerting, 0.99):.2f}  max {max(alerting):.2f} m")
  if baseline:
    print(f"  baseline (engaged, hands off, no alert)  n={len(baseline)}"
          f"  p50 {statistics.median(baseline):.2f}  p90 {pct(baseline, 0.90):.2f}"
          f"  p99 {pct(baseline, 0.99):.2f}  max {max(baseline):.2f} m")

  if args.sweep:
    print()
    print("=== HOW MANY ALERTS SURVIVE EACH THRESHOLD ===")
    print("   An episode survives if it EVER reaches the threshold, or was unmeasurable")
    print("   (unmeasurable fails open -- the gate shows the alert when it cannot see the lane).")
    print()
    ws = [worst(e) for e in episodes]
    print(f"  {'threshold m':>13}{'shown':>8}{'silenced':>10}{'shown %':>10}")
    for thr in SWEEP:
      shown = sum(1 for w in ws if w >= thr)
      mark = "  <- SHIPPED" if abs(thr - LANE_DEVIATION_M) < 1e-9 else ""
      print(f"  {thr:>13.2f}{shown:>8}{len(ws) - shown:>10}"
            f"{100.0 * shown / len(ws):>9.1f}%{mark}")
    print()
    print("  unmeasurable episodes (shown at EVERY threshold): "
          f"{sum(1 for w in ws if w == float('inf'))}")


if __name__ == "__main__":
  main()
