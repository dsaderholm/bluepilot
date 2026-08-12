"""FusionPilot: README.md must match the parts it is generated from.

The README is assembled by tools/bp_build_readme.py so that three branches can share one document
without conflicting -- each owns files nobody else touches. That only holds if the committed
README.md actually reflects those files. Edit a part, forget to rebuild, and the published page
silently disagrees with the source of truth, which is worse than either alone: the next person edits
the part again and their change appears to do nothing.

Cheap to check and impossible to notice by eye, which is exactly the shape of guard this repo keeps.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def test_readme_matches_its_parts():
  result = subprocess.run([sys.executable, str(REPO / "tools" / "bp_build_readme.py"), "--check"],
                          capture_output=True, text=True, cwd=str(REPO))
  assert result.returncode == 0, (
    f"README.md does not match readme/sections/ and readme/fragments/.\n"
    f"Run: python tools/bp_build_readme.py\n\n{result.stdout}{result.stderr}")


def test_every_fragment_anchor_has_a_directory():
  """An anchor naming a directory that does not exist is a silent no-op, so a branch could add its
  section to `readme/fragments/feature/` (singular) and see nothing happen with no error."""
  anchors = set()
  for section in (REPO / "readme" / "sections").glob("*.md"):
    for line in section.read_text(encoding="utf-8").split("\n"):
      if line.strip().startswith("<!-- fragments: "):
        anchors.add(line.strip()[len("<!-- fragments: "):].split("-->")[0].strip())
  assert anchors, "no fragment anchors found; the README would not be extensible"
  for name in sorted(anchors):
    assert (REPO / "readme" / "fragments" / name).is_dir(), (
      f"section files reference `{name}` but readme/fragments/{name}/ does not exist")
