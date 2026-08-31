import os
import operator
import platform

from cereal import car, custom
from openpilot.common.params import Params
from openpilot.common.bluepilot import is_bluepilot
from openpilot.system.hardware import PC, TICI
from openpilot.system.manager.process import PythonProcess, NativeProcess, DaemonProcess
from openpilot.system.hardware.hw import Paths

from openpilot.sunnypilot.mapd.mapd_manager import MAPD_PATH
from openpilot.sunnypilot.mapd import MAPD_V2_PATH, MAPD_V2_ON

from openpilot.sunnypilot.models.helpers import get_active_model_runner
from openpilot.sunnypilot.sunnylink.utils import sunnylink_need_register, sunnylink_ready, use_sunnylink_uploader

WEBCAM = os.getenv("USE_WEBCAM") is not None

def driverview(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started or params.get_bool("IsDriverViewEnabled")

def notcar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and CP.notCar

def iscar(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not CP.notCar

def logging(started: bool, params: Params, CP: car.CarParams) -> bool:
  run = (not CP.notCar) or not params.get_bool("DisableLogging")
  return started and run

def ublox_available() -> bool:
  return os.path.exists('/dev/ttyHS0') and not os.path.exists('/persist/comma/use-quectel-gps')

def ublox(started: bool, params: Params, CP: car.CarParams) -> bool:
  use_ublox = ublox_available()
  if use_ublox != params.get_bool("UbloxAvailable"):
    params.put_bool("UbloxAvailable", use_ublox, block=True)
  return started and use_ublox

def joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("JoystickDebugMode")

def not_joystick(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not params.get_bool("JoystickDebugMode")

def long_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("LongitudinalManeuverMode")

def lat_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and params.get_bool("LateralManeuverMode")

def not_long_maneuver(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not params.get_bool("LongitudinalManeuverMode")

def qcomgps(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started and not ublox_available()

def always_run(started: bool, params: Params, CP: car.CarParams) -> bool:
  return True

def only_onroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return started

def only_offroad(started: bool, params: Params, CP: car.CarParams) -> bool:
  return not started

def use_github_runner(started, params, CP: car.CarParams) -> bool:
  return not PC and params.get_bool("EnableGithubRunner") and (
    not params.get_bool("NetworkMetered") and not params.get_bool("GithubRunnerSufficientVoltage"))

def use_copyparty(started, params, CP: car.CarParams) -> bool:
  return bool(params.get_bool("EnableCopyparty"))

def sunnylink_ready_shim(started, params, CP: car.CarParams) -> bool:
  """Shim for sunnylink_ready to match the process manager signature."""
  return sunnylink_ready(params)

def sunnylink_need_register_shim(started, params, CP: car.CarParams) -> bool:
  """Shim for sunnylink_need_register to match the process manager signature."""
  return sunnylink_need_register(params)

def use_sunnylink_uploader_shim(started, params, CP: car.CarParams) -> bool:
  """Shim for use_sunnylink_uploader to match the process manager signature."""
  return use_sunnylink_uploader(params)

def is_tinygrad_model(started, params, CP: car.CarParams) -> bool:
  """Check if the active model runner is SNPE."""
  return bool(get_active_model_runner(params, not started) == custom.ModelManagerSP.Runner.tinygrad)

def is_stock_model(started, params, CP: car.CarParams) -> bool:
  """Check if the active model runner is stock."""
  return bool(get_active_model_runner(params, not started) == custom.ModelManagerSP.Runner.stock)

def mapd_ready(started: bool, params: Params, CP: car.CarParams) -> bool:
  """FusionPilot: v1 stops running once v2 is the source, because nothing reads it any more.

  In state 2 `mapd_manager` builds `MapdV2MapData`, so Speed Limit Assist takes its numbers from
  `mapdOut` and v1's `/dev/shm/params` output goes to nobody. Running it anyway was TWO map daemons
  for one consumer, and it showed up as heat rather than as a bug: route 389 ran a mean CPU of
  87.1 C with the fan at 97% and a 93.9 C peak, against 79.3 C / 73% on route 388 which had no v2.
  He heard the fan pinned for a whole drive.

  In states 0 and 1 v1 still runs, and must: state 0 is v1 alone, and state 1 is the comparison --
  v2 observing while v1 still feeds SLA. Only state 2 makes it redundant.

  MapdV2 is read on every call rather than cached because manager re-evaluates this predicate as
  the state changes; the process simply stops being started once the switch moves.
  """
  if not os.path.exists(Paths.mapd_root()):
    return False
  # The param read is guarded because this predicate previously COULD NOT FAIL -- it was a
  # filesystem check. On a fresh flash, before scons rebuilds params_pyx from params_keys.h, reading
  # a newly declared key raises UnknownKeyName; that is a documented first-boot window. Letting it
  # propagate would take v1 down with v2 and leave the car with no speed limits at all, so an
  # unreadable param falls back to running v1, which is what happened before this gate existed.
  try:
    return bool(params.get("MapdV2", return_default=True) != MAPD_V2_ON)
  except Exception:
    return True

def mapd_v2_ready(started: bool, params: Params, CP: car.CarParams) -> bool:
  """FusionPilot: run mapd v2 in states 1 (observe) and 2 (on), never in 0.

  Both non-zero states run the process, because observing IS the point of state 1: mapdOut lands in
  the route at 20 Hz beside v1's behavior, which is the only way to compare the two on identical
  input -- v1 records nothing about what it saw. State 2 additionally switches what Speed Limit
  Assist reads; that decision lives in mapd_manager, not here.

  STATE 0 MUST RUN NOTHING, and that is why the param is checked here at all. An earlier version
  keyed only on the binary being present, so anyone tracking this branch for ICBM alone paid a fifth
  of a core and 200 MB for a migration that is ours, not theirs. The binary ships either way; what
  it costs is opt-in.
  """
  if not (os.path.exists(MAPD_V2_PATH) and os.path.exists(Paths.mapd_root())):
    return False

  try:
    if not params.get("MapdV2", return_default=True) > 0:
      return False
    # FusionPilot: the stall watchdog's restart request. mapd_manager sets this when mapdOut has
    # been silent while the localizer had a valid position -- see `_watch_for_stall`. Returning
    # False is how a Python daemon asks manager to bounce a native process it does not own.
    #
    # This is NOT redundant with `restart_if_crash=True` on the process. That watches for the
    # process DYING; on 2026-08-30 mapd_v2 stayed alive with running=True and no exit code and
    # published nothing for five consecutive drives, three of them from a fresh boot.
    #
    # THE CLEAR HAPPENS HERE, DELIBERATELY, AND A SIDE EFFECT IN A PREDICATE IS THE LESSER EVIL.
    # The first version had mapd_manager release the request on its next tick, which quietly made
    # the watchdog able to DISABLE THE THING IT GUARDS: `mapd_manager` is registered without
    # `restart_if_crash`, and `NativeProcess.start()` returns early while `self.proc` is not None,
    # so a mapd_manager that dies between setting the request and releasing it stays dead -- and
    # mapd_v2 stays stopped for the whole drive with Speed Limit Assist silently on nothing. That
    # is a worse outcome than the stall it was built to fix.
    #
    # Safe because `ensure_running` calls `should_run` EXACTLY ONCE per process per pass and stops
    # the process in the same pass (process.py:258-264): this pass stops mapd_v2, the next starts
    # it. One bounce, self-limiting, and it needs no other process to be alive.
    if params.get_bool("MapdV2RestartRequest"):
      params.put_bool("MapdV2RestartRequest", False)
      return False
  except Exception:
    # Same first-boot window `mapd_ready` guards: before scons rebuilds params_pyx from
    # params_keys.h a newly declared key raises UnknownKeyName. Fall back to running v2, which is
    # the behaviour that existed before the watchdog.
    return True

  return True

def uploader_ready(started: bool, params: Params, CP: car.CarParams) -> bool:
  if not params.get_bool("OnroadUploads"):
    return only_offroad(started, params, CP)

  return always_run(started, params, CP)

def or_(*fns):
  return lambda *args: operator.or_(*(fn(*args) for fn in fns))

def and_(*fns):
  return lambda *args: operator.and_(*(fn(*args) for fn in fns))

procs = [
  DaemonProcess("manage_athenad", "system.athena.manage_athenad", "AthenadPid"),

  NativeProcess("loggerd", "system/loggerd", ["./loggerd"], logging),
  NativeProcess("encoderd", "system/loggerd", ["./encoderd"], only_onroad),
  NativeProcess("stream_encoderd", "system/loggerd", ["./encoderd", "--stream"], notcar),
  PythonProcess("logmessaged", "system.logmessaged", always_run),

  NativeProcess("camerad", "system/camerad", ["./camerad"], driverview, enabled=not WEBCAM),
  PythonProcess("webcamerad", "tools.webcam.camerad", driverview, enabled=WEBCAM),
  PythonProcess("proclogd", "system.proclogd", only_onroad, enabled=platform.system() != "Darwin"),
  PythonProcess("journald", "system.journald", only_onroad, platform.system() != "Darwin"),
  PythonProcess("micd", "system.micd", iscar),
  PythonProcess("timed", "system.timed", always_run, enabled=not PC),

  PythonProcess("modeld", "selfdrive.modeld.modeld", and_(only_onroad, is_stock_model)),
  PythonProcess("dmonitoringmodeld", "selfdrive.modeld.dmonitoringmodeld", driverview, enabled=(WEBCAM or not PC)),

  PythonProcess("sensord", "system.sensord.sensord", only_onroad, enabled=not PC),
  PythonProcess("ui", "selfdrive.ui.ui", always_run, restart_if_crash=True),
  # BluePilot: use a fork-local subclass for optional custom sounds; upstream soundd remains unchanged.
  PythonProcess("soundd", "selfdrive.ui.bp.soundd_bp" if is_bluepilot() else "selfdrive.ui.soundd", driverview),
  # End BluePilot
  PythonProcess("locationd", "selfdrive.locationd.locationd", only_onroad),
  NativeProcess("_pandad", "selfdrive/pandad", ["./pandad"], always_run, enabled=False),
  PythonProcess("calibrationd", "selfdrive.locationd.calibrationd", only_onroad),
  PythonProcess("torqued", "selfdrive.locationd.torqued", only_onroad),
  PythonProcess("controlsd", "selfdrive.controls.controlsd", and_(not_joystick, iscar)),
  PythonProcess("joystickd", "tools.joystick.joystickd", or_(joystick, notcar)),
  PythonProcess("selfdrived", "selfdrive.selfdrived.selfdrived", only_onroad),
  PythonProcess("card", "selfdrive.car.card", only_onroad),
  PythonProcess("deleter", "system.loggerd.deleter", always_run),
  PythonProcess("dmonitoringd", "selfdrive.monitoring.dmonitoringd", driverview, enabled=(WEBCAM or not PC)),
  # BluePilot: restart_if_crash -- a diag-port serial fault (see qcomgpsd.py's reconnect
  # handling) is now recovered in-process, but this is a backstop for anything else that
  # still takes the process down; without it a crash meant no GPS for the rest of the drive.
  PythonProcess("qcomgpsd", "system.qcomgpsd.qcomgpsd", qcomgps, enabled=TICI, restart_if_crash=True),
  PythonProcess("pandad", "selfdrive.pandad.pandad", always_run),
  PythonProcess("paramsd", "selfdrive.locationd.paramsd", only_onroad),
  PythonProcess("lagd", "selfdrive.locationd.lagd", only_onroad),
  PythonProcess("ubloxd", "system.ubloxd.ubloxd", ublox, enabled=TICI),
  PythonProcess("pigeond", "system.ubloxd.pigeond", ublox, enabled=TICI),
  PythonProcess("plannerd", "selfdrive.controls.plannerd", not_long_maneuver),
  PythonProcess("maneuversd", "tools.longitudinal_maneuvers.maneuversd", long_maneuver),
  PythonProcess("lateral_maneuversd", "tools.lateral_maneuvers.lateral_maneuversd", lat_maneuver),
  PythonProcess("radard", "selfdrive.controls.radard", only_onroad),
  PythonProcess("hardwared", "system.hardware.hardwared", always_run),
  PythonProcess("modem", "system.hardware.tici.modem", always_run, enabled=TICI),
  PythonProcess("tombstoned", "system.tombstoned", always_run, enabled=not PC),
  PythonProcess("updated", "system.updated.updated", only_offroad, enabled=not PC),
  PythonProcess("uploader", "system.loggerd.uploader", uploader_ready),
  PythonProcess("statsd", "system.statsd", always_run),
  PythonProcess("feedbackd", "selfdrive.ui.feedback.feedbackd", only_onroad),

  # debug procs
  NativeProcess("bridge", "cereal/messaging", ["./bridge"], notcar),
  PythonProcess("webrtcd", "system.webrtc.webrtcd", notcar),
  PythonProcess("webjoystick", "tools.bodyteleop.web", notcar),
  PythonProcess("joystick", "tools.joystick.joystick_control", and_(joystick, iscar)),

  # sunnylink <3
  DaemonProcess("manage_sunnylinkd", "sunnypilot.sunnylink.athena.manage_sunnylinkd", "SunnylinkdPid"),
  PythonProcess("sunnylink_registration_manager", "sunnypilot.sunnylink.registration_manager", sunnylink_need_register_shim),
  PythonProcess("statsd_sp", "sunnypilot.sunnylink.statsd", and_(always_run, sunnylink_ready_shim)),
]

# sunnypilot
procs += [
  # Models
  PythonProcess("models_manager", "sunnypilot.models.manager", only_offroad),
  NativeProcess("modeld_tinygrad", "sunnypilot/modeld_v2", ["./modeld"], and_(only_onroad, is_tinygrad_model)),

  # Backup
  PythonProcess("backup_manager", "sunnypilot.sunnylink.backups.manager", and_(only_offroad, sunnylink_ready_shim)),

  # mapd
  # FusionPilot: `exec`, so stopping this actually stops it. Without it bash FORKS the binary and
  # manager kills only the wrapper -- observed on the device 2026-08-18 with MapdV2 switched to 2:
  # managerState read `mapd running=False pid=0` while ps still showed the daemon alive at the same
  # age as mapd_v2. The gate that stops v1 was working; the process just outlived being stopped, so
  # the two-daemon load survived until a reboot. `exec` makes bash replace itself with the binary.
  NativeProcess("mapd", Paths.mapd_root(), ["bash", "-c", f"exec {MAPD_PATH} > /dev/null 2>&1"], mapd_ready),
  # FusionPilot: mapd v2, launched exactly like v1 -- through bash, with stdout and stderr discarded.
  #
  # An earlier version of this line ran the binary bare, under a comment claiming its output "goes to
  # swaglog where a route can be checked against it". That was wrong twice over: nativelauncher
  # os.execvp's the binary with MANAGER's stdout inherited, so there is no swaglog routing at all,
  # and mapd v2's default log level is unverified. An unbounded 20 Hz writer into manager's output,
  # on a device already at 90% full, is not a thing to discover on a long drive.
  #
  # Nothing is lost by discarding it. What we actually want from v2 -- tileLoaded, waySelectionType,
  # every other field -- is published on mapdOut and logged into the route, which is the point of the
  # migration. If its stdout is ever wanted, raise the log level through MapdSettings and run it by
  # hand rather than leaving it writing for every drive.
  # FusionPilot: RESTART IT IF IT DIES. Speed Limit Assist reads mapdOut in state 2, so a dead
  # mapd_v2 is SLA silently losing its speed limit source mid-drive -- and until 2026-08-24
  # nothing brought it back, because NativeProcess did not accept `restart_if_crash` at all.
  # He hit it on route 000003b4: 441 "not running: mapd_v2" events in one drive, and the only
  # cure was pulling over and rebooting. The notes said "only a reboot recovers" as though it
  # were a property of the daemon; it was a missing keyword argument.
  NativeProcess("mapd_v2", Paths.mapd_root(), ["bash", "-c", f"exec {MAPD_V2_PATH} > /dev/null 2>&1"], mapd_v2_ready, restart_if_crash=True),  # exec: see mapd above
  PythonProcess("mapd_manager", "sunnypilot.mapd.mapd_manager", always_run),

  # locationd
  NativeProcess("locationd_llk", "sunnypilot/selfdrive/locationd", ["./locationd"], only_onroad),
]

# BluePilot: portal and route preprocessor processes
if is_bluepilot():
  def _bp_portal_enabled(started, params, CP):
    return params.get_bool("EnableWebRoutesServer")
  def _bp_route_preprocessor_enabled(started, params, CP):
    return params.get_bool("EnableWebRoutesServer") and only_offroad(started, params, CP)
  procs += [
    PythonProcess("bp_portal", "bluepilot.backend.bp_portal", _bp_portal_enabled),
    PythonProcess("bp_route_preprocessor", "bluepilot.backend.routes.preprocessor", _bp_route_preprocessor_enabled),
  ]

if os.path.exists("./github_runner.sh"):
  procs += [NativeProcess("github_runner_start", "system/manager", ["./github_runner.sh", "start"], and_(only_offroad, use_github_runner), sigkill=False)]

if os.path.exists("../../sunnypilot/sunnylink/uploader.py"):
  procs += [PythonProcess("sunnylink_uploader", "sunnypilot.sunnylink.uploader", use_sunnylink_uploader_shim)]

if os.path.exists("../../third_party/copyparty/copyparty-sfx.py"):
  sunnypilot_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
  copyparty_args = [f"-v{Paths.crash_log_root()}:/swaglogs:r"]
  copyparty_args += [f"-v{Paths.log_root()}:/routes:r"]
  copyparty_args += [f"-v{Paths.model_root()}:/models:rw"]
  copyparty_args += [f"-v{sunnypilot_root}:/sunnypilot:rw"]
  copyparty_args += ["-p8080"]
  copyparty_args += ["-z"]
  copyparty_args += ["-q"]
  procs += [NativeProcess("copyparty-sfx", "third_party/copyparty", ["./copyparty-sfx.py", *copyparty_args], and_(only_offroad, use_copyparty))]

managed_processes = {p.name: p for p in procs}
