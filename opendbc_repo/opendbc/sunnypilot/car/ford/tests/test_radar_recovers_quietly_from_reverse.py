"""FusionPilot: the radar must stay "unavailable" for a moment after Reverse, not flip back early.

Taken from BluePilot PR #190 (issue #188) on 2026-09-12, because it is HIS alert: the week of
2026-09-08 showed "Communication Issue Between Processes | longitudinalPlan" 12 times across 26 drives,
every one a second or two after `radarTempUnavailable` -- i.e. every time he backed out of a space.
Route 00000447 t+644..646: reverse, radar unavailable, drive, then the comm-issue alert.

The mechanism, from upstream's own measurement on a Delphi MRR (his radar -- FORD_FUSION_MK5 keeps
the default `Bus.radar: RADAR.DELPHI_MRR`): the scan-index freeze that means "in Reverse" clears
before the radar is producing detections again. `radarTempUnavailable` is what suppresses selfdrived's
generic commIssue while it is set, so it dropping a beat early lets commIssue fire instead.

These drive the REAL `_update_delphi_mrr` with a stand-in parser, because the hold is a counter and a
counter that is never ticked from a real call proves nothing. The selfdrived half of the PR (a 200 ms
debounce on the catch-all) cannot be exercised offline and is covered by reading, not here.
"""
from types import SimpleNamespace as NS

from opendbc.car import structs
from opendbc.car.ford.radar_interface import RadarInterface


class _Zeros(dict):
  """Any detection message the walk asks for reads as all zeros -- no valid points."""
  def __missing__(self, key):
    return 0


class _Vl(dict):
  def __missing__(self, key):
    self[key] = _Zeros()
    return self[key]


def _radar():
  r = RadarInterface.__new__(RadarInterface)
  r.pts = {}
  r.points = []
  r.clusters = []
  r.track_id = 0
  r.radar_unavailable_cnt = 0
  r.prev_headerScanIndex = 0
  r.radar_unavailable_hold_frames = 0
  r.scan_index_invalid_cnt = 0
  r.rcp = NS(vl=_Vl())
  return r


def _frame(r, scan_index):
  """One radar frame. Returns whether it reported the radar temporarily unavailable."""
  r.rcp.vl["MRR_Header_InformationDetections"]["CAN_SCAN_INDEX"] = scan_index
  ret = structs.RadarData()
  r._update_delphi_mrr(ret)
  return bool(ret.errors.radarUnavailableTemporary)


def _reverse(r, frames=10):
  """In Reverse the radar keeps re-sending its last header, so the scan index stops advancing."""
  return [_frame(r, r.prev_headerScanIndex) for _ in range(frames)]


def _drive(r, frames):
  """Back in Drive: the scan index advances 0,1,2,3,0,... on every frame again."""
  return [_frame(r, (r.prev_headerScanIndex + 1) % 4) for _ in range(frames)]


def test_reverse_reports_the_radar_unavailable():
  r = _radar()
  assert _reverse(r)[-1]


def test_the_flag_is_HELD_after_leaving_reverse():
  """The whole fix. Without the hold this is False on the very first Drive frame."""
  r = _radar()
  _reverse(r)
  held = _drive(r, r.RADAR_UNAVAILABLE_HOLD_FRAMES)
  assert all(held), f"the unavailable flag dropped after {held.index(False)} frames of Drive"


def test_and_then_it_CLEARS_rather_than_sticking():
  """A hold that never ends would take the radar away for the rest of the drive."""
  r = _radar()
  _reverse(r)
  seq = _drive(r, r.RADAR_UNAVAILABLE_HOLD_FRAMES + 3)
  assert not seq[-1]


def test_the_hold_counts_from_the_END_of_reverse_not_its_start():
  """A long reverse must not use up the hold before he shifts to Drive."""
  r = _radar()
  _reverse(r, frames=r.RADAR_UNAVAILABLE_HOLD_FRAMES * 4)
  assert _drive(r, 1)[0]


def test_the_hold_is_about_one_second_of_radar_frames():
  """~33 Hz header rate. Pinned so nobody stretches it into hiding a genuinely dead radar."""
  assert RadarInterface.RADAR_UNAVAILABLE_HOLD_FRAMES == 33
