"""FusionPilot: read the rear radar DIGEST off bus 1.

WHAT THIS IS NOT. It is not a second RadarInterface and it does not produce tracks. The feeder
microcontroller has already reduced 64 detections at 33 Hz -- ~2150 frames/s, measured on the bench
2026-08-14 -- to the nearest closing target per side. This decodes three small messages.

WHY IT LIVES HERE rather than in card.py. Every line this fork adds to an upstream file is a merge
conflict paid on every future update, and `card` is upstream. So the logic is ours, in our own
directory, and card asks it two questions.

WHY IT IS NOT A SECOND RadarInterface, which is the tempting shape. `RadarInterface.pts` becomes
`liveTracks` wholesale, `liveTracks` becomes `radarState`, and `radarState` feeds unconfirmed_lead
-- which ICBM acts on by commanding the SET SPEED DOWN. A rear target reaching that path would brake
the car for a vehicle behind it with clear road ahead. Keeping this a separate object publishing a
separate message is the entire isolation, and test_rear_radar_isolation.py pins it.

WHERE THE OTHER HALF LIVES, because it is not obvious and was missed once: the feeder firmware is
`tools/rear_radar_feeder/rear_radar_feeder.ino` (Teensy 4.0, written and bench-tested 2026-08-14),
it MIRRORS `tools/bp_rear_digest_sim.py` which is the reference implementation, and that reduction
is unit tested in `selfdrive/car/tests/test_rear_digest_reduction.py`. The hardware reasoning --
part choice, mounting, why bus 1 cannot be shared -- is `BP-REAR-RADAR-PLAN.md` at the repo root,
whose sections 3 and 4 are STALE: they plan around an ESR that needs Vehicle_Data and SensorInput
to radiate, and the part is now an MRR that free-runs with no transmit path at all.

When the simulator and the firmware disagree, Python is right.
"""
from opendbc.can import CANParser

DBC_NAME = "bp_rear_radar"

# The feeder emits onto bus 1, the same bus the front radar already uses. It is 60-73% loaded, which
# is why the raw radar gets a PRIVATE bus and only the digest comes here: three messages at 20 Hz is
# 60 frames/s against the 2150 the sensor actually produces.
BUS = 1

MESSAGES = [
  ("RearRadarLeft", 20),
  ("RearRadarRight", 20),
  ("RearRadarStatus", 20),
]

# A digest older than this is not a clear road, it is a dead feeder. See RearApproach: unavailable
# must never read as safe. Generous against 20 Hz so a couple of dropped frames are not an outage.
MAX_STALE_S = 0.5

# Below this the feeder is seeing so few detection frames that the radar behind it is not healthy,
# whatever the digest says. The bench measured 33 Hz with the sensor idle and looking at nothing.
MIN_DETECTION_HZ = 10


class RearRadarParser:
  """Decodes the digest, or reports unavailable. Never raises into card."""

  def __init__(self, enabled: bool = True):
    self.enabled = bool(enabled)
    self.cp = None
    if self.enabled:
      try:
        self.cp = CANParser(DBC_NAME, MESSAGES, BUS)
      except Exception:  # noqa: BLE001 - a missing DBC must not stop the car starting
        self.cp = None

  @property
  def available(self) -> bool:
    return self.cp is not None

  def update(self, can_list) -> dict | None:
    """One frame. Returns what card publishes, or None when there is nothing to say.

    None is NOT 'clear'. It means no digest, and the consumer treats that as unavailable -- which is
    the whole reason this returns None rather than a zeroed structure that reads like an empty road.
    """
    if self.cp is None:
      return None
    try:
      self.cp.update_strings(can_list)
    except Exception:  # noqa: BLE001 - a malformed frame must not take down card
      return None

    alive = bool(self.cp.can_valid)
    status = self.cp.vl["RearRadarStatus"]
    detection_hz = int(status["DetectionHz"])

    def side(name):
      v = self.cp.vl[name]
      return {
        "detected": bool(v["Detected"]),
        "dRel": float(v["DRel"]),
        "yRel": float(v["YRel"]),
        "vRel": float(v["VRel"]),
        "targetCount": int(v["TargetCount"]),
      }

    return {
      # BOTH conditions. A feeder that keeps talking after its radar dies would otherwise report an
      # empty road forever, which is the failure this whole field exists to prevent.
      "dataAvailable": alive and bool(status["RadarAlive"]) and detection_hz >= MIN_DETECTION_HZ,
      "radarAlive": bool(status["RadarAlive"]),
      "detectionHz": min(255, detection_hz),
      "validDetections": min(255, int(status["ValidDetections"])),
      "left": side("RearRadarLeft"),
      "right": side("RearRadarRight"),
    }
