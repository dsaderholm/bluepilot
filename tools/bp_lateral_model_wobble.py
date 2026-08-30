"""Is the wobble the model's, and would averaging its PATH remove it? Read-only, off-device.

Established already: the plan oscillates ~4.7 s on a steady bend, the command follows it exactly,
the car follows the command 0.31 s late, and the plan does NOT react to the tracking error (r=+0.09)
-- so the oscillation originates in the model, not in a loop through the car.

That leaves one question that decides whether anything can be done in THIS repo:

  1. Does the model's own output reach the command unmodified? `controlsState.desiredCurvature` is
     `modelV2.action.desiredCurvature` through `clip_curvature`, which applies an ISO jerk rate
     limit. A rate limiter inside an oscillating loop can itself sustain the oscillation, so this
     has to be measured rather than reasoned about -- at 75 mph the limit works out to 0.0046 1/m/s
     against a fastest observed plan change of 0.0018, i.e. it should never bind. Confirm it.

  2. `action.desiredCurvature` is a POINT SAMPLE of the model's predicted path at one instant
     (lat_action_t ahead). The model also publishes the whole path. If the path is smooth in SPACE
     but the point sample is noisy in TIME, then averaging the path over a short window around the
     sample point removes the wobble while keeping the road -- and `lateral_angle_ext` already
     interpolates that exact array, so it is a few lines in code this fork owns.

     This compares, frame by frame:
         point   = interp(t_sample, T_IDXS, kappa)          what is used today
         window  = mean of kappa over t_sample +- WINDOW_S  the candidate
     and reports how much each wobbles in time while the road is genuinely curving. If `window` is
     much steadier at the same mean, the fix is real. If both wobble equally, the model's whole
     path is moving and averaging along it buys nothing.

    python tools/bp_lateral_model_wobble.py <dir-of-rlog.zst> <route> [max_segments]
"""
import glob
import os
import statistics
import sys

import capnp
import numpy as np
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
T_IDXS = [0.0, 0.00976562, 0.0390625, 0.08789062, 0.15625, 0.24414062, 0.3515625,
          0.47851562, 0.625, 0.79101562, 0.9765625, 1.18164062, 1.40625, 1.65039062,
          1.9140625, 2.19726562, 2.5, 2.82226562, 3.1640625, 3.52539062, 3.90625,
          4.30664062, 4.7265625, 5.16601562, 5.625, 6.10351562, 6.6015625, 7.11914062,
          7.65625, 8.21289062, 8.7890625, 9.38476562, 10.0]
SAMPLE_T = 0.468           # lat_action_t on his car
WINDOW_S = 0.45            # +- this around the sample point
MIN_KAPPA = 8e-4


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def wobble(x):
  """Mean |frame-to-frame change|, the thing that becomes steering movement."""
  x = np.asarray(x)
  return float(np.mean(np.abs(np.diff(x)))) if len(x) > 2 else float("nan")


def main():
  d, route = sys.argv[1], sys.argv[2]
  maxseg = int(sys.argv[3]) if len(sys.argv) > 3 else 40
  files = sorted([f for f in glob.glob(os.path.join(d, "*.rlog.zst"))
                  if os.path.basename(f).split("--")[0] == route], key=seg_index)[:maxseg]

  pts, wins, model_act, ctrl_des = [], [], [], []
  v_now = 0.0
  hands = False
  lat = False
  last_action = None
  for p in files:
    try:
      with open(p, "rb") as fh:
        raw = zstandard.ZstdDecompressor().stream_reader(fh).read()
      evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
    except Exception:
      continue
    while True:
      try:
        m = next(evs)
      except StopIteration:
        break
      except Exception:
        break
      w = m.which()
      try:
        if w == "carControl":
          lat = bool(m.carControl.latActive)
        elif w == "carState":
          cs = m.carState
          v_now = float(cs.vEgo)
          hands = bool(cs.steeringPressed)
        elif w == "controlsState" and last_action is not None:
          ctrl_des.append(float(m.controlsState.desiredCurvature))
          model_act.append(last_action)
          last_action = None
        elif w == "modelV2":
          mv = m.modelV2
          try:
            last_action = float(mv.action.desiredCurvature)
          except Exception:
            last_action = None
          oz = mv.orientationRate.z
          if len(oz) < 17 or v_now < 17.9 or hands or not lat:
            continue
          k = np.array(oz, dtype=float) / max(0.01, v_now)
          tt = np.array(T_IDXS[:len(k)])
          point = float(np.interp(SAMPLE_T, tt, k))
          if abs(point) < MIN_KAPPA:
            continue
          sel = (tt >= SAMPLE_T - WINDOW_S) & (tt <= SAMPLE_T + WINDOW_S)
          if sel.sum() < 3:
            continue
          pts.append(point)
          wins.append(float(np.mean(k[sel])))
      except Exception:
        continue

  print("=== %s : IS THE WOBBLE THE MODEL'S, AND DOES AVERAGING ITS PATH HELP? ===" % route)
  print()
  if len(ctrl_des) > 500:
    a = np.array(model_act)
    c = np.array(ctrl_des)
    n = min(len(a), len(c))
    diff = np.abs(a[:n] - c[:n])
    print("  1. DOES clip_curvature MODIFY THE MODEL'S OUTPUT?")
    print("     paired frames: %d   median |action - controlsState|: %.8f 1/m" % (n, statistics.median(diff)))
    print("     frames differing by >1e-6: %.2f%%" % (100.0 * float((diff > 1e-6).mean())))
    if float((diff > 1e-6).mean()) < 0.02:
      print("     -> clip_curvature is NOT binding. The command IS the model's own number.")
    else:
      print("     -> it IS modifying the output; the rate limiter is a live suspect.")
    print()

  print("  2. POINT SAMPLE vs PATH-AVERAGED, on curving road, hands off")
  if len(pts) < 500:
    print("     insufficient qualifying frames (%d)" % len(pts))
    return
  wp, ww = wobble(pts), wobble(wins)
  print("     qualifying model frames: %d" % len(pts))
  print("     mean |frame-to-frame change|   point %.7f   window +-%.2fs %.7f" % (wp, WINDOW_S, ww))
  if wp > 0:
    print("     averaging the path changes the wobble by %+.1f%%" % (100.0 * (ww - wp) / wp))
  print("     median magnitude              point %.6f   window %.6f"
        % (statistics.median([abs(x) for x in pts]), statistics.median([abs(x) for x in wins])))
  print()
  if wp > 0 and ww < 0.75 * wp:
    print("     -> The path IS smoother than the point sample. Averaging along it is a real fix and")
    print("        lateral_angle_ext already interpolates this exact array.")
  else:
    print("     -> The whole path moves together, so averaging along it buys nothing. The model is")
    print("        re-planning the road each frame, and no smoothing WITHIN one frame can fix that.")


if __name__ == "__main__":
  main()
