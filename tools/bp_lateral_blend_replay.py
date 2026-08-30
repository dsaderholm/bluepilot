"""Replay a recorded drive through BOTH the old and new blend logic. Read-only, off-device.

*"Can you show me a before and after with these changes?"* -- and the honest answer needs its limit
stated first:

  **THIS SHOWS WHAT THE COMMAND WOULD HAVE BEEN. IT CANNOT SHOW WHAT THE CAR DOES BACK.**

`actual` curvature is the PSCM physically responding, and no model of it exists here. So a
difference in these traces is a difference in what we ASK for, which is a necessary condition for a
behaviour change and not a sufficient one. That caveat is why this file exists as a tool rather than
as a claim.

What it replays, per frame, from the recorded modelV2 / controlsState / liveDelay:

    OLD  _kappa_entering  = kappa_at_t_base > abs(des)
         _desired_falling = abs(des) < abs(last) - 0.010
         b_blend          = b*0.25 if exit else b            (a SNAP)

    NEW  _kappa_entering  = abs(des) > 0.001 and kappa_at_t_base > abs(des)*1.25
         _desired_falling = abs(last) > 0.001 and abs(des) < abs(last)*0.8
         b_blend          = ramp 0.1/call toward b*0.25 (exit) / b*0.35 (straight) / b

and reports `requested = predicted*b_blend + desired*(1-b_blend)` under each.

`_pscm_lim` and `_dbc_sat` are not reconstructable from a route, and both are documented as not
firing in angle mode on this car -- so the exit branch here is driven by `_desired_falling` alone,
which is the one that changed. Stated because it bounds what the replay can claim.

    python tools/bp_lateral_blend_replay.py <dir> <route> <t_start> <t_end> [--out FILE]
"""
import glob
import json
import os
import sys

import capnp
import numpy as np
import zstandard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

MS_TO_MPH = 2.23694
DT_MDL = 0.05
B = 0.50
STEER_STEP = 5            # the angle path runs once per 5 control frames (20 Hz)
VLT_T_EXTRA_MAX = 0.10
VLT_V_LOW_MS, VLT_V_HIGH_MS = 25.0 * 0.44704, 55.0 * 0.44704
VLT_KAPPA_FULL, VLT_KAPPA_TAPER = 0.005, 0.020
T_IDXS = [0.0, 0.00976562, 0.0390625, 0.08789062, 0.15625, 0.24414062, 0.3515625,
          0.47851562, 0.625, 0.79101562, 0.9765625, 1.18164062, 1.40625, 1.65039062,
          1.9140625, 2.19726562, 2.5, 2.82226562, 3.1640625, 3.52539062, 3.90625,
          4.30664062, 4.7265625, 5.16601562, 5.625, 6.10351562, 6.6015625, 7.11914062,
          7.65625, 8.21289062, 8.7890625, 9.38476562, 10.0]


def seg_index(p):
  try:
    return int(os.path.basename(p).split("--")[2].split(".")[0])
  except (IndexError, ValueError):
    return -1


def main():
  d, route = sys.argv[1], sys.argv[2]
  t_lo, t_hi = float(sys.argv[3]), float(sys.argv[4])
  out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None

  files = sorted([f for f in glob.glob(os.path.join(d, "*.rlog.zst"))
                  if os.path.basename(f).split("--")[0] == route], key=seg_index)
  files = [f for f in files if (t_lo / 60 - 3) <= seg_index(f) <= (t_hi / 60 + 2)]

  frames = []
  t0 = None
  des = 0.0
  v = 0.0
  hands = False
  delay = 0.38
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
      mono = m.logMonoTime / 1e9
      if t0 is None or mono < t0:
        t0 = mono
      ts = mono - t0
      w = m.which()
      try:
        if w == "controlsState":
          des = float(m.controlsState.desiredCurvature)
        elif w == "carState":
          v = float(m.carState.vEgo)
          hands = bool(m.carState.steeringPressed)
        elif w == "liveDelay":
          delay = float(m.liveDelay.lateralDelay)
        elif w == "modelV2" and t_lo <= ts <= t_hi:
          oz = m.modelV2.orientationRate.z
          if len(oz) >= 17 and v > 5.0:
            frames.append((ts, des, np.array(oz, dtype=float) / max(0.01, v), v, hands, delay))
      except Exception:
        continue

  if not frames:
    print(json.dumps({"error": "no frames"}))
    return

  # modelV2 is 20 Hz, which is already the angle path's own cadence -- so one frame here is one call.
  rows = []
  last_old = last_new = 0.0
  b_new = B
  for ts, d_c, k, v_e, hd, dly in frames:
    tt = T_IDXS[:len(k)]
    t_base = min(max(dly, 0.1), 0.15) + DT_MDL
    kap_at_base = abs(float(np.interp(t_base, tt, k)))
    blend_base = min(max(dly, 0.1), 0.45) + DT_MDL * 1.5
    sf = float(np.interp(v_e, [VLT_V_LOW_MS, VLT_V_HIGH_MS], [1.0, 0.0]))

    ent_old = kap_at_base > abs(d_c)
    ent_new = abs(d_c) > 0.001 and kap_at_base > abs(d_c) * 1.25
    kf_old = 1.0 if ent_old else float(np.interp(abs(d_c), [VLT_KAPPA_FULL, VLT_KAPPA_TAPER], [1.0, 0.0]))
    kf_new = 1.0 if ent_new else float(np.interp(abs(d_c), [VLT_KAPPA_FULL, VLT_KAPPA_TAPER], [1.0, 0.0]))
    pred_old = float(np.interp(blend_base + VLT_T_EXTRA_MAX * sf * kf_old, tt, k))
    pred_new = float(np.interp(blend_base + VLT_T_EXTRA_MAX * sf * kf_new, tt, k))

    fall_old = abs(d_c) < abs(last_old) - 0.010
    fall_new = abs(last_new) > 0.001 and abs(d_c) < abs(last_new) * 0.8
    b_old = B * 0.25 if (not ent_old and fall_old) else B

    if not ent_new and fall_new:
      tgt = B * 0.25
    elif not ent_new and not fall_new and abs(d_c) < 0.00125:
      tgt = B * 0.35
    else:
      tgt = B
    b_new = min(tgt, b_new + 0.1) if tgt > b_new else max(tgt, b_new - 0.1)

    rows.append({
      "t": round(ts - frames[0][0], 2),
      "des": round(d_c * 1000, 3),
      "old": round((pred_old * b_old + d_c * (1 - b_old)) * 1000, 3),
      "new": round((pred_new * b_new + d_c * (1 - b_new)) * 1000, 3),
      "bo": round(b_old, 3), "bn": round(b_new, 3),
      "mph": round(v_e * MS_TO_MPH), "hands": hd,
    })
    last_old, last_new = d_c, d_c

  diffs = [abs(r["new"] - r["old"]) for r in rows]
  doc = {"route": route, "n": len(rows),
         "radius_m": round(1.0 / max(np.median([abs(r["des"]) for r in rows]) / 1000, 1e-6)),
         "max_abs_diff_per_km": round(max(diffs), 3),
         "frames_differing": sum(1 for x in diffs if x > 0.001),
         "points": rows}
  txt = json.dumps(doc)
  if out:
    open(out, "w", encoding="utf-8").write(txt)
    print("wrote %s  n=%d  median radius %d m  frames differing %d/%d  max diff %.3f 1/km"
          % (out, doc["n"], doc["radius_m"], doc["frames_differing"], doc["n"], doc["max_abs_diff_per_km"]))
  else:
    print(txt)


if __name__ == "__main__":
  main()
