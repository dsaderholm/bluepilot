#!/usr/bin/env python3
"""FusionPilot: assemble README.md from parts, so three branches can share one document.

WHY THIS EXISTS
---------------
The README describes the whole fork, but the fork is three branches -- ICBM and Smart Cruise Control
here, passing assist, and the radar detector -- each rebasing onto this one and each needing to
describe its own features. A single hand-edited file makes that a conflict on every rebase, and git
resolves it wrong in a specific and dangerous way: it replays the feature branch's OLDER copy over
the base's newer one, silently reverting the entire document. That happened on 2026-08-11, reverting
a same-day rewrite, and was caught only by someone reading the result.

So each branch owns FILES NOBODY ELSE TOUCHES, and README.md is generated. Two branches adding a
section add two different files, which git merges without an opinion. If README.md itself conflicts,
the resolution is no longer a careful hunk-by-hunk merge -- take either side and re-run this.

LAYOUT
------
    readme/sections/*.md          the shared document, owned by the base branch
    readme/fragments/features/    one file per feature area
    readme/fragments/diagnostics/ one bullet per branch that adds a diagnostic
    readme/fragments/portability/ one bullet per branch with its own hardware caveats

Sections are concatenated in filename order. A section may contain an anchor line:

    <!-- fragments: features -->

which is replaced by every file in that fragment directory, also in filename order. Number your files
with room to insert (10, 20, 30) and pick a name nobody else would: `40-passing-assist.md`, not
`40-section.md`.

USAGE
-----
    python tools/bp_build_readme.py            # rewrite README.md
    python tools/bp_build_readme.py --check    # exit 1 if README.md is stale, print a diff

`--check` is what the test uses, so a parts edit that never made it into README.md cannot ship.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SECTIONS = REPO / "readme" / "sections"
FRAGMENTS = REPO / "readme" / "fragments"
README = REPO / "README.md"

ANCHOR = "<!-- fragments: "

BANNER = ("<!-- GENERATED FILE. Edit readme/sections/ or readme/fragments/, then run\n"
          "     python tools/bp_build_readme.py\n"
          "     Editing this file directly will be overwritten. -->\n")


def read_parts(directory: Path) -> list[str]:
  if not directory.is_dir():
    return []
  return [p.read_text(encoding="utf-8").strip("\n") for p in sorted(directory.glob("*.md"))]


def build() -> str:
  if not SECTIONS.is_dir():
    sys.exit(f"no {SECTIONS} -- nothing to build from")

  out: list[str] = []
  for section in sorted(SECTIONS.glob("*.md")):
    for line in section.read_text(encoding="utf-8").split("\n"):
      if line.strip().startswith(ANCHOR):
        name = line.strip()[len(ANCHOR):].split("-->")[0].strip()
        parts = read_parts(FRAGMENTS / name)
        if not parts:
          # An anchor with nothing behind it is normal: this branch may not carry that feature.
          # Leaving no trace is deliberate, so the rendered page does not advertise a gap.
          continue
        out.append("\n\n".join(parts))
      else:
        out.append(line)

  text = "\n".join(out)
  while "\n\n\n" in text:
    text = text.replace("\n\n\n", "\n\n")
  return BANNER + "\n" + text.strip("\n") + "\n"


def main() -> int:
  ap = argparse.ArgumentParser()
  ap.add_argument("--check", action="store_true",
                  help="exit 1 if README.md does not match the parts")
  args = ap.parse_args()

  built = build()
  current = README.read_text(encoding="utf-8") if README.exists() else ""

  if args.check:
    if built == current:
      print("README.md is up to date")
      return 0
    print("README.md is STALE -- run: python tools/bp_build_readme.py\n")
    diff = difflib.unified_diff(current.split("\n"), built.split("\n"),
                                fromfile="README.md (committed)", tofile="README.md (from parts)",
                                lineterm="")
    print("\n".join(list(diff)[:60]))
    return 1

  README.write_text(built, encoding="utf-8")
  n_frag = sum(len(read_parts(d)) for d in FRAGMENTS.iterdir()) if FRAGMENTS.is_dir() else 0
  print(f"wrote README.md from {len(list(SECTIONS.glob('*.md')))} sections "
        f"and {n_frag} fragments")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
