import json
import math
import platform

import numpy as np

from cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.navd.helpers import coordinate_from_param, Coordinate
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import MIN_V

# FusionPilot: the mapped-corner factor is blended across the CORNER's own speed, not the car's.
# That distinction is the whole point. A loop ramp is a 25 mph corner entered at 75, and a highway
# sweeper is a 50 mph corner entered at 75 -- identical ego speed, opposite requirements, so keying
# the blend on vEgo cannot tell them apart. Vision's pair keys on vEgo because it is regulating
# lateral acceleration continuously; this one is picking a number for a specific corner.
#
# Measured on route 00000338 at t+796 on 2026-08-10, which is why this exists: the map's own number
# for a highway bend was 48 mph, a single global factor of 90 asked for 43, and the owner overrode
# with the accelerator and took the bend at 51 mph pulling 2.9 m/s^2 without difficulty. The same 90
# was set on 2026-08-08 for the opposite reason -- a ramp his retrofit PSCM wanted taken at 20 rather
# than the advisory speed. Both reports are correct and one knob could not serve them.
# Band top is 45 mph, deliberately BELOW the 48 mph bend that prompted this. Inside the band a
# 48 mph corner still gets 99% of the tight factor, which is the old behaviour with extra steps;
# above it the map's own number stands, which is what the measurement says it should.
_MAP_FACTOR_V_BP = [11.18, 20.12]  # m/s, 25-45 mph on the CORNER speed

MapState = VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState

ACTIVE_STATES = (MapState.turning, )
ENABLED_STATES = (MapState.enabled, MapState.overriding, *ACTIVE_STATES)

R = 6373000.0  # approximate radius of earth in meters
TO_RADIANS = math.pi / 180
TO_DEGREES = 180 / math.pi
TARGET_JERK = -0.6  # m/s^3 There's some jounce limits that are not consistent so we're fudging this some
TARGET_ACCEL = -1.2  # m/s^2 should match up with the long planner limit
# BluePilot: TARGET_ACCEL above is the shipped default, and it is BOTH knobs at once -- how hard
# SCC-Map slows AND how early it starts. The trigger test is "am I within the distance needed to
# reach the corner speed at this deceleration", so a gentler target lengthens that distance and the
# cycle begins further out. There is no separate earliness control to add, unlike SCC-Vision where
# threshold and magnitude really are independent.
#
# Worked example, 70 mph into a 30 mph loop ramp: at -1.2 m/s^2 the required distance is ~330 m; at
# -0.8 it is ~500 m. On a ramp with a straight first section that difference decides whether the
# set speed starts falling while you can still do something gentle about it.
#
# The value that matters for the stop lamps is 1.3 m/s^2 (UN R13-H). The stock -1.2 sits just under
# it deliberately. Going past 1.3 here is asking for the brake lights, which is sometimes the right
# trade on a tight ramp and should be a decision rather than an accident.

# BluePilot: cross-check the map's curve against what the camera actually sees.
#
# SCC-Map slows from OSM way geometry. When that geometry is wrong -- a road since straightened, a
# badly drawn bend, or GPS placing the car on the frontage road instead of the freeway -- it slows
# for a curve that is not there, and nothing can stop it: both controllers feed a min() in the
# planner, so either may LOWER the target and neither can veto the other. SCC-Vision detecting no
# curve is not a "no", it is silence, and min() ignores silence.
#
# So the veto has to live here. It only applies once the curve is close enough for the camera to
# have a real opinion -- beyond that, "the model sees nothing" means "not yet", not "nothing is
# there", and vetoing on it would disable the one thing SCC-Map is for: seeing around a bend the
# camera cannot.
#
# 4 s, not the model's full 10 s plan. Reported from a drive that exits were not slowing enough.
#
# The model PLANS 10 s ahead, but that is not the same as being able to see a curve 10 s away. On a
# freeway approaching an exit the camera is looking down the freeway, not around the ramp -- so it
# reports a straight road and the veto fired. SCC-Map triggers 500 m out at -0.8 m/s^2, and a 10 s
# horizon is 313 m at 70 mph, so the veto was suppressing the map for the entire final approach.
#
# That is this failsafe defeating the exact case SCC-Map exists for, which is the risk named when it
# was written and then not bounded tightly enough. At 4 s the veto only reaches 125 m -- close
# enough that the camera genuinely has the curve in frame, and a phantom curve right in front of you
# is still caught.
MODEL_HORIZON_S = 4.0
# ...but only for corners slow enough to be a RAMP. A highway-speed mapped corner gets the model's
# real reach, and the difference decides whether the veto can fire at all.
#
# max_pred_lat_acc is a 97th percentile over the WHOLE modelV2 plan, which runs to 10 s -- about
# 330 m at 74 mph. The 4 s bound was therefore never a statement about what the camera can see; it
# was a bound on a number already summarizing far more road. At highway speed it makes the veto
# unreachable: SCC-Map's trigger distance for 74 -> 52 mph at -1.2 m/s^2 is ~232 m, always outside a
# 132 m gate, so the map commits before the veto is ever allowed to look.
#
# Measured on route 0000033c, 2026-08-11, the drive that prompted this. At t+142, the frame the map
# committed to 52 mph, predicted lateral acceleration was 0.24 -- under MODEL_DISAGREE_LAT_ACC, so
# the camera was already saying there is no curve here. The set speed then fell 75 -> 52 in four
# seconds on a road measuring 0.00-0.24 m/s^2, and the owner overrode with the accelerator. The real
# bend arrived much later and peaked at 2.0, which at his measured comfort needs no slowing at all.
#
# THE SPLIT IS THE SAFETY ARGUMENT. On a real exit the model predicts the path it expects to drive,
# which is straight down the highway, so a ramp's curvature may never enter the plan until the car is
# on it -- vetoing there would disable the one thing SCC-Map is for. That risk belongs to RAMPS, and
# a ramp is a slow corner. A mapped corner of 45 mph or more is a highway bend, on the road the model
# is already predicting, so its silence is evidence rather than blindness.
MODEL_HORIZON_HIGH_SPEED_S = 10.0  # matches modelV2's T_IDXS span
# Predicted lateral acceleration below this means the camera is looking at a straight road. Well
# under _ENTERING_PRED_LAT_ACC_TH (1.3) in vision_controller: this is not "is there a curve worth
# slowing for", it is "is there a curve at all".
MODEL_DISAGREE_LAT_ACC = 0.4
# The camera seeing SOMETHING is not the camera agreeing. Measured on route 00000348 t+1510,
# 2026-08-11: the map demanded 50 mph on an 80 mph freeway, predicted lateral acceleration was 1.22 --
# comfortably over the "no curve at all" threshold above, so the absolute test says nothing -- and the
# owner overrode and held 65-70 through the bend without difficulty. Vision's own target across that
# stretch was 84-98 mph. Two controllers disagreeing by a factor of two, with the map winning
# unopposed.
#
# So the second test asks what the MODEL'S OWN VIEW implies. Its predicted peak lateral acceleration
# at the current speed gives a curvature, and that curvature gives the speed at which the bend would
# reach a normal lateral-acceleration budget. If the map is demanding far less than that, the map is
# not describing the road the camera is looking at.
#
# _A_LAT_REG_MAX_REF is upstream's 2.0 deliberately, NOT the owner's tuned curve factors. This is a
# question about the road, not about his taste, and it must not move when he retunes comfort.
_A_LAT_REG_MAX_REF = 2.0
MODEL_IMPLIED_SPEED_FRACTION = 0.75

SCC_MAP_DECEL_MIN = 0.4   # m/s^2, magnitude. Gentler than this and the trigger distance is absurd.
SCC_MAP_DECEL_MAX = 2.5
TARGET_OFFSET = 1.0  # seconds - This controls how soon before the curve you reach the target velocity. It also helps
                     # reach the target velocity when inaccuracies in the distance modeling logic would cause overshoot.
                     # The value is multiplied against the target velocity to determine the additional distance. This is
                     # done to keep the distance calculations consistent but results in the offset actually being less
                     # time than specified depending on how much of a speed differential there is between v_ego and the
                     # target velocity.


def velocities_from_param(param: str, params: Params):
  if params is None:
    params = Params()

  json_str = params.get(param)
  if json_str is None:
    return None

  velocities = json.loads(json_str)

  return velocities


def calculate_accel(t, target_jerk, a_ego):
  return a_ego + target_jerk * t


def calculate_velocity(t, target_jerk, a_ego, v_ego):
  return v_ego + a_ego * t + target_jerk/2 * (t ** 2)


def calculate_distance(t, target_jerk, a_ego, v_ego):
  return t * v_ego + a_ego/2 * (t ** 2) + target_jerk/6 * (t ** 3)


# points should be in radians
# output is meters
def distance_to_point(ax, ay, bx, by):
  a = math.sin((bx-ax)/2)*math.sin((bx-ax)/2) + math.cos(ax) * math.cos(bx)*math.sin((by-ay)/2)*math.sin((by-ay)/2)
  c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

  return R * c  # in meters


class SmartCruiseControlMap:
  v_target: float = 0
  a_target: float = 0.
  v_ego: float = 0.
  a_ego: float = 0.
  output_v_target: float = V_CRUISE_UNSET
  output_a_target: float = 0.

  def __init__(self):
    self.params = Params()
    self.mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else self.params
    self.enabled = self.params.get_bool("SmartCruiseControlMap")
    self.target_accel = TARGET_ACCEL
    self.model_lat_acc = 0.0
    self.model_vetoed = False   # logged so a missing slowdown can be explained rather than guessed
    self.camera_not_seen = False  # the second veto, kept apart from the first so a drive can tell
    self.target_distance = float('inf')
    self.map_factor = 1.0
    self.map_high_speed_factor = 1.0
    self.long_enabled = False
    self.long_override = False
    self.is_enabled = False
    self.is_active = False
    self.state = MapState.disabled
    self.v_cruise = 0
    self.target_lat = 0.0
    self.target_lon = 0.0
    self.frame = -1

    self.last_position = coordinate_from_param("LastGPSPosition", self.mem_params) or Coordinate(0.0, 0.0)
    self.target_velocities = velocities_from_param("MapTargetVelocities", self.mem_params) or []
    # Declared HERE, not just in update(). An attribute that only exists once some other method has
    # run is the exact shape of the bug that made the car undrivable on 2026-08-15 -- `self.gap` on
    # the Ford CarController, set in a method that was never called. update_calculations() is
    # reachable without update() and the suite proved it within a minute of this being missing.
    self.mapd_v2_path: tuple | None = None

  def get_v_target_from_control(self) -> float:
    if self.is_active:
      return max(self.v_target, MIN_V)

    return V_CRUISE_UNSET

  def get_a_target_from_control(self) -> float:
    return self.a_ego

  def update_params(self):
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("SmartCruiseControlMap")
      # BluePilot: stored as tenths of m/s^2, magnitude. Negated here because every use below wants
      # the signed deceleration, and clipped so a bad param cannot make the trigger distance
      # infinite (which would read as "SCC-Map never fires") or slam the brakes.
      decel = self.params.get("SmartCruiseControlMapDecel", return_default=True) / 10.
      # BluePilot: the magnitude knob, applied to the corner speed itself. Scaling here rather than
      # at the output means the trigger distance is computed against the speed we will actually ask
      # for, so a lower factor also starts earlier -- which is the physically consistent pairing.
      self.map_factor = self.params.get("SmartCruiseControlMapFactor", return_default=True) / 100.
      # Deliberately NOT a rename of the key above. His stored 90 already means what he chose it to
      # mean -- tight ramps slower than the advisory -- so leaving it as the low-speed end preserves
      # that with no migration, and the new key fixes only the case that was measured wrong.
      self.map_high_speed_factor = self.params.get("SmartCruiseControlMapHighSpeedFactor",
                                                   return_default=True) / 100.
      self.target_accel = -min(max(decel, SCC_MAP_DECEL_MIN), SCC_MAP_DECEL_MAX)

  def update_calculations(self) -> None:
    # FusionPilot: same two inputs, either source. Everything below this point -- the walk, the
    # trigger arithmetic, the corner-speed factor pair and all four defenses -- is untouched by the
    # migration and MUST stay that way for now. Each defense was bought with a measured event on
    # these roads, and re-deriving them against a full profile is a separate job to do WITH drive
    # data, not the same afternoon as the source swap. Change one thing at a time on a car.
    if self.mapd_v2_path is not None:
      self.last_position, self.target_velocities = self.mapd_v2_path
    else:
      self.last_position = coordinate_from_param("LastGPSPosition", self.mem_params) or Coordinate(0.0, 0.0)
      self.target_velocities = velocities_from_param("MapTargetVelocities", self.mem_params) or []

    lat = self.last_position.latitude
    lon = self.last_position.longitude

    if self.last_position is None or self.target_velocities is None:
      return

    min_dist = 1000
    min_idx = 0
    distances = []

    # find our location in the path
    for i in range(len(self.target_velocities)):
      target_velocity = self.target_velocities[i]
      tlat = target_velocity["latitude"]
      tlon = target_velocity["longitude"]
      d = distance_to_point(lat * TO_RADIANS, lon * TO_RADIANS, tlat * TO_RADIANS, tlon * TO_RADIANS)
      distances.append(d)
      if d < min_dist:
        min_dist = d
        min_idx = i

    # only look at values from our current position forward
    forward_points = self.target_velocities[min_idx:]
    forward_distances = distances[min_idx:]

    # find velocities that we are within the distance we need to adjust for
    valid_velocities = []
    for i in range(len(forward_points)):
      target_velocity = forward_points[i]
      tlat = target_velocity["latitude"]
      tlon = target_velocity["longitude"]
      tv = target_velocity["velocity"]
      if tv > self.v_ego:
        continue

      d = forward_distances[i]

      a_diff = (self.a_ego - self.target_accel)
      accel_t = abs(a_diff / TARGET_JERK)
      min_accel_v = calculate_velocity(accel_t, TARGET_JERK, self.a_ego, self.v_ego)

      max_d = 0
      if tv > min_accel_v:
        # calculate time needed based on target jerk
        a = 0.5 * TARGET_JERK
        b = self.a_ego
        c = self.v_ego - tv
        t_a = -1 * ((b**2 - 4 * a * c) ** 0.5 + b) / 2 * a
        t_b = ((b**2 - 4 * a * c) ** 0.5 - b) / 2 * a
        if not isinstance(t_a, complex) and t_a > 0:
          t = t_a
        else:
          t = t_b
        if isinstance(t, complex):
          continue

        max_d = max_d + calculate_distance(t, TARGET_JERK, self.a_ego, self.v_ego)
      else:
        t = accel_t
        max_d = calculate_distance(t, TARGET_JERK, self.a_ego, self.v_ego)

        # calculate additional time needed based on target accel
        t = abs((min_accel_v - tv) / self.target_accel)
        max_d += calculate_distance(t, 0, self.target_accel, min_accel_v)

      if d < max_d + tv * TARGET_OFFSET:
        valid_velocities.append((float(tv), tlat, tlon, d))

    # Find the smallest velocity we need to adjust for
    min_v = 100.0
    target_lat = 0.0
    target_lon = 0.0
    # BluePilot: how far away the chosen curve is, kept so the model cross-check knows whether the
    # camera could even see it yet.
    self.target_distance = float('inf')
    for tv, lat, lon, d in valid_velocities:
      if tv < min_v:
        min_v = tv
        target_lat = lat
        target_lon = lon
        self.target_distance = d

    if self.v_target < min_v and not (self.target_lat == 0 and self.target_lon == 0):
      for i in range(len(forward_points)):
        target_velocity = forward_points[i]
        tlat = target_velocity["latitude"]
        tlon = target_velocity["longitude"]
        tv = target_velocity["velocity"]
        if tv > self.v_ego:
          continue

        if tlat == self.target_lat and tlon == self.target_lon and tv == self.v_target:
          return

      # not found so let's reset
      self.v_target = 0.0
      self.target_lat = 0.0
      self.target_lon = 0.0

    self.v_target = min_v * self._factor_for_corner(min_v)
    self.target_lat = target_lat
    self.target_lon = target_lon

  def _factor_for_corner(self, corner_v: float) -> float:
    """FusionPilot: blend the tight-corner and highway-corner factors across the corner's own speed."""
    return float(np.interp(corner_v, _MAP_FACTOR_V_BP,
                           [self.map_factor, self.map_high_speed_factor]))

  def _update_state_machine(self) -> tuple[bool, bool]:
    # ENABLED, TURNING
    if self.state != MapState.disabled:
      if not self.long_enabled or not self.enabled:
        self.state = MapState.disabled
      elif self.long_override:
        self.state = MapState.overriding

      else:
        # ENABLED
        if self.state == MapState.enabled:
          if self.v_cruise > self.v_target != 0:
            self.state = MapState.turning

        # TURNING
        elif self.state == MapState.turning:
          if self.v_cruise <= self.v_target or self.v_target == 0:
            self.state = MapState.enabled

        # OVERRIDING
        elif self.state == MapState.overriding:
          if not self.long_override:
            if self.v_cruise > self.v_target != 0:
              self.state = MapState.turning
            else:
              self.state = MapState.enabled

    # DISABLED
    elif self.state == MapState.disabled:
      if self.long_enabled and self.enabled:
        if self.long_override:
          self.state = MapState.overriding
        else:
          self.state = MapState.enabled

    enabled = self.state in ENABLED_STATES
    active = self.state in ACTIVE_STATES

    return enabled, active

  def _model_disagrees(self, target_distance_m: float) -> bool:
    """Does the camera see a straight road where the map claims a curve?

    Only meaningful inside the model's own horizon. Outside it the model has nothing to say and
    silence must not be read as disagreement.
    """
    # _MAP_FACTOR_V_BP[1] is the same 45 mph line that separates ramps from highway bends for the
    # corner-speed factors. One definition, used by both, so they cannot drift apart.
    #
    # A LOW CORNER SPEED IS ONLY A RAMP IF WE ARE ARRIVING AT HIGHWAY SPEED. Added 2026-08-26.
    #
    # The conservative 4 s horizon exists for one reason, stated in `_camera_has_not_seen_it`: on an
    # exit the model predicts the path it expects to drive -- straight down the highway -- so a
    # ramp's curvature may never enter its plan, and camera silence there is BLINDNESS rather than
    # evidence. That argument is about approaching a ramp AT SPEED. It says nothing about a 12 mph
    # corner claimed on a surface street the car is already crawling along, and applying it there
    # made the veto unreachable on exactly the frames that needed it.
    #
    # MEASURED, routes 000003c8 and 000003c9, 2026-08-26 -- four phantom corners and one real one:
    #
    #     t+1074  v_ego 21 mph   target 13   R_map  19 m   R_drove 422 m   22.3x too tight
    #     t+1085  v_ego 33 mph   target 13   R_map  19 m   R_drove 422 m   22.3x
    #     t+ 289  v_ego 29 mph   target 12   R_map  16 m   R_drove 306 m   19.0x
    #     t+ 300  v_ego 25 mph   target 14   R_map  22 m   R_drove 442 m   20.1x
    #     t+ 785  v_ego 71 mph   target 21   R_map  49 m   R_drove 122 m   map agrees  <- REAL
    #
    # The phantoms arrive at 21-33 mph; the one genuine corner at 71. `v_ego` separates them
    # cleanly, and it is the same quantity the ramp argument is really about.
    #
    # Worked, t+289: braking 29 -> 12 mph at 0.8 m/s^2 is 87 m, against a 4 s horizon of 52 m. The
    # gate returned False and the map walked the target to 12 unchallenged. At the model's real
    # 10 s reach the horizon is 130 m, the corner is inside it, and the camera gets ASKED.
    #
    # THIS MAKES THE VETO REACHABLE; IT DOES NOT GUARANTEE IT FIRES. Whether it does depends on
    # `model_lat_acc` against MODEL_DISAGREE_LAT_ACC (0.4), and the lateral acceleration these
    # corners actually produced was 0.44-0.86 -- close enough to the threshold that some may still
    # pass. Publishing `modelLatAcc` is what would settle it and is deliberately NOT bundled here.
    ramp_approach = self.v_target < _MAP_FACTOR_V_BP[1] and self.v_ego >= _MAP_FACTOR_V_BP[1]
    horizon = self.v_ego * (MODEL_HORIZON_S if ramp_approach else MODEL_HORIZON_HIGH_SPEED_S)
    if target_distance_m > horizon or horizon <= 0:
      return False

    # 1. The camera sees no curve at all.
    if self.model_lat_acc < MODEL_DISAGREE_LAT_ACC:
      return True

    # 2. The camera sees a curve, but a far gentler one than the map is describing. Highway corners
    #    only -- a ramp keeps the conservative bound, because there the model may legitimately not
    #    have the corner in its plan at all and its silence is blindness rather than evidence.
    #
    #    Vetoing here is not "ignore the corner": it removes only the MAP's contribution, and
    #    SCC-Vision goes on running as the near-field expert. So a highway bend the map overstated
    #    degrades to camera-based curve control, which is the thing that handles bends you can see.
    #    EXTENDED TO SLOW ROADS 2026-08-26, the same correction the horizon gate needed. `ramp_like`
    #    means "a low corner speed", which is a RAMP only if we are ARRIVING at highway speed --
    #    see the block above. On a surface street the camera has the bend in frame and its opinion
    #    is evidence, not blindness.
    #
    #    THIS IS THE PIECE THAT CATCHES THE FOURTH PHANTOM. Route 000003c9 t+289: the camera saw
    #    0.46-0.68 m/s^2, which CLEARS the "no curve at all" test above (0.4) -- so defense 2
    #    correctly declines, because there genuinely was some curvature. But at v_ego 13.3 m/s that
    #    reading implies a road safely takeable at 38-46 mph, while the map was demanding 28. A
    #    factor of 20 in radius, and the camera is the one looking at it.
    #
    #        model_lat_acc 0.46 -> implied_ok_v 27.7 m/s, veto below 20.8 m/s (46 mph)
    #        model_lat_acc 0.68 -> implied_ok_v 22.8 m/s, veto below 17.1 m/s (38 mph)
    #
    #    The map asked 12.4 m/s. It fires on the most conservative reading, not just the best one.
    if ramp_approach or self.v_ego <= 0:
      return False
    implied_ok_v = self.v_ego * math.sqrt(_A_LAT_REG_MAX_REF / max(self.model_lat_acc, 1e-3))
    return self.v_target < implied_ok_v * MODEL_IMPLIED_SPEED_FRACTION

  def _camera_has_not_seen_it(self, target_distance_m: float) -> bool:
    """A HIGHWAY corner the camera cannot see yet must not be acted on unchallenged.

    Measured on route 00000365, 2026-08-12. The map commanded 50 mph on an I-215 sweeper and walked
    the set speed 79 -> 64 before anything questioned it. Both camera vetoes were unreachable, and
    not by a narrow margin -- `_model_disagrees` returns False at its distance gate before either
    test runs, because SCC-Map publishes the corner speed exactly when braking must BEGIN:

        braking 79 -> 50 mph at 0.8 m/s^2   = 467 m
        model horizon at 79 mph             = 353 m

    So the corner is 114 m beyond the camera's reach at the moment it is acted on. That gate is
    right -- silence from a camera that cannot see the corner is not evidence -- but returning False
    let the map act anyway, which made the veto structurally unreachable rather than merely quiet.

    The dead band is wide. The veto can only be reached when the braking distance fits inside the
    horizon, `(v1^2 - v2^2) / 2a <= 10 * v1`, which at 79 mph means corners of 58 mph or faster; and
    it is disabled below 45 mph as ramp-like. **So at 79 mph it could only ever protect corners
    between 58 and 79 mph**, and 45-58 mph -- the band that produces the largest slowdowns -- had no
    protection at all.

    So a highway corner waits until the camera can see it. The cost is bounded and small: braking
    from 79 to 50 within 353 m needs 1.06 m/s^2 rather than 0.8, which ICBM can deliver (its set
    speed falls at 3.3 mph/s, about 1.5 m/s^2). What it buys is that the camera gets asked at all.

    **RAMPS ARE DELIBERATELY EXEMPT AND MUST STAY THAT WAY.** On an exit the model predicts the path
    it expects to drive -- straight down the highway -- so a ramp's curvature may never enter its
    plan until the car is on it. Waiting there would delay the one case that already has too little
    room; see "THE EXIT THAT NEVER SLOWS ENOUGH" in CLAUDE.md. Same 45 mph line as everything else.
    """
    # NO CORNER, NO QUESTION. `target_distance` initialises to inf and `v_target` is unset when the
    # map has nothing to say, and both comparisons below are TRUE against inf -- so without this
    # guard the answer is "not seen yet" on every frame the map is idle, which is most of a drive.
    # That is not merely wrong, it is a state that flips continuously the whole time longitudinal is
    # engaged, and it is exactly the window a comma 4 owner reported trouble in. `_model_disagrees`
    # never had this problem because its distance gate returns False for an unreachable corner;
    # this check inverts that gate, so it has to establish there IS a corner first.
    if not math.isfinite(target_distance_m) or not math.isfinite(self.v_target):
      return False
    if self.v_target >= _MAP_FACTOR_V_BP[1]:
      horizon = self.v_ego * MODEL_HORIZON_HIGH_SPEED_S
      return horizon > 0 and target_distance_m > horizon
    return False

  def update(self, long_enabled: bool, long_override: bool, v_ego, a_ego, v_cruise,
             model_lat_acc: float = 0.0, mapd_v2_path=None) -> None:
    self.long_enabled = long_enabled
    self.long_override = long_override
    self.v_ego = v_ego
    self.a_ego = a_ego
    self.v_cruise = v_cruise
    # None means "use v1". Passed in rather than read here so this class keeps taking its inputs
    # from its caller instead of growing a second opinion about which map source is live.
    self.mapd_v2_path = mapd_v2_path

    self.update_params()
    self.update_calculations()

    self.is_enabled, self.is_active = self._update_state_machine()

    # BluePilot: the camera's veto, applied at the single output point rather than inside the state
    # machine -- the machine's own bookkeeping stays untouched, so a veto that lifts resumes cleanly
    # instead of having to re-enter from disabled. is_active is cleared too, so the SCC-M badge does
    # not claim to be acting while it is being overruled.
    self.model_lat_acc = model_lat_acc
    # Two separate claims, deliberately not merged: the camera looked and saw something gentler, or
    # the camera has not been able to look yet. Both suppress the map here; only the first is the
    # model disagreeing, and folding them into one predicate makes the log unreadable.
    #
    # FusionPilot: EVALUATED SEPARATELY AND KEPT SEPARATELY, 2026-08-25. The comment above says the
    # two claims are "deliberately not merged" and then merged them into one bool with `or`, which
    # short-circuits -- so a drive could never say which one fired, and on 2026-08-25 that left a
    # real report ("dropped to 20 for no reason") attributable to SCC-Map but not explainable.
    # Both are published now. Note `or` also meant `_camera_has_not_seen_it` was never evaluated
    # whenever `_model_disagrees` was true, so it could not even be logged from inside itself.
    disagrees = bool(self.is_active and self._model_disagrees(self.target_distance))
    self.camera_not_seen = bool(self.is_active and self._camera_has_not_seen_it(self.target_distance))
    self.model_vetoed = disagrees or self.camera_not_seen
    if self.model_vetoed:
      self.is_active = False

    self.output_v_target = self.get_v_target_from_control()
    self.output_a_target = self.get_a_target_from_control()

    self.frame += 1
