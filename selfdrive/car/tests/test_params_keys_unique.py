"""BluePilot: every param is declared exactly once in params_keys.h.

`keys` is an initializer list for an unordered_map, so a duplicate entry is not a compile error and
not a runtime error -- the later one is silently dropped and the first one wins. Two declarations
that disagree on type or default therefore produce a device that behaves as neither, with nothing
anywhere saying so.

This is a MERGE hazard specifically, and one that is about to be real: BPDefaultsGeneration is
declared near the top of the file on this branch and in the BluePilot block further down on
passing-assist-phase1. Git merges both without a conflict, because they are hundreds of lines apart.
Every long-lived branch that adds a key to a different part of this file is the same trap.

Static -- no compiled Params needed.
"""

import pathlib
import re
from collections import Counter


def _repo_root() -> pathlib.Path:
  for d in pathlib.Path(__file__).resolve().parents:
    if (d / "common" / "params_keys.h").exists():
      return d
  raise RuntimeError("repo root not found")


KEYS_H = _repo_root() / "common" / "params_keys.h"


def _declarations() -> list[str]:
  # Only the map entries: `{"Name", {FLAGS, TYPE, "default"}},`. Comments mentioning a key in
  # passing must not count, or this becomes noise nobody reads.
  return re.findall(r'^\s*\{"(\w+)",\s*\{', KEYS_H.read_text(encoding="utf-8"), re.MULTILINE)


def test_no_key_is_declared_twice():
  dupes = {k: n for k, n in Counter(_declarations()).items() if n > 1}
  assert not dupes, f"declared more than once in params_keys.h: {dupes}"


def test_the_scan_actually_found_the_keys():
  """A regex that matches nothing would make the test above pass on anything at all."""
  found = _declarations()
  assert len(found) > 300, f"only {len(found)} declarations parsed -- the pattern has drifted"
  assert "BPDefaultsGeneration" in found
