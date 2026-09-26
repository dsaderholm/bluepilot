"""FusionPilot: BP-DEFAULTS.md must match what tools/bp_list_defaults.py emits.

Same guard as test_readme_is_built.py, for the same reason and a worse failure. This table is what
he walks the settings screens against -- *"I will go through each setting, check the description for
recommended value, and set my value to it"* -- and his car keeps the FIRST value it ever booted for
a key, so a wrong row sends him to change a setting to a number the code does not ship.

IT HAD ALREADY DRIFTED, found reviewing the pinned-holds deletion on 2026-09-25. The committed file
still listed `SmartCruiseControlVisionEarliness`, a param this fork had DELETED; gave High Speed
Adjustment Factor as 0.87 against a shipped 0.68 and Low Speed as 0.92 against 0.981 -- both numbers
he tunes his steering against; and was missing seven FusionPilot rows including Lane Centering
Strength and Hold Steering Through Stops.

AND THE CAUSE WAS THE GENERATOR, not carelessness. `--md` printed the table alone, so running the
command the file's own header prescribes DESTROYED that header. The safe-looking recovery is to
hand-edit one row, which is exactly what happened, repeatedly. The header is emitted by the tool now
and this test is what stops the pair drifting apart again.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOC = REPO / "BP-DEFAULTS.md"
TOOL = REPO / "tools" / "bp_list_defaults.py"


def test_bp_defaults_matches_the_generator():
  result = subprocess.run([sys.executable, str(TOOL), "--md"],
                          capture_output=True, text=True, cwd=str(REPO))
  assert result.returncode == 0, f"the generator itself failed:\n{result.stdout}{result.stderr}"
  generated = result.stdout.replace("\r\n", "\n").strip()
  committed = DOC.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
  assert generated == committed, (
    "BP-DEFAULTS.md no longer matches tools/bp_list_defaults.py.\n"
    "Run: python tools/bp_list_defaults.py --md > BP-DEFAULTS.md\n"
    "Do NOT hand-edit a row -- that is how it drifted into listing a deleted param and two wrong "
    "steering factors."
  )


def test_the_generator_emits_its_own_header():
  """The drift's root cause: `--md > BP-DEFAULTS.md` used to wipe the explanation, so nobody ran
  it. If the header goes back to being hand-maintained, regenerating silently deletes it again."""
  result = subprocess.run([sys.executable, str(TOOL), "--md"],
                          capture_output=True, text=True, cwd=str(REPO))
  head = result.stdout.replace("\r\n", "\n").split("| Where |")[0]
  assert "do not hand-edit" in head.lower(), (
    "the generated output no longer carries the 'do not hand-edit' header, so redirecting it over "
    "BP-DEFAULTS.md throws the explanation away and invites hand-editing all over again")
  assert "bp_list_defaults.py --md > BP-DEFAULTS.md" in head, (
    "the header must name the FULL redirect command; naming only the script is what produced a "
    "header that could not survive its own instructions")
