"""Safety guard for the sign reader: it must never be able to make driving worse.

The reader is a nice-to-have running beside the driving stack on a device that has no spare
capacity to lend it. On 2026-11-12 the first drive with it actually reading never engaged (comm
errors, a lagging selfdrived) and the drive with it off was fine, so this is the contract now:
it starts late, looks slowly, and switches ITSELF off for the rest of the drive at the first sign
that the car is struggling. Pure logic with an injected clock, so every rule is tested.
"""
from __future__ import annotations

# Seconds after the process starts before it looks at a single frame. The car is still bringing
# its own processes up (locationd, calibration, selfdrived all report errors while they start).
STARTUP_QUIET_S = 45.0

# Never look more often than this, whatever the model asks for: 4 looks a second at most.
MIN_INFERENCE_GAP_S = 0.25

# One inference taking this long means the device is overloaded (it takes ~0.33 s on one core when
# the device is idle); three in a row and the reader stands down.
SLOW_INFERENCE_S = 1.0
SLOW_INFERENCE_LIMIT = 3

# Onroad events that mean the driving stack is struggling. Seeing ANY of them stops the reader.
TROUBLE_EVENTS = frozenset({
  "commIssue", "commIssueAvgFreq", "selfdrivedLagging", "locationdTemporaryError", "locationdPermanentError",
  "controlsdLagging", "processNotRunning", "modeldLagging", "paramsdTemporaryError", "paramsdPermanentError",
})


class Guard:
  def __init__(self, started_at: float):
    self.started_at = started_at
    self.last_inference_at = None
    self.slow_in_a_row = 0
    self.stopped_reason: str | None = None

  def stopped(self) -> bool:
    return self.stopped_reason is not None

  def stop(self, reason: str) -> None:
    if self.stopped_reason is None:  # the first reason is the cause; later ones are consequences
      self.stopped_reason = reason

  def note_events(self, names) -> None:
    """Event names from the car's onroadEvents. Any trouble event latches the reader off for the drive."""
    for name in names:
      if str(name) in TROUBLE_EVENTS:
        self.stop(f"car reported {name}")
        return

  def may_infer(self, now: float) -> bool:
    if self.stopped() or now - self.started_at < STARTUP_QUIET_S:
      return False
    return self.last_inference_at is None or now - self.last_inference_at >= MIN_INFERENCE_GAP_S

  def note_inference(self, now: float, duration_s: float) -> None:
    self.last_inference_at = now
    if duration_s >= SLOW_INFERENCE_S:
      self.slow_in_a_row += 1
      if self.slow_in_a_row >= SLOW_INFERENCE_LIMIT:
        self.stop(f"{self.slow_in_a_row} inferences in a row took over {SLOW_INFERENCE_S:.0f} s")
    else:
      self.slow_in_a_row = 0
