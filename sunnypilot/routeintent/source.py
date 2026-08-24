"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FusionPilot: ROUTE INTENT -- the producer side. What a transport has to implement, and nothing else.

THE WHOLE CONTRACT IS FOUR VALUES: a maneuver, a distance, whether the distance is real, and the
moment the instruction was last confirmed. A transport author's job is to fill in `poll()` and to
map their source's vocabulary onto `Maneuver`. Everything about staleness, bounds and what may be
refused lives on the consumer, in `route_intent.py`, so a second transport cannot arrive with a
second opinion about any of it.

WHY THE INTERFACE EXISTS BEFORE ANY TRANSPORT DOES. Two of the three candidates depend on other
people -- the canbox that would route MS-CAN is not fitted, and Waze does not expose a route to
third parties -- so the order they land in is not ours to choose. Sequencing the consumer behind
whichever one arrives first is how a feature waits on a bug report in somebody else's tracker.
Building against `StubSource` costs nothing and makes the three interchangeable.

THREE RULES, each of which is a bug this fork has already paid for
------------------------------------------------------------------
1. STAMP AT RECEIPT AND CACHE THE STAMP. `observed_mono_ns` is when YOUR TRANSPORT last confirmed
   the instruction, not when you are publishing. A bridge that re-stamps on every publish reports
   a dead WiFi link as a live route forever, and the consumer's freshness test -- the only thing
   standing between a stale instruction and a refused pass -- silently stops working.

2. A SOURCE THAT CANNOT MEASURE THE DISTANCE MUST NOT INVENT ONE. Set `distance_known=False` and
   leave the number alone. `RearApproachSide.from_blis` set `ttc = 0.0` for a sensor with no range
   and a car merely SITTING in the blind spot would have commanded an emergency abort at 50 Hz;
   the docstring said it could not happen while the code did it from the first line.

3. AN INSTRUCTION YOU CANNOT CLASSIFY IS `Maneuver.unknown`, NOT THE NEAREST LABEL AND NOT SILENCE.
   The consumer refuses on `unknown`, which is the conservative direction and free -- so a
   transport may ship before its classifier is complete, and a glyph nobody has seen before costs
   a pass rather than passing as `continueAhead`.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from cereal import custom

Maneuver = custom.RouteIntentBP.Maneuver
Source = custom.RouteIntentBP.Source


@dataclass(frozen=True)
class Instruction:
  """One navigation instruction, normalised.

  `maneuver` and `source` are the capnp ENUMERANT NAMES as strings -- 'exitRight', 'phoneBridge'.
  Strings rather than the integer values because that is what `str()` on a live capnp field gives,
  so a producer and a log read the same word, and because `int()` on a capnp enum raises outright
  (see test_no_int_on_capnp_enums.py).
  """
  maneuver: str = "none"
  distance_m: float = 0.0
  distance_known: bool = False
  observed_mono_ns: int = 0

  @staticmethod
  def nothing() -> Instruction:
    """No route active. Distinct from 'the transport is broken', which is a source publishing
    nothing at all -- the consumer ages that out and reaches the same verdict by a different
    route, and a log can tell them apart."""
    return Instruction(maneuver="none", observed_mono_ns=time.monotonic_ns())


class RouteIntentSource(ABC):
  """A transport. Implement `poll()`; the rest is plumbing.

  SOURCES DO NOT DECIDE ANYTHING. They do not know about passing assist, about the lookahead bound
  or about which maneuvers matter -- if a source ever grows a reason to suppress its own output,
  that reasoning belongs on the consumer where it can be read once instead of once per transport.
  """

  #: which KIND of transport this is -- a `Source` enumerant name.
  source: str = "none"

  @abstractmethod
  def poll(self) -> Instruction | None:
    """The current instruction, or None if there is nothing new to say this cycle.

    None is NOT 'no route' -- that is `Instruction.nothing()`, which carries a fresh stamp and
    tells the consumer the source is awake and the road is clear of maneuvers. Returning None
    leaves the last published instruction to age out on its own, which is what a transport that
    has simply not heard anything this cycle should do.
    """
    raise NotImplementedError


def fill_message(msg, instruction: Instruction, source: str) -> None:
  """Copy an Instruction onto a `routeIntentBP` sub-message.

  Here rather than in each transport, so the four fields cannot drift apart across sources -- and
  so `observedMonoTime` is written from the instruction rather than from the clock at publish time,
  which is rule 1 made mechanical instead of remembered.

  **YOU MUST ALSO SET `msg.valid = True` ON THE EVENT**, and this function cannot do it for you --
  it is handed the sub-message, not the event. SubMaster assigns `sm.valid[...]` straight from that
  field, and the consumer refuses anything not valid. So a transport that fills every field
  perfectly and forgets one line publishes into silence, with no error anywhere. It is the most
  likely way a new source fails on its first run.
  """
  msg.maneuver = instruction.maneuver
  msg.distance = float(instruction.distance_m)
  msg.distanceKnown = bool(instruction.distance_known)
  msg.source = source
  msg.observedMonoTime = int(instruction.observed_mono_ns)


class StubSource(RouteIntentSource):
  """A scripted route, for the bench and for tests. Publishes as `Source.stub`, always.

  THE SOURCE FIELD IS WHY THIS IS SAFE TO HAVE ON THE DEVICE. A drive log that recorded a scripted
  route as though a real navigator had spoken would be worse than no instrument at all -- it is the
  shape of every denominator error in this fork's history, where two populations were read as one.
  `stub` is a different enumerant and a drive report can throw it out.

  The script is a list of (elapsed_seconds, maneuver, distance_m_or_None). Each entry takes effect
  at its elapsed time and holds until the next, so a whole approach is a handful of lines:

      [(0.0, "continueAhead", None), (10.0, "exitRight", 600.0), (25.0, "exitRight", 100.0)]

  A distance of None means the source has the glyph and not the number -- which is a real state a
  notification scraper reaches, and it is the case the consumer treats as no claim.
  """

  source = "stub"

  def __init__(self, script, clock=time.monotonic):
    self.script = sorted(script, key=lambda e: e[0])
    self.clock = clock
    self.t0 = clock()

  def poll(self) -> Instruction | None:
    elapsed = self.clock() - self.t0
    entry = None
    for at, maneuver, distance in self.script:
      if elapsed + 1e-9 >= at:
        entry = (maneuver, distance)
      else:
        break
    if entry is None:
      return None
    maneuver, distance = entry
    # STAMPED NOW, and legitimately so: a stub genuinely does confirm its instruction every cycle,
    # because the script is the source. A transport reading a link must not copy this line.
    return Instruction(maneuver=maneuver,
                       distance_m=0.0 if distance is None else float(distance),
                       distance_known=distance is not None,
                       observed_mono_ns=time.monotonic_ns())
