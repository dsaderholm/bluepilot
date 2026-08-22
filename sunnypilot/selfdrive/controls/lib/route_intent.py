"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: ROUTE INTENT -- the consumer. What his own navigator says he is about to do, and the
one thing this fork is allowed to do with it.

WHY THIS EXISTS AT ALL
----------------------
The map knows where every ramp is. It cannot know which one is HIS. That gap is why the `onRamp`
gate is REACTIVE -- it fires once mapd already places the car on a motorwayLink, which is after he
has moved -- and it is why a pass can still be offered four hundred metres before his exit.

A route closes it and nothing else on the car can. Measured rather than assumed: mapd's own
`waySelectionType == predicted` calls the fork correctly on 96-100% of resolutions and arrives with
a MEDIAN OF 1.0 SECONDS of lead against a budget of about eight. See `bluepilot/ROUTE-INTENT.md`
section 5a and `tools/bp_route_intent_score.py`. mapd is not doing badly -- predicting a fork early
needs the DESTINATION, which it does not have -- but it means the cheap answer is closed and the
instruction has to come from something that knows where he is going.

THE TRANSPORT IS DELIBERATELY NOT NAMED HERE
--------------------------------------------
Three candidates and two of them depend on other people:

  the car's own CAN     the APIM broadcasts route state -- the cluster draws turn-by-turn, so the
                        instruction crosses SOME bus. Not one openpilot reads today; pending the
                        canbox that BLIS is pending on. This is the target.
  a phone bridge        a navigation app on his phone, relayed to the device. Alive today: Waze's
                        own notification carries a maneuver glyph and a distance. The FALLBACK.
  an on-device router   a destination entered here, Mapbox-style. Nobody is asking for it.

So this class subscribes to `routeIntentBP` and never to a transport. A source is a thing that
publishes that message; see `sunnypilot/routeintent/source.py` for what one has to implement, and
`tools/bp_route_intent_stub.py` for a scripted one that needs no hardware.

AND IT MAY ONLY REFUSE
----------------------
The fork's rule -- EVIDENCE THAT OPENS A MANEUVER MUST NEVER BE CHEAPER THAN EVIDENCE THAT REFUSES
ONE -- decides the whole shape of this file, and it bites harder here than anywhere it has been
applied before, because every candidate source is somebody else's software running on somebody
else's schedule and reaching us over a link we do not control.

Read in the REFUSING direction, both failure modes are benign and neither is new:

    a source wrongly says "exit ahead"      a pass is not offered. Costs coverage.
    a source wrongly says nothing           the feature behaves exactly as it does today.

Read in the OPENING direction -- "his route goes left, so a left pass is fine" -- a stale
instruction moves the car. That version is not built and is not one line away from being built:
the only public predicate on this class is `refuses_pass`, there is no method that returns
permission, and `test_route_intent.py` parses this module and fails if one appears. Same shape as
`test_it_never_reads_fords_command` and `test_mapd_schema.py`, and for the same reason -- prose
saying "we must not" has already been outlived by the code twice in this fork.

The identical argument has now been made twice this week about signals that scored PERFECTLY on
questions they were not allowed to answer: `oneWay` would separate the false oncoming vetoes
cleanly, and the lane anchor would open the left gate on essentially every motorway frame. Both
are refused. This is the third.
"""
from __future__ import annotations

import time

from cereal import custom

Maneuver = custom.RouteIntentBP.Maneuver
Source = custom.RouteIntentBP.Source

# The two enumerants that are NOT a commitment. Everything else -- including `unknown`, and
# including any enumerant a later transport author adds -- refuses.
#
# The DEFAULT DIRECTION matters more than the list. A new maneuver type appearing in the schema and
# silently NOT refusing is the failure that cannot be seen in a log; a new one refusing costs a
# pass and shows up as `routeManeuver` in blockedBy. So the test is written as "not in this set"
# rather than "in the set of committing maneuvers", and the set kept is the short one.
NO_CLAIM_MANEUVERS = frozenset({"none", "continueAhead"})

# HOW LONG AN INSTRUCTION MAY GO UNCONFIRMED BEFORE IT MEANS NOTHING.
#
# At 31 m/s (70 mph) three seconds is 93 m the source has not corrected, against a refusal bound
# with several hundred metres of slack in it -- so the bound absorbs the error and the gate needs
# no extrapolation. It must not have any, either: extrapolating a distance while the source is
# silent is inventing evidence, which is the thing this file exists not to do. Stale means NO
# CLAIM, and no claim returns the feature to exactly today's behaviour.
MAX_INSTRUCTION_AGE_S = 3.0

# HOW FAR BEFORE THE MANEUVER TO GO QUIET. A TIME, converted at the current speed, because a pass
# is a fixed-duration maneuver and a fixed distance means twenty-seven seconds of silence at 25 mph
# and nine at 75.
#
# Derived rather than measured, and stated so it can be corrected rather than inherited:
#
#   ~15 s   the commanded pass itself, suggestion to back-in-lane. That figure is already in
#           passing_assist.py, where LIMIT_DROP_LOOKAHEAD_M is reasoned from it.
#   ~5 s    getting back across to the lane the exit is on, and settling there.
#
# 20 s at 70 mph is about 620 m, which is long -- deliberately. The limit-drop gate chose 250 m
# over 300 because a limit change is on the horizon most of the time on an arterial and refusing
# at the full pass length would go quiet constantly. THIS GATE HAS THE OPPOSITE ECONOMICS: it fires
# only for maneuvers on HIS OWN ROUTE, which is one or two per trip, so a generous bound costs
# almost nothing. That asymmetry is the whole reason route intent is worth having over the map.
#
# Wants fitting from drive data the day a transport lands -- against how often it goes quiet, and
# whether a pass offered inside the window was one he would have made.
LOOKAHEAD_S = 20.0

# ...and a floor, so the bound does not collapse toward zero at low speed. 150 m is roughly one
# pass at the minimum speed the feature runs at.
LOOKAHEAD_MIN_M = 150.0


class RouteIntent:
  """Reads `routeIntentBP` and answers exactly one question: is a maneuver close enough to refuse?

  Everything else on this class is a diagnostic. There is no `allows`, no `ok` and no `clear`, and
  adding one is a test failure rather than a code review conversation.
  """

  def __init__(self):
    self.maneuver = "none"
    self.source = "none"
    self.distance_m = 0.0
    self.distance_known = False
    self.age_s = 0.0
    # Did a USABLE instruction reach us this frame? Not "is the transport alive" -- a bridge that
    # keeps republishing a dead link's last instruction is alive and is saying nothing.
    self.available = False

  def reset(self) -> None:
    self.__init__()

  def update(self, sm, now_ns: int | None = None) -> None:
    """Take this frame's instruction, or none.

    `now_ns` is injectable for tests and for replay. On the car it is `time.monotonic_ns()`, the
    same clock `cereal.messaging` stamps `logMonoTime` with.

    AGE IS MEASURED TO NOW, NOT TO THE MESSAGE'S SEND TIME, and that is the whole point of the
    stamp. SubMaster holds the last message it received forever, so a publisher that died an hour
    ago still presents a perfectly well-formed frame; and a phone bridge whose link has died keeps
    SENDING fresh messages carrying a minutes-old instruction. Neither is distinguishable from a
    live route by anything except a stamp taken at RECEIPT, which is why the schema carries one and
    why nothing here consults sm.alive for freshness.
    """
    self.reset()

    try:
      if not (sm.alive['routeIntentBP'] and sm.valid['routeIntentBP']):
        return
      msg = sm['routeIntentBP']
      maneuver = str(msg.maneuver)
      source = str(msg.source)
      observed = int(msg.observedMonoTime)
      distance_known = bool(msg.distanceKnown)
      distance = float(msg.distance)
    except (KeyError, AttributeError, TypeError, ValueError):
      return

    # A source that did not stamp has not said WHEN, and an instruction with no when is not
    # evidence. Zero is also the capnp default, so this is what an unset field reads as -- the case
    # that would otherwise sail through looking like an instruction issued in 1970.
    if observed <= 0:
      return

    now = time.monotonic_ns() if now_ns is None else int(now_ns)
    age = (now - observed) / 1e9
    # A stamp in the FUTURE is a clock disagreement, not a fresh instruction. Refusing to believe it
    # is free; believing it would make an arbitrarily old instruction look arbitrarily fresh.
    if age < 0.0 or age > MAX_INSTRUCTION_AGE_S:
      return

    self.maneuver = maneuver
    self.source = source
    self.distance_m = distance
    self.distance_known = distance_known
    self.age_s = age
    self.available = True

  def refuses_pass(self, v_ego: float) -> bool:
    """Is his next maneuver close enough that starting a pass now is the wrong move?

    THE DISTANCE-UNKNOWN CASE IS PERMISSIVE, and it is the one place this differs from the
    conservative default everywhere else in the file. A maneuver with no distance carries no bound,
    so refusing on it would go quiet for the ENTIRE ROUTE rather than for the approach -- which is
    not a conservative version of this gate, it is a different and much worse feature. The schema
    lets a source say "a turn is coming and I do not know how far" precisely so it does not have to
    invent a number, and the price of that honesty is paid here.
    """
    if not self.available or self.maneuver in NO_CLAIM_MANEUVERS or not self.distance_known:
      return False
    # A negative distance is a source bug, not a maneuver already behind us -- and either way there
    # is no bound in it. Zero IS a real reading: the maneuver is upon us.
    if self.distance_m < 0.0:
      return False
    return self.distance_m <= max(LOOKAHEAD_MIN_M, float(v_ego) * LOOKAHEAD_S)
