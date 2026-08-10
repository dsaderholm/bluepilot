"""
FusionPilot: run the detector for a long time on plausible-but-hostile road and see if it falls over.

Every other test here asserts a specific behavior on a scene built to produce it. None asks the
different question: given an hour of arbitrary driving, does anything raise, does any published
number go to NaN or infinity, does any counter run away, and is any share still a share.

That matters because this runs inside plannerd. An exception is not a wrong readout -- it is the
planner dying, and passing assist is not important enough to be able to do that.

THE SCENE HAS TO EVOLVE, NOT RE-ROLL, and that is the whole difficulty. The first version drew
every value fresh each second, ran a full hour, and exercised almost nothing: with the lead's speed
and presence resampled constantly, nothing ever persisted the two seconds a confirmation needs, so
wantedSeconds finished at zero and every assertion below passed over a machine that had never
started. Randomness alone is not coverage; it has to be randomness with continuity.

So the road is a random walk with occasional discrete events, and the tail of this file asserts the
run actually produced confirmations and suggestions -- because a soak that proves nothing looks
exactly like one that proves everything.

Deterministic: a fixed seed, so a failure is reproducible rather than a story about one run.
"""

import math
import random

from cereal import custom
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import PassingAssistDetector
from openpilot.sunnypilot.selfdrive.controls.lib.passing_assist import TIMELINE_MAX
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_passing_assist import (
  CRUISE_MS, make_sm, track, keep_right_det,
)

Side = custom.LongitudinalPlanSP.PassingAssist.Side


class Road:
  """A road that changes the way roads change: mostly drifting, occasionally something happens."""

  def __init__(self, rng):
    self.rng = rng
    self.v_ego = 31.0
    self.v_lead = 27.0
    self.d_rel = 120.0
    self.status = True
    self.left_bs = self.right_bs = False
    self.blinker = self.blinker_right = False
    self.curve = 0.0
    self.edges = (-6.0, 6.0)
    self.widen = 0.0
    self.tracks = []
    self.lead_accel = 0.0

  def step(self):
    r = self.rng
    self.v_ego = min(45.0, max(0.0, self.v_ego + r.gauss(0, 0.05)))
    self.v_lead = min(45.0, max(0.0, self.v_lead + r.gauss(0, 0.05)))
    self.d_rel = min(250.0, max(1.0, self.d_rel + (self.v_lead - self.v_ego) * DT_MDL))
    self.lead_accel = max(-6.0, min(3.0, self.lead_accel + r.gauss(0, 0.05)))

    if r.random() < 0.002:                    # a different car ahead
      self.v_lead, self.d_rel = r.uniform(0.0, 45.0), r.uniform(20.0, 240.0)
    if r.random() < 0.001:                    # radar loses or re-finds the lead
      self.status = not self.status
    if r.random() < 0.002:
      self.left_bs, self.right_bs = r.random() > 0.6, r.random() > 0.6
    if r.random() < 0.001:                    # the driver uses the stalk
      self.blinker, self.blinker_right = r.random() > 0.5, r.random() > 0.8
    elif r.random() < 0.02:
      self.blinker = self.blinker_right = False
    if r.random() < 0.001:                    # the road itself changes
      self.curve = r.choice([0.0, 0.0, 400.0, -400.0, 180.0, -180.0])
      self.edges = (r.uniform(-12.0, -1.5), r.uniform(1.5, 12.0))
      self.widen = r.choice([0.0, 0.0, 0.0, 4.0])
    if r.random() < 0.004:                    # traffic beside, and coming the other way
      self.tracks = [track(r.uniform(3.0, 200.0), r.uniform(-16.0, 16.0), r.uniform(-70.0, 30.0))
                     for _ in range(r.choice([0, 1, 1, 2, 4]))]

    return dict(
      v_lead=self.v_lead, v_ego=self.v_ego, d_rel=self.d_rel, status=self.status,
      left_bs=self.left_bs, right_bs=self.right_bs,
      blinker=self.blinker, blinker_right=self.blinker_right,
      curve=self.curve, edges=self.edges, right_edge_widen=self.widen,
      tracks=list(self.tracks), lead_accel=self.lead_accel, set_speed=CRUISE_MS,
      blis_avail=r.random() > 0.1, tsr_avail=r.random() > 0.1, edge_stds=(0.1, 0.1),
    )


FLOATS = ("confirmSeconds", "leadDRel", "speedDeficit", "referenceSpeed", "leadAccel",
          "crawlSeconds", "crawlLongestSeconds", "maneuverSeconds", "driverPassLeadSeconds",
          "missedDeficitMph", "accBrakingOnsetMax", "minApproachActive", "wantedSeconds",
          "topBlockedShare", "clearShare", "oncomingSeenSeconds", "longestIgnoredSeconds")

COUNTERS = ("maneuverAborts", "emergencyAborts", "keepRightAborts", "crawlEvents",
            "driverPasses", "driverPassesAgreed", "suggestionsMade", "suggestionsTaken",
            "lifetimeDrives", "lifetimePasses", "lifetimeAgreed")


def _published(det):
  msg = custom.LongitudinalPlanSP.new_message()
  det.publish(msg.passingAssist)
  pa = msg.passingAssist
  for name in FLOATS:
    v = float(getattr(pa, name))
    assert math.isfinite(v), f"{name} is {v}"        # capnp accepts NaN; the panel would show it
  for name in COUNTERS:
    assert 0 <= int(getattr(pa, name)) <= 65535, f"{name} out of range"
  assert 0.0 <= pa.topBlockedShare <= 1.0, f"topBlockedShare is {pa.topBlockedShare}"
  assert 0.0 <= pa.clearShare <= 1.0, f"clearShare is {pa.clearShare}"
  return pa


def test_an_hour_of_arbitrary_road():
  rng = random.Random(20260804)
  det = keep_right_det()
  road = Road(rng)
  for i in range(int(3600 / DT_MDL)):
    det.update(make_sm(**road.step()), CRUISE_MS, rng.random() > 0.02)
    if i % 2000 == 0:
      _published(det)
  pa = _published(det)

  # ...and the run has to have actually exercised something. Without this the test passes on a
  # machine that never started, which is precisely what the first version of it did.
  assert pa.wantedSeconds > 30.0, "an hour produced no confirmed slow lead; the road is too random"
  assert pa.suggestionsMade > 0, "never suggested a pass in an hour"


def test_the_same_seed_gives_the_same_answer():
  """If it does not, something here depends on wall-clock time or on iteration order, and no
  measurement it produces could be trusted across drives."""
  def once():
    rng = random.Random(7)
    det = PassingAssistDetector()
    road = Road(rng)
    for _ in range(4000):
      det.update(make_sm(**road.step()), CRUISE_MS, True)
    return (det.suggestion, det.blocked_by, round(det.approach_seconds, 6),
            det.driver_passes, round(det.missed_deficit_mph, 6), det.suggestions_made)
  assert once() == once()


def _containers(obj, depth=0):
  """Every list/dict/set reachable from the detector, with its length."""
  out = {}
  for name, v in vars(obj).items():
    if isinstance(v, (list, dict, set)):
      out[name] = len(v)
    elif hasattr(v, "__dict__") and depth < 2:
      for inner, n in _containers(v, depth + 1).items():
        out[f"{name}.{inner}"] = n
  return out


def test_nothing_grows_without_bound_over_a_long_drive():
  """The one failure class an hour of driving would not show as a crash.

  A list appended once per frame is 72,000 entries an hour. It would not raise, would not fail any
  assertion, and would quietly consume the device's memory on a long drive -- which is exactly the
  drive this feature is for. Nothing offline catches that except looking.

  Everything here is keyed by the Blocked enum, so it converges rather than grows. The bound is
  generous on purpose: the point is to catch per-frame accumulation, not to pin an exact size.
  """
  rng = random.Random(1)
  det = keep_right_det()
  road = Road(rng)
  for _ in range(int(120 / DT_MDL)):
    det.update(make_sm(**road.step()), CRUISE_MS, True)
  early = _containers(det)
  for _ in range(int(1800 / DT_MDL)):        # thirty more minutes
    det.update(make_sm(**road.step()), CRUISE_MS, True)
  late = _containers(det)

  assert late, "found no containers at all -- this test would pass on anything"
  # A container with a documented cap is checked against ITS cap rather than the blanket one, or
  # this test degenerates into "no ring buffer may be larger than 64". Everything else converges
  # because it is keyed by an enum; the timeline is a deliberate bounded ring and has to be allowed
  # to reach its own size -- and still caught if it ever stops being bounded.
  capped = {"_timeline": TIMELINE_MAX}
  for name, n in late.items():
    limit = capped.get(name, 64)
    assert n <= limit, f"{name} reached {n} entries against a cap of {limit}; something accumulates"
  # Fifteen times the frames must not mean fifteen times the size -- for the containers that
  # CONVERGE. A ring buffer does not converge, it fills: the timeline holds one entry per state
  # change, so more driving legitimately means more entries until it reaches its cap, and the cap
  # above is the check that matters for it.
  for name, n in late.items():
    if name in capped:
      continue
    assert n <= max(early.get(name, 0), 1) * 8, f"{name} grew {early.get(name, 0)} -> {n}"
