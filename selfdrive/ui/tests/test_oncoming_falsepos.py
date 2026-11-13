"""The oncoming false-positive tally must not get its verdict backwards.

`tools/bp_oncoming_falsepos.py` answers a question passing_assist.py has owed since the veto was
written -- does it fire on divided highway, where it cannot be right -- and it is the verification
of a fix that shipped and has never been checked on road. Its whole output is four numbers and a
verdict, so miscounting the cross-tab is the only way it can fail, and it would fail by reporting a
leak as clean or the reverse.
"""
from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace as NS

SPEC = importlib.util.spec_from_file_location(
  "bp_oncoming_falsepos", pathlib.Path(__file__).resolve().parents[3] / "tools/bp_oncoming_falsepos.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
Tally = mod.Tally

FAST = 30.0
CRAWL = 1.0


class _Msg:
  def __init__(self, kind, **fields):
    self._kind = kind
    setattr(self, kind, NS(**fields))

  def which(self):
    return self._kind


def car(v=FAST):
  return _Msg("carState", vEgo=v)


def plan(veto):
  return _Msg("longitudinalPlanSP", passingAssist=NS(oncomingAnySide=veto))


def mapd(one_way, hwy="motorway", tile=True):
  return _Msg("mapdOut", oneWay=one_way, highwayClass=hwy, tileLoaded=tile)


def test_the_veto_up_on_a_one_way_carriageway_is_the_false_positive():
  """The whole point. A divided highway's carriageway is one-way, so oncoming traffic in an
  adjacent lane is impossible by construction -- the veto cannot be right there."""
  t = Tally()
  t.feed_segment([car(), plan(True), mapd(True)])
  assert t.cells[(True, True)] == 1
  assert t.motorway_cells[(True, True)] == 1


def test_the_veto_up_on_a_two_way_road_is_the_veto_working():
  t = Tally()
  t.feed_segment([car(), plan(True), mapd(False)])
  assert t.cells[(True, True)] == 0
  assert t.cells[(True, False)] == 1


def test_quiet_on_a_one_way_carriageway_is_the_good_case():
  t = Tally()
  t.feed_segment([car(), plan(False), mapd(True)])
  assert t.cells[(False, True)] == 1
  assert t.cells[(True, True)] == 0


def test_no_tile_is_not_a_claim_that_the_road_is_two_way():
  """`tileLoaded` false means the map cannot answer. Counting it as two-way would let unavailable
  masquerade as evidence, which is the same fault the radar side has a rule about."""
  t = Tally()
  t.feed_segment([car(), plan(True), mapd(True, tile=False)])
  assert t.frames == 0
  assert t.mapd_frames == 1, "the frame is still seen, it just cannot be scored"


def test_crawling_is_not_scored():
  t = Tally()
  t.feed_segment([car(v=CRAWL), plan(True), mapd(True)])
  assert t.frames == 0


def test_a_frame_before_any_decision_is_not_scored():
  """Without a published veto state there is nothing to cross-tab, and defaulting it either way
  invents data."""
  t = Tally()
  t.feed_segment([car(), mapd(True)])
  assert t.frames == 0


def test_city_one_way_streets_stay_out_of_the_motorway_row():
  """A one-way city street has no oncoming either, but it is not the road the complaint was about.
  Keeping it out of the judged row is what stops a downtown drive diluting the I-15 answer."""
  t = Tally()
  t.feed_segment([car(), plan(True), mapd(True, hwy="residential")])
  assert t.cells[(True, True)] == 1
  assert t.motorway_cells[(True, True)] == 0


def test_the_veto_state_persists_until_the_next_decision_frame():
  """longitudinalPlanSP and mapdOut arrive at different rates, so the last published veto state is
  what applies to the mapd frames that follow it."""
  t = Tally()
  t.feed_segment([car(), plan(True), mapd(True), mapd(True), plan(False), mapd(True)])
  assert t.cells[(True, True)] == 2
  assert t.cells[(False, True)] == 1
