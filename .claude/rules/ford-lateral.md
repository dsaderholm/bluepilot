---
paths:
  - "opendbc_repo/opendbc/sunnypilot/car/ford/lateral_*.py"
  - "opendbc_repo/opendbc/sunnypilot/car/ford/hold_wheel_cap.py"
  - "opendbc_repo/opendbc/sunnypilot/car/ford/angle_gains.py"
  - "opendbc_repo/opendbc/sunnypilot/car/ford/lane_center_trim.py"
  - "opendbc_repo/opendbc/car/ford/carcontroller.py"
  - "opendbc_repo/opendbc/car/ford/carstate.py"
  - "tools/bp_lateral_*.py"
  - "tools/bp_lane_centering_ab.py"
  - "tools/bp_stop_hold_wheel.py"
  - "tools/bp_eps_current.py"
  - "tools/bp_steer_*.py"
---
# Ford angle-mode steering: facts already measured (don't re-derive)

- Signs: wheel angle + = LEFT; curvature + = RIGHT; `livePose.angularVelocityDevice.z` + = right.
- Command: `path_angle = kappa_cmd * v * curvature_factor`, sent in `LateralMotionControl` 0x3D3 (979, not 982),
  start bit 31, on sendcan; bus echo is src 128. `FORD_DBC_PATH_ANGLE_MAX` 0.5235 rad.
- `pathAngleFinal * steerRatio` is NOT wheel degrees. The turn asked for, in wheel degrees:
  `geo = -degrees(atan(kappa * 2.85)) * 17.07`. The PSCM gives more wheel per command the slower the car.
- Gain schedule is LEVEL + SLOPE: low = interp(v,[11.18,31.29],[1.0, damp]); high = interp(v,...,[1.30*lof,
  anchor*hif]); ramp over |kappa| 0.0005 -> interp(v,...,[0.02,0.0045]). Flat point hif = damp/anchor
  (anchor 1.15 on CAN; `angle_gains.py` per platform). Below |kappa| 0.0005 the high factor does nothing.
- His gains were tuned by driving. Never recommend a gain change off a steady-state number: print
  turning-in / holding / unwinding; a gain scales entry and exit alike. Quote any step in degrees at the
  wheel (his 0.01 steps are ~0.03 deg, imperceptible; dither under 0.3 deg is not felt).
- Latency is correctly compensated (lagd ~0.39 s, measured car rotation). Don't re-chase
  `steerActuatorDelay`, the 0.15 clip or LagdValueCache. Curve ping-pong is the model's plan (~50% of the
  curve), upstream of every gain, trim and limiter.
- PSCM signals: `LatCtlLim_D_Stat` is dead on non-CAN-FD Fords; EPAS_INFO `SteMdule_I_Est` is magnitude
  only; column torque = byte0*0.0625-8 and reads the rack breaking free as a push, so torque is not a
  hands detector. 0x085 angle all-ones is the PSCM's own fault, raised as `steerFaultPermanent`.
- Stop hold (`FordLowSpeedAngleHold_ang`, mph): rights always, lefts only with a radar lead within 8 m; the
  latch keeps |kappa| 0.005-0.10 and forgets after 15 m of quiet road; `hold_wheel_cap` trims only while
  the wheel is past geo*1.1+5 deg AND still winding. The stall blip needs v > 9 m/s and |path_angle| < 0.10.
- Tight turns need low speed: the ISO clamp is 3.0/v^2 and `MAX_CURVATURE` 0.2 (5 m). The curvature
  signal caps at a 48 m radius, one reason angle mode stays. Lane-centering trim is off below 20 mph.
- Tests: change curvature over several frames; a one-frame jump trips the override and zeroes latches.
- Detail: `notes/HISTORY.md`, headings starting "LATERAL:", "2026-09-04: THE LATERAL CHAIN", "HOLD THE
  STEERING THROUGH A STOP", "THE STOP HOLD NOW HOLDS THE WHEEL", "THE RIGHT TURN AT THE END OF 000004c2".
