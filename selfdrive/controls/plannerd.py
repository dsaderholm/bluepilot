#!/usr/bin/env python3
from cereal import car, custom
from openpilot.common.gps import get_gps_location_service
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.ldw import LaneDepartureWarning
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
import cereal.messaging as messaging


def main():
  config_realtime_process(5, Priority.CTRL_LOW)

  cloudlog.info("plannerd is waiting for CarParams")
  params = Params()
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  cloudlog.info("plannerd got CarParams: %s", CP.brand)

  cloudlog.info("plannerd is waiting for CarParamsSP")
  CP_SP = messaging.log_from_bytes(params.get("CarParamsSP", block=True), custom.CarParamsSP)
  cloudlog.info("plannerd got CarParamsSP")

  gps_location_service = get_gps_location_service(params)

  ldw = LaneDepartureWarning()
  longitudinal_planner = LongitudinalPlanner(CP, CP_SP)
  pm = messaging.PubMaster(['longitudinalPlan', 'driverAssistance', 'longitudinalPlanSP'])
  # BluePilot: carStateBP carries Ford's own ACCDATA brake request. The radar-blind lead detector
  # needs it to tell "nothing is braking for this" from "stock ACC is already on it" -- without it
  # the detector kept commanding and kept alerting while Ford was visibly slowing for the same car.
  # FusionPilot: mapdExtendedOut carries mapd v2's path ahead -- a list of points, each with its own
  # curvature and target velocity -- which is what SCC-Map walks instead of v1's MapTargetVelocities
  # once MapdV2 is on. Subscribed unconditionally: with v2 off nothing publishes it and the socket
  # stays quiet at the cost of one dict entry, whereas a conditional list would make the SubMaster's
  # contents depend on a param read once at process start.
  sm = messaging.SubMaster(['carControl', 'carState', 'controlsState', 'liveParameters', 'radarState', 'modelV2', 'selfdriveState',
                            # BluePilot: carStateBP carries the raw TSR and BLIS signals the
                            # passing-assist observer records. Not in any all_checks() list, so a
                            # platform that never publishes it (non-Ford, non-BluePilot) is
                            # unaffected -- sm.valid stays False and the observer reports it.
                            # selfdriveStateSP carries ICBM's held baseline, which is the driver's own set speed
                            # when they have taken it back from ICBM.
                            # liveTracks is the FULL front-radar object list, including the off-path
                            # tracks radard discards after picking its two in-path leads. It is how
                            # the observer sees the lane it would move into. Note the rate: card
                            # emits it at ~8.3 Hz on a Delphi MRR, not the 20 Hz declared in
                            # services.py, so anything counting frames must gate on sm.updated.
                            # rearRadarBP is the rear digest, published only when a feeder is
                            # fitted -- absent on every other car, where sm.valid stays False and
                            # RearApproach correctly reports unavailable rather than clear.
                            # mapdOut is mapd v2's own message, and passing assist needs exactly one
                            # field from it: oneWay. A divided carriageway is a one-way way, which is
                            # the ONLY thing that separates it from an undivided road with a centre
                            # turn lane -- the two are geometrically identical from the car, and the
                            # sensor-only fix was replayed against a real drive and measured dead.
                            # Absent unless MapdV2 is 1 or 2, where sm.valid stays False and the
                            # oncoming veto keeps its pre-map behaviour exactly.
                            # routeIntentBP is his own navigator's next instruction, published by
                            # whichever transport is fitted -- the car's own CAN, a phone bridge, a
                            # router -- and by none of them today. Absent on every car including
                            # this one, where sm.valid stays False and RouteIntent reports
                            # unavailable, so passing assist behaves exactly as it does now. See
                            # route_intent.py; freshness comes from the message's own stamp rather
                            # than from liveness here, because the publish rate belongs to the
                            # transport and this subscription must not know about transports.
                            'liveMapDataSP', 'carStateSP', 'carStateBP', 'liveTracks', 'rearRadarBP', 'mapdOut',
                            'selfdriveStateSP', 'mapdExtendedOut', 'routeIntentBP', gps_location_service],
                           poll='carState')

  while True:
    sm.update()
    longitudinal_planner.sla.update_car_state(sm['carState'])
    if sm.updated['modelV2']:
      longitudinal_planner.update(sm)
      longitudinal_planner.publish(sm, pm)

      ldw.update(sm.frame, sm['modelV2'], sm['carState'], sm['carControl'])
      msg = messaging.new_message('driverAssistance')
      msg.valid = sm.all_checks(['carState', 'carControl', 'modelV2', 'liveParameters'])
      msg.driverAssistance.leftLaneDeparture = ldw.left
      msg.driverAssistance.rightLaneDeparture = ldw.right
      pm.send('driverAssistance', msg)


if __name__ == "__main__":
  main()
