---
description: Take new commits from the ICBM branch into this one, test, push and update the car. Use whenever he asks about ICBM commits at all -- "any new ICBM commits?", "get the latest ICBM commits", "check ICBM", "merge ICBM" -- and whenever a session starts with an ICBM merge outstanding.
---

Take the newest commits from `icbm-manual-override-and-tuning` into the current branch, then put
them on the car. Run the whole thing; do not stop half way to report progress.

**Never `git checkout` another branch in this worktree, and never ask a git question.** Merge, do
not rebase — that decision is already made.

## 1. Is there anything to take?

```
git fetch -q origin
git log --oneline HEAD..origin/icbm-manual-override-and-tuning
```

**If that is empty, say so in one line and stop.** Do not merge, do not push, do not touch the car.
"No new ICBM commits" is a complete answer.

**If the current branch IS `icbm-manual-override-and-tuning`**, say so and stop — there is nothing
to merge into. Run `/deploy` instead if the intent was to update the car.

## 2. Merge

```
git merge --no-edit origin/icbm-manual-override-and-tuning
```

Resolve any conflicts yourself. If the merge touched
`sunnypilot/selfdrive/car/intelligent_cruise_button_management/controller.py`, re-read **The ICBM
button contract** in CLAUDE.md and check the resolution against it — the tests cannot catch a merge
that quietly changes what a cruise button means.

If it touched `selfdrive/controls/plannerd.py`, resolve the SubMaster list as a **union**, and then
parse it explicitly, because nothing offline imports it:

```
python -c "import ast; ast.parse(open('selfdrive/controls/plannerd.py',encoding='utf-8').read())"
```

## 3. Test — the bar is 0 failures

```
python tools/bp_offline_test.py
```

**Never report a branch as safe to flash, and never push, with a red suite.** Read the result before
the push runs — do not chain them so the push happens regardless. If something fails, diagnose it or
say plainly that it is unexplained. Do not dismiss a failure as a stub artifact.

## 4. Push, then update the car

```
git push origin HEAD
```

Then do everything in `/deploy` — resolve the device, check `IsOnroad`, reset, verify by content,
reboot. Do not hand him a command to run.

## 5. Report

Short. What came in (the commit subjects), that it merged clean or what you resolved, and that the
car is updated and rebooted. Do not analyse ICBM's behaviour at length — another session owns that
work; this command only ships it.
