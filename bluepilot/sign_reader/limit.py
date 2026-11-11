"""When may the camera sign reader's limit be used as the speed limit?

One pure function, so the rule is testable without the planner and visible in one place. The
resolver (sunnypilot/.../speed_limit_resolver.py) calls it and substitutes the answer for the car's
own sign recognition (TSR), which the retrofit car this fork is built for never produced.
"""

# A posted limit is a whole number of mph or km/h in a sane range. The model only ever holds real
# limits, but this is the last gate before the car's set speed, so it is checked anyway.
MIN_LIMIT_MS = 2.2    # ~5 mph
MAX_LIMIT_MS = 40.3   # ~90 mph


def sign_reader_limit(enabled: bool, alive: bool, valid: bool, model_loaded: bool, speed_limit_ms: float) -> float:
  """The reader's held limit in m/s, or 0.0 for "this source has no limit".

  Zero on ANY doubt: switched off, no recent message (process dead, camera gone), message flagged
  invalid, the weights never loaded, or a value outside what a posted limit can be. Zero is what the
  resolver already treats as "this source has no limit", and the resolver does NOT fall back to the
  car's own sign recognition when the reader is on: that is excluded on this fork on purpose.
  """
  if not (enabled and alive and valid and model_loaded):
    return 0.0
  try:
    limit = float(speed_limit_ms)
  except (TypeError, ValueError):
    return 0.0
  if not (MIN_LIMIT_MS <= limit <= MAX_LIMIT_MS):  # also rejects NaN: every comparison with NaN is False
    return 0.0
  return limit
