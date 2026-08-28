"""PlannerChecks must explain the rule it claims to explain.

A Salt Lake City -> Yosemite drive raised `commIssue` repeatedly on the curvy sections. That event
is ET.SOFT_DISABLE, so it DISENGAGES rather than merely warning, and it fires when plannerd marks
`longitudinalPlan` invalid -- which it did on 162 of 162 frames in one segment while publishing at a
clean 20.0 Hz. Which of all_checks' three tests failed, and for which service, was recorded nowhere.

The diagnostic is only worth anything if it names the SAME services upstream's rule uses. If
upstream edits its list on a merge and ours does not, the field keeps reporting confidently about
the wrong set -- which is worse than not publishing it, because it would be believed.
"""
import ast
import re

PLANNER = "selfdrive/controls/lib/longitudinal_planner.py"
OURS = "sunnypilot/selfdrive/controls/lib/longitudinal_planner.py"


def _upstream_plan_valid_services():
  """The service_list literal upstream passes to all_checks for longitudinalPlan.valid.

  Parsed rather than grepped: the words appear in prose in both files, and this must read the
  actual argument of the actual call or it proves nothing.
  """
  tree = ast.parse(open(PLANNER, encoding="utf-8").read())
  for node in ast.walk(tree):
    if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
      continue
    tgt = node.targets[0]
    if not (isinstance(tgt, ast.Attribute) and tgt.attr == "valid"):
      continue
    if not (isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "all_checks"):
      continue
    for kw in node.value.keywords:
      if kw.arg == "service_list" and isinstance(kw.value, ast.List):
        return tuple(e.value for e in kw.value.elts)
    if node.value.args and isinstance(node.value.args[0], ast.List):
      return tuple(e.value for e in node.value.args[0].elts)
  return None


def test_the_upstream_rule_is_still_findable():
  """If upstream restructures publish(), this test must fail loudly rather than pass vacuously."""
  assert _upstream_plan_valid_services() is not None, (
    f"could not find longitudinalPlan.valid = ...all_checks(service_list=[...]) in {PLANNER}; "
    "PlannerChecks may now be explaining a rule that no longer exists")


def _our_plan_valid_services():
  """Parsed, not imported: longitudinal_planner pulls in sunnypilot.models.helpers, which needs
  device-only modules and cannot load offline."""
  tree = ast.parse(open(OURS, encoding="utf-8").read())
  for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)        and node.targets[0].id == "PLAN_VALID_SERVICES":
      return tuple(e.value for e in node.value.elts)
  return None


def test_planner_checks_mirror_the_plan():
  ours = _our_plan_valid_services()
  assert ours is not None, "PLAN_VALID_SERVICES not found"
  assert _upstream_plan_valid_services() == ours


def test_the_bit_order_is_documented_for_the_reader():
  """The masks are useless without their bit order, and it lives in one place."""
  capnp = open("cereal/custom.capnp", encoding="utf-8").read()
  block = capnp[capnp.index("plannerChecks @11"):]
  PLAN_VALID_SERVICES = _our_plan_valid_services()
  doc = capnp[:capnp.index("plannerChecks @11")]
  order = re.findall(r"\d=(\w+)", doc[doc.rindex("Bit order"):])
  assert tuple(order) == PLAN_VALID_SERVICES, f"capnp documents {order}, code uses {PLAN_VALID_SERVICES}"
  assert "notAlive @0" in block and "freqBad @1" in block and "notValid @2" in block
