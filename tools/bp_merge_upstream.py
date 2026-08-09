#!/usr/bin/env python3
"""Merge a newer FusionPilot into this fork, handling the parts that are known to be painful.

    python tools/bp_merge_upstream.py              # merge the newest upstream release, detected
    python tools/bp_merge_upstream.py --rebase     # replay our commits on top instead
    python tools/bp_merge_upstream.py --branch bp-dev
    python tools/bp_merge_upstream.py --dry-run    # show what would come in, change nothing

MERGE vs REBASE, because it matters more than it looks: this fork carries dozens of its own
commits. A merge resolves each conflict ONCE. A rebase replays every commit and can surface the
same conflict repeatedly, once per commit that touches the file -- and controller.py has been
rewritten many times, so that is the painful case. Merge is the default for that reason. --rebase
exists because a linear history is a legitimate preference, not because it is easier here.

Staying current matters more than any individual change in this fork, so the goal is that updating
never gets deferred because it looks like a chore.

What this does that a bare `git merge` does not:

  * tags a rollback point BEFORE touching anything
  * refuses to start on a dirty tree, so the tag actually means something
  * auto-resolves car_list.json by REGENERATING it. That file is generated from PLATFORMS, adding
    one platform re-sorts the whole thing, and it is the largest conflict surface in the fork by an
    order of magnitude. Hand-merging it is both painful and wrong.
  * for every remaining conflict, prints what OUR change in that file is, so it can be preserved
    rather than rediscovered from the diff each time
  * never commits the merge and never pushes -- conflicts and review are the human's call

Deliberately stops short of resolving anything else. A merge that silently changes what a cruise
button means is the worst outcome here, and no script should be trusted with that.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_REMOTE = "upstream"
# Fallback only. The real branch is DISCOVERED -- see latest_release_branch below.
FALLBACK_BRANCH = "bp-7.0"
# BluePilot ships each release as its OWN BRANCH: bp-1.1, bp-2.0, ... bp-7.0, and bp-8.0 next.
# A hardcoded branch name is therefore a time bomb -- the day bp-8.0 lands, a pinned bp-7.0 keeps
# merging a frozen branch and reports success, so the tree looks current while sitting a whole
# release behind and nothing anywhere says so.
#
# Anchored exactly, because the remote is full of near-misses that must never be picked up:
# bp-dev, bp-dev-ui, bp-dev-f150-mk14.5, bp-sync-06102026, bp-no-stall, bp-livedelay-icon.
# Only bp-<major>.<minor> is a release.
RELEASE_RE = re.compile(r"^bp-(\d+)\.(\d+)$")

# Generated files: never hand-merge, just re-run the generator. It rewrites the file wholesale, so
# conflict markers in it do not matter and neither does which side "ours" means -- which is worth
# having, because --ours/--theirs INVERT between merge and rebase and that is an easy way to
# silently keep the wrong side.
GENERATED = {
  "opendbc_repo/opendbc/sunnypilot/car/car_list.json":
    [sys.executable, "opendbc_repo/opendbc/sunnypilot/car/platform_list.py"],
}

# What our change is in each file upstream also touches. Printed on conflict so a future merge
# knows what it is protecting instead of reverse-engineering it from the diff.
OURS = {
  "selfdrive/ui/bp/layouts/settings/bluepilot.py":
    "pinion-yaw toggle gated on the ALT_STEER_ANGLE flag rather than a platform name; "
    "ACC status toggle wording",
  "opendbc_repo/opendbc/car/ford/carcontroller.py":
    "3 lines: the standstill resume gate (LongitudinalExt.resume_allowed)",
  "sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py":
    "ALL of it -- the fork's core work. Re-read CLAUDE.md 'The ICBM button contract' after "
    "resolving; tests will not catch a resolution that is internally consistent but wrong",
  "sunnypilot/sunnylink/settings_ui.json": "our settings entries",
  "sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_assist.py": "SLA hooks",
  "sunnypilot/selfdrive/controls/lib/longitudinal_planner.py": "unconfirmed-lead plumbing",
  "sunnypilot/selfdrive/controls/lib/smart_cruise_control/vision_controller.py":
    "curve sensitivity / earliness factors",
  "opendbc_repo/opendbc/car/ford/carstate.py":
    "TSR flag gating and the single Traffic_RecognitnData registration -- a DUPLICATE here "
    "crashes card at init and strands the car on 'waiting to start'",
  "opendbc_repo/opendbc/sunnypilot/car/ford/carstate_ext.py": "TSR parsing, brake-light status",
  "selfdrive/ui/bp/onroad/hud_renderer_bp.py": "HOLD / ACC / brake-lamp readouts (mostly additive)",
  "sunnypilot/selfdrive/car/cruise_ext.py": "button-timer dict copied rather than shared",
  "selfdrive/ui/sunnypilot/layouts/settings/cruise.py": "our ICBM settings and section headers",
  "common/params_keys.h": "our Icbm* params",
  "cereal/custom.capnp": "overrideState / vBaseline / unconfirmedLead fields",
  "opendbc_repo/opendbc/car/structs.py": "dataclass mirrors of the capnp fields above",
  "opendbc_repo/opendbc/car/ford/values.py": "FORD_FUSION_MK5",
  "system/sentry.py": "crash reporting opt-in guard (owner's request)",
}


def git(*args: str, check: bool = True) -> str:
  result = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
  if check and result.returncode != 0:
    print(f"\n  git {' '.join(args)} failed:\n{result.stderr.strip()}", file=sys.stderr)
    sys.exit(1)
  return result.stdout.strip()


def conflicted() -> list[str]:
  return [ln for ln in git("diff", "--name-only", "--diff-filter=U").splitlines() if ln]


def latest_release_branch(remote: str) -> tuple[str, list[str]]:
  """Highest bp-<major>.<minor> on the remote, and every release seen, newest first.

  Sorted numerically on (major, minor), not as strings -- "bp-10.0" sorts BELOW "bp-9.0"
  lexically, which is the classic way this kind of check quietly stops working at the tenth
  release rather than the first.
  """
  try:
    out = git("ls-remote", "--heads", remote)
  except Exception:  # noqa: BLE001 - offline, or the remote is gone; fall back and say so
    return FALLBACK_BRANCH, []
  found = []
  for line in out.splitlines():
    name = line.rsplit("refs/heads/", 1)[-1].strip()
    m = RELEASE_RE.match(name)
    if m:
      found.append(((int(m.group(1)), int(m.group(2))), name))
  if not found:
    return FALLBACK_BRANCH, []
  found.sort(reverse=True)
  return found[0][1], [n for _, n in found]


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--remote", default=DEFAULT_REMOTE)
  parser.add_argument("--branch", default=None,
                      help="override the auto-detected newest release branch")
  parser.add_argument("--rebase", action="store_true",
                      help="replay our commits on top of upstream instead of merging; see the "
                           "module docstring for why merge is the default")
  parser.add_argument("--dry-run", action="store_true", help="show what would come in, change nothing")
  args = parser.parse_args()
  branch = args.branch
  if branch is None:
    branch, releases = latest_release_branch(args.remote)
    if releases:
      print(f"  newest release on {args.remote}: {branch}"
            + (f"   (also seen: {', '.join(releases[1:4])})" if len(releases) > 1 else ""))
      if branch != FALLBACK_BRANCH:
        print(f"  NOTE: this is past {FALLBACK_BRANCH}, which is what this script used to pin.")
    else:
      print(f"  could not list {args.remote} branches; falling back to {branch}")
  target = f"{args.remote}/{branch}"

  if git("status", "--porcelain"):
    print("Working tree is dirty. Commit or stash first -- the rollback tag is worthless otherwise.",
          file=sys.stderr)
    return 1

  print(f"fetching {args.remote} ...")
  git("fetch", args.remote)

  incoming = git("log", "--oneline", f"HEAD..{target}")
  if not incoming:
    print(f"Already up to date with {target}.")
    return 0

  count = len(incoming.splitlines())
  files = git("diff", "--name-only", f"HEAD...{target}")
  print(f"\n{count} commit(s) incoming from {target}, touching {len(files.splitlines())} file(s).")
  print("\n".join("  " + ln for ln in incoming.splitlines()[:15]))
  if count > 15:
    print(f"  ... and {count - 15} more")

  if args.dry_run:
    print("\n--dry-run: nothing changed.")
    return 0

  tag = f"pre-upstream-{git('rev-parse', '--short', 'HEAD')}"
  git("tag", "-f", tag)
  print(f"\nrollback point: git reset --hard {tag}")

  if args.rebase:
    print(f"rebasing onto {target} ...")
    subprocess.run(["git", "rebase", target], cwd=REPO)
  else:
    print(f"merging {target} (not committing) ...")
    subprocess.run(["git", "merge", "--no-commit", "--no-ff", target], cwd=REPO)

  # Generated files: regenerate and stage. Never hand-merge, and never pick a side -- the generator
  # rewrites the file wholesale, so conflict markers in it are irrelevant. Explicitly NOT using
  # `git checkout --ours/--theirs` here: those invert between merge and rebase, which is a silent
  # way to keep the wrong version.
  for path, command in GENERATED.items():
    if path in conflicted() or path in git("diff", "--name-only", "--cached").splitlines():
      print(f"\nregenerating {path} rather than merging it ...")
      result = subprocess.run(command, cwd=REPO, capture_output=True, text=True)
      if result.returncode != 0:
        print(f"  generator FAILED -- resolve {path} by hand:\n{result.stderr.strip()}",
              file=sys.stderr)
      else:
        git("add", "--", path)
        print("  regenerated and staged")

  remaining = conflicted()
  if remaining:
    print(f"\n{len(remaining)} file(s) still conflicting. What is ours in each:\n")
    for path in remaining:
      note = OURS.get(path, "not a file this fork usually changes -- check whether ours is needed")
      print(f"  {path}\n      {note}\n")
    if args.rebase:
      print("Resolve, `git add` them, then `git rebase --continue`. Expect to see the same file "
            "again on a later commit -- that is rebase, not a mistake.")
      print(f"Abandon: git rebase --abort  (or git reset --hard {tag})")
    else:
      print("Resolve, `git add` them, then re-run this script to finish the checks.")
      print(f"Abandon: git merge --abort  (or git reset --hard {tag})")
    return 2

  print("\nno conflicts. running checks ...\n")
  checks = subprocess.run([sys.executable, "tools/bp_offline_test.py"], cwd=REPO)
  if checks.returncode != 0:
    print("\nTESTS FAILED. Do not flash. Diagnose before committing the merge -- "
          "test_structs_capnp_parity and test_can_parser_messages both exist because an upstream "
          "merge broke them before.", file=sys.stderr)
    return 1

  print("\nMerge is staged and green. Not committed and not pushed -- review, then:")
  print("  git commit")
  print(f"  git push origin {git('rev-parse', '--abbrev-ref', 'HEAD')}")
  print("\nBefore flashing, re-read CLAUDE.md 'The ICBM button contract' against any resolution "
        "made in controller.py.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
