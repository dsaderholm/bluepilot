"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Ford ICBM (Intelligent Cruise Button Management) implementation.
"""

from opendbc.car import structs, DT_CTRL
from opendbc.car.can_definitions import CanData
from opendbc.sunnypilot.car.ford import fordcan_ext
from opendbc.sunnypilot.car.ford.gap_control import SIGNAL_DECREASE, SIGNAL_INCREASE, SIGNAL_TOGGLE
from opendbc.sunnypilot.car.intelligent_cruise_button_management_interface_base import IntelligentCruiseButtonManagementInterfaceBase
from openpilot.common.swaglog import cloudlog

ButtonType = structs.CarState.ButtonEvent.Type
SendButtonState = structs.IntelligentCruiseButtonManagement.SendButtonState

# Ford cruise control button signals for speed adjustment
# These correspond to the signals in the Steering_Data_FD1 CAN message (ID 131)
BUTTON_SIGNALS = {
  SendButtonState.increase: "CcAslButtnSetIncPress",  # Set + Increase button (speed up)
  SendButtonState.decrease: "CcAslButtnSetDecPress",  # Set + Decrease button (speed down)
}

# BluePilot: the physical gap buttons, watched so the driver's own press can be told from ours.
_DRIVER_GAP_SIGNALS = (SIGNAL_INCREASE, SIGNAL_DECREASE, SIGNAL_TOGGLE)


class IntelligentCruiseButtonManagementInterface(IntelligentCruiseButtonManagementInterfaceBase):
  """WARNING: `self` IS THE CARCONTROLLER, NOT AN INSTANCE OF THIS CLASS.

  ford/carcontroller.py calls `IntelligentCruiseButtonManagementInterface.update(self, ...)`
  class-style, and the line that would have constructed a real instance is commented out right
  above it. So this class is never instantiated, `__init__` never runs, and ANY attribute an
  __init__ here sets does not exist at runtime.

  Every attribute used below is therefore either assigned inside `update` itself or created by the
  CarController's own __init__ (`self.icbm_gap`). Adding `self.foo = ...` in an __init__ here reads
  as correct, passes review, and raises AttributeError on the first control frame -- which is what
  happened on 2026-08-15 and left the car undrivable.
  """

  def update(self, CC_SP, CS, packer, CAN, frame, last_button_frame) -> tuple[list[CanData], int]:
    """
    Update ICBM state and generate button press messages.

    Args:
      CC_SP: CarControlSP structure with ICBM commands
      CS: CarState with stock button values
      packer: CAN message packer
      CAN: Ford CAN bus configuration
      frame: Current frame number
      last_button_frame: Frame number of last button press

    Returns:
      Tuple of (can_sends list, updated last_button_frame)
    """
    can_sends = []
    self.CC_SP = CC_SP
    self.ICBM = CC_SP.intelligentCruiseButtonManagement
    self.frame = frame
    self.last_button_frame = last_button_frame

    # NO SUPPRESSION HERE. An ICBM stand-down while openpilot authors ACCDATA was added and fully
    # REVERTED on 2026-08-23 -- it froze the set speed wherever the camera latch caught it, and the
    # follow-up that suppressed only DOWNWARD presses was worse still, because down is the direction
    # an exit ramp needs. He reported being stuck at 25, 27 and 35 against SLA targets of 35, 35 and
    # 40, and then took an exit at 5.20 m/s^2 lateral.
    #
    # The parameter and its branch survived that revert with no caller, which left the dangerous
    # rule sitting here under a comment that read as though it were the reviewed design. Removed.
    #
    # THE CORRECT RULE IS HIS AND IS NOT IMPLEMENTED: move the set speed toward the DRIVER'S AIM
    # and stop there -- neither "always" nor "never" nor either direction. It needs the aim, which
    # lives in ICBM's units in selfdrived, and the carcontroller has no is_metric to compare
    # against it. Do not reach for a direction test again as a shortcut to it.
    preempted = self.ICBM.sendButton != SendButtonState.none
    if preempted:
      button_signal = BUTTON_SIGNALS[self.ICBM.sendButton]

      # Ford sends button messages at 10Hz (every 0.1s), but we send at 20Hz (every 0.05s) per CarControllerParams.BUTTONS_STEP
      # Only send if enough time has passed since last button press
      if (self.frame - self.last_button_frame) * DT_CTRL > 0.05:
        # Send button press to both camera and main bus (same as cancel/resume)
        can_sends.append(fordcan_ext.create_button_msg(packer, CAN.camera, CS.buttons_stock_values,
                                                     icbm_button=button_signal))
        can_sends.append(fordcan_ext.create_button_msg(packer, CAN.main, CS.buttons_stock_values,
                                                     icbm_button=button_signal))
        self.last_button_frame = self.frame

    # BluePilot: ACC follow-gap, second in line behind the set speed.
    #
    # The ordering is not arbitrary. The set speed is how ICBM slows the car for curves and leads,
    # and nothing about a follow distance may ever delay that.
    #
    # But the machine is TOLD it has been preempted rather than simply not being called. An earlier
    # version skipped the call entirely, on the theory that pausing kept the press shape intact. It
    # does the opposite: nothing holds the bit asserted while we stand down, the SCCM's own frames
    # keep arriving at the camera with the gap bits clear, and the resumed remainder lands as a
    # SECOND press -- two toggle steps for one intended, or a delta of 2 that the probe reads as
    # "incDec works" and latches for the drive. So a preempted press is abandoned and retried whole.
    gap_signal = self._update_gap(CS, preempted)
    if not preempted:
      if gap_signal is not None and (self.frame - self.last_button_frame) * DT_CTRL > 0.05:
        can_sends.append(fordcan_ext.create_button_msg(packer, CAN.camera, CS.buttons_stock_values,
                                                       icbm_button=gap_signal))
        can_sends.append(fordcan_ext.create_button_msg(packer, CAN.main, CS.buttons_stock_values,
                                                       icbm_button=gap_signal))
        self.last_button_frame = self.frame

    return can_sends, self.last_button_frame

  def _update_gap(self, CS, preempted: bool = False) -> str | None:
    """Read the camera's reported gap, feed the lease, and return the signal to press.

    Every failure to read returns 0, which the controller treats as "not readable" and refuses to
    start a lease on -- the requester asked explicitly that it not start blind, and a controller
    that presses without being able to see the result is the open-loop design this replaced.
    """
    # An exception raised from here does not disable a feature -- it propagates out of
    # CarController.update, through card's control loop, and stops the car being drivable. That
    # happened. So this whole path is caught and LATCHED OFF on any failure: a follow-distance
    # convenience must degrade to doing nothing, never to taking steering and cruise with it.
    if getattr(self, "icbm_gap_failed", True):
      return None

    try:
      try:
        gap_now = int(CS.acc_tja_status_stock_values["AccTGap_D_Dsply"])
      except (KeyError, TypeError, ValueError, AttributeError):
        gap_now = 0

      try:
        driver_pressing = any(CS.buttons_stock_values[s] for s in _DRIVER_GAP_SIGNALS)
      except (KeyError, TypeError, AttributeError):
        driver_pressing = False

      target = int(getattr(self.ICBM, "gapTarget", 0))
      was = (self.icbm_gap.mode, self.icbm_gap.last_result)
      signal = self.icbm_gap.update(gap_now, target, driver_pressing, preempted)

      # Log every transition, once. Whether the camera honours an injected gap press at all is the
      # one thing about this feature that cannot be settled offline, and the first real request is
      # the experiment -- so its outcome has to end up somewhere readable rather than only in the
      # controller's own state.
      if (self.icbm_gap.mode, self.icbm_gap.last_result) != was:
        cloudlog.warning("ICBM gap: mode=%s inverted=%s result=%s gap=%d target=%d",
                         self.icbm_gap.mode, self.icbm_gap.inverted, self.icbm_gap.last_result,
                         gap_now, target)
      return signal
    except Exception:  # noqa: BLE001 -- see above; this must never reach card
      self.icbm_gap_failed = True
      cloudlog.exception("ICBM gap control disabled for this drive")
      return None
