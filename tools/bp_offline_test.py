#!/usr/bin/env python3
"""Run this fork's offline-testable suite on the Python version the device actually uses.

WHY THIS EXISTS
---------------
Two separate things make `pytest` on a dev box lie about this repo:

1. Wrong interpreter. pyproject pins ">= 3.12.3, < 3.13". On Python 3.14, PEP 649 makes class
   annotations lazy, so opendbc's auto_dataclass -- which reads `cls.__dict__['__annotations__']`
   -- sees nothing and silently converts no fields. Every `auto_field()` default stays a raw
   sentinel object instead of becoming an int/bool/str. Tests then fail with things like
   "unsupported operand type(s) for |=: 'object' and 'int'" that do not happen on the device.
   Those failures are easy to wave away as environment noise, which is exactly the habit that
   lets a real failure hide among them.

2. Missing compiled extensions. common/params_pyx is a Cython module built by scons, and the
   repo conftest imports Params at collection time, so nothing collects at all without it. This
   script stubs Params and the other device-only leaves, and runs pytest with --noconftest.

It re-execs itself under the correct interpreter, so `python tools/bp_offline_test.py` works
whatever Python you happen to invoke it with.

USAGE
-----
    python tools/bp_offline_test.py                 # the default set below
    python tools/bp_offline_test.py path/to/test.py # anything pytest accepts
    BP_TEST_PYTHON=/path/to/python3.12 python tools/bp_offline_test.py

If no 3.12 is found it tells you how to make one rather than running on the wrong version:
    uv python install 3.12
    uv venv --python 3.12 <sandbox>/.venv-bp312
    uv pip install --python <sandbox>/.venv-bp312/Scripts/python.exe \
        pytest pycapnp numpy zstandard requests tqdm crcmod-plus sympy pyserial raylib

WHAT IT DOES NOT COVER
----------------------
Anything needing the compiled extensions, a real CAN bus, or a display: process integration,
the settings screens rendering, panda safety. Onroad HUD drawing has its own offline check --
see tools/../selfdrive/ui/bp/onroad/tools/preview_acc_status.py.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WANT = (3, 12)

# The suites that genuinely run without the compiled extensions. Keep this list honest: a suite
# that cannot run offline belongs out of it, not skipped inside it.
DEFAULT_TARGETS = [
  # Named rather than by directory: sunnypilot/selfdrive/car/tests/ also holds device-only tests.
  "sunnypilot/selfdrive/car/intelligent_cruise_button_management/tests/",
  "opendbc_repo/opendbc/sunnypilot/car/ford/tests/",
  # (that directory glob already covers test_button_mapping.py)
  "sunnypilot/selfdrive/controls/lib/tests/test_unconfirmed_lead.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_map_model_veto.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_map_veto_published.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_mapd_v2_path.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_map_factor_split.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_scc_leaves_disabled.py",
  # Named files, not selfdrive/ui/tests/ -- test_raylib_ui.py, test_soundd.py, test_feedbackd.py
  # and test_translations.py all need raylib or the device and break collection for the folder.
  # That is also how test_settings_recommend_defaults.py sat here unrun on the day it was written:
  # the suite total did not move and nothing said why. test_bp_tests_are_registered guards it now.
  "selfdrive/ui/tests/test_settings_fit.py",
  "selfdrive/ui/tests/test_settings_recommend_defaults.py",
  "selfdrive/ui/tests/test_readme_is_built.py",
  "selfdrive/ui/tests/test_sunnylink_settings_complete.py",
  "selfdrive/ui/tests/test_drive_clock.py",
  # Named files, not the directory: test_speed_limit_assist.py in the same folder imports the
  # sunnylink/API stack (jwt and friends) and cannot collect without the device environment.
  # Listing the folder pulled it in and broke collection for everything.
  "sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_upcoming_limit.py",
  "sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_sla_unset_invariant.py",
  "sunnypilot/selfdrive/selfdrived/tests/test_model_stop_alert_wording.py",
  "sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_speed_limit_resolver.py",
  "selfdrive/car/tests/test_structs_capnp_parity.py",
  "selfdrive/car/tests/test_pre_enable_standstill.py",
  "selfdrive/ui/bp/onroad/tests/",
  "system/tests/test_sentry_disabled_by_default.py",
  # Guards the policy stated in CLAUDE.md's "Params, defaults, and his settings": nothing may write
  # a settings key. It lived only on the passing assist branch, where the policy is not stated, so
  # the other branches were unguarded against the exact failure it was written for.
  "sunnypilot/system/tests/test_no_migration_writes_settings.py",
  # A duplicate params_keys.h entry is silently dropped by the unordered_map rather than being
  # an error, and two long-lived branches adding the same key in different places is how it
  # happens. BPDefaultsGeneration was exactly that, before it was removed with the defaults
  # migration on 2026-08-08.
  "selfdrive/car/tests/test_params_keys_unique.py",
  "selfdrive/car/tests/test_no_int_on_capnp_enums.py",
  "selfdrive/car/tests/test_dec_slow_down_published.py",
  "selfdrive/car/tests/test_capnp_accepts_published_types.py",
  "sunnypilot/mapd/tests/test_only_one_map_daemon_runs.py",
  "sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_sla_leaves_disabled_while_icbm_moves_the_cluster.py",
  "sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_sla_survives_a_gas_override.py",
  "sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_the_announcement_defers_to_a_hold.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_the_blindness_veto_needs_a_blind_camera.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_vision_target_holds_its_minimum.py",
  "opendbc_repo/opendbc/sunnypilot/car/ford/tests/test_replay_his_drive.py",
  "opendbc_repo/opendbc/sunnypilot/car/ford/tests/test_tsr_reliability.py",
  "sunnypilot/selfdrive/selfdrived/tests/test_unconfirmed_lead_alert_truth.py",
  "opendbc_repo/opendbc/sunnypilot/car/ford/tests/test_apim_gps_quality.py",
  "opendbc_repo/opendbc/sunnypilot/car/ford/tests/test_synthesized_set_cruise.py",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/tests/test_tile_curvature.py",
  # Named file, not the directory: test_mapd_version.py beside it hashes the installed mapd binary
  # and cannot run without the device. The schema guard pins mapd v2's ordinals, which are theirs
  # rather than ours -- capnp reads by position, so drift decodes as different fields with no error.
  "sunnypilot/mapd/tests/test_mapd_schema.py",
  "sunnypilot/mapd/tests/test_mapd_v2_map_data.py",
  "sunnypilot/mapd/tests/test_mapd_v2_restarts.py",
  "sunnypilot/mapd/tests/test_mapd_v2_stall_watchdog.py",
  "sunnypilot/mapd/tests/test_mapd_v2_binary.py",
  "sunnypilot/mapd/tests/test_mapd_settings.py",
  # The value behind every +/- control. The widget needs pyray and cannot collect here, which
  # is why its logic lives in param_value_cache.py -- there was nothing to test before, and
  # nothing caught the read that wiped his angle tuning.
  "selfdrive/ui/bp/widgets/tests/",
]


def find_interpreter() -> str | None:
  """Locate a 3.12 interpreter: explicit override, then the sandbox venv, then uv's store."""
  if (env := os.environ.get("BP_TEST_PYTHON")):
    return env

  exe = "python.exe" if os.name == "nt" else "python"
  bindir = "Scripts" if os.name == "nt" else "bin"

  candidates = [
    REPO.parent / ".venv-bp312" / bindir / exe,   # sibling of the repo/worktree
    REPO / ".venv-bp312" / bindir / exe,
  ]
  uv_store = Path.home() / ("AppData/Roaming/uv/python" if os.name == "nt" else ".local/share/uv/python")
  if uv_store.is_dir():
    candidates += sorted(uv_store.glob("cpython-3.12.*/" + ("python.exe" if os.name == "nt" else "bin/python3")))

  for path in candidates:
    if path.exists():
      return str(path)
  return None


def install_stubs() -> None:
  """Stand in for the device-only modules the suite imports at collection time."""
  keys_src = (REPO / "common" / "params_keys.h").read_text()
  defaults = dict(re.findall(r'\{"(\w+)", \{[^,]+, (?:INT|BOOL|FLOAT), "([^"]+)"\}', keys_src))
  known = set(re.findall(r'\{"(\w+)",', keys_src))
  # Which keys hold text rather than a number. The device returns None or a string for these, and
  # returning 0 instead is not a harmless stub difference: json.loads(0) raises TypeError, which is
  # how SmartCruiseControlMap could not be constructed in a test at all. A stub that answers with
  # the wrong TYPE fails in ways the device never would, which is the one thing it must not do.
  text_keys = set(re.findall(r'\{"(\w+)", \{[^,]+, (?:STRING|JSON|BYTES|TIME)[,}]', keys_src))

  rt = types.ModuleType("openpilot.common.realtime")
  rt.DT_CTRL, rt.DT_MDL, rt.DT_HW = 0.01, 0.05, 0.5
  rt.Ratekeeper = type("Ratekeeper", (), {"__init__": lambda self, *a, **k: None,
                                          "keep_time": lambda self: False,
                                          "monitor_time": lambda self: False, "frame": 0})
  for name in ("drop_realtime", "config_realtime_process", "set_realtime_priority",
               "set_core_affinity"):
    setattr(rt, name, lambda *a, **k: None)
  rt.sec_since_boot = lambda: 0.0
  sys.modules["openpilot.common.realtime"] = rt

  hw = types.ModuleType("openpilot.system.hardware")
  hw.HARDWARE = type("H", (), {"get_device_type": staticmethod(lambda: "pc")})()
  hw.PC, hw.TICI = True, False
  sys.modules["openpilot.system.hardware"] = hw

  mq = types.ModuleType("msgq")
  for name in ("fake_event_handle", "drain_sock_raw", "MultiplePublishersError", "IpcError",
               "Context", "Poller", "SubSocket", "PubSocket", "SocketEventHandle",
               "toggle_fake_events", "set_fake_prefix", "get_fake_prefix", "delete_fake_prefix",
               "wait_for_one_event"):
    setattr(mq, name, type(name, (), {}))
  sys.modules["msgq"] = mq

  class Params:
    """Reads defaults straight out of params_keys.h, so a test that references an undeclared
    key fails here the same way it would on the device."""
    def __init__(self, *a, **k):
      pass

    def get(self, key, *a, **k):
      if key not in known:
        raise KeyError(f"UnknownKeyName: {key}")
      if key in text_keys:
        return defaults.get(key)          # None when unset, exactly like the device
      raw = defaults.get(key, "0")
      return float(raw) if "." in raw else int(raw)

    def get_bool(self, key, *a, **k):
      if key not in known:
        raise KeyError(f"UnknownKeyName: {key}")
      return defaults.get(key, "0") == "1"

    def put(self, *a, **k):
      pass

    def put_bool(self, *a, **k):
      pass

  pp = types.ModuleType("openpilot.common.params")
  pp.Params = Params
  sys.modules["openpilot.common.params"] = pp

  sl = types.ModuleType("openpilot.common.swaglog")
  sl.cloudlog = type("_Log", (), {"__getattr__": lambda self, _: (lambda *a, **k: None)})()
  sys.modules["openpilot.common.swaglog"] = sl

  # FusionPilot: `sunnypilot/selfdrive/car/interfaces.py` imports system.sentry at module level,
  # which reaches athena/registration and then PyJWT -- a device dependency. That one import chain
  # made the whole file untestable offline, and the file holds the two gates that decide whether
  # ICBM exists at all. Stubbing the leaf is enough; nothing under test calls into it.
  # Stub sentry itself rather than the chain under it -- it reaches athena, the comma API and
  # PyJWT, none of which exist here and none of which anything under test calls.
  #
  # `import openpilot.system.sentry as sentry` binds through the PARENT package, so a sys.modules
  # entry for the leaf alone is not enough -- `openpilot.system` has to exist and carry `sentry` as
  # an attribute. That is why the first attempt still failed with "cannot import name 'system'".
  sentry = types.ModuleType("openpilot.system.sentry")
  for _name in ("capture_exception", "capture_warning", "set_tag", "init", "bind_user"):
    setattr(sentry, _name, lambda *a, **k: None)
  if "openpilot.system" not in sys.modules:
    osys = types.ModuleType("openpilot.system")
    osys.__path__ = []  # a package, so submodule imports resolve against it
    sys.modules["openpilot.system"] = osys
  sys.modules["openpilot.system"].sentry = sentry
  sys.modules.setdefault("openpilot.system.sentry", sentry)

  # sunnylink's statsd is the other module-level import in that file, and chasing ITS imports is
  # the wrong cut -- it wants pyzmq, then hardware.hw.Paths, then system.version, and each stub
  # only reveals the next. `interfaces.py` uses one name from it. Stub the module.
  statsd = types.ModuleType("openpilot.sunnypilot.sunnylink.statsd")
  statsd.STATSLOGSP = type("_Stats", (), {"__getattr__": lambda self, _: (lambda *a, **k: None)})()
  sys.modules.setdefault("openpilot.sunnypilot.sunnylink.statsd", statsd)


def main() -> int:
  if sys.version_info[:2] != WANT:
    interpreter = find_interpreter()
    if interpreter is None:
      print(f"Running on Python {sys.version.split()[0]}, but this repo requires "
            f"{WANT[0]}.{WANT[1]} and results differ (see the module docstring).\n"
            f"No 3.12 found. Create one:\n"
            f"  uv python install 3.12\n"
            f"  uv venv --python 3.12 {REPO.parent / '.venv-bp312'}\n"
            f"  uv pip install --python {REPO.parent / '.venv-bp312'} pytest pycapnp numpy "
            f"zstandard requests tqdm crcmod-plus sympy pyserial raylib\n"
            f"Or set BP_TEST_PYTHON to a 3.12 interpreter.", file=sys.stderr)
      return 2
    return subprocess.call([interpreter, str(Path(__file__).resolve()), *sys.argv[1:]])

  sys.path.insert(0, str(REPO))
  install_stubs()

  import pytest
  targets = sys.argv[1:] or DEFAULT_TARGETS
  # --noconftest: the repo conftest imports Params (compiled) at collection time.
  # -o addopts=: pyproject sets xdist flags (-n --dist=loadgroup) that need plugins this
  #              lightweight environment does not install, and parallelism buys nothing here.
  # -p no:cacheprovider: keeps .pytest_cache out of the tree.
  # The PytestConfigWarning filter is narrow on purpose: pyproject declares options belonging to
  # a C++ test plugin that is not installed here, and two lines of that noise per run is how real
  # warnings stop getting read. Every other warning still surfaces.
  return pytest.main(["--noconftest", "-q", "-o", "addopts=", "-p", "no:cacheprovider",
                      "-W", "ignore::pytest.PytestConfigWarning",
                      "--rootdir", str(REPO), *targets])


if __name__ == "__main__":
  raise SystemExit(main())
