#!/usr/bin/env python3
"""FusionPilot: publish a SCRIPTED route, so the route-intent gate can be exercised with no
transport fitted.

WHY THIS SHIPS WITH THE GATE RATHER THAN AFTER IT. `IcbmModelStopEnabled` was built, shipped, and
could not be turned on without SSH -- and he reported the feature as broken twice when it was
merely unenableable. The route-intent gate is a stronger version of the same hazard: it is inert by
construction until somebody else's software arrives, so without this it would sit in the tree for
weeks with nothing ever having driven it end to end.

WHAT IT CAN DO, IN FULL: cause passing assist to NOT suggest a pass. That is the whole blast radius,
because route intent may only refuse -- see `route_intent.py`. It cannot steer, cannot signal,
cannot touch the set speed and cannot open a maneuver, and `test_route_intent.py` parses the
detector and fails if any of that changes.

AND EVERY FRAME IT PUBLISHES IS LABELLED `stub`. A drive log that recorded a scripted route as
though a navigator had spoken would be worse than no instrument at all -- two populations read as
one is the shape of every denominator error in this fork's history. `RouteIntentBP.source` is a
different enumerant and any drive report can throw it out.

USAGE, on the device:

    cd /data/openpilot && PYTHONPATH=/data/openpilot /usr/local/venv/bin/python3 \
        tools/bp_route_intent_stub.py --approach

    # or a script of your own: seconds,maneuver,metres  (metres omitted = distance unknown)
    ... tools/bp_route_intent_stub.py --script "0,continueAhead 8,exitRight,700 20,exitRight,150"

Watch it work with `tools/bp_passing_report.py` afterwards, or live:

    ... -c "import cereal.messaging as m; s=m.sub_sock('longitudinalPlanSP'); ..."

It stops on its own. `--seconds` bounds the run and there is no unbounded mode, deliberately: a
diagnostic left running is a gate refusing passes for a reason nobody remembers switching on.
"""
from __future__ import annotations

import argparse
import sys
import time

# A whole approach to an exit, at the shape a real navigator produces: an active route saying
# nothing, then the instruction appearing at a distance, then counting down through the gate's
# bound. At 30 m/s the gate refuses from about 600 m, so the middle entry is outside it and the
# last two are inside -- which is what makes the transition visible rather than just the end state.
APPROACH = [
  (0.0, "continueAhead", None),
  (8.0, "exitRight", 900.0),
  (16.0, "exitRight", 500.0),
  (24.0, "exitRight", 150.0),
  (32.0, "exitRight", 20.0),
  (40.0, "continueAhead", None),
]

PUBLISH_HZ = 5.0


def parse_script(text: str):
  """`"0,continueAhead 8,exitRight,700"` -> the StubSource script form.

  A two-field entry means the distance is UNKNOWN, not zero. That is a real state a notification
  scraper reaches -- the glyph parses and the number does not -- and it is the one the consumer
  treats as no claim, so it has to be expressible here or the permissive branch can never be
  exercised on the bench.
  """
  out = []
  for chunk in text.split():
    parts = chunk.split(",")
    if len(parts) == 2:
      at, maneuver, distance = parts[0], parts[1], None
    elif len(parts) == 3:
      at, maneuver, distance = parts[0], parts[1], float(parts[2])
    else:
      raise SystemExit(f"bad script entry {chunk!r}: want seconds,maneuver[,metres]")
    out.append((float(at), maneuver, distance))
  return out


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--approach", action="store_true", help="the built-in exit approach")
  ap.add_argument("--script", default=None, help='"seconds,maneuver[,metres] ..."')
  ap.add_argument("--seconds", type=float, default=60.0, help="bounded on purpose; no unbounded mode")
  args = ap.parse_args()

  if not args.approach and not args.script:
    raise SystemExit("nothing to publish -- pass --approach or --script")

  try:
    import cereal.messaging as messaging
    from cereal import custom
    from openpilot.sunnypilot.routeintent.source import StubSource, fill_message
  except ImportError as e:  # noqa: BLE001
    sys.exit(f"needs the device environment ({e}); see the docstring for the interpreter to use")

  script = parse_script(args.script) if args.script else APPROACH
  names = {str(x) for x in custom.RouteIntentBP.Maneuver.schema.enumerants}
  # Checked here rather than let capnp raise mid-run: a typo in a maneuver name would otherwise
  # kill the publisher a few seconds in, having already put a refusal on the wire.
  bad = sorted({m for _, m, _ in script} - names)
  if bad:
    raise SystemExit(f"not maneuvers: {bad}\nknown: {sorted(names)}")

  src = StubSource(script)
  pm = messaging.PubMaster(['routeIntentBP'])

  print(f"# publishing {len(script)} scripted instruction(s) as source=stub for {args.seconds:.0f}s")
  print("# the ONLY thing this can do is stop passing assist suggesting a pass")
  for at, maneuver, distance in script:
    where = "distance unknown" if distance is None else f"{distance:.0f} m"
    print(f"#   t+{at:5.1f}  {maneuver:<14} {where}")

  t0 = time.monotonic()
  last = None
  while time.monotonic() - t0 < args.seconds:
    inst = src.poll()
    if inst is not None:
      msg = messaging.new_message('routeIntentBP')
      msg.valid = True
      fill_message(msg.routeIntentBP, inst, src.source)
      pm.send('routeIntentBP', msg)
      now = (inst.maneuver, inst.distance_m, inst.distance_known)
      if now != last:
        d = f"{inst.distance_m:.0f} m" if inst.distance_known else "distance unknown"
        print(f"t+{time.monotonic() - t0:5.1f}  {inst.maneuver:<14} {d}")
        last = now
    time.sleep(1.0 / PUBLISH_HZ)

  # NOT a final "no route" message. Silence is what the consumer is built to age out -- it reaches
  # the same verdict within MAX_INSTRUCTION_AGE_S -- and sending a tidy `none` on the way out would
  # test a path a crashed transport never takes.
  print("# done. The gate releases on its own once the last stamp ages out.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
