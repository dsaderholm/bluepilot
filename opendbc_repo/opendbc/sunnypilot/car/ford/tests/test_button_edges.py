"""FusionPilot: one press of a button must produce exactly one press event.

Route 000003b7, 2026-08-24. Raw CAN, bit 29 of Steering_Data_FD1 on `src 0`: RES+ held from
t+86.60 to t+87.32. ONE hold. The decoder emitted about seventy `accelCruise` events in that
0.72 s, and 452 across the drive.

The cause was that RES+ has TWO signal names in `BUTTONS` and both stored their edge memory in the
same `button_states` slots. One of them is always low while the other is high, and the low one's
release path cleared the memory the high one had set, so every frame looked like a new press.

What that cost downstream is why this file exists rather than a note: each phantom press ran the
v_cruise button path, `vCruiseCluster` oscillated 22 <-> 25 with the real dash sitting still at 22,
and Speed Limit Assist -- which reads set-speed movement as "the driver took over" -- fell out of
`active` and back in 1238 times, chiming "Set speed changed" on each re-entry.

The companion file `test_button_mapping.py` says this dispatch "needs a CAN parser and cannot run
offline". It needs `cp.vl[addr][signal]`, which is two nested dicts, so it can.
"""
from types import SimpleNamespace as NS

from opendbc.car import Bus, structs
from opendbc.sunnypilot.car.ford.carstate_ext import CarStateExt
from opendbc.sunnypilot.car.ford.values_ext import BUTTONS

ButtonType = structs.CarState.ButtonEvent.Type
ADDR = "Steering_Data_FD1"
SET_INC = "CcAslButtnSetIncPress"     # ICBM presses this one
RES_INC = "CcAslButtnResIncPress"     # the wheel sends this one
ALL_SIGNALS = sorted({b.can_msg for b in BUTTONS})


class FakeParser:
  """Just `cp.vl[addr][signal]` -- the only thing the dispatch reads."""

  def __init__(self):
    self.vl = {ADDR: dict.fromkeys(ALL_SIGNALS, 0)}

  def set(self, **signals):
    self.vl[ADDR].update(signals)


def _ext():
  ext = CarStateExt(NS(flags=0, carFingerprint="FORD_FUSION_MK5"), NS())
  return ext


def _frames(ext, cp, n, cruise_enabled=True):
  """Run n frames and return every button event emitted, in order."""
  out = []
  for _ in range(n):
    ret = structs.CarState.new_message() if hasattr(structs.CarState, "new_message") else structs.CarState()
    ret.cruiseState.enabled = cruise_enabled
    ret_sp = structs.CarStateSP.new_message() if hasattr(structs.CarStateSP, "new_message") else structs.CarStateSP()
    # NO try/except HERE. The first version skipped on any exception, so all seven tests reported
    # "skipped" against a stub that was one dict key short -- a test file that could not fail.
    ext.update(ret, ret_sp, {Bus.pt: cp})
    # `ext.button_events`, not `ret.buttonEvents`: the dispatch parks them on the ext and
    # carstate.py splices them into ret (`*self.button_events`). Reading ret here returns an empty
    # list on every frame and every assertion below passes vacuously.
    out.extend([(str(b.type).split(".")[-1], bool(b.pressed)) for b in ext.button_events])
  return out


def _presses(events, kind):
  return [e for e in events if e[0] == kind and e[1]]


def test_a_held_res_plus_is_one_press_not_seventy():
  """THE REPORTED BUG, in its measured shape: a 0.72 s hold is 72 frames at 100 Hz."""
  ext, cp = _ext(), FakeParser()
  cp.set(**{RES_INC: 1, SET_INC: 0})
  events = _frames(ext, cp, 72)
  n = len(_presses(events, "accelCruise"))
  assert n == 1, f"a single held RES+ produced {n} press events: the 100 Hz storm from route 000003b7"


def test_the_release_still_arrives():
  """Suppressing repeats must not suppress the edge that ends the press."""
  ext, cp = _ext(), FakeParser()
  cp.set(**{RES_INC: 1, SET_INC: 0})
  _frames(ext, cp, 30)
  cp.set(**{RES_INC: 0, SET_INC: 0})
  events = _frames(ext, cp, 5)
  releases = [e for e in events if e[0] == "accelCruise" and not e[1]]
  assert len(releases) == 1, f"expected exactly one release, got {len(releases)}"


def test_two_separate_presses_are_two_events():
  """The fix must not collapse genuine repeat presses into one."""
  ext, cp = _ext(), FakeParser()
  for _ in range(2):
    cp.set(**{RES_INC: 1, SET_INC: 0})
    _frames(ext, cp, 20)
    cp.set(**{RES_INC: 0, SET_INC: 0})
    _frames(ext, cp, 20)
  # count across the whole sequence
  ext2, cp2 = _ext(), FakeParser()
  events = []
  for _ in range(2):
    cp2.set(**{RES_INC: 1, SET_INC: 0})
    events += _frames(ext2, cp2, 20)
    cp2.set(**{RES_INC: 0, SET_INC: 0})
    events += _frames(ext2, cp2, 20)
  assert len(_presses(events, "accelCruise")) == 2


def test_the_icbm_signal_alone_behaves_the_same():
  """ICBM asserts SetInc. Held, it must also be one press -- the bug is symmetric."""
  ext, cp = _ext(), FakeParser()
  cp.set(**{RES_INC: 0, SET_INC: 1})
  events = _frames(ext, cp, 72)
  assert len(_presses(events, "accelCruise")) == 1


def test_both_signals_high_is_still_one_button():
  """They are two names for RES+, not two buttons."""
  ext, cp = _ext(), FakeParser()
  cp.set(**{RES_INC: 1, SET_INC: 1})
  events = _frames(ext, cp, 40)
  assert len(_presses(events, "accelCruise")) == 1


def test_with_cruise_off_it_is_a_resume_and_still_one():
  """RES+ means resume when cruise is off. The dedup must hold on that arm too."""
  ext, cp = _ext(), FakeParser()
  cp.set(**{RES_INC: 1, SET_INC: 0})
  events = _frames(ext, cp, 72, cruise_enabled=False)
  assert len(_presses(events, "resumeCruise")) == 1
  assert len(_presses(events, "setCruise")) == 0, "RES+ must never report a set"


def test_set_minus_is_unaffected():
  """SET- has a single signal and must keep working exactly as before."""
  ext, cp = _ext(), FakeParser()
  cp.set(CcAslButtnSetDecPress=1)
  events = _frames(ext, cp, 40)
  assert len(_presses(events, "decelCruise")) == 1
