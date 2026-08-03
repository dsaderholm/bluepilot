"""
BluePilot fork: crash/log reporting must stay OPT-IN.

This exists for merges, not for the code. Upstream edits system/sentry.py often -- DSN changes,
noise filters, integrations -- so the opt-in guard at the top of init() is exactly the kind of
small local change that gets dropped when a conflict is resolved in upstream's favour. Nothing
about that failure is visible: the fork keeps working and quietly starts transmitting again.

So assert the property rather than trusting the diff. If a rebase or merge loses the guard, this
fails, and it names what went missing.
"""

import re
from pathlib import Path

SENTRY = Path(__file__).parents[1] / "sentry.py"
PARAMS = Path(__file__).parents[2] / "common" / "params_keys.h"
PARAM = "BPSentryEnabled"


def _init_body() -> str:
  """Source of init() up to the first sentry_sdk.init(), i.e. everything before it can transmit."""
  src = SENTRY.read_text()
  start = src.index("def init(")
  end = src.index("sentry_sdk.init(", start)
  return src[start:end]


class TestSentryStaysOptIn:
  def test_param_exists_and_defaults_off(self):
    keys = PARAMS.read_text()
    m = re.search(rf'\{{"{PARAM}", \{{[^}}]*?, BOOL, "(\d)"\}}', keys)
    assert m, f"{PARAM} missing from common/params_keys.h -- was it lost in a merge?"
    assert m.group(1) == "0", f"{PARAM} default is '{m.group(1)}', must be '0' (off)"

  def test_init_checks_the_param_before_transmitting(self):
    body = _init_body()
    assert PARAM in body, (
      f"system/sentry.py::init() no longer checks {PARAM} before calling sentry_sdk.init(). "
      "A merge has probably taken upstream's version of this function. Restore the early return "
      "at the top of init() -- see the comment there."
    )

  def test_the_guard_actually_returns(self):
    """Referencing the param is not enough; it has to short-circuit."""
    body = _init_body()
    guard = re.search(rf"if not .*{PARAM}.*:\s*\n\s*return", body)
    assert guard, (
      f"{PARAM} is referenced in init() but does not guard an early return before "
      "sentry_sdk.init(). Reporting would still be live."
    )
