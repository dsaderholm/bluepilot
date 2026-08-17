#!/usr/bin/env python3
"""Worldwide OSM key frequencies, for arguing about which tag a consumer should ask for.

WHY GLOBAL AND NOT OUR ROADS. Every coverage number this fork has produced came from Overpass over
a corridor we drive, which answers "can MY car use this" and cannot answer "is this tag common".
mapd serves a planet file, so a maintainer choosing between two tags is asking the second question.
US 6 carrying `change` on 27% of ways says nothing about whether `change` is the tag the world uses,
and a 27% that turns out to be 0% one valley over (US 89, measured 2026-08-17) is exactly how a
parochial number gets quoted as a general one.

taginfo is the right instrument for that question and Overpass is not: taginfo counts the whole
planet, already aggregated, and the API is cheap.

WHAT IT DOES NOT TELL YOU. A key's global count is not its usefulness. `divider` may be rarer than
`change` and still be the better ask if it is concentrated on the roads that matter, and a key can
be common because it is easy to survey rather than because it carries information. Pair any number
here with a coverage measurement on real roads before drawing a conclusion.

  python tools/bp_osm_tag_census.py                # the lane-restriction family
  python tools/bp_osm_tag_census.py change divider # specific keys
"""
import json
import sys
import time
import urllib.parse
import urllib.request

API = "https://taginfo.openstreetmap.org/api/4/key/stats?key="

# The question this list exists to answer, from pfeiferj on mapd issue 129: is there a more common
# tag carrying roughly the same information as `change`? Grouped by what they actually state, since
# "same information" is the whole argument and two keys can look adjacent while answering
# different questions.
FAMILIES = {
  "may I cross the line": [
    "change", "change:lanes", "change:lanes:forward", "change:lanes:backward",
    "overtaking", "overtaking:forward", "overtaking:backward", "overtaking:both_ways",
  ],
  "what is between the lanes": [
    "divider", "separation", "separation:left", "separation:right", "median",
  ],
  "how many lanes, whose": [
    "lanes", "lanes:forward", "lanes:backward", "lanes:both_ways",
  ],
  "per-lane designations (for scale)": [
    "turn:lanes", "placement", "hov:lanes", "access:lanes", "width:lanes",
  ],
}


def stats(key, retries=3):
  """Total object count for a key, or None if taginfo will not say."""
  for attempt in range(retries):
    try:
      with urllib.request.urlopen(API + urllib.parse.quote(key), timeout=45) as r:
        data = json.load(r)
      for row in data.get("data", []):
        if row.get("type") == "all":
          return int(row.get("count", 0))
      return 0
    # OSError, not URLError: URLError and TimeoutError are both SUBCLASSES of OSError, so catching
    # them by name catches strictly less than it looks like. A DNS failure or a reset connection
    # can surface as a plain OSError and would escape, taking the whole census down on one bad key.
    # ValueError covers json.JSONDecodeError, which subclasses it -- taginfo serves an HTML error
    # page under load, and that is a parse failure rather than a network one.
    except (OSError, ValueError):
      if attempt == retries - 1:
        return None
      time.sleep(2 * (attempt + 1))
  return None


def main():
  keys = sys.argv[1:]
  families = {"requested": keys} if keys else FAMILIES

  results = {}
  for family, members in families.items():
    for k in members:
      if k not in results:
        results[k] = stats(k)
        time.sleep(0.3)          # be polite to a free service

  baseline = results.get("lanes") or 0
  for family, members in families.items():
    print()
    print(f"=== {family} ===")
    print(f"  {'key':<28}{'objects':>14}{'vs lanes':>11}")
    for k in members:
      n = results.get(k)
      if n is None:
        print(f"  {k:<28}{'<no answer>':>14}{'':>11}")
        continue
      rel = f"{100.0 * n / baseline:.2f}%" if baseline else "n/a"
      print(f"  {k:<28}{n:>14,}{rel:>11}")

  print()
  print("'vs lanes' is share of the `lanes` key's object count, as a scale reference only.")
  print("A key being rarer is not a reason to refuse it; see the module docstring.")


if __name__ == "__main__":
  main()
