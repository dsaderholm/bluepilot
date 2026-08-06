#!/usr/bin/env python3
"""BluePilot: every setting this branch adds or re-defaults, grouped by where it lives on screen.

    python tools/bp_list_defaults.py            # print it
    python tools/bp_list_defaults.py --md       # markdown, for BP-DEFAULTS.md

WHY IT IS A SCRIPT AND NOT A LIST IN A FILE
Because a list in a file goes stale, and this project already paid for that: ten passing-assist
defaults moved during development and nothing anywhere recorded it, so the settings screen described
one thing and his car held another for weeks.

The titles come from the settings layouts by parsing the actual calls -- a regex over the same files
mislabelled two of them, which is why this walks the AST and pairs a `param=` with the title in its
OWN call rather than the nearest one. The defaults come from params_keys.h, compared against the
upstream merge base so only what this fork changed appears.

It reads nothing from the device and knows nothing about what is stored there. It is what the code
SHIPS, which is the thing to check a device against.
"""
import ast, re, subprocess, sys, pathlib

ROOT = pathlib.Path('.').resolve()
MENUS = {
  "selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/passing_assist_settings.py": "Steering > Customize Passing Assist",
  "selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/blinker_settings.py": "Steering > Customize Blinker",
  "selfdrive/ui/sunnypilot/layouts/settings/steering_sub_layouts/lane_change_settings.py": "Steering > Customize Lane Change",
  "selfdrive/ui/sunnypilot/layouts/settings/steering.py": "Steering",
  "selfdrive/ui/sunnypilot/layouts/settings/cruise.py": "Cruise",
  "selfdrive/ui/bp/layouts/settings/bluepilot.py": "BluePilot",
}

def strings(node):
  """Every literal string anywhere under a node, in source order."""
  return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

title_for, menu_for = {}, {}
for rel, menu in MENUS.items():
  p = ROOT / rel
  if not p.exists():
    continue
  for call in [n for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n, ast.Call)]:
    param = next((k.value.value for k in call.keywords
                  if k.arg == "param" and isinstance(k.value, ast.Constant)), None)
    if not param or param in title_for:
      continue
    # the title is the first tr("...") in this call, keyword or positional
    kw = next((k.value for k in call.keywords if k.arg in ("title", "button_text")), None)
    cand = strings(kw) if kw is not None else []
    if not cand:
      for a in call.args:
        cand = strings(a)
        if cand:
          break
    if cand:
      title_for[param], menu_for[param] = cand[0], menu

entry = re.compile(r'\{"(\w+)",\s*\{[^}]*?,\s*(\w+),\s*"([^"]*)"\s*\}\s*\}')
def defaults(t): return {m.group(1): (m.group(2), m.group(3)) for m in entry.finditer(t)}
now = defaults((ROOT / "common/params_keys.h").read_text(encoding='utf-8'))
base = subprocess.run(["git","merge-base","HEAD","upstream/bp-7.0"],capture_output=True,text=True).stdout.strip()
up = defaults(subprocess.run(["git","show",f"{base}:common/params_keys.h"],capture_output=True,text=True).stdout)

rows = [(menu_for.get(k,""), title_for.get(k,""), k, typ, val,
         "new" if k not in up else f"was {up[k][1]}")
        for k,(typ,val) in sorted(now.items())
        if k not in up or up[k][1] != val]
import collections

md = "--md" in sys.argv
by = collections.defaultdict(list)
for menu, title, key, typ, val, note in rows:
  if title:
    by[menu].append((title, key, typ, val, note))

if md:
  print("| Where | Control | Ships as | Key |")
  print("|---|---|---|---|")
for menu in sorted(by):
  if not md:
    print()
    print(menu)
  for title, key, typ, val, note in sorted(by[menu]):
    shown = ("On" if val == "1" else "Off") if typ == "BOOL" else val
    moved = "" if note == "new" else f"  ({note})"
    if md:
      print(f"| {menu} | {title} | **{shown}**{moved} | `{key}` |")
    else:
      print(f"   {title[:44]:<44} {shown:<7}{moved}")

if not md:
  shown_n = sum(len(v) for v in by.values())
  print()
  print(f"{shown_n} with a control, {len(rows) - shown_n} code-only keys with none")
