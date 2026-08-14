"""FusionPilot: what stock Ford ACC is doing, read in one place for both screens.

Companion to icbm_hud_state.py, and for the same reason: the big screen and the comma 4 have
separate renderer trees with no shared base class, so porting the ACC pill invites a second copy of
the classification below. The DRAWING has to be written twice -- 536x240 is not a scaled 2160x1080
-- but deciding whether the car is accelerating, coasting or braking must not be.

The classification is not obvious and the ordering carries meaning:

  1. `accDecelRequest` WINS OUTRIGHT. It means the pads are being used, whatever else is requested.
  2. `accPropulsionRequest` above the deadband is ACCEL. `accAccelRequest` cannot tell accelerating
     from coasting on its own -- despite the name it is AccBrkTot_A_Rq, the BRAKE total.
  3. NEGATIVE propulsion is engine braking: closed throttle and a downshift, no friction brakes.
     Whether stock ACC ever uses this channel is UNVERIFIED -- fordcan.py suggests the PCM may
     decide to downshift on its own and this signal may never go negative. Showing it is how that
     gets settled.
  4. Precharge is NOT braking. It pressurises the system so a later application arrives without
     slack: no meaningful deceleration, no stop lamps, no pad wear. Counting it as BRAKE overstated
     how often the friction brakes were doing anything, which is the number worth trusting when the
     goal is to use the pads as little as possible. It keeps its own state because ACC precharging
     means it expects to brake shortly, which is worth seeing coming.

ACC_DEADBAND exists because ACC trims constantly at small values; with no deadband the readout
never sits still. ACC_PROPULSION_INACTIVE is the "no request" sentinel, not a -5 m/s^2 request --
opendbc sends INACTIVE_GAS = -5.0 whenever longitudinal is off, and without this the pill would sit
on ENG BRAKE permanently.
"""
from __future__ import annotations

from dataclasses import dataclass

ACC_DEADBAND = 0.15             # m/s^2; below this a propulsion request reads as coasting
ACC_PROPULSION_INACTIVE = -4.5  # m/s^2; at or below this the signal carries no request at all


@dataclass
class AccHudState:
  """What ACC is asking for. Defaults are "nothing to report"."""

  state: str = ""      # "" | ACCEL | COAST | ENG BRAKE | PRE-BRAKE | BRAKE
  accel: float = 0.0   # m/s^2 magnitude behind that state, 0 when the state has none

  @property
  def has_state(self) -> bool:
    return bool(self.state)


def read_acc_hud_state(sm) -> AccHudState:
  """Current ACC request from `carStateBP`, or the empty default if it is unavailable.

  Never raises: a HUD that throws takes the whole on-road screen with it. Reads the stock ACCDATA
  the camera sends, so this is FORD's own request even though openpilot is not the longitudinal
  controller -- which is exactly why it is worth showing.
  """
  out = AccHudState()
  try:
    if not sm.valid['carStateBP']:
      return out
    bls = sm['carStateBP'].brakeLightStatus
    if not (bls.accDataAvailable and sm['carState'].cruiseState.enabled):
      return out

    if bls.accDecelRequest:
      out.state, out.accel = "BRAKE", bls.accAccelRequest
    elif bls.accPropulsionRequest > ACC_DEADBAND:
      out.state, out.accel = "ACCEL", bls.accPropulsionRequest
    elif ACC_PROPULSION_INACTIVE < bls.accPropulsionRequest < -ACC_DEADBAND:
      out.state, out.accel = "ENG BRAKE", abs(bls.accPropulsionRequest)
    elif bls.accPrechargeRequest:
      out.state, out.accel = "PRE-BRAKE", 0.0
    else:
      out.state, out.accel = "COAST", 0.0
  except Exception:  # noqa: BLE001 -- see docstring; a HUD must not raise
    return AccHudState()
  return out
