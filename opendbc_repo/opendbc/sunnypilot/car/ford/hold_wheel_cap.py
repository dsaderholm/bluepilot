"""FusionPilot: keep the stop hold from winding the wheel past the turn the model asked for.

Route 000004bb, 2026-09-28, a right turn to a stop with FordLowSpeedAngleHold_ang at 10 mph. Hands
off throughout. `geo` is the wheel angle that turns the car on the curvature being commanded:

      mph    command   kappa    wheel    geo    wheel/geo
     12.5    0.048    0.0077    -15.3   -21.3     0.72     <- above the hold speed: wheel ~ geo
     10.3    0.038    0.0126    -32.1   -35.2     0.91
      8.2    0.069    0.0129    -44.1   -36.0     1.22     <- the speed term is floored from here
      5.8    0.102    0.0179    -63.0   -49.9     1.26
      3.9    0.096    0.0169    -80.1   -47.2     1.70
      2.4    0.104    0.0181   -119.1   -50.4     2.36
      0.0    0.110    0.0190   -130.3   -53.0     2.46     <- stopped, held there for four seconds

The command is flat and the wheel more than doubles. The hold keeps a PATH ANGLE, and the PSCM turns
a given path angle into more wheel the slower the car goes (its own lookahead shrinks with speed), so
flooring the speed term hands it the same number at a standstill that meant half the wheel at
10 mph. Above the hold speed the command's own `v_ego` factor cancels that and the wheel lands on
`geo`, which is why nothing like this happens in ordinary driving.

WHAT THIS DOES. While the speed term is floored, it measures the wheel against `geo` and trims the
command whenever the wheel is past it and still winding out. It only ever REMOVES command -- the
scale is at most 1, so the worst it can do is the controller as it already was.

THE TARGET IS THE BICYCLE MODEL, `steerRatio * atan(kappa * wheelbase)`, and it errs permissive on
purpose. Measured 2026-09-28 across 9,557 hands-off frames at 2-10 mph: the gyro's curvature over
the model's is 1.11-1.19 at every wheel angle from 15 to 540 degrees, so this car turns ~14% TIGHTER
than `geo` says. A target set exactly at the measured relation would cut low-speed turning the
moment the rack delivered what was asked; this one only trims wheel beyond what already
over-delivers, with a margin on top.

IT TRIMS ONLY WHILE THE WHEEL IS STILL WINDING. At a standstill the rack is sticky -- of the 15
hands-off holds on record that reached a standstill (routes 000004a0..bc), 10 kept the wheel under a
third of `geo` and only 3 went past 1.2x it -- so a wheel past the limit and not moving is left where
it is. Trimming there would push the scale down against a
rack that is not answering, and store up a step for the pull-away, which is the "goes back to the
middle and then tries to go back" this feature exists to remove.

RELEASE is a ramp, never a step: back toward 1 at RELEASE_RATE once the wheel is back under `geo`,
whenever the hold is not flooring the speed, and after the car leaves the hold band. A hold released
at 9 mph with the scale at 0.5 would otherwise double the command in one call.
"""
import math
from collections import deque

WHEEL_MARGIN_FRAC = 0.10   # on top of geo, which already errs ~14% permissive
WHEEL_MARGIN_DEG = 5.0     # so a gentle curve's few degrees of noise cannot start a trim
TRIM_GAIN = 2.0            # scale per second, per unit of fractional excess over the limit
TRIM_RATE_MAX = 0.6        # scale per second, the most it may come off
RELEASE_RATE = 0.4         # scale per second back toward 1
WINDING_DEG_S = 1.0        # outward faster than this, measured over RATE_WINDOW_CALLS, is winding
RATE_WINDOW_CALLS = 5      # 0.25 s at 20 Hz -- the angle is quantised at 0.1 deg, and a one-call
                           # rate would read that flicker as 2 deg/s of winding on a still wheel
SCALE_MIN = 0.3            # the most command it may take away, 70%


def geo_wheel_deg(kappa: float, steer_ratio: float, wheelbase: float) -> float:
  """Wheel angle (degrees, + LEFT) that turns the car on `kappa` (1/m, + RIGHT) -- see the note on
  signs in lateral_angle_ext above ANGLE_HOLD_MAX_MPH."""
  return -math.degrees(math.atan(kappa * wheelbase)) * steer_ratio


class HoldWheelCap:
  def __init__(self):
    self.scale = 1.0
    self._wheels = deque(maxlen=RATE_WINDOW_CALLS + 1)

  def reset(self):
    self.scale = 1.0
    self._wheels.clear()

  @property
  def trim(self) -> float:
    """Share of the command taken away, 0.0 when none -- what the route publishes."""
    return 1.0 - self.scale

  def _release(self, dt: float) -> float:
    self.scale = min(1.0, self.scale + RELEASE_RATE * dt)
    return self.scale

  def update(self, active: bool, pressed: bool, kappa_cmd: float, wheel_deg: float,
             steer_ratio: float, wheelbase: float, dt: float) -> float:
    """The factor to multiply the path-angle command by this call. `active` is the hold flooring
    the speed term; everything else releases."""
    if not (math.isfinite(kappa_cmd) and math.isfinite(wheel_deg) and steer_ratio > 0.0 and wheelbase > 0.0):
      return self._release(dt)
    self._wheels.append(wheel_deg)
    if not active:
      return self._release(dt)
    if pressed:
      # His hands are on the wheel; what it reads is not the rack answering the command.
      return self.scale

    target = geo_wheel_deg(kappa_cmd, steer_ratio, wheelbase)
    if wheel_deg * target <= 0.0:
      # The wheel is on the other side from the request, or straight: nothing to wind past.
      return self._release(dt)
    limit = abs(target) * (1.0 + WHEEL_MARGIN_FRAC) + WHEEL_MARGIN_DEG
    over = abs(wheel_deg) - limit
    if over > 0.0:
      # Past the limit AND still going: trim. Past it and sitting still is a sticky rack, and is left
      # alone -- trimming a rack that is not answering only stores up a step for the pull-away.
      winding = False
      if len(self._wheels) > RATE_WINDOW_CALLS:
        winding = (abs(wheel_deg) - abs(self._wheels[0])) / (RATE_WINDOW_CALLS * dt) > WINDING_DEG_S
      if winding:
        cut = min(TRIM_RATE_MAX, TRIM_GAIN * over / limit) * dt
        self.scale = max(SCALE_MIN, self.scale - cut)
      return self.scale
    # Between `geo` and the limit is a DEAD ZONE: nothing trims and nothing is given back. It is what
    # keeps a stopped car's wheel still. With the release set just 3 deg under the limit, the rack
    # model settled 3 deg under it, released, wound back out and trimmed again -- a slow +-7 deg
    # cycle at a standstill, which is the wheel moving at a red light. Command comes back only once
    # the wheel is under the turn asked for, which is what pulling away does as the PSCM's gain
    # falls with speed.
    if abs(wheel_deg) < abs(target):
      return self._release(dt)
    return self.scale
