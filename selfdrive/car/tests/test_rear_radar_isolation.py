"""FusionPilot: rear radar detections must never reach the lead path.

THE FAILURE THIS PREVENTS, stated concretely. `radarState` is not just chevrons on a screen -- it
feeds `unconfirmed_lead.py`, whose output ICBM acts on by commanding the SET SPEED DOWN. A rear
target leaking into the lead path would therefore slow the car for a vehicle BEHIND it, and the
driver would experience unexplained braking with a clear road ahead.

Today that cannot happen, and not by filtering: `radard` subscribes to exactly one radar source,
`liveTracks`, which only `card` publishes and only from `RadarInterface.update()`. The rear radar
is a separate parser publishing a separate message. Nothing routes automatically.

So this is a STRUCTURAL guarantee, and structural guarantees are exactly the kind that a later
change erases without anyone noticing -- the rear plumbing lands, someone reaches for the radar
data that is already there, and the isolation is gone with every test still green. These assertions
are static so they cost nothing and cannot be driven around.
"""
import ast
import pathlib

REAR_NAMES = ("bp_rear_radar", "rearRadarBP", "RearRadar", "rear_radar")


def _root() -> pathlib.Path:
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise AssertionError("repo root not found")


def _src(rel: str) -> str:
  return (_root() / rel).read_text(encoding="utf-8")


class TestTheLeadPathCannotSeeBehindTheCar:

  def test_radard_never_mentions_the_rear_radar(self):
    """radard turns tracks into leads. It has no legitimate reason to know a rear sensor exists,
    and the day it does is the day a rear target can become a lead."""
    src = _src("selfdrive/controls/radard.py")
    found = [n for n in REAR_NAMES if n in src]
    assert not found, f"radard.py references the rear radar: {found}"

  def test_the_radar_interface_never_mentions_the_rear_radar(self):
    """RadarInterface.pts becomes liveTracks wholesale. A rear detection appended there is a lead
    by definition -- there is no later stage that could tell it apart."""
    src = _src("opendbc_repo/opendbc/car/ford/radar_interface.py")
    found = [n for n in REAR_NAMES if n in src]
    assert not found, f"radar_interface.py references the rear radar: {found}"

  def test_radard_subscribes_to_exactly_one_radar_source(self):
    """Pinned as a LIST rather than an absence, because the dangerous change is additive: someone
    adds a subscription and wires it into RD.update() in the same commit."""
    tree = ast.parse(_src("selfdrive/controls/radard.py"))
    lists = []
    for node in ast.walk(tree):
      if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "SubMaster"
          and node.args and isinstance(node.args[0], ast.List)):
        lists.append([e.value for e in node.args[0].elts if isinstance(e, ast.Constant)])
    assert lists, "no SubMaster call found in radard.py -- this test has gone stale"
    for subs in lists:
      radar_sources = [s for s in subs if "radar" in s.lower() or s == "liveTracks"]
      assert radar_sources == ["liveTracks"], (
        f"radard subscribes to more than liveTracks for radar data: {radar_sources}")

  def test_live_tracks_is_published_only_from_the_radar_interface(self):
    """The other end of the same guarantee. If liveTracks were ever assigned from anything but the
    RadarInterface's own return, the isolation above would be decorative."""
    tree = ast.parse(_src("selfdrive/car/card.py"))
    sources = []
    for node in ast.walk(tree):
      if isinstance(node, ast.Assign):
        for t in node.targets:
          if isinstance(t, ast.Attribute) and t.attr == "liveTracks":
            sources.append(node.value)
    assert sources, "nothing assigns liveTracks in card.py -- this test has gone stale"
    for value in sources:
      assert isinstance(value, ast.Name), (
        f"liveTracks assigned from a non-trivial expression: {ast.dump(value)[:80]}")
      assert value.id == "RD", f"liveTracks assigned from {value.id!r}, expected the RadarData 'RD'"
