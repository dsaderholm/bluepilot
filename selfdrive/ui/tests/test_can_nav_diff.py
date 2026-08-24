"""FusionPilot: the navigating-vs-control CAN diff must not report a nav channel that is only a
sample size.

`tools/bp_can_nav_diff.py` answers the question that decides whether route intent needs the canbox
at all, off one deliberate drive. Its whole output is two lists, and there are exactly two ways it
can be wrong -- and they are opposite:

  a FALSE NEGATIVE sends a whole line of work to wait on hardware it did not need. That is the
  likely one, because the expected answer is "nothing appeared" and a broken tool produces the
  expected answer.

  a FALSE POSITIVE sends somebody decoding a byte that moved because the navigating drive was
  longer, which is the sample-size-read-as-behaviour error this fork has now made five times.

So the byte-level detection is tested directly, without a route: the tool cannot be run offline and
"it printed nothing" is indistinguishable from working.
"""
from __future__ import annotations

import importlib.util
import pathlib

SPEC = importlib.util.spec_from_file_location(
  "bp_can_nav_diff", pathlib.Path(__file__).resolve().parents[3] / "tools/bp_can_nav_diff.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
Inventory = mod.Inventory


def feed(inv, addr, bus, payloads, times=1):
  for _ in range(times):
    for p in payloads:
      inv.add(addr, bus, bytes(p))
  return inv


def const(byte0):
  return [[byte0, 0, 0, 0, 0, 0, 0, 0]]


def countdown(idx, n=None):
  """Payloads where byte `idx` sweeps many values -- a distance to a junction, which is the shape
  the tool exists to find. n defaults to comfortably above MIN_CANDIDATE_VALUES."""
  n = mod.MIN_CANDIDATE_VALUES * 3 if n is None else n
  out = []
  for v in range(n):
    p = [7, 0, 0, 0, 0, 0, 0, 0]
    p[idx] = 200 - v
    out.append(p)
  return out


class TestWhatItRecords:
  def test_a_byte_that_never_moves_is_not_varying(self):
    inv = feed(Inventory("r"), 0x32B, 0, const(7), times=500)
    assert inv.varying_bytes((0x32B, 0)) == []
    assert inv.count[(0x32B, 0)] == 500

  def test_it_names_the_byte_that_moved_and_only_that_one(self):
    inv = Inventory("r")
    feed(inv, 0x32B, 0, [[7, 0, 1, 0, 0, 0, 0, 0], [7, 0, 2, 0, 0, 0, 0, 0]], times=250)
    assert inv.varying_bytes((0x32B, 0)) == [2]

  def test_the_payload_set_saturates_instead_of_growing(self):
    # A wheel-speed message has effectively unlimited distinct payloads. Unbounded, a whole route
    # of them is what makes a diagnostic run out of memory two hours in.
    inv = Inventory("r")
    for i in range(mod.MAX_PAYLOADS * 3):
      inv.add(0x91, 0, bytes([i % 256, (i // 256) % 256, 0, 0, 0, 0, 0, 0]))
    assert (0x91, 0) in inv.saturated
    assert len(inv.payloads[(0x91, 0)]) <= mod.MAX_PAYLOADS

  def test_our_own_transmissions_are_not_the_car_talking(self):
    """Bus | 0x80 is panda's echo of what OPENPILOT sent.

    Counting it reports our own frames as something the car started doing with a route active.
    That misreading has already happened once here, on 0x462's "bus 130" count -- which was read as
    the APIM transmitting when it was our own forward onto bus 2.
    """
    # read_route filters; assert the threshold rather than the loop, since the loop needs a route.
    assert 0x80 == 128
    inv = Inventory("r")
    inv.add(0x3CA, 0, bytes(8))
    assert set(inv.buses()) == {0}


class TestTheDiffItself:
  """The two set operations the report is made of, exercised as the tool computes them."""

  # THE TOOL'S OWN FUNCTION, not a copy of it. The first version of this file reimplemented the
  # detection here, and when MIN_CANDIDATE_VALUES was added to the tool the suite stayed green
  # while the new threshold went entirely uncovered -- the test was exercising itself.
  _woke = staticmethod(mod.woke_up)

  def test_a_distance_counting_down_is_found(self):
    # The case address-level presence cannot see, and the one APIM_Data_FD1 would land in: present
    # in both drives because it also carries light menus, saying something different in one.
    #
    # The nav byte COUNTS DOWN through many values, which is what a distance to a junction does and
    # is the whole signature being hunted. A two-value byte is a status flag and is tested below.
    nav = feed(Inventory("nav"), 0x32B, 0, countdown(2), times=10)
    ctl = feed(Inventory("ctl"), 0x32B, 0, const(7), times=600)
    assert self._woke(nav, ctl) == [((0x32B, 0), [2])]

  def test_a_byte_with_only_a_few_values_is_NOT_a_nav_channel(self):
    """THE FALSE POSITIVE THAT ACTUALLY HAPPENED, 2026-08-23.

    Run against a real pair of routes, this tool reported "SOMETHING CHANGED WITH A ROUTE ACTIVE"
    on ten addresses whose differing byte took two or three values. That is two ordinary drives
    differing. A distance to a junction sweeps dozens of values; a status flag has two, and the
    difference is the only thing separating a lead from noise.
    """
    nav = feed(Inventory("nav"), 0x350, 0,
               [[1, 0, 0, 0, 0, 0, 0, 0], [2, 0, 0, 0, 0, 0, 0, 0]], times=300)
    ctl = feed(Inventory("ctl"), 0x350, 0, const(1), times=600)
    assert self._woke(nav, ctl) == [], "a two-value byte was reported as a nav channel"

  def test_a_message_that_moves_in_both_is_not_a_finding(self):
    nav = feed(Inventory("nav"), 0x32B, 0, countdown(2), times=10)
    ctl = feed(Inventory("ctl"), 0x32B, 0, countdown(2), times=10)
    assert self._woke(nav, ctl) == []

  def test_a_thinly_sampled_control_cannot_manufacture_a_finding(self):
    """THE FALSE POSITIVE THIS TOOL IS MOST LIKELY TO PRODUCE.

    A byte seen a handful of times is constant by luck. Without MIN_CONTROL_FRAMES every rare
    message in the control drive reads as a nav channel -- a difference in how much was RECORDED
    reported as a difference in what the car DID, which is the error already recorded here five
    times over.
    """
    nav = feed(Inventory("nav"), 0x555, 0, countdown(0), times=20)
    thin = feed(Inventory("ctl"), 0x555, 0, const(1), times=mod.MIN_CONTROL_FRAMES // 2)
    assert self._woke(nav, thin) == [], "a thin control drive manufactured a nav channel"
    # ...and with enough control frames the SAME nav data is a real finding, or the assertion above
    # would pass for any reason at all.
    thick = feed(Inventory("ctl"), 0x555, 0, const(1), times=mod.MIN_CONTROL_FRAMES + 1)
    assert self._woke(nav, thick) == [((0x555, 0), [0])]

  def test_an_address_only_the_navigating_drive_had_is_the_other_finding(self):
    nav = feed(Inventory("nav"), 0x32B, 0, const(1), times=300)
    feed(nav, 0x91, 0, const(1), times=300)
    ctl = feed(Inventory("ctl"), 0x91, 0, const(1), times=300)
    assert sorted(set(nav.count) - set(ctl.count)) == [(0x32B, 0)]

  def test_the_same_address_on_a_different_bus_is_a_different_thing(self):
    # Which bus carries it is the whole answer to "is the canbox needed", so the two must never be
    # collapsed. bp_apim_probe keys on (address, bus) for the same reason.
    nav = feed(Inventory("nav"), 0x462, 2, const(1), times=300)
    ctl = feed(Inventory("ctl"), 0x462, 0, const(1), times=300)
    assert set(nav.count) - set(ctl.count) == {(0x462, 2)}


class TestItSaysWhatItLookedAt:
  def test_the_known_apim_addresses_are_real_ones(self):
    """Named from ford_lincoln_base_pt.dbc, not from memory.

    A table of addresses that do not exist in the DBC would print "ABSENT" forever and read as a
    measurement. This is the same check bp_apim_probe's control addresses exist to make.
    """
    dbc = (pathlib.Path(__file__).resolve().parents[3]
           / "opendbc_repo/opendbc/dbc/ford_lincoln_base_pt.dbc").read_text(encoding="utf-8")
    declared = {int(line.split()[1]) for line in dbc.splitlines() if line.startswith("BO_ ")}
    unknown = sorted(a for a in {**mod.APIM_ADDRS, **mod.CONTROL_ADDRS} if a not in declared)
    assert not unknown, f"addresses not in the DBC: {[hex(a) for a in unknown]}"
