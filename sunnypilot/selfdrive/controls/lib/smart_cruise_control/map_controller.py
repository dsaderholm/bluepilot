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
# Predicted lateral acceleration below this means the camera is looking at a straight road. Well
# under _ENTERING_PRED_LAT_ACC_TH (1.3) in vision_controller: this is not "is there a curve worth
# slowing for", it is "is there a curve at all".
MODEL_DISAGREE_LAT_ACC = 0.4

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
    self.last_position = coordinate_from_param("LastGPSPosition", self.mem_params) or Coordinate(0.0, 0.0)
    lat = self.last_position.latitude
    lon = self.last_position.longitude

    self.target_velocities = velocities_from_param("MapTargetVelocities", self.mem_params) or []

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
    horizon = self.v_ego * MODEL_HORIZON_S
    if target_distance_m > horizon or horizon <= 0:
      return False
    return self.model_lat_acc < MODEL_DISAGREE_LAT_ACC

  def update(self, long_enabled: bool, long_override: bool, v_ego, a_ego, v_cruise,
             model_lat_acc: float = 0.0) -> None:
    self.long_enabled = long_enabled
    self.long_override = long_override
    self.v_ego = v_ego
    self.a_ego = a_ego
    self.v_cruise = v_cruise

    self.update_params()
    self.update_calculations()

    self.is_enabled, self.is_active = self._update_state_machine()

    # BluePilot: the camera's veto, applied at the single output point rather than inside the state
    # machine -- the machine's own bookkeeping stays untouched, so a veto that lifts resumes cleanly
    # instead of having to re-enter from disabled. is_active is cleared too, so the SCC-M badge does
    # not claim to be acting while it is being overruled.
    self.model_lat_acc = model_lat_acc
    self.model_vetoed = bool(self.is_active and self._model_disagrees(self.target_distance))
    if self.model_vetoed:
      self.is_active = False

    self.output_v_target = self.get_v_target_from_control()
    self.output_a_target = self.get_a_target_from_control()

    self.frame += 1
