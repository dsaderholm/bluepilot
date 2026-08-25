"""FusionPilot: does Ford really slow the car WITHOUT the friction brakes?

`bp_ford_brake_curve.py` said Ford asserts its brake bits only 7-43% of the time between -0.2 and
-2.7 m/s^2, with a dip to 7% around -2.0. If that is true it is the whole reason Ford ACC feels
better than op long, because openpilot asserts its own brake at -0.14 unconditionally.

BUT THE BITS MIGHT NOT BE THE STORY. `AccBrkTot_A_Rq` goes to ABS_ESC directly, so the ABS may act
on the VALUE whether or not the request bits are set -- in which case "bit not asserted" would not
mean "no friction braking" and the whole finding evaporates.

THE BRAKE LAMP IS GROUND TRUTH. `carStateBP.brakeLightStatus.brakeLightsOn` is read off the car,
and the struct's own comment says precharge "pressurises the system without meaningful deceleration
and normally lights nothing". So the lamp is what actually-braking looks like.

Per bucket of Ford's commanded acceleration this reports:

  LAMP        the fraction of frames where the friction brakes genuinely engaged. THE ANSWER.
  decel bit   AccBrkDecel_B_Rq, for comparison with the earlier tool
  prop        Ford's MEAN AccPrpl_A_Rq -- how hard it is leaning on the powertrain. If the
              hierarchy is real this goes sharply negative exactly where the lamp stays dark.

DRIVER BRAKING IS EXCLUDED. A lamp lit by his own foot says nothing about what the ACC chose, and
including it would manufacture the opposite of the finding.

    python tools/bp_ford_decel_hierarchy.py 000003bb 000003bc 000003bd 000003be
"""
import os
import sys
from collections import defaultdict

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402

OUR_BRAKE_TARGET = -0.14
OUR_MIN_GAS = -0.5
BUCKET = 0.1
LO, HI = -3.5, 0.2


def seg_index(n):
  try:
    return int(n.rsplit("--", 1)[1])
  except Exception:
    return -1


def main(routes):
  n_tot = defaultdict(int)
  n_lamp = defaultdict(int)
  n_decel = defaultdict(int)
  prop_sum = defaultdict(float)
  prop_n = defaultdict(int)
  skipped_driver = 0

  cruise_on = False
  brake_pressed = False
  v_ego = 0.0

  for route in routes:
    segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
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
          cruise_on = m.carState.cruiseState.enabled
          brake_pressed = m.carState.brakePressed
          v_ego = m.carState.vEgo
          continue
        if w != "carStateBP":
          continue
        bls = m.carStateBP.brakeLightStatus
        if not bls.accDataAvailable or not bls.dataAvailable:
          continue
        if not cruise_on or v_ego < 2.0:
          continue
        if brake_pressed:
          skipped_driver += 1
          continue

        accel = float(bls.accAccelRequest)
        if not (LO <= accel <= HI):
          continue
        b = round(accel / BUCKET) * BUCKET
        n_tot[b] += 1
        n_lamp[b] += bool(bls.brakeLightsOn)
        n_decel[b] += bool(bls.accDecelRequest)
        prop = float(bls.accPropulsionRequest)
        if abs(prop + 5.0) >= 0.005:      # skip the inactive sentinel
          prop_sum[b] += prop
          prop_n[b] += 1

  total = sum(n_tot.values())
  if not total:
    print("no usable frames -- is brakeLightStatus populated on these routes?"); return

  print(f"\n{total} frames of Ford ACC driving, above 2 m/s, driver's foot OFF the brake")
  print(f"({skipped_driver} frames excluded because he was braking himself)\n")
  print(f"{'Ford cmd':>9}  {'frames':>7}  {'LAMP ON':>8}  {'decel bit':>10}  {'mean prop':>10}")
  print("-" * 56)
  lamp_cross = None
  for b in sorted(n_tot, reverse=True):
    n = n_tot[b]
    if n < 25:
      continue
    lamp = 100.0 * n_lamp[b] / n
    dec = 100.0 * n_decel[b] / n
    prop = (prop_sum[b] / prop_n[b]) if prop_n[b] else float('nan')
    if lamp_cross is None and lamp >= 50.0:
      lamp_cross = b
    print(f"{b:>9.1f}  {n:>7}  {lamp:>7.1f}%  {dec:>9.1f}%  {prop:>10.2f}")

  print("-" * 56)
  print(f"  openpilot asserts its brake at {OUR_BRAKE_TARGET:+.2f} m/s^2, always")
  if lamp_cross is not None:
    print(f"  FORD's brake LAMP crosses 50% at {lamp_cross:+.2f} m/s^2")
    print(f"  -> openpilot brakes {abs(lamp_cross - OUR_BRAKE_TARGET):.2f} m/s^2 earlier than Ford")
  else:
    print("  Ford's brake lamp never crossed 50% in any populated bucket")
  print("\n  'mean prop' is Ford's AccPrpl_A_Rq. Where it goes below "
        f"{OUR_MIN_GAS} m/s^2, Ford is asking the\n  powertrain for deceleration openpilot cannot request at all.")


main(sys.argv[1:])
