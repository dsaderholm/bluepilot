---
description: Merge the newest BluePilot release into the fork, end to end. Use whenever he asks for a newer BluePilot -- "update BluePilot", "get the latest version", "there's a new BluePilot" -- which is the whole request; handle it start to finish.
---

He has asked for "the latest BluePilot". That is the whole request — handle it start to finish.

**Never ask him a git question.** Not merge vs rebase, not how to resolve a conflict, not which
branch. He has said plainly he does not want git terminology. Decide and proceed.

## 1. Run the script

```
python tools/bp_merge_upstream.py
```

It discovers the newest `bp-<major>.<minor>` release branch itself — never pin one, and **`bp-dev`
is never a merge source**: *"I never want anything to do with bp-dev, that is the BluePilot team."*

Upstream is merged into `icbm-manual-override-and-tuning` only. **If this worktree is not that one,
the script prints the `cd` to run — follow what it says** rather than reasoning about branches.

It tags a rollback point first, refuses a dirty tree, regenerates `car_list.json` rather than
merging it, prints what *ours* is in each conflict, runs the suite, and stops without committing.

## 2. Conflicts — resolve them yourself

Keep our change, re-apply theirs around it, `git add`, re-run the script to finish the checks.

- **`car_list.json` is GENERATED. Never hand-merge it.** The script handles it; by hand it is
  `git checkout --theirs` then `python opendbc_repo/opendbc/sunnypilot/car/platform_list.py`.
- **`README.md` — take either side and re-run `python tools/bp_build_readme.py`.** The parts are the
  source of truth and the file is disposable.
- **`plannerd.py`'s SubMaster list is a UNION.** Different branches add different inputs; taking
  either side whole silently removes one a controller already reads, and nothing offline notices.
- After anything in `controller.py`, re-read **The ICBM button contract** in CLAUDE.md and check the
  resolution against it.

## 3. Checks, in this order

1. `python tools/bp_offline_test.py` — 0 failures.
2. `ruff check --isolated --select F821,F811,F401,F841 <changed .py>` — compare findings against the
   merge base before assuming they are yours.
3. Parse `plannerd.py` explicitly if it was touched — **the suite passes with that file full of
   conflict markers**, because nothing offline imports it:
   `python -c "import ast; ast.parse(open('selfdrive/controls/plannerd.py',encoding='utf-8').read())"`

**Tests fail → say so plainly and do not tell him it is safe to flash.** Diagnose it.

## 4. Then the rest of the fork

`git worktree list` shows the other worktrees. Rebase each onto the updated base and run its tests
too. Each branch also owns its own SunnyLink settings — run
`python tools/bp_sunnylink_settings_audit.py` where new params exist.

## 5. Ship it

Commit, push, and update the car — do everything in `/deploy`. Then report the summary: what came
in, what you resolved, tests green, car updated.

If it goes wrong, the script printed a rollback tag: `git reset --hard pre-upstream-<sha>`.
