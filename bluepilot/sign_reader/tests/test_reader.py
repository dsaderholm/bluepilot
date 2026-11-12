"""Sign reader: the pieces that run without MNN, a camera or the device.

The model weights are not in this repository on purpose (their licence is unconfirmed), so none of
this loads a model. test_model_replay.py runs the real thing when the weights and a route are on
disk and skips cleanly otherwise.
"""
import re
import types
from pathlib import Path

import numpy as np
import pytest

from bluepilot.sign_reader import reader as R

REPO = Path(__file__).resolve().parents[3]

SPEC = {
  "numbers": ["livePose.orientationNED.z", "livePose.alive", "gpsLocation.hasFix", "gpsLocation.age",
              "gpsLocationExternal.hasFix", "gpsLocationExternal.age", "mapdOut.alive", "mapdOut.tileLoaded",
              "mapdOut.wayId", "frogpilotCarState.vEgo"],
  "text": ["mapdOut.roadName", "mapdOut.wayRef"],
  "outputs": ["frameId", "mode", "nextFrameId", "nextMode", "signAgreement", "signSpeedLimit", "speedLimit",
              "heldSignConfidence"],
}


class FakeSM:
  """Just enough SubMaster: sm[service], .alive, .recv_time, .data."""

  def __init__(self, with_external=False):
    ns = types.SimpleNamespace
    self.data = {
      "carState": ns(vEgo=31.25),
      "livePose": ns(inputsOK=True, sensorsOK=True),
      "liveLocationKalman": ns(orientationNED=ns(value=[0.0, 0.0, 1.5])),
      "gpsLocation": ns(hasFix=True),
      "mapdOut": ns(tileLoaded=True, wayId=1234, roadName="1300 East", wayRef=""),
    }
    if with_external:
      self.data["gpsLocationExternal"] = ns(hasFix=True)
    self.alive = {k: True for k in self.data}
    self.recv_time = {k: 0.0 for k in self.data}

  def __getitem__(self, k):
    return self.data[k]


def test_text_input_is_nul_joined_bytes():
  text = R.build_text(["1300 East", "I-215"])
  assert text.dtype == np.uint8
  assert bytes(text) == b"1300 East\x00I-215"


def test_context_vector_layout_and_state_roundtrip():
  state = np.arange(8, dtype=np.float32) + 100
  ctx = R.build_context(7, 1928, 1208, 1928, 1928 * 1208, [1.0, 2.0, 3.0], state)
  assert ctx.dtype == np.float32
  assert list(ctx[:5]) == [7, 1928, 1208, 1928, 1928 * 1208]
  assert list(ctx[5:8]) == [1.0, 2.0, 3.0]
  assert list(ctx[8:]) == list(state)  # the model's last output goes back in untouched


def test_decode_reading_picks_published_fields_and_zeroes_junk():
  index = R.output_index(SPEC["outputs"])
  state = np.zeros(len(index), np.float32)
  state[index["speedLimit"]] = 31.29
  state[index["signSpeedLimit"]] = np.nan
  state[index["signAgreement"]] = 0.97
  out = R.decode_reading(state, index)
  assert set(out) == set(R.READING_OUTPUTS)
  assert out["speedLimit"] == pytest.approx(31.29, abs=1e-3)
  assert out["signSpeedLimit"] == 0.0  # NaN is "no limit", never a number somebody could act on
  assert out["signAgreement"] == pytest.approx(0.97, abs=1e-6)


def test_decode_reading_tolerates_a_model_missing_an_output():
  assert R.decode_reading(np.zeros(1, np.float32), {"speedLimit": 0})["heldSignConfidence"] == 0.0


def test_read_field_maps_every_input_the_model_lists():
  sm = FakeSM()
  assert R.read_field(sm, "frogpilotCarState.vEgo") == pytest.approx(31.25)
  assert R.read_field(sm, "livePose.orientationNED.z") == pytest.approx(1.5)
  assert R.read_field(sm, "livePose.alive") is True
  assert R.read_field(sm, "mapdOut.wayId") == 1234
  assert R.read_field(sm, "mapdOut.roadName") == "1300 East"
  assert R.read_field(sm, "mapdOut.tileLoaded") is True
  # every field the real model asks for is answerable
  for f in SPEC["numbers"] + SPEC["text"]:
    R.read_field(sm, f)


def test_age_of_a_source_that_never_reported_is_huge_not_zero():
  sm = FakeSM()
  assert R.read_field(sm, "gpsLocation.age") == R.NEVER_S  # recv_time 0 means never received


def test_missing_external_gps_reads_as_no_fix_and_ancient():
  sm = FakeSM(with_external=False)
  assert R.read_field(sm, "gpsLocationExternal.hasFix") is False
  assert R.read_field(sm, "gpsLocationExternal.age") == R.NEVER_S


def test_unknown_input_refuses_instead_of_feeding_zeros():
  with pytest.raises(R.UnsupportedInput):
    R.read_field(FakeSM(), "mapdOut.somethingNew")


def test_services_needed_covers_the_spec_without_inventing_any():
  needed = R.services_needed(SPEC)
  assert needed == sorted({"carState", "livePose", "liveLocationKalman", "gpsLocation", "mapdOut"})
  assert "gpsLocationExternal" not in needed and "frogpilotCarState" not in needed


def test_step_without_a_model_is_not_reachable_by_accident(tmp_path):
  assert R.SignReader.load(tmp_path / "no-weights-here.mnn") is None  # missing file or missing MNN: None, no raise


def test_wants_frame_follows_the_models_own_schedule():
  reader = R.SignReader([None, None], SPEC)
  assert reader.wants_frame(0)
  reader.state[reader.index["nextFrameId"]] = 40
  assert not reader.wants_frame(39) and reader.wants_frame(40)


def test_nv12_from_bgr_geometry():
  pytest.importorskip("cv2")
  frame = np.full((8, 16, 3), 128, np.uint8)
  nv12 = R.nv12_from_bgr(frame)
  assert nv12.size == 16 * 8 * 3 // 2
  assert nv12.dtype == np.uint8


def test_weights_are_not_in_the_repo():
  """Their licence is unconfirmed. If this fails someone committed a model; read bluepilot/sign_reader/reader.py first."""
  found = [p for p in (REPO / "bluepilot").rglob("*.mnn")]
  assert not found, f"weights committed: {found}"


def test_message_schema_and_registrations_agree():
  custom = (REPO / "cereal/custom.capnp").read_text()
  assert "struct SignReadingBP @0x9ccdc8676701b412" in custom  # the reserved slot's id, reused
  log = (REPO / "cereal/log.capnp").read_text()
  assert re.search(r"signReadingBP @138 :Custom\.SignReadingBP", log)
  assert "customReserved12 @" not in log  # claimed, not left as a second entry for the same slot
  services = (REPO / "cereal/services.py").read_text()
  assert '"signReadingBP"' in services


def test_schema_compiles_and_roundtrips_a_reading():
  pytest.importorskip("capnp")
  from cereal import log  # the repo's own loader; loading the schema a second way aborts the whole process
  event = log.Event.new_message()
  m = event.init("signReadingBP")
  m.speedLimit = 31.29
  m.modelLoaded = True
  m.frameId = 4_000_000_000  # UInt32, a long drive's worth of frames
  with log.Event.from_bytes(event.to_bytes()) as back:
    assert back.which() == "signReadingBP"
    assert back.signReadingBP.speedLimit == pytest.approx(31.29, abs=1e-3)
    assert back.signReadingBP.frameId == 4_000_000_000


def test_param_defaults_off():
  keys = (REPO / "common/params_keys.h").read_text()
  assert re.search(r'\{"BPSignReaderEnabled", \{PERSISTENT \| BACKUP, BOOL, "0"\}\}', keys)


def test_process_is_added_not_replacing_and_gated_on_the_param():
  src = (REPO / "system/manager/process_config.py").read_text()
  assert 'PythonProcess("bp_sign_reader", "bluepilot.sign_reader.sign_reader", _bp_sign_reader_enabled)' in src
  fn = src.split("def _bp_sign_reader_enabled", 1)[1].split("procs +=", 1)[0]
  assert "started" in fn and 'get_bool("BPSignReaderEnabled")' in fn


def _mnn_importable():
  try:
    import MNN  # noqa: F401
    return True
  except ImportError:
    return False


@pytest.mark.skipif(_mnn_importable(), reason="the system has MNN, so the fallback folder is never consulted")
def test_mnn_comes_from_the_lib_folder_when_the_system_has_none(tmp_path, monkeypatch):
  import sys
  assert R.SignReader.import_mnn(tmp_path / "missing") is None
  pkg = tmp_path / "MNN"
  pkg.mkdir()
  (pkg / "__init__.py").write_text("MARK = 'from the lib folder'\n")
  monkeypatch.setattr(sys, "path", list(sys.path))
  try:
    assert R.SignReader.import_mnn(tmp_path).MARK == "from the lib folder"
  finally:
    sys.modules.pop("MNN", None)


def _install_module():
  import importlib.util
  spec = importlib.util.spec_from_file_location("bp_install_sign_reader", REPO / "tools/bp_install_sign_reader.py")
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_the_installer_only_copies_under_data_bluepilot_and_changes_nothing_else():
  inst = _install_module()
  steps = inst.plan(Path("/x/speed_limit_reader.mnn"), Path("/y/mnn"), "comma@10.69.10.206")
  assert steps[0][0] == "ssh" and "mkdir -p" in steps[0][2]
  for step in steps:
    joined = " ".join(step)
    for banned in ("reboot", "shutdown", "rm ", "git ", "pip", "systemctl", "/usr/local/venv/lib", "/etc/"):
      assert banned not in joined, (banned, step)
    if step[0] == "scp":
      assert step[-1].split(":", 1)[1].startswith("/data/bluepilot/"), step
  # the device is never written outside the two folders; the last step only imports MNN to prove it loads
  assert "import MNN" in steps[-1][-1]


def test_the_installer_prints_a_plan_and_does_nothing_without_yes(tmp_path):
  import subprocess
  import sys
  weights = tmp_path / "w.mnn"
  weights.write_bytes(b"x")
  runtime = tmp_path / "rt"
  runtime.mkdir()
  r = subprocess.run([sys.executable, str(REPO / "tools/bp_install_sign_reader.py"), "--weights", str(weights),
                      "--runtime", str(runtime)], capture_output=True, text=True, timeout=30)
  assert r.returncode == 0 and "plan only" in r.stdout and "scp" in r.stdout


def test_a_memoryview_frame_is_accepted_as_the_device_hands_it_over():
  """The first on-car drive failed with: AttributeError: 'memoryview' object has no attribute 'size'.

  VisionBuf.data on the device is a memoryview. Every other test here fed numpy arrays, so none of
  them could see it. This is the type the camera actually gives.
  """
  raw = bytes(range(256)) * 4
  out = R.as_flat_u8(memoryview(raw))
  assert out.dtype == np.uint8 and out.ndim == 1 and out.size == len(raw)  # `.size` is what the model call uses
  assert bytes(out) == raw
  arr = np.arange(24, dtype=np.uint8).reshape(2, 12)
  assert R.as_flat_u8(arr).shape == (24,)  # an array, even a 2-D one, comes out flat


def test_step_converts_the_frame_before_it_is_used():
  """Guards the call site, not just the helper: the conversion must happen inside step()."""
  import inspect
  assert "nv12 = as_flat_u8(nv12)" in inspect.getsource(R.SignReader.step)
