"""FusionPilot: `openpilotLongitudinalControl` stopped meaning "openpilot is driving".

Under the stock-ACC passthrough that flag is True while Ford authors every command. Upstream uses it
as shorthand for "openpilot's own plan is driving the car", and every consumer that does is wrong in
this configuration. Two have bitten already, both in this file:

    FCW suppression      chimed while Ford braked normally for a lead   (found by audit)
    gap -> personality   cycled a setting that steers a discarded plan  (he reported it twice)

So this asserts on the SOURCE for every use of the flag in selfdrived, because the failure is not
that any one of them is wrong -- it is that the next one added will be wrong the same way.
"""
from __future__ import annotations

import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1] / "selfdrived.py").read_text(encoding="utf-8")


def _guards_of(target_attr):
  """Every `if` condition enclosing an assignment to `self.<target_attr>`.

  Resolved through the AST rather than by line proximity. The first version of this test measured
  distance from a COMMENT and passed against the exact mutant it was written for -- the third time
  in two days an assertion matched a label instead of the thing.
  """
  tree = ast.parse(SRC)
  for node in ast.walk(tree):
    for child in ast.iter_child_nodes(node):
      child.parent = node
  guards = []
  for node in ast.walk(tree):
    if not isinstance(node, ast.Assign):
      continue
    hit = any(isinstance(t, ast.Attribute) and t.attr == target_attr
              and isinstance(t.value, ast.Name) and t.value.id == "self" for t in node.targets)
    if not hit:
      continue
    cur = getattr(node, "parent", None)
    while cur is not None:
      if isinstance(cur, ast.If):
        guards.append(ast.unparse(cur.test))
      cur = getattr(cur, "parent", None)
  return guards


def test_the_gap_button_does_not_cycle_personality_under_the_passthrough():
  """His report, twice: "when I adjusted my gap, it said personality on the screen". With the
  passthrough on, openpilot's personality steers a plan that is thrown away, so the press should
  reach Ford's own follow distance instead of silently changing nothing."""
  guards = _guards_of("personality")
  assert guards, "nothing guards the personality write any more -- has it moved?"
  longitudinal = [g for g in guards if "openpilotLongitudinalControl" in g]
  assert longitudinal, "the personality write is no longer gated on longitudinal control at all"
  assert any("stock_acc_passthrough" in g for g in guards), (
    "the gap button still cycles openpilot's personality with the passthrough on, where that "
    f"setting drives nothing -- the press has to reach Ford's follow distance. guards: {guards}")


def test_the_fcw_suppression_still_knows_about_the_passthrough():
  """Guards the earlier fix in the same file, so a later edit cannot quietly undo it."""
  assert "stock_is_the_brake" in SRC, "the FCW suppression no longer asks who is braking"
  i = SRC.index("stock_is_the_brake =")
  assert "stock_acc_passthrough" in SRC[i:i + 200], (
    "stock_is_the_brake stopped consulting the passthrough, so the model FCW will chime while "
    "Ford brakes normally for a lead")


def test_the_inert_passthrough_is_announced_and_only_once():
  """The camera cancelling kills Ford ACC for the whole drive, and until now only a pill said so.

  Route 0000038d: cancel and deny on 8,988 of 8,990 engaged frames from t+30.8. He worked it out
  from the seat and called it "annoying that it bricks it for the whole drive" -- so it needs to
  reach him rather than wait to be noticed.

  ONCE, on the transition. It does not recover within a drive, and a repeating alert for a permanent
  condition is one he learns to ignore, which is worse than silence: then it fails to reach him on
  the day it matters. But it must RE-ARM if the camera ever clears, because
  `passthrough_cancel_frames` resets on any other refusal reason -- latching for the ignition cycle
  would hide a second occurrence."""
  assert "accPassthroughInert" in SRC, "nothing raises the inert alert -- the event is dead"
  i = SRC.index("EventNameSP.accPassthroughInert")
  window = SRC[max(0, i - 900):i + 300]
  assert "acc_passthrough_inert_announced" in window, (
    "the alert is raised with no once-only latch, so it repeats every frame for a condition that "
    "lasts the whole drive")
  assert "= False" in SRC[i:i + 900], (
    "nothing re-arms the latch when the camera clears, so a second occurrence in the same ignition "
    "cycle would be silent")

  # And it must be in the SUBMASTER LIST, not merely mentioned. The first version of this assertion
  # searched the whole file, which the read `self.sm.alive['controllerStateBP']` satisfies on its
  # own -- so removing it from the subscription still passed. Scope to the SubMaster construction.
  sub = SRC[SRC.index("messaging.SubMaster(["):]
  sub = sub[:sub.index(")")]
  assert "'controllerStateBP'" in sub, (
    "selfdrived does not SUBSCRIBE controllerStateBP, so `alive` is False forever, the branch never "
    "runs and the alert cannot fire")

  # And the SP events go on their own object; `self.events` would be a NameError-free silent no-op
  # into the wrong stream.
  assert "self.events_sp.add(EventNameSP.accPassthroughInert)" in SRC, (
    "the inert alert is added to the wrong events object -- SP events publish on onroadEventsSP")
  assert "EventNameSP = custom.OnroadEventSP.EventName" in SRC, (
    "EventNameSP is undefined in this module -- raising the alert is a NameError in the control "
    "loop, which takes selfdrived down")


def test_controllerstatebp_is_ignored_so_its_absence_cannot_disengage():
  """A DIAGNOSTIC SUBSCRIPTION MUST NOT BE ABLE TO TAKE OPENPILOT OUT.

  `controllerStateBP` is subscribed only to read `accAuthority` for the inert alert, but it is
  published solely by `bp_card_publisher` when the Ford BluePilot carcontroller sets
  `lateralUncertainty`. Where that is absent the service never arrives, `sm.all_alive()` is False,
  and selfdrived adds `EventName.commIssue` -- a DISENGAGING event. openpilot would be unusable on
  somebody else's car because of an alert added for diagnostics, and his friend runs this same
  branch for ICBM alone.

  `ignore_alive` still populates `sm.alive[...]`, so the alert keeps working; only the aggregate
  check is relaxed. Caught in review the same night it was written."""
  i = SRC.index("ignore = ")
  block = SRC[i:SRC.index("self.sm = messaging.SubMaster(", i)]
  assert "'controllerStateBP'" in block, (
    "controllerStateBP is subscribed but not ignored, so a car that does not publish it raises "
    "commIssue every frame and openpilot disengages -- for a diagnostic")
