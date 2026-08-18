"""FusionPilot: never two map daemons for one consumer.

He heard it before any log said it: "my fan sounded like it was pinned at 100% the whole time."
Measured afterwards across the two drives:

    route 388 (no v2)     CPU mean 79.3 C, fan mean 73%
    route 389 (v2 observe) CPU mean 87.1 C, fan mean 97%, peak 93.9 C

Observe is SUPPOSED to cost that -- running both is the entire point of the state, because v1
records nothing about what it saw and the only way to compare them is on the same drive. What is
NOT supposed to cost it is state 2, where `mapd_manager` builds `MapdV2MapData` and Speed Limit
Assist reads `mapdOut`. There v1's output goes to nobody and the process is pure heat.
"""
from __future__ import annotations

import ast
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[3]
SRC = (REPO / "system" / "manager" / "process_config.py").read_text(encoding="utf-8")


def _fn(name):
  """The named predicate's source. `process_config` cannot be imported offline -- it pulls in the
  whole manager chain -- so this reads it, which is the same approach the rest of the fork takes for
  surfaces the suite cannot execute."""
  tree = ast.parse(SRC)
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == name:
      return ast.unparse(node)
  raise AssertionError(f"{name} is gone from process_config")


def test_v1_stops_when_v2_is_the_source():
  """The fix for the fan. In state 2 SLA reads mapdOut, so v1 is a second daemon producing output no
  process consumes -- 87.1 C mean and a 97% fan on route 389 against 79.3 C / 73% on 388."""
  src = _fn("mapd_ready")
  assert "MapdV2" in src, (
    "mapd v1 runs without consulting MapdV2, so it keeps running with v2 switched ON -- two map "
    "daemons for one consumer, which is the fan he heard for a whole drive")
  assert "MAPD_V2_ON" in src, (
    "mapd_ready checks MapdV2 but not against MAPD_V2_ON -- state 1 (observe) MUST still run v1, "
    "because that is the comparison; only state 2 makes it redundant")


def test_observe_still_runs_both_because_that_is_the_comparison():
  """Guards the other direction: a fix that stops v1 in state 1 as well would silently delete the
  observe mode, since v1 is what SLA still reads there and v2 records only for comparison."""
  src = _fn("mapd_ready")
  assert "!=" in src or "not " in src, (
    "the MapdV2 check does not look like an inequality against MAPD_V2_ON -- if it became `== 0` or "
    "`< 1`, observe mode stops feeding SLA and the comparison is gone")


def test_v2_still_never_runs_in_state_zero():
  """Unchanged, and load bearing for somebody else's car: anyone tracking this branch for ICBM alone
  must not pay a fifth of a core and 200 MB for a migration that is ours."""
  src = _fn("mapd_v2_ready")
  assert "MapdV2" in src and "> 0" in src, (
    "mapd v2 no longer checks that the user opted in")


def test_the_map_daemons_are_exec_ed_so_stopping_them_works():
  """`bash -c "<binary> ..."` FORKS, so manager kills the wrapper and the daemon is orphaned.

  Observed on the device 2026-08-18 with MapdV2 switched to 2: managerState read
  `mapd running=False pid=0` while `ps` still showed the binary alive at the same age as mapd_v2.
  The gate above was working -- the process simply outlived being stopped, so the two-daemon load
  he heard as a pinned fan survived until a reboot.

  `exec` makes bash replace itself with the binary, so the pid manager holds IS the daemon."""
  for name in ("mapd", "mapd_v2"):
    i = SRC.index(f'NativeProcess("{name}"')
    line = SRC[i:SRC.index("\n", i)]
    assert "exec " in line, (
      f'{name} is launched without `exec`, so bash forks it and manager can only kill the wrapper '
      f'-- stopping the process leaves the daemon running: {line.strip()}')
