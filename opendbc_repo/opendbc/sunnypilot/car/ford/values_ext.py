"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from collections import namedtuple

from opendbc.car import structs
from opendbc.car.docs_definitions import CarParts, Device
from opendbc.car.ford.values import CAR
from opendbc.car.lateral import AngleSteeringLimits

ButtonType = structs.CarState.ButtonEvent.Type
Button = namedtuple('Button', ['event_type', 'can_addr', 'can_msg', 'values'])

# Ford cruise control buttons are in the Steering_Data_FD1 message (CAN ID 131)
# These signals are 1-bit flags: 1 = pressed, 0 = not pressed
#
# Note: Some buttons are combo buttons that emit multiple ButtonEvent types, chosen by cruise state.
#
# BluePilot: CcAslButtnSetIncPress emitted setCruise when cruise was off. On this wheel that signal
# comes from the button labelled "RES +" -- confirmed, because it is the signal the ICBM press path
# captures when the owner builds a hold with the + side. Pressing RES to resume was therefore
# reported to openpilot as SET, which is the one event that DISCARDS the driver's hold.
#
# THAT "CONFIRMED" WAS CIRCULAR AND WRONG, and it cost weeks. 2026-08-20, measured off the wire:
# ICBM's press path captures a hold when the SET SPEED MOVES, via the idle-adopt fallback -- it does
# not need a button event at all. So "ICBM captured a hold" was never evidence about which signal
# the wheel sends, and nobody had looked.
#
# Rising edges of each signal on Steering_Data_FD1, bus 0 -- the DRIVER side, with ICBM's own
# injections excluded (those land on bus 128/130 as TX echoes):
#
#     route 0000039d    CcAslButtnSetIncPress  0     CcAslButtnResIncPress  6
#     route 0000039f    CcAslButtnSetIncPress  0     CcAslButtnResIncPress  6
#
# HIS "+" BUTTON HAS NEVER ONCE REACHED OPENPILOT. `CcAslButtnSetIncPress` is not a signal this
# wheel sends; the car sends `CcAslButtnResIncPress`. openpilot therefore saw no accelCruise event
# ever, so `vCruiseCluster` never incremented on a `+` press and ICBM never captured a hold from
# one -- while the CAR moved its own set speed, because the stalk talks to the PCM directly.
#
# That is the whole of his long-running complaint: *"Increasing my speed with the plus when SLA
# doesn't have a number still does the ICBM speed sometimes... and then decreases back down to what
# I originally pressed set at."* The dash went up, openpilot did not know, and ICBM drove it back to
# a hold that his press could not update.
#
# ICBM's own INJECTION still uses CcAslButtnSetIncPress and is left alone: the PCM plainly accepts
# it, since that is how ICBM has been moving the set speed all along. Send and receive are simply
# not the same signal on this car.
#
# That is the original "holds are not remembered on resume" report. The behavioural detector added
# later works around it by comparing the landed set speed against the pre-cancel value, and that is
# why holds survive today -- the label was still wrong underneath.
#
# The wheel is CNCL / RES+ / SET- with a separate dedicated CNCL, so the correct reading is:
#   RES +   -> resume when off, increase when engaged
#   SET -   -> set when off, decrease when engaged
#   CNCL    -> cancel
# which is what the mapping below now says.
#
# There is also a separate CcAslButtnSetPress signal for a standalone "Set" button, unused here --
# this wheel has no such button.
BUTTONS = [
  # RES + : resume when cruise is off, increase the set speed when engaged.
  #
  # BOTH SIGNALS, because the wheel sends ResInc and only ICBM sends SetInc. Kept rather than
  # swapped: SetInc costs nothing here (it has zero driver-side edges on this car, and openpilot
  # does not read its own transmissions), and another Ford wheel may well use it.
  Button(ButtonType.accelCruise, "Steering_Data_FD1", "CcAslButtnSetIncPress", [1]),
  Button(ButtonType.resumeCruise, "Steering_Data_FD1", "CcAslButtnSetIncPress", [1]),
  Button(ButtonType.accelCruise, "Steering_Data_FD1", "CcAslButtnResIncPress", [1]),
  Button(ButtonType.resumeCruise, "Steering_Data_FD1", "CcAslButtnResIncPress", [1]),

  # SET - : set when cruise is off, decrease the set speed when engaged.
  Button(ButtonType.decelCruise, "Steering_Data_FD1", "CcAslButtnSetDecPress", [1]),
  Button(ButtonType.setCruise, "Steering_Data_FD1", "CcAslButtnSetDecPress", [1]),

  # CNCL : a dedicated button on this wheel. Ford still names the signal Cncl/Res and reports a
  # resume from it when cruise is off, which is harmless -- resume is now reachable from either.
  Button(ButtonType.cancel, "Steering_Data_FD1", "CcAslButtnCnclResPress", [1]),
  Button(ButtonType.resumeCruise, "Steering_Data_FD1", "CcAslButtnCnclResPress", [1]),

  # Main cruise button (on/off toggle)
  Button(ButtonType.mainCruise, "Steering_Data_FD1", "CcButtnOnOffPress", [1]),
]


class FordSafetyFlagsSP:
  """Sunnypilot-level safety flags for Ford.

  Carried in CP_SP.safetyParam and delivered to the safety firmware as
  current_safety_param_sp (the separate SP uint16, USB control 0xdf) -- NOT the main
  safetyConfigs[].safetyParam. ford_init reads it with GET_FLAG(current_safety_param_sp,
  ...), same pattern as Subaru STOP_AND_GO (subaru_common.h). Plain int constants, not
  IntFlag: CP_SP.safetyParam must stay a plain int through capnp serialization in card.
  """
  STEER_ANGLE_CURVATURE = 1



# Geometry-table index for the steering-angle curvature measurement, packed into
# CP_SP.safetyParam bits 1-4 when STEER_ANGLE_CURVATURE is set. Must match the
# ford_pinion_geometry table in safety/modes/ford.h row for row (enforced by
# test_ford.py's geometry-consistency test against CarSpecs + calc_slip_factor).
# Index 0 is reserved as invalid: the firmware treats flag-set-but-no-index as feature
# off, so a half-configured param can never select the wrong geometry silently.
# FORD_EDGE_MK2 is deliberately absent: ALT_STEER_ANGLE platforms read a RELATIVE pinion
# angle (SteeringPinion_Data_Alt + learned offset) and lack the absolute measurement
# this feature needs -- the toggle no-ops there and yaw behavior is kept.
FORD_PINION_GEOMETRY_SHIFT = 1
FORD_PINION_GEOMETRY_INDEX = {
  CAR.FORD_BRONCO_SPORT_MK1: 1,
  CAR.FORD_ESCAPE_MK4: 2,
  CAR.FORD_ESCAPE_MK4_5: 3,
  CAR.FORD_EXPEDITION_MK4: 4,
  CAR.FORD_EXPLORER_MK6: 5,
  CAR.FORD_FOCUS_MK4: 6,
  CAR.FORD_F_150_LIGHTNING_MK1: 7,
  CAR.FORD_F_150_MK14: 8,
  CAR.FORD_MAVERICK_MK1: 9,
  CAR.FORD_MONDEO_MK5: 10,
  CAR.FORD_MUSTANG_MACH_E_MK1: 11,
  CAR.FORD_RANGER_MK2: 12,
}


# BluePilot: Max curvature for steering command (m^-1), from DBC file limits
CURVATURE_MAX = 0.02

# BluePilot: Curvature rate limits — 3-point breakpoints for smoother lateral control.
# Upstream opendbc uses 2-point ([5, 25]) with more conservative values.
# These allow higher rates at low speed for responsiveness, lower rates at mid-speed
# for comfort, and very low rates at highway speed for stability.
#
# Control (Python) uses stricter windup than unwind so OP stays inside panda when apply_std
# picks the wrong table vs steer_angle_cmd_checks. Safety firmware uses looser symmetric ROCs
# (former “down” table for both up/down) — see ford.h FORD_LIMITS.
# Tests: test_ford.py ANGLE_RATE_* match ford.h, not the stricter BP_ANGLE_LIMITS up row.
_BP_ANGLE_RATE_UP = ([5, 16, 25], [0.0025, 0.0012, 0.00008])
_BP_ANGLE_RATE_DOWN = ([5, 16, 25], [0.0025, 0.0014, 0.00018])
BP_ANGLE_LIMITS = AngleSteeringLimits(
  0.02,  # Max curvature for steering command, m^-1
  _BP_ANGLE_RATE_UP,
  _BP_ANGLE_RATE_DOWN,
)


def apply_bp_device_mount(car_docs, CP):
  """BluePilot: attach the comma device and the matching Ford harness.

  This used to pick between the angled and standard comma3 mount per vehicle, since Ford
  windshield rakes vary. Upstream opendbc has since collapsed that taxonomy -- `Device` offers
  only `four` and `Mount` only `mount`, so there is no angled variant left to select and the
  per-vehicle list that chose between them went with it.

  Kept as a function rather than folded back into ford/values.py::init_make so the hook there
  stays a one-liner: it is an upstream file, and every line we touch is a merge conflict forever.

  Until 2026-08-08 this still named `Device.threex_angled_mount`/`Device.threex`, which stopped
  existing upstream. That is an AttributeError at import of opendbc.car.docs, so anything walking
  PLATFORMS died -- including platform_list.py, which is how car_list.json is generated and which
  bp_merge_upstream.py runs on every merge. Broken the same way on upstream/bp-7.0; reported there.
  """
  from opendbc.car.ford.values import CarHarness, FordFlags
  harness = CarHarness.ford_q4 if CP.flags & FordFlags.CANFD else CarHarness.ford_q3
  car_docs.car_parts = CarParts([Device.four, harness])

