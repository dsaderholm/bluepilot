# FusionPilot — working notes

Named 2026-08-09. This fork is **FusionPilot**; **BluePilot is still upstream** and updates are
still taken from it, so "BluePilot" in these notes means the thing we merge FROM unless it plainly
means this fork.

The rename was deliberately confined to what he SEES -- the home screen, the settings tab, the two
settings strings that named the fork, the README, and the `FusionPilot:` marker in files that are new
here. Directories (`bluepilot/`, `selfdrive/ui/bp/`), file names, imports, param keys and the 316
`BluePilot:` markers in upstream's own code all keep their names, because renaming them is a merge
conflict forever and breaks the thing he cares about most: *"I want updating to newer BluePilot
versions to still be easy."*

## THE DEVICE AUTO-UPDATES. A PUSH IS A DEPLOY.

Found by the passing assist session on 2026-08-12, from the device's own reflog: it pulls
`Reset to FETCH_HEAD` unattended, roughly hourly.

```
330369129  2026-08-12 05:18 +0000
330369129  2026-08-12 02:19 +0000
9d5bc1b9d  2026-08-12 00:49 +0000
7f82ca85c  2026-08-11 18:55 +0000
```

**So pushing to the branch his car tracks puts code on his car, with nobody deciding to send it.**
That branch is currently `passing-assist-phase1`, not this one -- check
`git rev-parse --abbrev-ref HEAD` on the device rather than assuming.

This was believed and TOLD TO HIM the other way round: that a push is inert until he runs `git pull`.
It is not. The consequence is that an untested push reaches a car being driven, so:

- **Hold a push on the tracked branch until he asks for it**, unless it is a fix he is waiting on.
- The suite passing is not optional before pushing there. There is no manual gate behind it.
- The command below is how he takes an update DELIBERATELY and immediately. It is not the only way
  code arrives.

## START HERE if the owner asks to update

They will open a fresh session and say something like *"update BluePilot"*, *"get the latest
version"*, or *"there's a new BluePilot"*. That is the whole request. Handle it end to end:

```bash
python tools/bp_merge_upstream.py
```

**This fork has several worktrees, one per line of work, and a fresh session lands in whichever
one it lands in.** Upstream is merged into `icbm-manual-override-and-tuning` only; the others pick
the update up by being rebased onto it afterwards. The script checks this and, if it is in the
wrong place, prints the `cd` to run instead -- so just follow what it says rather than reasoning
about branches.

After the merge lands, rebase the other worktrees onto it and run their tests too. `git worktree
list` shows them.

**A fix belongs to the branch that owns the code, not to the branch you happened to find it from.**
Stated as a rule on 2026-08-05: *"don't fix any ICBM related stuff on this branch, it should only
be fixed on the ICBM branch."*

So when work on a feature branch turns up a bug in ICBM, the BluePilot settings page, or anything
else `icbm-manual-override-and-tuning` owns: `cd` to that worktree, fix it there, commit, push,
then rebase this branch onto it. Do NOT fix it in place. Fixing it in place strands the fix on a
branch that has not merged -- so a device flashed from ICBM still has the bug -- and guarantees a
conflict on the next rebase against the eventual real fix.

This does not narrow what may be fixed. Anything related to what is being built is in scope
whatever layer owns it; the rule is only about WHERE the commit lands.

Then:

1. **No conflicts, tests green** → show them the summary, commit, push, and give them the
   copy-pasteable device command below. Done.
2. **Conflicts** → resolve them yourself. The script prints what *ours* is in each file. Keep our
   change, re-apply theirs around it, `git add`, re-run the script. Then as above.
3. **Tests fail** → say so plainly and do not tell them it is safe to flash. Diagnose it.

Then give them exactly this, and nothing more complicated:

```bash
cd /data/openpilot && git fetch && git reset --hard origin/<branch> && sudo reboot
```

**NOT `git pull`.** It fails on the car every time a branch is rebased, which is most updates here:
rebasing gives every commit a new id, so pull tries to MERGE the rewritten branch into the device's
older copy of itself and conflicts against its own history. It happened on 2026-08-06 and left him
standing at the car with four conflicts and a half-finished merge:

    "This is why I always just use the updater on SunnyPilot"

Fair. The reset form is no harder to paste, works whether or not the branch was rewritten, and
cannot half-apply. The device is a deployment with no local edits, so discarding them is free --
and if that ever stops being true, the answer is still not `pull`.

If he is already stuck mid-merge, `git merge --abort;` in front of it clears the state first.

**Rules for this task, learned the hard way:**

- **Never ask them a git question.** Not merge vs rebase, not how to resolve a conflict, not which
  branch. They have said plainly they do not want git terminology. Decide and proceed.
- **Never report it as safe to flash with a failing test.** A dismissed test is how a duplicate CAN
  registration reached the car and stranded it on "waiting to start".
- After resolving anything in `controller.py`, re-read **The ICBM button contract** further down and
  check the resolution against it. Tests cannot catch a merge that quietly changes what a cruise
  button means.
- If something goes badly wrong, the script printed a rollback tag: `git reset --hard <tag>`.

Everything below is background for when that is not enough.

## You can SSH into the car yourself

Set up 2026-08-08. **No key file, no IP, no config** -- it already works:

```bash
ssh comma@comma-34b959b "bash -lc 'cd /data/openpilot && python tools/bp_route_report.py latest'"
```

Three things make that work, each of which cost a wrong guess first:

- **Hostname, not IP.** `comma-34b959b` resolves over mDNS. His IP changes constantly -- "I go a lot
  of places" -- and none of that matters. Do not ask him for an IP.
- **Bitwarden Desktop is the SSH agent.** The private key never leaves the vault; `ssh` asks
  Bitwarden to sign. `ssh-add -l` shows it as "My SSH Key (ED25519)". **Do not ask him to export a
  key to disk** -- that was suggested first and correctly refused, and it was never necessary.
- **`bash -lc` or python is not on PATH.** A non-interactive SSH session skips the profile, so plain
  `python` gives "command not found". The interpreter is `/usr/local/venv/bin/python`.

Use PowerShell for this, not the Bash tool: the Windows OpenSSH agent is a named pipe and Git Bash's
ssh does not speak it.

**`pgrep -f <name>` MATCHES THE WAITER'S OWN COMMAND LINE.** 2026-08-19, and it cost over an hour
across three jobs while their results sat finished on disk. This never exits:

```bash
until [ -s /tmp/out.txt ] && ! pgrep -f myjob.py > /dev/null; do sleep 15; done
```

`pgrep -f myjob.py` finds the `bash -lc until ... pgrep -f myjob.py ...` process itself, so `! pgrep`
is false forever. He caught it -- *"Will you though? You have two tasks that have been running for
over an hour..."* -- and `/tmp/pscm.txt` had been complete for 70 minutes.

Use the bracket trick that `ps` usage here already uses, or drop the process check entirely:

```bash
until [ -s /tmp/out.txt ]; do sleep 15; done; cat /tmp/out.txt        # simplest, usually enough
until ! pgrep -f "[m]yjob.py" > /dev/null; do sleep 15; done          # if the check is needed
```

**And prefer `nohup ... &` on the device over holding an SSH session open**: this laptop changes
networks constantly, and a dropped connection killed one analysis mid-run. Start it detached, poll
the file.

**WRAP EVERY REMOTE COMMAND IN A SINGLE-QUOTED HERE-STRING.** PowerShell expands `$(...)` inside
double quotes *before* ssh ever sees it, so this:

```powershell
ssh comma@comma-34b959b "bash -lc 'echo HEAD=$(git rev-parse --short HEAD)'"
```

runs `git rev-parse` on THIS MACHINE and reports the local repo as though it were the car. Escaping
the dollar as `\$(` does not save it either. On 2026-08-12 that produced a confident, entirely wrong
report that the device had switched branches and pulled a commit -- from a laptop's own git. The
device was on a different branch at a different commit the whole time.

```powershell
$cmd = @'
cd /data/openpilot && echo "HEAD: $(git rev-parse --short HEAD)"
'@; ssh comma@comma-34b959b "bash -lc '$cmd'"
```

`@'...'@` is literal; only the outer `$cmd` interpolates. **If a remote reading agrees suspiciously
well with what is on the laptop, suspect this before believing it.**

**What to do with it.** Read freely -- reports, logs, params, `git log`. Anything that WRITES to the
device, changes a setting, or restarts a process: say what the command is and why before running it.
Never touch it while he is driving. He was surprised to find a session already connected -- "I didn't
even know you were connected!" -- so say when you connect and what you ran.

**The device's own logs are not the route.** `/data/log/swaglog.*` holds every daemon's cloudlog,
including the UI's, and a route's `logMessage` stream does NOT carry all of it. A crash that is
missing from `bp_route_report` may still be sitting in swaglog:

```bash
ssh comma@comma-34b959b "bash -lc 'grep -h \"<the error you cannot find>\" /data/log/* | head -1'"
```

That is how a UI crash was finally found, after two rounds of hunting for it in the route.

## Run tests with `tools/bp_offline_test.py`, never bare `pytest`

```bash
python tools/bp_offline_test.py                      # the offline-testable suite
python tools/bp_offline_test.py path/to/test_x.py    # anything pytest accepts
```

Bare `pytest` does not work here and, worse, **fails in ways that look like environment noise**:

- **Wrong interpreter.** `pyproject.toml` pins `>= 3.12.3, < 3.13`. On Python 3.14, PEP 649 makes
  class annotations lazy, so opendbc's `auto_dataclass` — which reads
  `cls.__dict__['__annotations__']` — sees nothing and converts no fields. Every `auto_field()`
  default stays a raw sentinel `object` instead of becoming an `int`/`bool`/`str`. Tests then fail
  with things like `unsupported operand type(s) for |=: 'object' and 'int'` that cannot happen on
  the device. The runner re-execs itself under 3.12, so it works whatever Python you invoke it with.
- **Missing compiled extensions.** `common/params_pyx` is a Cython module built by scons, and the
  repo `conftest.py` imports `Params` at collection time, so nothing collects without it. The
  runner stubs `Params` and the other device-only leaves and passes `--noconftest`.

The bar is **0 failures**, not a particular total — the count moves with the branch and with what
is in `DEFAULT_TARGETS`, so a hardcoded number here just goes stale and stops being checked.

Do not learn to ignore a failure. Three were dismissed as "pre-existing stub artifacts" across two
sessions, and that habit is what let a startup crash reach the car. Diagnose it, or say plainly
that it is unexplained — never report a branch as safe to flash with a red test in it.

Environment (already set up): `../.venv-bp312`, a 3.12.13 venv. Recreate with:

```bash
uv python install 3.12
uv venv --python 3.12 ../.venv-bp312
uv pip install --python ../.venv-bp312/Scripts/python.exe pytest pytest-mock pycapnp numpy zstandard requests tqdm crcmod-plus sympy pyserial raylib ruff pycryptodome sentry-sdk
```

## What offline tests do NOT cover

Anything needing compiled extensions, a real CAN bus, or a display. Two specific gaps have bitten:

**`CarController.update` IS covered now** -- see the carcontroller section above. It was not until
2026-08-15, and the day it was not is the day the car would not drive.

- **`get_can_parsers`.** `CANParser` rejects a duplicate message address with
  `RuntimeError("Duplicate Message Check: N")` at car init, which kills `card` and leaves the
  device on "waiting to start". This happened for real when an upstream merge added a second
  `Traffic_RecognitnData` registration. `test_can_parser_messages.py` now stubs `CANParser`/`DBC`
  and asserts on the argument lists across all flag combinations. **Anything that builds a parser
  or runs at car init needs a test of this shape** — the behavioral suite never reaches it.
- **Settings screens rendering.** Structure and names can be checked statically (see below), but
  nothing renders them offline.

Onroad HUD drawing *is* checkable, and the check is **trustworthy**:
`selfdrive/ui/bp/onroad/tools/preview_acc_status.py` renders the shipped drawing methods to PNG at
device scale. The owner confirmed on 2026-08-03, after driving it, that the car looks *exactly*
like the preview renders -- colors, sizes, spacing, all of it.

`selfdrive/ui/bp/onroad/tools/preview_passing_panel.py` does the same for the passing-assist
panel, which is now that whole feature's readout -- every gate, the dry run of the maneuver, the
slow-pass warning and the drive summary all land in the same three lines. It prints each panel's
pixel size and asserts none exceeds the screen. **Add a scene to its SCENES whenever a new panel
state is introduced**; the first render found three readouts that were being assembled and then
silently dropped, which the full suite had passed over.

So treat a preview render as the answer, not an approximation. Iterate on it until it looks right
and ship that; do not caveat UI work with "we won't know until you drive it", and do not ask the
owner to judge a layout you have not rendered. It calls the real drawing methods out of
hud_renderer_bp.py rather than reimplementing them, which is why it stays accurate -- keep it that
way, and add a scene to SCENES whenever a new state is introduced.

## Before saying a branch is safe to flash

1. `python tools/bp_offline_test.py` — expect 0 failed.
2. `ruff check --isolated --select F821,F811,F401,F841 <changed .py files>` — F821 (undefined name)
   is the one that matters; it catches what import tests cannot reach. Compare any finding against
   the merge base before treating it as yours.
3. For changes under `opendbc/car/` or anything in `card`: confirm nothing new is constructed at
   car init without a test, and that every CAN signal read exists in the DBC.
4. For new `Params` keys: confirm each is declared in `common/params_keys.h`. The stubbed `Params`
   raises on unknown keys the way the device does.

## AN EXCEPTION IN carcontroller MAKES THE CAR UNDRIVABLE. TEST IT BY RUNNING IT.

2026-08-15. The car would not drive. One line, on the first control frame:

    File "opendbc/sunnypilot/car/ford/icbm.py", line 111, in _update_gap
      was_mode, was_result = self.gap.mode, self.gap.last_result
    AttributeError: 'CarController' object has no attribute 'gap'. Did you mean: 'gas'?

**Why it happened.** `ford/carcontroller.py` calls the ICBM interface CLASS-STYLE --
`IntelligentCruiseButtonManagementInterface.update(self, ...)` -- with `self` being the
CarController, and the line that would construct a real instance is COMMENTED OUT right above it.
So that class is never instantiated, its `__init__` never runs, and any attribute it sets does not
exist at runtime.

**It is a trap because four sibling classes do the opposite.** `LateralCurvExt`, `LateralAngleExt`,
`LongitudinalExt` and `HudExt` all have their `__init__` called explicitly at lines 79-82. One class
in five breaks the pattern, and it is the one with a commented-out call rather than a missing one --
so the file reads as though all five are initialized.

**Per-drive state for the ICBM path goes on the Ford CarController's own `__init__`.** `self.icbm_gap`
is created there for exactly this reason. Never add an `__init__` to
`IntelligentCruiseButtonManagementInterface`; `test_carcontroller_smoke.py` asserts it has none.

**Why nothing caught it.** 607 tests were green, ruff was clean, and the code reads as obviously
correct. Every test in this fork exercised either PURE LOGIC (`gap_control.py`, `controller.py`) or
arguments at a stubbed boundary. **Nothing offline had ever called `CarController.update`.** Pure
logic cannot catch a wiring mistake between two objects, and that is precisely the category that
takes the car off the road rather than merely making a feature wrong.

**`opendbc/sunnypilot/car/ford/tests/test_carcontroller_smoke.py` closes it.** It builds the REAL
CarController with the real DBC and his real CarParams, and drives `update()` for 400 frames across
engaged/not x sendButton none/increase/decrease x gapTarget 0/1/3/5. Verified by reintroducing the
bug: it reproduces the AttributeError above character for character.

Two things make it worth trusting, and both must be preserved:

- **It matches card's call convention exactly** -- `CC` is a capnp READER, `CC_SP` is the opendbc
  dataclass, mirroring `self.CI.apply(CC, convert_carControlSP(CC_SP), now_nanos)` in card.py. The
  first draft passed a builder and failed on `actuators.as_builder()`, which is the tell.
- **`CS` is a STRICT object, not a Mock**, with its stock-value dicts derived from the real DBC. A
  Mock returns a Mock for `self.gap` and the test passes while the car does not start. This is the
  same "a stub laxer than the real thing hides the bug it was built to catch" failure recorded
  elsewhere in this file, and it would have applied here perfectly.

**THE RULE: anything that adds state or a call to the carcontroller path gets a case in that smoke
test, in the same commit.** Not "is the logic right" -- does a real CarController survive being
driven.

**And write the feature so it CANNOT do this again.** An exception in `_update_gap` does not disable
a feature; it propagates out of `CarController.update`, through card's control loop, and stops the
car. So the whole gap path is wrapped and LATCHED OFF on any failure, with `icbm_gap_failed`
defaulting to True when absent so a missing attribute disables the feature instead of the car. A
follow-distance convenience must degrade to doing nothing. Apply that shape to any future addition
in this layer.

## Do not fix UNRELATED upstream bugs in this fork

**The test, stated by the owner on 2026-08-08: does this change what MY CAR does, what I SEE, or
whether I can TAKE THE NEXT BluePilot?** Not "is it a real bug", and not "is this file ours". If the
answer is no, report it upstream and leave it.

**Staying upgradable outranks being right elsewhere.** Every upstream line this fork modifies is a
merge conflict paid on every future update, forever. An update that feels like a chore is an update
that gets deferred, and falling behind upstream costs more than any individual bug being correct.

A worked set, from the branch review on 2026-08-08 where all four were proposed together:

| Proposed | Verdict | Why |
|---|---|---|
| `apply_bp_device_mount` naming a `Device` member upstream deleted | **take** | AttributeError broke `car_list.json` generation, which `bp_merge_upstream.py` runs on every update -- it broke the upgrade path itself |
| `FORD_FUSION_MK5` docs name parsing to no years | **take** | his car, unsearchable in his own vehicle picker |
| `FORD_MONDEO_MK5` year format, same root cause | **drop** | not his car |
| `IcbmResumeMinLeadSpeed` label showing km/h while the code uses `MPH_TO_MS` | **drop** | real, and ours by file -- but he is not metric, so it cannot affect his car |

The last row is the one to internalize: **ours-by-file is not enough on its own.**

This is a personal fork of BluePilot, which forks sunnypilot, which forks openpilot. A bug that
belongs to one of those layers, and has nothing to do with the work here, should be **reported
there, not patched here**.

Every upstream line this fork modifies is a merge conflict paid for on every future rebase, forever.
That is worth it for something this car needs and free-riding on someone else's maintenance for
anything else.

**What this fork BUILDS is the exception, and it is broad.** Not ICBM specifically -- ICBM is one
of several things here, alongside passing assist, the radar detector, Speed Limit Assist and holds,
and the Smart Cruise Control tuning. Anything touching any of them is in scope whatever layer owns
the file, and so is anything they depend on: `cruise_ext.py`'s button timers feed ICBM's press
stand-down, so they are ICBM's business too, and the same reasoning extends to each of the others.
"That is upstream's file" is never on its own a reason to leave one of this fork's features broken.

**But being one of ours is NOT sufficient.** Both halves of the test still have to hold -- it has to
be something this fork builds, AND it has to reach his car. A bug in a feature we own that cannot
affect him is still a drop, and the `IcbmResumeMinLeadSpeed` row in the table above is exactly that
case: our feature, our file, real bug, dropped because he does not drive in metric.

The rule is about bugs that are *not ours* and about ours that *cannot reach him*. A boot-splash
warning is neither ours nor his. A metric-only label is ours but not his.

Layers, outermost first — check which one a file belongs to before editing it:

| Path | Owner |
|---|---|
| `selfdrive/ui/bp/`, `bluepilot/`, `launch_chffrplus.sh`, `scripts/boot_logo.sh` | BluePilot |
| `sunnypilot/`, `opendbc/sunnypilot/`, `selfdrive/ui/sunnypilot/` | sunnypilot |
| everything else | openpilot / opendbc |

**Legitimate reasons to touch an upstream file:**

- a new `Params` key (`common/params_keys.h`), capnp field (`cereal/custom.capnp`) or its dataclass
  mirror (`opendbc/car/structs.py`) that this fork's features need
- this car's platform: `opendbc/car/ford/values.py`, `torque_data/override.toml`, fingerprints
- anything ICBM-related at all -- see above, no justification needed
- a bug that is genuinely load-bearing for other work here
- something the owner explicitly asked for, e.g. the Sentry opt-in guard in `system/sentry.py`
- new test files, which are additive and never conflict

**Not legitimate**, and the case that prompted this rule: on 2026-08-03 a PWD/`$(pwd)` inconsistency
in `launch_chffrplus.sh` was fixed here because it printed a warning on the boot splash. Correct
fix, wrong repo — a cosmetic message, in a boot-critical file that cannot be tested offline, with no
connection to this car. Reverted. If something like that is worth fixing, send it upstream.

When in doubt, ask rather than fix. Reordering upstream's own UI items counts too, even though it
looks harmless.

## Merging a newer BluePilot

**Do not ask the owner to choose between merge and rebase, or any other git decision.** They have
said plainly they do not want to deal with git terminology -- they ask for "the latest BluePilot"
and expect it handled. Default to merge, which is the easier path for a fork carrying this many
commits, and only use `--rebase` if they specifically ask for it by name.

Staying current matters more than any individual change here — an update that looks like a chore is
an update that gets deferred. So it is one command:

```bash
python tools/bp_merge_upstream.py               # merge the newest upstream RELEASE, auto-detected
python tools/bp_merge_upstream.py --dry-run     # what would come in, changes nothing
```

**Each BluePilot release is its own branch** -- bp-1.1, bp-2.0, ... bp-7.0, and bp-8.0 next -- so the
script DISCOVERS the newest rather than pinning one. A pinned name is a time bomb: the day bp-8.0
lands, a script still pointed at bp-7.0 merges a frozen branch, reports success, and leaves the tree
a whole release behind with nothing saying so. It prints which branch it picked, and says so loudly
when that is past bp-7.0.

Detection is anchored to `bp-<major>.<minor>` exactly, and sorts numerically. The remote is full of
near-misses that must never be picked up -- `bp-dev`, `bp-dev-ui`, `bp-dev-f150-mk14.5`,
`bp-sync-06102026`, `bp-no-stall` -- and a string sort would put bp-10.0 below bp-9.0.

**`bp-dev` is the BluePilot team's active development branch and is never a merge source.** He
tracks releases only: *"I never want anything to do with bp-dev, that is the BluePilot team."*
`--branch` can still force one, but nothing should.

It tags a rollback point first, refuses to start on a dirty tree, regenerates `car_list.json`
instead of merging it, prints what *ours* is in each remaining conflict, runs the test suite, and
stops without committing or pushing. Resolve conflicts, `git add`, re-run it to finish the checks.

**`car_list.json` is generated — never hand-merge it**, which is why the script handles it. It is
the largest conflict surface in the fork by an order of magnitude (~420 lines rewritten, because
adding one platform re-sorts the whole file). By hand, if ever needed:

```bash
git checkout --theirs opendbc_repo/opendbc/sunnypilot/car/car_list.json
python opendbc_repo/opendbc/sunnypilot/car/platform_list.py
```

Everything else, ranked by how often upstream touches it — the top of this list is where conflicts
will actually land, and what our change is in each so it can be preserved rather than rediscovered:

| File | Upstream commits/yr | What is ours |
|---|---|---|
| `selfdrive/ui/bp/layouts/settings/bluepilot.py` | ~47 | pinion-yaw toggle gated on the `ALT_STEER_ANGLE` flag, not a platform name; ACC status toggle wording |
| `opendbc/car/ford/carcontroller.py` | ~34 | 3 lines: the standstill resume gate |
| `sunnypilot/.../intelligent_cruise_button_management/controller.py` | ~16 | all of it — this is the fork's core work |
| `sunnypilot/sunnylink/settings_ui.json` | ~18 | our settings entries |
| `sunnypilot/.../speed_limit/speed_limit_assist.py` | ~12 | SLA hooks |
| `sunnypilot/.../longitudinal_planner.py` | ~13 | unconfirmed-lead plumbing |
| `opendbc/car/ford/carstate.py` | ~10 | TSR flag gating + `Traffic_RecognitnData` registration |
| `opendbc/sunnypilot/car/ford/carstate_ext.py` | ~8 | TSR parsing, brake-light status |
| `selfdrive/ui/bp/onroad/hud_renderer_bp.py` | ~10 | HOLD / ACC / lamp readouts — almost purely additive |

Everything not listed is either a new file (never conflicts) or a pure addition.

**After any merge, before flashing**, in this order — the first two exist specifically because an
upstream merge broke them before:

1. `python tools/bp_offline_test.py` — 0 failures.
   - `test_structs_capnp_parity` catches a capnp field added without its dataclass mirror, which
     crashed `card` at startup once.
   - `test_can_parser_messages` catches a duplicate CAN registration, which stranded the car on
     "waiting to start" after upstream added a second `Traffic_RecognitnData`.
2. `ruff check --isolated --select F821,F811,F401,F841 <changed .py>` — compare findings against the
   merge base before assuming they are yours.
3. Re-read any conflict resolution in `controller.py` against **The ICBM button contract** below.
   A merge that silently changes what a button means is the worst outcome here, and the tests will
   not catch it if the resolution is internally consistent.

If it goes wrong: `git reset --hard pre-upstream-<sha>`.

## README.md is GENERATED. Add a file, do not edit it.

The README describes the whole fork, but the fork is three branches -- this one, passing assist, and
the radar detector -- each rebasing onto this one and each needing to describe its own features. As a
single hand-edited file that was a conflict on every rebase, and git resolved it wrong in a specific
way: it replays the feature branch's OLDER copy over the base's newer one, silently reverting the
whole document. That happened on 2026-08-11 to a same-day rewrite and was caught only by reading the
result.

So each branch owns files nobody else touches:

```
readme/sections/*.md            the shared document, owned by THIS branch
readme/fragments/features/      one file per feature area
readme/fragments/diagnostics/   one bullet per branch that adds a diagnostic
readme/fragments/portability/   one bullet per branch with its own hardware caveats
python tools/bp_build_readme.py     # rewrites README.md
```

**To add your branch's section: create ONE new file in the right fragment directory and rebuild.**
Two branches adding a section now add two different files, which git merges without an opinion.

- Number with room to insert (10, 20, 30) and name it after your feature -- `40-passing-assist.md`,
  never `40-section.md`, or two branches collide again on the same path.
- Never hand-edit `README.md`. `test_readme_is_built.py` fails if it does not match its parts.
- If `README.md` conflicts anyway, do NOT merge it hunk by hunk. Take either side and re-run the
  build; the parts are the source of truth and the file is disposable.
- An anchor with no fragments behind it renders nothing, deliberately -- a branch without passing
  assist should not advertise a gap where it would go.

**SAY PLAINLY WHAT DOES NOT ACTUATE.** This carve-out was dropped when this section was rewritten on
2026-08-11 and had to be put back the same day. It is the one place "Talk about the finished system"
below does NOT apply: that rule is about answering the owner, who is always describing the finished
behavior and does not need the caveat. A README is read by strangers who have no way to know, and one
written under that rule describes scaffolding as shipped behavior.

Put the limitation in the same sentence as the description, not a footnote. Passing assist opens with
"This moves nothing today, on any car", which is the right shape.

**And put it in the right section.** A limitation true for everyone is a FEATURE limitation and
belongs in the feature fragment; only something about the reader's car belongs in portability. Filing
"it actuates nowhere" under portability tells a reader their car is the reason, when it is not.

**Sections are this branch's to edit; fragments are each branch's own.** If a change belongs in the
shared prose -- the status section, portability, licence, safety -- it goes in `readme/sections/`
here and reaches the others by rebase, which is the direction that works.

**Rebase onto the BRANCH, not onto a hash somebody quoted you.** A hash in a handoff message is stale
the moment the base is pushed to again, and resolving conflicts against a version that no longer
exists is wasted work. Use `origin/icbm-manual-override-and-tuning` and check the tip when you start.

## Name a feature for what it DOES, never for ICBM

ICBM is an **actuator adapter**, not a feature. It exists because stock Ford ACC will not take a
longitudinal command, so the planner's desired speed has to be translated into cruise-button
presses.

**openpilot alpha long is unusable on Ford** -- the owner has tried it and calls it absolute
trash. Do not suggest flipping the toggle to compare them, and do not use "op long is coming" to
justify sequencing anything. He has no idea when it will be good and neither does anyone else.

The toggle does exist: `interfaces_ext.py` forces `alphaLongitudinalAvailable = True` on every Ford
and makes it authoritative -- `openpilotLongitudinalControl = bool(alpha_long)`, with
`intelligentCruiseButtonManagementAvailable` True for the case where it is off. Worth knowing when
reasoning about which code path runs; not a recommendation.

So ICBM is the long-term path on this car, which makes this naming rule MORE important, not less:
anything durable has to outlive an ICBM era measured in years.

So `Icbm*` is reserved for things that are meaningless when the toggle is on: button injection and the button contract, the
target drop/rise limiters (they only exist to make Ford coast instead of brake), the custom
press increments, the standstill resume gate, the 20 mph floor, the radar-blind lead detector and
the model-stop path. Every one of those solves a problem that disappears.

**Anything meant to outlive that gets its own name.** `SpeedLimit*`, `SmartCruiseControl*`,
`PassingAssist*` are right: they say what the feature is. A radar-detector integration is a
`RadarDetector*` thing — it has nothing to do with cruise buttons and must keep working when they
are gone. Naming it `Icbm*` files a durable feature under the scaffolding and guarantees it gets
deleted with it, or kept for the wrong reason.

The line to apply: **would this still make sense if openpilot were driving the car directly?** If
yes, it is not an ICBM feature, whatever module it currently lives in.

### HOLDS BELONG TO SLA, NOT ICBM. HE HAS NOW ASKED FOR THIS TWICE, AND IT IS CROSS-BRANCH.

2026-08-26, unprompted and with feeling: *"The next thing we definitely need to do across the board
on all branches is move holds and pinned holds to SLA. It is not just for ICBM! I wish you had never
done that."*

He said the same thing on 2026-08-18 -- *"holds shouldn't even be a part of ICBM, they are a part of
SLA"* -- and it was written down as a "known violation, deliberately left alone", which is a
description of a decision nobody made. **It is a task, and it is his, and it is not merely naming.**

**WHY IT IS NOT COSMETIC.** A hold is "against THIS posted limit I want a different number". That is
a statement about speed policy and it has nothing to do with how the speed is achieved -- so it is
actuator-independent by construction and must survive ICBM being deleted. Filed under `Icbm*` it
gets deleted with the scaffolding, or kept for the wrong reason. Under op long today the button
layer goes away and holds go with it, which is the bug this migration removes.

**HE EXPLICITLY DEFERRED IT: *"But don't do that now."*** Do not start it opportunistically. When it
is started:

- it is `icbm-manual-override-and-tuning` work, because that branch owns the code and the others
  rebase onto it -- doing it anywhere else strands it
- `IcbmPinnedHolds`, `IcbmHoldObservations` and `IcbmBaselineResetDelta` are PERSISTENT keys, so
  renaming discards his stored values. Use the `_BP_LATERAL_SCHEME_PARAM_RENAMES` machinery in
  `params_migration.py` that exists for exactly this
- the capnp fields (`vBaseline`, `baselineSource`, `pinSuggestion`) have WIRE HISTORY in every
  recorded route -- renumbering them makes every drive on disk decode as garbage. Rename the field,
  never the ordinal
- the settings labels, the SunnyLink YAML and the HUD reader all name it too; the audit
  (`bp_sunnylink_settings_audit.py`) needs the new prefix or it silently reports 100% reachable

**Known violations, deliberately left alone UNTIL THEN.** `IcbmPinnedHolds*` and
`IcbmBaselineResetDelta` are the HOLD concept, which is a planner idea that happens to live in the
button layer — "aim at my number instead of the posted limit, and keep everything else working
against it" needs no buttons at all. They are misnamed. Renaming a `PERSISTENT` key discards its
stored value, so this waits until holds actually move into the planner, and is done through the
`_BP_LATERAL_SCHEME_PARAM_RENAMES` machinery in `params_migration.py` that already exists for
exactly this.

**A new prefix used to carry a second obligation** -- registering it in `_BP_TRACKED_PREFIXES` so
its shipped defaults could reach the car. That mechanism was removed on 2026-08-08 and settings now
behave exactly as they do upstream, so there is nothing to register. See "Params, defaults, and his
settings".

## The ICBM button contract

Settled on the road, 2026-08-03. Do not change these meanings without asking — they are muscle
memory now, and the owner has already relearned them once.

A HOLD is the driver's own set speed. While one is held every other ICBM feature keeps working
against it: curves still slow the car, the hazard path still fires, and the speed returns to the
driver's number afterwards rather than to the speed limit.

| Key on the wheel | Cruise engaged | Cruise off |
|---|---|---|
| `RES +` (`CcAslButtnSetIncPress`) | `accelCruise` — creates or raises a HOLD | `resumeCruise` — engages and **keeps** the hold |
| `SET −` (`CcAslButtnSetDecPress`) | `decelCruise` — creates or lowers a HOLD | `setCruise` — engages; **with an SLA number it CLEARS the hold** and SLA takes the speed, **with no SLA number it HOLDS the speed at the press** (stock ACC) |

**THE SET-WHEN-OFF ROW IS CONDITIONAL AS OF 2026-08-25, and both halves came from one road
report.** Clearing is right only when there is something to hand the speed BACK to. With SLA
quiet, clearing left the car aiming at the planner's cruise target instead of at what he had
just asked for -- and the clearing half was itself broken, deferring to a set-speed comparison
that read his SET as a RESUME because the dash still carried the old number. The button event
now decides; `set_press_frames` mirrors `resume_press_frames`, and RESUME wins if both are armed.
| `CNCL` (`CcAslButtnCnclResPress`) | `cancel` | also reports `resumeCruise`; harmless, resume is reachable from either |

The wheel is **CNCL / RES+ / SET−**, with CNCL as its own dedicated button — confirmed from a photo
on 2026-08-04. RES+ and SET− are single keys that change meaning with cruise state.

**Corrected 2026-08-04:** `CcAslButtnSetIncPress` previously emitted `setCruise` when cruise was
off, so every press of RES to resume reached openpilot as a SET — the one event that discards the
driver's hold. That is the original "holds are not remembered on resume" report, and the behavioural
detector (comparing the landed set speed against the pre-cancel value) had been compensating for a
mislabelled event rather than a missing one. The detector stays as belt-and-braces.
`test_button_mapping.py` guards the table.

A hold is also cleared by returning the set speed to exactly SLA's target, or by the posted limit
moving more than `IcbmBaselineResetDelta`. It is NOT cleared by curves or lead vehicles.

**THE TAIL OF A RESUME PRESS DOES NOT CREATE A HOLD -- `RESUME_TAIL_FRAMES`, added 2026-08-22.**
This is the one deliberate exception to the RES+ row of the table above, and it exists because two
correct facts combine into a wrong outcome. He reported it as *"a hold got set without me doing plus
and minus"*; route 000003aa, from ONE press of RES+:

    809.83  enab=False  resumeCruise    <-- he presses RES+, and nothing else
    809.85  enab=False  resumeCruise
    809.86  enab=True   accelCruise     <-- SAME physical press, cruise engaged part-way
    809.88  enab=True   accelCruise     -> HOLD CREATED at 32 mph on a 35 road

RES+ derives its meaning per frame from the cruise state, which is the contract and is right. And
this car's SCCM clears the button bit between frames -- see "Buttons cannot hold" -- so one physical
press arrives as a BURST of press/release cycles. The instant cruise engages mid-burst the rest of
that press reads as `+`.

**KEYED ON PROXIMITY TO THE RESUME, not on having recently engaged.** The engage-edge version was
tried first and broke 23 tests, correctly: a `+` pressed a moment after engaging is ordinary and
must still work. The phantom arrived 0.02 s after the resume; the genuine presses on those same
drives came 3.5 s and later.

**Only CREATION is suppressed**, and the whole press block is skipped rather than just the capture
inside it -- guarding the capture alone did nothing, because `override_state = manual` is set
unconditionally below it and the press-settle path then took the baseline from the cluster anyway.

Two things that decided the design:

- **SET does not hold the current vehicle speed**, even though Ford's PCM briefly sets it there.
  If it did, every engagement would create a hold and SLA would never manage a limit unless the
  driver explicitly handed it back each drive. `+` is already the deliberate "I want a different
  number" gesture, so SET is left meaning "engage and manage it".
- **Tap moves the set speed 1 mph, press-and-hold moves it 5 mph** — the car's behavior, not
  openpilot's. Model set-speed movement as 5 mph jumps with stationary gaps, never a 1 mph ramp.

## WITHOUT SLA, EVERYTHING IS A HOLD. (This section's old rule is RETIRED -- 2026-08-25.)

His spec, and it replaces three earlier versions of the same rule:

  *"Without SLA, then everything should be a hold, and function identically to how stock Ford ACC
  functions, just with the added benefit of holds being remembered."*

**HE IS THE ONE WHO SPOTTED WHY THE OLD RULE EXPIRED**, and the history confirms it exactly:

  *"no posted limit means no hold was probably an old rule back before we had the max and hold
  speeds combined."*

    2026-08-15  enforce_hold_policy lands      "+/- just moves the max speed"
    2026-08-21  max_box_state lands            "the big number is what the car is being driven to"
    2026-08-22  the HOLD badge is deleted      the box IS the hold

For those six days a hold really was a SECOND number on screen in its own badge, so "do the max
speed, not the little number" was a meaningful instruction. Once the badge went, "just the max
speed" and "everything is a hold" describe the SAME SCREEN -- and all the rule still did was throw
away persistence across a cruise cycle and the trace pinned holds learn from.

**THE FUNCTION IS CALLED `enforce_hold_policy`, NOT `enforce_no_limit_no_hold`.** This file used
the second name for days after the code stopped using it. It is now an inert hook that returns
immediately, kept only so a future policy has an obvious home;
`test_the_policy_hook_is_now_inert` fails if anything is smuggled back into it.

**AND THE PIN GATE HAD TO NARROW IN THE SAME CHANGE, or pins die.** `apply_pinned_hold` deferred to
ANY live hold. With a baseline now usually present, that meant a pin could never fire on the roads
pins are FOR -- the 2026-08-16 failure ("no limit means no hold" killing pinned holds outright)
arriving from the other direction, and mutation testing is what caught that it was untested.

The gate now defers only to `BaselineSource.press`:

    press                     he pressed +/- here. A decision. The pin stands aside.
    fallbackIdle / Counter    the set speed drifted and a hold was INFERRED. Carried in from the
                              last road, not about this place. The pin wins.
    pinned                    one remembered number superseding another. The pin wins, as before.

Route 00000379 is why those are genuinely different: a hold was up for 36.5% of that drive reading
`fallbackIdle` while he pressed SET five times and nothing else. Route 0000033c is still honoured --
his 75 there came from a real `+`, so it reads `press` and the pin still defers.

**What follows is the SUPERSEDED rule, kept because its reports and measurements are still true.**

## (SUPERSEDED) NO POSTED LIMIT MEANS NO HOLD. THE MAX SPEED IS THE WHOLE INTERFACE THERE.

Asked for on 2026-08-15, twice, after two long highway drives:

  *"I want the +/- to just affect the max speed like normal, like when ICBM is off entirely, not
  affect the little number above the max speed."*
  *"I never want to affect the number on the top. That's ICBM's job, not mine."*
  *"There's no point in having the max speed be stuck where I hit set when there is no SLA."*

**Read the screen the way he does, because there are two "little numbers" and only one is meant.**
In the set-speed box the BIG number is `vCruiseCluster` (openpilot's own v_cruise -- the MAX, his).
The small text above it normally reads "MAX", and sunnypilot REPLACES it with the DASH set speed
`speedCluster` whenever ICBM is actively moving it (`show_icbm_status`). That top number is the one
he means, and it is ICBM's to move, never his. The HOLD badge is a third thing again.

**What the drives showed.** Route 00000378: SLA had a limit in 98.4% of plan frames, holds ran
70-80, `baselineSource` read `press`. Working as designed. Route 00000379: a hold was held for
**36.5%** of the drive with `overrideState` reading manual and `baselineSource` reading
`fallbackIdle` -- the path that INFERS a press from set-speed movement. He pressed SET five times and
pressed nothing else all drive. Nearly every hold on that drive was inferred rather than chosen.

**THE "1.7% OF FRAMES HAD A LIMIT" FIGURE THAT USED TO SIT IN THAT SENTENCE IS WITHDRAWN, 2026-08-16.
It was measured over the front segments of a 53-segment route, where the car was PARKED.** Whole
route, above 5 mph, it is **50.9%** of 62,940 plan frames -- re-measured twice by two sessions that
had independently fallen into the same trap, and cross-checked against 00000378. He said "the speed
limit works" at the time and he was right. See `bluepilot/MAPD-V2-PLAN.md` for the corrected numbers
and for the sampling bug behind them, which affects any whole-drive percentage produced with a
`--max-segments` cap.

**This does not disturb the rule below.** He asked for it in his own words, twice, and the inferred
holds on 00000379 are logged fact independent of how much of that drive had a limit. What changes is
only how OFTEN the no-limit case arises: about 14% of the road he covers, not nearly all of it.

**The rule: `enforce_no_limit_no_hold()`, called straight after `update_manual_override`.** A hold
only ever meant "for THIS posted limit I want a different number". With no limit there is nothing to
want a different number than, and `v_baseline = v_cruise_cluster` at every capture site -- so the
hold is a second name for a value he can already see and already controls. With no baseline
`apply_baseline` is the identity, ICBM aims at the planner's cruise target, and the max speed
behaves exactly as it does with ICBM off, while curves, leads and the hazard path keep working
because none of them ever depended on a baseline existing.

**PINNED holds are the exception, and he confirmed they are the COMMON case where it matters:**
*"we do still want pinned holds since those are frequently done when SLA doesn't have a number."*
So the carve-out is the main path on no-limit roads, not an edge case -- which meant it had to be
protected properly, and it was not at first:

- The inferred fallback rewrote `v_baseline` from the cluster on any set-speed movement, and the
  press-settle path rewrote it again a few lines later. A pin is EDGE-TRIGGERED and already spent by
  then, so nothing restored it and nothing on screen said why. **Both write sites now exempt a
  pinned baseline.** The second one is the one that actually bit, and it was found by tracing every
  write to `v_baseline` after guarding only the first failed to fix the test.
- A real BUTTON press still takes the baseline over from a pin -- that path sets the value and the
  `press` label unconditionally. Overriding a pin by hand is deliberate; drifting off one because
  the cluster moved is not. With no limit the hand override then correctly leaves no hold at all.
- A third guard was added to the LABEL write and then REVERTED: it sits nested inside the value
  guard, so it can never fire. Mutation testing is what showed it -- removing it broke no test. A
  guard that cannot fire, carrying a comment that calls it load-bearing, is worse than no guard.

That is also why this is a single call at the end rather than a
guard at the three capture sites: `apply_pinned_hold` runs INSIDE `update_manual_override`, so a
blanket rule there deletes the whole pinned-holds feature silently. A pin is an explicit gesture at
an explicit place and is the one hold that still means something with no limit -- so it survives,
and `worth_showing` now draws the badge for a pinned hold even with no SLA, or he would have a speed
governing the car with nothing on screen and no tap target to remove it.

**AND IT KILLED PINNED HOLDS OUTRIGHT FOR TWO DAYS. Found 2026-08-17 from his DEVICE, not the
code**, when he asked whether everything was good: `IcbmHoldObservations` was 6 KB and written that
morning, while `IcbmPinnedHolds` was `[]` and five days stale. **He has never successfully created a
pin**, and he had described them as frequently used -- so the discrepancy was the finding.

Both halves of the feature keyed on `v_baseline`, which the rule sets to zero:

- `selfdrived.update_pinned_holds` calls `observe_hold` only when `baseline > 0`, so on a no-limit
  road NOTHING was recorded and no suggestion could ever form.
- **The badge is the ONLY tap target for pinning.** `_hold_rect` is set where the badge is drawn and
  cleared to None everywhere else -- *"no badge on screen, no tap target"*. No hold meant no badge
  meant the gesture did not exist. On precisely the roads pins are for.
  (**On the big screen that target is the SET-SPEED BOX since 2026-08-22** -- the badge is deleted;
  see the section below. The shape of the bug is unchanged and is why the new rect is set outside
  the ACC stack. mici still has its badge and this paragraph is still literally true there.)

The fix is three pieces, and the middle one is the one that is easy to miss:

1. `no_limit_hold_speed` keeps the baseline as it stood one frame before clearing. It is still the
   DELIBERATE press it always was; observing `v_cruise_cluster` instead would record every number he
   passes through and drown the signal that makes a suggestion mean anything.
2. `worth_showing` is now also true for a BARE PIN SUGGESTION with no hold, and `display_value`
   draws the offered speed -- taking `baseline` there rendered a badge reading `0`.
3. `pinSuggestion` is read OUTSIDE the hold branch. It had been read inside it, so it was
   unreachable in the exact case it exists for.

### A HOLD WALKED BACK TO SLA'S NUMBER COULD NEVER CLEAR. HE REPORTED IT TWICE.

*"It looks like setting the hold back to SLA does not clear the hold."* Said on 2026-08-21 and
again earlier, answered both times with a test that says it does. He was right and the test was
measuring something else.

**The rule is two halves** -- arm `baseline_diverged` while the hold DIFFERS from `v_target_raw`,
then clear when it comes back -- **and both halves lived at the bottom of `update_manual_override`,
which returns early on any frame a cruise button is pressed** (line ~1111). The only frames in which
the hold actually differs are the ones where he is pressing it down toward SLA's number. So the arm
never ran on a single one of them:

    t+816.7   baseline 39   vTargetRaw 35   diverged False   <-- should have armed here
    t+817.4   baseline 35   vTargetRaw 35   diverged False   <-- nothing left to observe
    ...9 s at baseline == target, the hold never clears...
    t+826.2   he switched cruise off, which is what actually ended it

**Measured on route 000003a8, and the earlier diagnosis was wrong.** This session first reported it
as "clears 9.4 s late, delayed by the press-settle stand-down". It does not clear at all -- what
looked like a late clear was him pressing the main cruise button. Press-settle was a red herring:
the press PATH returns long before the press-settle block is even reached.

**Fixed by moving the ARM to `update_calculations`**, which runs every frame and which no early
return in the override path can skip. Only the arm moved; clearing stays where it was, because
clearing ACTS on the car and acting mid-press would undo the press he is making. Observing is free.

**THE TEST THAT SAID OTHERWISE IS THE LESSON.** `test_the_hold_clears_when_the_source_oscillates`
walks the speed down too -- but it RELEASES the button and runs 12 idle frames between presses,
which hands the arm exactly the frame the real stalk never gives it. Fixtures more orderly than
reality, for the fourth recorded time in this file. The new test holds the button on every frame
and raises the hold while cruise is OFF, which is what the log shows him doing.

### THE HOLD BADGE IS GONE, 2026-08-22. THE SET-SPEED BOX IS THE HOLD NOW.

His instruction, and he was right that it was overdue: *"the hold badge is completely removed from
the code since we are just going to use the target speed, right?"* It was not removed -- the
`max_box_state` work on 2026-08-21 made the BIG NUMBER show the hold and tinted the box while the
hold owned it, and left the badge drawing the very same number underneath. A hold rendered twice.

**Everything the badge uniquely carried moved rather than being dropped.** That list is the whole
risk of this change, because each item is a thing that quietly stops existing if it is forgotten:

| the badge did | where it went |
|---|---|
| the hold's number | the big number in the box (`max_box_state.aim`) |
| "not yours to change" (locked) | the box stops tinting -- `hold_locked` on `MaxBoxState` |
| the pin dot / suggestion ring | the same corner, of the box, in `HudRendererSP._draw_set_speed` |
| the speed being OFFERED | the label slot, **rank 3**, above MAX and below the dash number |
| **the tap target for pinning** | `_hold_rect = self._set_speed_rect` |
| the +/- arrow | **dropped.** Rank 1 already shows the dash number whenever the car is not at the aim -- which is every moment the arrow was drawn -- and it says how far off, not merely which way. |

**THE TAP TARGET IS THE ONE THAT BITES.** `_hold_rect` used to be set inside `_draw_hold_badge`,
so deleting the badge deletes pinning outright unless it is re-fed. That exact failure has already
happened once here -- `IcbmPinnedHolds` sat `[]` for two days while observations piled up, because
the badge carrying the rect was not drawn on the roads pins are for. It is now set in `_render`
next to `_draw_set_speed` rather than inside the ACC stack, because that stack returns early in
several states and the gesture has to survive all of them.
`test_the_tap_target_is_the_set_speed_box` parses for the assignment and was verified to fail when
fed the wrong rect.

**THE COMMA 4 KEEPS ITS BADGE, deliberately.** `MiciHudRendererBP` extends mici's own renderer, not
sunnypilot's, so it never had `max_box_state` -- on that screen the badge is the ONLY place the hold
appears, and deleting it there would remove the readout rather than de-duplicate it. The two screens
now draw the hold differently, which is allowed and always was (`icbm_hud_state`'s own docstring:
the drawing is genuinely different on 536x240 and has to be written twice; deciding WHAT IS TRUE
does not). If mici ever adopts the aim-in-the-box rule, that is when its badge goes.

**The preview renders it** -- `preview_acc_status.py`, 14 scenes including pinned, offered, locked,
and a hold with no limit. Its scenes are dicts now: the old tuples had one `hold` column standing
for both the hold and the offered pin, which was fine while the badge showed whichever existed and
is wrong now that they are different inputs producing different pictures.

**The lesson is where it was found.** Three sessions of tests, mutation testing and code review did
not surface it, because every one of them asked whether the code did what it said. Two param files
with mismatched timestamps did. **When a feature has persistent state, read the state off the
device** -- an empty store beside a growing one is a defect report nobody had to write.

**What this gives up, stated because he should hear it rather than discover it:** a number he sets
on a road with no coverage is no longer carried in as a hold when coverage returns. SLA takes the
speed at that point, and he presses + to override -- which now creates a hold, because a limit is
known. `TestAHoldMadeWhereNoLimitIsKnown` used to protect the opposite behaviour from a 2026-08-06
report; it is REPLACED, not weakened, because its premise no longer exists.

**And it exposed a fixture defect worth remembering.** `test_drive_scenario.py`'s `step()` built its
plan message with NO `speedLimit` field at all, so `LP_SP.speedLimit` raised on every frame and every
scenario in that file had silently been running as "no limit known" -- the rare road, everywhere.
`make_lp` in `test_manual_override.py` carries a docstring about being fixed for exactly this reason;
the second harness was never fixed with it. Two scenarios failed for a reason unrelated to the change
and that is how it surfaced. **When one fixture is documented as having had a hole, go and check the
others for the same hole.**

**Both halves were mutation-tested, and one test was vacuous until that was done:** disabling the
rule initially failed only ONE of the two tests, because the existing baseline-equals-target rule was
clearing the hold in the other. Green was not evidence; breaking the code on purpose was.

## ICBM CANNOT STOP THE CAR, AND FAKING A LEAD IS NOT THE WAY ROUND IT

Asked 2026-08-17: *"Can we not fake a lead vehicle to have Ford ACC come to a complete stop?"* The
motivation is correct -- **`get_minimum_set_speed()` returns 20 mph** (30 kph), which is FORD's floor,
not ours. Every ICBM feature commands through the set speed, so the model-stop path can walk the car
down toward 20 and no further. Stock ACC comes to a full stop only when its OWN radar sees a lead.

**AND THE 20 IS FORD'S, CONFIRMED BY HIM 2026-08-17: *"No, I can't set it lower than 20."*** Worth
recording because `get_minimum_set_speed()` is UPSTREAM sunnypilot's and returns the same 20 mph /
30 kph for Hyundai, Honda, Chrysler, Mazda and Ford alike -- a generic constant that nobody here had
checked against this car. It happens to be exactly right. **The question is closed; do not re-open it
hoping the floor is ours.**

**But the answer is no, four times over, and any one of them is fatal:**

1. **Panda's TX allowlist has no radar message at all.** `FORD_COMMON_TX_MSGS` is
   `Steering_Data_FD1` (buses 0 and 2), `ACCDATA_3`, `Lane_Assist_Data1`, `IPMA_Data`; the LONG
   variants add `ACCDATA` and `LateralMotionControl`. Nothing on bus 1, nothing radar.
2. **We are not in line with the radar.** The relay is on the CAMERA. Bus 1 can be read but its real
   frames cannot be removed, so injecting means two transmitters for the same IDs -- a conflict, not
   an override. Bus 1 is also already 60-73% loaded.
3. **A convincing stopped target is the exact input to AEB.** The camera fuses radar for FCW and
   emergency braking (`Cmbb_B_Enbl`, `FcwVisblWarn_B_Rq`, `FcwAudioWarn_B_Rq`, all in `ACCDATA_3`
   which the camera authors). Aiming for a smooth stop with no control over the transfer function
   from target to brake force, the plausible failure is a panic stop.
4. **The direct path already exists and is better.** `FORD_ACCDATA` IS the brake command --
   `AccBrkTot_A_Rq`, `AccBrkDecel_B_Rq`, `AccStopStat_B_Rq` -- and it is already in the TX list.
   Faking a target to persuade the camera to compute the braking we want is a worse version of
   sending the braking command ourselves.

**And 4 is the real conclusion: `ACCDATA` is LONG-only and carries `check_relay = true`**, so panda
verifies the camera's ACCDATA is relayed OUT before ours is let in. Openpilot longitudinal control is
therefore ALL-OR-NOTHING by construction. There is no arrangement where the camera keeps computing
ACC and we send ACCDATA for just the last few mph of a stop; the two cannot coexist on the bus.

Do not propose a fake lead, a fake target, or a partial ACCDATA takeover.

## THE STOCK ACC PASSTHROUGH IS DELETED, 2026-08-25. EVERYTHING BELOW IT IS A POSTMORTEM.

**Every section from here until "Params, defaults, and his settings" describes a feature that IS NO
LONGER ON THIS BRANCH.** The code, the params, the toggles, the capnp field and the panda flag were
all removed here. Read what follows for the lessons and the measurements -- both are good -- but do
not go looking for `create_acc_msg_passthrough`, `stop_override.py`, `accAuthority` or
`StockAccPassthrough` in this tree.

**THE CODE IS PRESERVED ON `passthrough-archive`, frozen at `25ae8a6413`** -- the last commit where
it was whole and working. That branch is DEAD: nothing rebases onto it, nothing merges out of it,
and it is not to be developed. It exists so the measurements can be re-run and the implementation
re-read, which is why deleting it outright was the wrong call:

    git show passthrough-archive:opendbc_repo/opendbc/sunnypilot/car/ford/stop_override.py

**HIS DECISION, and he was right:** *"Without complete stops without a lead on passthrough,
passthrough is useless. If we really can't figure something out, then we should entirely remove
it."* Then: *"we still don't want passthrough itself on any branch."*

**WHY IT COULD NOT WORK. Four independent reasons, each sufficient on its own:**

    continuous override      kills Ford ACC for the drive      measured, 3/3 beyond ~2 s;
                                                               under 1.84 s, 5/5 survived
    interleaving             the camera watches MOTION, not    handing back 1-in-5 frames does not
                             our frames                        change how the car is moving
    AccVeh_V_Trg             not sent to the brake controller  DBC receiver list: braking goes to
                                                               ABS_ESC, that field does not
    disengage, then stop     no panda authority with cruise    ford.h pcm_cruise_check ->
                             off                               controls_allowed -> longitudinal

**AND IT WAS STRICTLY WORSE THAN LEAVING OP LONG OFF.** Passthrough's only benefit was the stop
override. Everything else -- ICBM, SLA, both curve controllers, holds, the gap button -- works
identically with op long off, and with op long off Ford drives the car directly through a closed
relay: no forwarding, no clamping, no substituted frames, and no camera cancel possible.

**TWO THINGS SURVIVED THE DELETION because they stand on their own:**

- **The camera-ACCDATA readback** (`carstate_ext` -> `brakeLightStatus.accAccelRequest` /
  `accPropulsionRequest` / `accDecelRequest`). Reads the camera's frames off bus 2, which happens
  whether or not anything is forwarded. It feeds the brake-lamp pill AND it is the reference data
  for what replaced this work.
- **The panda-band clamps in `create_acc_msg`.** They apply to openpilot's OWN authored frame and
  stop panda silently dropping ACCDATA when a value lands outside its band.

**WHAT REPLACED IT: the `ford-acc-parity` branch (`../bluepilot-ford`).** Chasing why op long is
bad on this car turned up something specific -- openpilot asserts the friction brakes at
**-0.14 m/s^2** and clips its propulsion request at **-0.5**, while Ford ramps engine braking to
**-0.66** and only hands over to the brakes below **-1.1**. Ford blends the two across that whole
band; `if brake_actuate: gas = INACTIVE_GAS` makes that structurally impossible for openpilot. That
is his "op long can't coast", and it is two constants and one `if` rather than a planner problem.

**THIS BRANCH IS ICBM, SCC AND SLA ONLY.** That is what it was always for, and the passthrough
should have been its own branch from the start. Do not add longitudinal-authoring work here.

---

### BUT THERE IS ONE CANDIDATE SIDE DOOR: FORWARD THE CAMERA'S OWN ACCDATA, OVERRIDE ONLY THE STOP

Raised 2026-08-17 after he pushed back on two flat "no" answers. **He was right to push.** An earlier
version of this section ended "there is no side door", and that was too strong.

**The observation it rests on was measured this morning and its meaning was missed at the time.** The
APIM probe showed `0x462` with a `bus 130` count -- openpilot's own TX echo onto bus 2. **We forward
bus 0 traffic to the camera**, so with the relay open the camera keeps all its inputs, keeps computing
ACC, and keeps transmitting `ACCDATA` on bus 2.

So: run op long, read the camera's `ACCDATA`, **republish it byte-for-byte on bus 0**, and substitute
our own only for the seconds a stop needs. The car then behaves exactly as Ford ACC, because the
commands ARE Ford ACC's. **This does not require improving op long to match stock -- it borrows stock's
output and authors only the part stock will not do.**

**The mechanical blocker is absent, checked rather than assumed: `ACCDATA` (0x186, 390) carries NO
COUNTER AND NO CHECKSUM.** Every signal is a plain value, so verbatim forwarding is trivial and --
the part that matters -- handing control BACK after an override needs no resynchronization. Two more
facts from the same read: `AccVeh_V_Trg` ranges from 0 kph, so the control message has no floor and
confirms the 20 mph limit is set-speed only; and `AccBrkPrkEl_B_Rq` is in the message, so the
stop-and-hold vocabulary is already there.

**THE OTHER LIKELY KILLER WAS CHECKED AND IS NOT THERE.** Panda caps `AccBrkTot_A_Rq` at
**-3.4991 m/s^2** (`FORD_LONG_LIMITS.min_accel`), while the signal itself can express -20 -- so if
Ford ever brakes harder than panda allows, the forwarded frame is BLOCKED and the braking is lost at
exactly the wrong moment. Measured across six routes, 2026-08-17, using
`carStateBP.brakeLightStatus.accAccelRequest` which is that signal straight off the camera:

    189,418 braking frames.  ZERO above the limit.  Hardest Ford ever commanded: -2.70 m/s^2.

0.8 m/s^2 of headroom and nothing in the bulk of the distribution past -2.5. Stock AEB rides a
separate path and panda refuses `cmbb_deny` outright, so it is unaffected either way.

**AND THAT WAS THE WRONG SIGNAL TO HAVE CHECKED ALONE** -- caught in review, 2026-08-17, before any
drive. `FORD_LONG_LIMITS` has THREE bands and the brake cap is the loosest of them:

    AccBrkTot_A_Rq   [-3.4991, 1.9999]              <- the one measured above
    AccPrpl_A_Rq     [-0.5, 2.0]  or exactly -5.0   <- never looked at
    AccPrpl_A_Pred   [-0.5, 2.0]  or exactly -5.0   <- never looked at

The gas band is four times narrower and sits exactly where a coasting or engine-braking Ford lives,
and `longitudinal_gas_checks` runs against BOTH fields. `violation |= cmbb_deny` is a fourth exit,
and `ford/carstate.py` already reads that same bit as `accFaulted`, so the camera does set it.

Two consequences, both now in the code. `fordcan_ext.passthrough_admissible()` asks whether panda
would accept the frame BEFORE forwarding it and falls back to openpilot's own ACCDATA when it would
not -- because `ford_tx_hook` does not clamp, it drops the whole message, so an inadmissible frame
makes a 50 Hz message vanish and reappear, which is worse than either controller driving. And the
passthrough is gated on `CC.longActive`: with longitudinal inactive `get_longitudinal_allowed()` is
false and panda passes only the inactive frame, AND `create_acc_msg` clearing `Cmbb_B_Enbl` is how
openpilot's own disengagement reaches the car in the first place.

`tools/bp_accdata_bands.py` measures the refusal rate on drives already recorded. It is no longer a
safety question -- the fallback handles it -- but a high rate would mean the passthrough hands the
car back at exactly the interesting moments, which makes it a worse idea rather than a broken one.
**Run it before the first passthrough drive.**

**STOCK AEB IS NOT LOST UNDER OP LONG. Answered 2026-08-17 from the safety code, not assumed.**
The worry was that "trust Ford" is a weaker proposition if Ford's emergency braking cannot come
along. It can:

- `safety_fwd_hook` (opendbc/safety/safety.h:261) forwards camera -> car by default and blocks ONLY
  addresses in the TX list carrying `check_relay = true` for the destination bus. Its own comment
  names the reason: "Safety modes can opt out of this in the case of selective AEB forwarding."
- Ford's blocked set is `ACCDATA`, `ACCDATA_3`, `Lane_Assist_Data1`, `IPMA_Data` and the
  LateralMotionControl pair. **`ACCDATA_2` appears ZERO times in `modes/ford.h`**, so it is not in
  the TX list and is never blocked.
- `ford_hooks` sets no `.fwd`, so there is no Ford-specific blocking either.

`ACCDATA_2` is the message carrying `CmbbBrkDecel_B_Rq` -- `ford/carstate.py:229` reads it as
`ret.stockAeb`. It passes straight through to the ABS untouched, relay open or not. Stock AEB is
Ford's the whole time and openpilot never touches it.

This is also why `passthrough_admissible` refuses a frame with `CmbbDeny_B_Actl` set rather than
forwarding it: panda's `violation |= cmbb_deny` exists precisely so openpilot can never transmit a
frame that DENIES stock AEB, and forwarding Ford's own deny bit would trip it.

**Also learned: NO route on the device has ever had op long enabled**, so the camera question below
cannot be answered from existing data. It needs a drive.

## DRIVE A: THE CAMERA DOES KEEP WORKING. The first read of the numbers said the opposite and was wrong.

Route 00000383, 2026-08-18, `openpilotLongitudinalControl = True`, `StockAccPassthrough = 1`.
29,890 camera ACCDATA frames, 29,525 transmitted by us.

    camera asserted AccCancl_B_Rq   21,090 frames   70.6%
    camera asserted CmbbDeny_B_Actl      0 frames    0.0%
    we forwarded the camera's frame   4,940         16.7%
    we fell back to our own          24,571         83.3%

**THAT 70.6% IS THE WRONG DENOMINATOR AND IT PRODUCED THE WRONG CONCLUSION.** It was read as "the
camera spends the drive asking to cancel, so its loop is open and it knows" -- and the owner pushed
back rather than accepting it, which was correct. Restricted to frames where openpilot was actually
ENGAGED:

    ADMISSIBLE -- Ford's own frame passes everything    5,126   71.7%
    blocked by AccCancl_B_Rq                            1,333   18.6%
    blocked by AccPrpl_A_Pred band                        685    9.6%
    blocked by AccPrpl_A_Rq band                           10    0.1%
    blocked by AccBrkTot cap                                0    0.0%

The 70.6% was dominated by 15,075 frames where NOTHING WAS ENGAGED, and cancel is exactly what the
camera should say there. `CmbbDeny_B_Actl` was 0.0% across the entire drive -- the camera never
faulted. With cancel clear its mean `AccBrkTot_A_Rq` was +0.100 and its minimum -2.80: real ACC
commands. **The camera tracks openpilot's engagement state and keeps computing. The premise holds.**

**The lesson is the denominator.** "70.6% of all frames" and "17.7% of engaged frames" are the same
data and opposite conclusions, and the first was published as a finding. Restrict to the frames
where the feature is live BEFORE reading anything into a rate.

Everything the owner reported follows from it:

- **The park brake applied behind a stopped car.** Four of our transmitted frames carried
  `AccBrkPrkEl_B_Rq`. `create_acc_msg` never sets it and the relay blocks the camera's own copy, so
  the passthrough was the only path to the ABS. `carState.parkingBrake` was true for 2,231 frames
  with 20 `parkBrake` events. He had never seen the car do this and there was no indicator light.
- **It would not stay engaged.** `accFaulted` x82, and NOT from the camera --
  `carstate.py:202` reads `EngBrakeData.CcStat_D_Actl in (1, 2)`, the PCM's own cruise status. The
  PCM was being handed our authored command 83% of the time, Ford's 17%, and relayed cancels on top.
- **The gap button changed openpilot's aggressiveness.** `personalityChanged` x3. Under op long the
  stock handler takes the button first; it never reaches the camera.
- **ICBM had to be re-enabled and did not stick.** `interfaces.py:83` deletes the param under op
  long and it has no default, so it returns as off. Confirmed on the device: `unset`.

**AND THE REVIEW FINDING WAS THE MAIN EVENT.** `AccPrpl_A_Pred outside panda's band` is the dominant
refusal reason in the log, sweeping -1.79 -> -1.29 while coasting. That is the band recorded above as
"never looked at". Without `passthrough_admissible`, every one of those frames would have gone out
and panda would have dropped it -- 83% of a 50 Hz message vanishing and reappearing. The drive would
have been far worse than it was.

**AND THE FIX FOR THE SECOND-LARGEST BLOCKER IS ONE FIELD.** `AccPrpl_A_Pred` is the PREDICTED
accel -- a feed-forward hint to the powertrain, not a command -- and **upstream openpilot hardcodes
it to exactly -5.0**, panda's legal escape value, which is therefore what this PCM sees on every
normal op-long frame. `create_acc_msg_passthrough` now pins it and `passthrough_admissible` no
longer refuses on it: **71.7% -> 81.3% forwarded, for a hint the car already lives without.**

**AND THE 18.6% CANCEL WAS MOSTLY SELF-INFLICTED. Traced 2026-08-18, and this is the finding.**

    camera first asserts cancel            t+0.02    (whenever ACC is not running -- correct)
    five clean engagements                 t+22.1 .. t+234.45, 11.6/46.4/27.6/15.4/20.9 s
    camera asserts cancel WHILE ENGAGED    t+229.43
    WE FORWARD THAT CANCEL                 t+234.44
    the fifth engagement ends              t+234.45   <- ten milliseconds later
    everything after                       70 windows, mean 1.91 s

**Relaying the camera's cancel request knocked openpilot out, and every re-engagement relayed the
still-asserted cancel and knocked it out again -- seventy times.** That is the chime cycling he
reported, and the park brake at the stop is downstream of the same loop. It was ours, not Ford's.

**The honest refusal rate is 4.1%**: before the first forwarded cancel, the camera refused for 5.0 s
out of 121.8 s engaged. With cancel and deny now refused rather than relayed, the loop cannot form,
and the expected forwarding rate is the admissible share plus the pinned-pred share plus most of
what the loop was destroying.

**AND THE LATCH ITSELF IS EXPLAINED. IT IS THE SAME FIELD, and it is the whole story.** Traced to
the frame, 2026-08-18. In the seconds before t+229.43:

    camera  1353011100c5c400   AccBrkTot_A_Rq -0.71   AccPrpl_A_Pred -2.27   <- braking for a lead
    we sent 13f80000032205f4   accel          -0.06   gas             0.00   <- braking for nothing
    radar   lead at 22.1 m, closing at 1.9 m/s;  the car ACCELERATING, aEgo +0.1 -> +0.27

`AccPrpl_A_Pred` was out of panda's band, so the frame was refused and openpilot's own command went
out instead -- and openpilot was not braking. Over the run-up:

    window before the latch    OLD refused    NEW refuses    camera braking
    last  5 s                    31.6%           0.0%           24.8%
    last 20 s                    44.6%           1.0%           25.5%
    last 40 s                    51.7%           0.5%           16.5%

**The camera spent forty seconds watching the car ignore its braking requests, and then gave up.**
Half its commands were being thrown away over an advisory field the PCM does not need. Then we
forwarded the cancel it raised, and that produced the seventy re-engagement cycles.

So every symptom he reported -- the cycling, the chime, the park brake at the stop, "Ford ACC worked
for a little bit" -- traces to `AccPrpl_A_Pred` not being pinned. Pinning it takes that same window
from 51.7% refused to 0.5%.

**CLOSED 2026-08-19: THE BRICK HAS NOT RECURRED IN SIX CONSECUTIVE DRIVES.** He said the DTCs he had
offered probably no longer mattered, and the logs agree -- every route after 0000038d:

    0000038e  22 segs   inert 0   accFaulted 0   ford 99.9% of engaged
    0000038f  12 segs   inert 0   accFaulted 0   ford 99.3%
    00000390   6 segs   inert 0   accFaulted 0   ford 99.6%
    00000391  12 segs   inert 0   accFaulted 0   ford 98.3%
    00000392   1 seg    inert 0   accFaulted 0   (too short to score)
    00000393  26 segs   inert 0   accFaulted 0   ford 97.8%

Zero inert frames, zero `accFaulted`, Ford authoring 97.8-99.9% of engaged frames throughout. Both
mechanisms were identified from logs and neither ever needed a fault code: we were RELAYING THE
CAMERA'S OWN CANCEL back at it (70 re-engagement cycles) while discarding roughly half its braking
commands over `AccPrpl_A_Pred` (51.7% refused in the 40 s before it gave up). A DTC would only have
said the camera was unhappy, which was never the open question.

**The narrower thing still unproven** -- whether a camera that HAS latched releases cancel once its
commands are honoured -- is now unfalsifiable from this car, because it has not latched since. So
`passthrough_cancel_frames` stays as the ERROR-level detector rather than being removed: it is the
instrument that would catch a THIRD mechanism, and a third mechanism is the only thing that would
make the DTCs matter again.

**The park-brake path is real and its refusal stays**: four
transmitted frames carried `AccBrkPrkEl_B_Rq`. That is now refused along with cancel, deny, stop
status, brake pulse and auto-resume -- the bits panda does not police at all. **"Panda would allow
it" was never the same question as "we understand it", and the first version only asked the first
one.**

**THE UNKNOWN THAT DECIDES IT, and it cannot be settled offline:** while we forward faithfully the
camera's loop stays closed -- it commands, the car responds, its model stays consistent. During an
override it commands "hold 20" and watches the car stop anyway. Does it re-plan, fault, or drop ACC?
Nobody knows, and the answer arrives on the first attempt.

**DRIVE B (00000387, 2026-08-18) DID NOT ANSWER IT, and the reason is the useful part.** With
`AccPrpl_A_Pred` pinned, **91.1% of longActive frames carried Ford's own command** -- the passthrough
worked, and he confirmed it from the seat: *"it sure felt like Ford ACC not op long."* The camera
raised cancel **zero times** while longActive, and never asserted deny.

But that is not evidence the camera tolerates contradiction, because there was almost none:

    contradiction runs while longActive   347
    total contradicted time               1.3 s     <- across the WHOLE drive
    longest single run                    0.2 s
    largest disagreement                  1.08 m/s^2, for 10 frames, with Ford braking

Set that beside drive A, where **~40 s at ~51% refusal while the camera was braking** produced a
cancel latch that never cleared. The two drives bracket the question and neither is near the
override's regime: a stop from 20 mph is FIVE TO EIGHT SECONDS of continuous contradiction, two
orders of magnitude past anything measured, and an order under drive A.

**So the override's real unknown is a DURATION THRESHOLD nobody has measured**, and there is a second
unknown inside it: drive A contradicted by UNDER-braking relative to Ford, while the stop override
contradicts by OVER-braking. Whether the camera cares about the sign is not known either.

Two consequences for building it:

- **Bound the override in TIME explicitly**, not only by its trigger condition. "A stop line ahead"
  says when to start and nothing about when to stop, and the thing that bites is total continuous
  seconds of disagreement.
- **The latch detector already exists** -- `passthrough_cancel_frames` logs at ERROR after 5 s of
  continuous cancel. That is the instrument the first override drive is read with, and it should be
  treated as the experiment's readout rather than as an error nobody expects.

**THE ANSWER TO "WHAT DO I TURN ON" IS TWO TOGGLES, AND THE UI NOW SAYS SO.** He asked on
2026-08-18 and it was a fair complaint -- three plausible candidates, no guidance:

    openpilot Longitudinal Control (alpha)   ON     <- permission: opens the relay, puts ACCDATA in
                                                       panda's TX list
    Use Ford's Own ACC Commands              ON     <- who authors it: Ford
    Dynamic Experimental Control                    <- irrelevant
    Experimental Mode                               <- irrelevant

DEC and Experimental Mode choose how OPENPILOT computes acceleration, which is exactly the job
handed to Ford, so under the PURE passthrough they steer a plan that is discarded.

**THAT STOPPED BEING TRUE THE MOMENT THE STOP OVERRIDE EXISTED, and he is the one who caught it:**
*"Remember, I used to use alpha long with experimental mode and DEC."* That was the configuration
his complete stops came from, and it is not incidental --
`sunnypilot/.../longitudinal_planner.py:42`:

    def is_e2e(self, sm) -> bool:
      experimental_mode = sm['selfdriveState'].experimentalMode
      if not self.dec.active():
        return experimental_mode
      return experimental_mode and self.dec.mode() == "blended"

and `shouldStop` picks up `modelV2.action.shouldStop` -- the model's stop-for-a-light -- ONLY inside
that branch. Without Experimental Mode, `should_stop` is the MPC's alone, which stops for leads and
cruise targets and **never for a stop sign**.

**So the override cannot arm without Experimental Mode.** It is the one consumer of openpilot's plan
in this whole design, and the plan only contains a stop when the end-to-end model is driving it.
DEC on is fine and is what he ran -- it selects `blended` on `has_slow_down`, which is the same
signal the override triggers on -- but Experimental Mode is not optional.

**THAT SENTENCE WENT STALE ON 2026-08-20 AND NOBODY NOTICED FOR TWO DAYS.** It was true while the
trigger was `shouldStop`, which only reaches the plan under Experimental Mode. The 2026-08-20
rewrite moved the trigger to `dec.has_slow_down()` -- and `DEC.update()` is called unconditionally
in `longitudinal_planner.py:120`, with only `dec.active()` gated on the mode. So from that day the
override COULD arm with Experimental Mode off, and what it would then transmit is `lng.accel` from
an MPC that never planned a stop for a light: take authority from Ford, spend the camera's ~1.5 s
of tolerance, provoke a cancel that costs him ACC for the drive, and not brake to a stop.

**Now enforced rather than assumed** -- `experimental_mode` is an argument to
`FordStopOverride.update`, gated at ARMING only (a stop underway finishes) and placed AFTER the
curve/gap path, which brakes for leads and corners in either mode. Mutation-tested.

**The lesson is the one this file keeps recording: a claim about behaviour that lives only in prose
stops being true the moment the code moves.** The fix is not "check the note more often", it is to
make the note executable -- which is what the argument and its two tests now are.

Corrected in three places that carried the old claim: the passthrough toggle description, the
SunnyLink entry, and this file.

### AND A WHOLE TEST DRIVE WENT TO IT. THE `e` BUTTON FLIPS IT MID-DRIVE ON ONE TAP.

2026-08-23. *"I didn't notice that experimental mode was off! No wonder it didn't come to stops at
traffic lights today. I don't remember turning it off!"* He was right that he did not knowingly turn
it off. Measured across the last eight routes, first segment against last:

    000003ad   0.0%  ->    0.0%      <- the test drive. 0 of 56,371 frames.
    000003ac   0.0%  ->    0.0%
    000003ab  100.0% ->    0.0%      <- IT FLIPPED HERE, MID-DRIVE
    000003aa  100.0% ->  100.0%
    000003a9 .. a5   100% throughout

**One tap on the on-road `e` button, during route `ab`.** `exp_button.py`'s `_handle_mouse_release`
is a bare `put_bool("ExperimentalMode", not current)` -- no confirmation, no dialog -- and both its
gates pass on this car (`ExperimentalModeConfirmed` 1, `has_longitudinal_control` 1). Route `ab` is
the drive straight after `aa`, which is one of the two that latched the camera's cancel and lost him
Ford ACC, so he was plausibly reaching for the screen.

**Check 14 in `bp_drive_checkup.py` reads it off the wire per frame, deliberately not from the
param.** The param says what the device holds NOW -- he turned it back on at 16:33 before any of
this ran -- and a share rather than a yes/no is what makes a MID-DRIVE flip visible at all. A param
mtime cannot show one.

**And the settings toggle now says so live**, as a third instance of the dependent-toggle prefix
already used for the passthrough and auto-resume. It is NOT disabled, unlike those two: the mode is
toggled from the road screen and legitimately varies drive to drive, so it is a state to report, not
a misconfiguration to prevent. It also does not catch the tap, because he is not in that screen when
it happens -- check 14 is what does, after the fact.

**THE THEORY THIS REPLACED WAS WRONG AND THE MTIME IS WHAT KILLED IT.** The first explanation built
here was the delete-chain below -- and `AlphaLongitudinalEnabled` was last written 2026-08-17 and had
not been touched since, so it never fired. Same lesson as the `SpeedLimitPolicy` entry further up:
**check the param's mtime against the route times before attributing anything to code.**

### `_enforce_constraints` DESTROYS SETTINGS WHEN CarParams HAS NOT LOADED. FIXED 2026-08-23.

Latent, found while chasing the above, and fixed on its own merits. `ui_state._enforce_constraints`'s
`else` branch -- reached whenever `self.CP is None`, which is every UI start before
`CarParamsPersistent` loads -- called `params.remove("AlphaLongitudinalEnabled")`.

**It is a two-boot chain and every step of it is silent:**

    boot 1   CP is None    -> AlphaLongitudinalEnabled REMOVED
             the key has NO DEFAULT (params_keys.h:40), so it reads False forever after
    boot 2   CP loads, alphaLongitudinalAvailable is forced True on every Ford, so
             has_long = get_bool("AlphaLongitudinalEnabled") = False
             -> `if CP is not None and not has_long` REMOVES ExperimentalMode AND
                DynamicExperimentalControl
             -> selfdrived.py:135 removes ExperimentalMode again for the same reason

So one unlucky boot costs op long, the passthrough, the stop override and Experimental Mode, and
nothing anywhere says why. **Guarding the second removal on `CP is not None` -- which had already
been done -- only delays it by one boot; the cause is the first.** The `else` branch now clears
nothing at all.

**The safety argument for clearing them does not survive the alternative:** these are read at CAR
INIT from CarParams, which by then is loaded, and a stale param cannot enable something the car does
not support because the car is what decides. This is the fourth instance in this file of the same
rule -- *"not known yet" is not "not supported", and removing a PERSISTENT param is not a way to say
it* -- after the fifth ICBM gate, the settings screen, and `interfaces.py`.

Two UI changes so the state is not reachable by accident: **the passthrough toggle is disabled when
op long is off** (it authors nothing in that state, so switching it on would silently do nothing),
and its description now carries the whole answer including the two toggles that do not matter.

**What it costs, stated plainly.** Every ACC command would route through openpilot, so a bug produces
NO BRAKING rather than a wrong set speed -- a real step up in blast radius from button injection.
Panda's existing `ACCDATA` checks still apply. And it is op long as far as the car and the safety
mode are concerned, for the whole drive.

**AND ICBM CANNOT COEXIST WITH IT TODAY, which is the wrinkle that decides the sequencing.** With
`openpilotLongitudinalControl` true:

- `_initialize_intelligent_cruise_button_management` never clears `pcmCruiseSpeed`, so the ICBM
  controller's `run()` early-returns.
- `_cleanup_unsupported_params` **REMOVES the `IntelligentCruiseButtonManagement` param outright.**

So enabling op long costs holds, Speed Limit Assist, both curve controllers and pinned holds for the
whole drive. Acceptable for a 20-minute experiment; not a state to leave the car in. Making the two
coexist means changing sunnypilot's own gating in two places -- a permanent merge cost -- and it is
NOT needed for the first test, only for the feature. Do not pay it before the camera question is
answered.

**WHAT HAPPENS TO OPENPILOT'S LONGITUDINAL STACK: it runs, and every bit of it is DISCARDED.** He
asked whether Dynamic Experimental Control is involved. It is not, and checking why is the clearest
description of the mechanism. DEC lives entirely in `longitudinal_planner.py`, choosing MPC modes for
openpilot's own plan; nothing in the Ford carcontroller path reads it. Under the passthrough DEC
still picks a mode, the MPC still solves, `LongitudinalExt` still computes `lng.accel`/`lng.gas` --
and then Ford's frame goes out instead.

**That is the whole point rather than a side effect.** Op long is used purely as PERMISSION -- it is
what opens the relay and puts `ACCDATA` in panda's TX list -- while the numbers stay Ford's. Expect
experimental-mode and DEC indicators to keep showing state that is driving nothing.

### DRIVES C AND D (0000038e, 0000038f, 2026-08-19): EVERY FIX FROM THE 18th HELD

`tools/bp_drive_checkup.py` asks all of it in one pass over the last N routes. Both drives ran the
06:03 merge, verified BY CONTENT rather than by hash -- a rebase makes hashes lie, and every fix
below was confirmed present in the running tree before any number was read.

    ICBM actually ran        MAX/dash diverged 76% / 50%     was LOCKED (ICBM off)
    SLA reached active       76.3% / 58.2% of plan frames    was ZERO, all drive
    "set your speed to 70"   never                           was constant
    +/- moved the MAX        3 of 3 presses                  moved the ICBM number
    Ford authored ACC        99.9% / 99.3% of ENGAGED        91.1% on drive B
    passthrough went inert   never                           bricked a whole drive
    accFaulted               0                               82 on drive A

He reported no complaints on either, and for once that agrees with the instruments.

**THE DENOMINATOR ERROR HAPPENED A THIRD TIME, in the tool written to check for it.** `accAuthority`
`stock` is `not CC.longActive` -- cruise not engaged, nobody driving -- and counting it made Ford's
share read **42.8%** when Ford in fact authored **99.3%** of the frames where anything was asked of
anyone. Same shape as the 70.6% on drive A and the 23% on drive 389. **Restrict to frames where the
feature is LIVE before reading any rate**, and note that this is now a mistake with three instances
and a written warning, so the warning is not enough on its own -- check the denominator explicitly.

**A PEAK TEMPERATURE IS NOT EVIDENCE OF A RUNAWAY PROCESS.** The first version called 81 C "something
is still burning a core". **The device idles at 75 C parked with the engine off in August**, and `ps`
shows only `mapd_v2` at 0.7% CPU -- v1 is gone, so the two-daemon fix took. `deviceState` has no
`ambientTempC`; the ambient proxy is **`intakeTempC`**, the fan intake air reading. The tool now
prints peak beside intake and deliberately renders NO verdict.

**AND `athenad`/`sunnylinkd` WEBSOCKET CHURN IS NOT A PROCESS DEATH** -- 22 and 25 of them, counted
as failures. A check that fires every drive forever is how a real plannerd death gets scrolled past,
which already happened once here.

**THE TSR 80 LEAK IS CONFIRMED ON THE ROAD AND ALREADY CLOSED -- BY HIM, BEFORE IT WAS RECOMMENDED.**
The two drives BRACKET the setting change, which is what makes this a measurement rather than a
prediction. `SpeedLimitPolicy` was written at **12:49**; 0000038e ran before it and 0000038f at 13:15
after:

    0000038e   policy 3, map_data_priority   map=22072  none=3214  car=700   <- 80 mph x700
    0000038f   policy 1, MAP DATA ONLY       map=9889   none=3663  (no car at all)

`Policy.map_data_priority` is `[map, car]` and takes the FIRST NON-ZERO, so it consults the map first
and **falls through to the car wherever the map is quiet** -- and the car source was returning a
constant 80.

**That 80 was NOT the camera reading a sign, corrected 2026-08-21.** `TsrVLim1MsgTxt` is the no-data
sentinel 255 on every frame of every recent drive (see the TSR section below), so it cannot be the
source. Where the 80 actually came from is still open. The fix is unaffected either way: only
`map_data_only` excludes the car source, and that remains right.

`combined` has the identical hole, since `min()` over a single source is that source. `map_data_only`
is the only one that excludes it, and the later drive shows it working: zero car-sourced frames.

**And the process lesson is worth more than the finding.** It was reported to him as a live problem
he should go fix, hours after he had already fixed it, because the two drives were treated as one
population. **A setting can change BETWEEN drives, so read the param's mtime against the route start
times before attributing anything to a setting** -- `stat -c %y /data/params/d/<key>` is the whole
check. He said "I swear I changed it to Map Data Only" and he was right.



### THE STOP OVERRIDE CANNOT FIRE ON A STOP HE TAKES HIMSELF, AND HE TAKES ALL OF THEM

Measured 2026-08-19 on route 0000038f with `StockAccStopOverride` ON. It never fired, and that is
not a bug in it. Chased through the whole chain because the first two answers were both artifacts --
a field read off the wrong struct, then a blocker named from a manufactured zero:

    the model DID ask to stop        longitudinalPlan.shouldStop true on 878 of 13552 plan frames
    longControlState                 off 20646, pid 15125, `stopping` ZERO -- those are the only two
    engaged frames                   29174
    ...engaged AND <= 20 mph            248      <- 0.85%, about 2.5 s of an 11-minute drive
    ...engaged AND stopped (<1 mph)       0      <- never once

**HE DISENGAGES BEFORE EVERY STOP.** Braking drops ACC, so by the time the car is actually stopping
at a light, openpilot's longitudinal is `off` and `stopping` is unreachable -- `longcontrol.py` is
`if not active: long_control_state = LongCtrlState.off`, evaluated BEFORE any stopping condition.
The 878 frames where the model wanted a stop are frames where he was already on the brake.

So the override needs him to STAY ENGAGED down to a standstill, which is a change in what he does
rather than anything to change in the code. Worth saying plainly instead of tuning around: **it is
asking for a trust he has no particular reason to extend yet**, on a car whose stock ACC has never
once held a stop -- `standstill` is 0 frames here and on every drive checked before this one.

**Do not "fix" this by loosening the arming conditions.** Each was put there for a measured reason
and the honest reading is that the precondition never occurred. What settles it is one deliberate
approach to a red light with no car ahead, foot off the brake; `passthrough_cancel_frames` is the
readout, and the camera's tolerance for sustained contradiction is what the drive would measure.

**And `hasSlowDown` IS NOT `shouldStop`** -- 3,490 frames of the first against 878 of the second on
one drive. DEC's urgency filter means "slowing for something"; the plan committing to a stop is a
different and much rarer state. Anything reasoning about STOPS has to read the second.

### THE STOP OVERRIDE IS BUILT, 2026-08-18. `opendbc/sunnypilot/car/ford/stop_override.py`.

**It authors NOTHING. It chooses which already-authored frame goes out.** `create_acc_msg` already
clamps to panda's bands, already drives the split brake/precharge hysteresis, and already never
touches the unpoliced bits that applied the park brake. So the override is a DECISION -- send
openpilot's command instead of Ford's for these few seconds -- rather than a second CAN authoring
path that would have to re-learn all of that. Keep it that way.

    fires when   the model is planning a stop (dec.hasSlowDown), the model's own stop ENDPOINT is
                 inside v^2/(2*1.5)*1.3 -- an URGENCY test, which is what that arithmetic reduces to
                 independent of speed: "a stop harder than 1.15 m/s^2, which the set speed cannot
                 deliver" -- at or below 20 mph, and NO radar lead within 60 m
    ends on      lead appeared | time bound | hold bound | long inactive | the model stops asking
                 for 0.5 s straight
    HOLDS ON     reaching a standstill. It does NOT hand back there: Ford will not hold a stop
                 without a lead, and handing back at 0.5 mph is what made the car creep.
    then         SPENT -- refuses to re-arm until hasSlowDown drops, so a stop that does not
                 complete cannot re-trigger every frame

**REWRITTEN 2026-08-20 and the old spec is kept nowhere, because it described behaviour that could
never happen.** `longControlState == stopping` is `shouldStop`, measured across 21,936 frames on
three drives as a STOPPED-CAR state -- never true above 3 mph. Requiring it to START a stop was
circular, and it is why the override had never fired on any drive. The section further down that
says "do NOT rewrite the trigger" was written before that measurement existed; the deliberate
approach it asked for is exactly what produced it (route 0000039a, his light, engaged, foot off the
brake, set speed walking 80 -> 57, `shouldStop` false the whole way).

`hasSlowDown` reaches the carcontroller through `longitudinalPlanSP` on its own SubMaster -- one
subscription rather than a capnp field plus controlsd plumbing. That is the field published the day
before for diagnostics; it turned out to be the trigger.

**A LEAD DISQUALIFIES IT** because stock ACC does that whole stop itself, better than openpilot
would, and overriding there spends contradiction budget on a case Ford already handles.

**THE TIME BOUND IS THE PART THAT IS EASY TO GET WRONG, AND IT WAS.** `update` runs inside the
ACCDATA block, gated on `ACC_CONTROL_STEP = 2` -- so it ticks at **50 Hz, not the 100 Hz control
rate**. The constant was first written as "800 = 8 s at 100 Hz" and would have been SIXTEEN seconds
of continuous contradiction, against the ~40 s that latched the camera on drive A. It is now derived
from `MAX_ACTIVE_S` and `OVERRIDE_HZ` with a test pinning `OVERRIDE_HZ` against `ACC_CONTROL_STEP`,
so the factor of two cannot come back.

### IT FIRED. IT COST HIM FORD ACC TWICE, PERMANENTLY, AND THE ARMING FLOOR DID NOT HELP.

Measured 2026-08-22 on the first three drives where the override actually ran. He reported it before
any log was opened: *"The two times I permanently lost Ford ACC were for this stop and when it
randomly braked on the most recent drive"*, and *"for both I pulled over and restarted the car to
get Ford ACC back."* Both are override episodes and there are exactly two.

    route  armed      ran     camera cancel   outcome
    a9     26.1 mph   1.1 s   none            fine
    a8     34.2 mph  12.6 s   +1.6 s          NEVER RELEASED (64 s, to the end of the route)
    aa     39.6 mph   2.6 s   +1.6 s          NEVER RELEASED

**Both latching arms were well above `ARM_MIN_SPEED`.** The 25 mph floor added on 2026-08-20 was
supposed to be the fix for exactly this and it prevented nothing.

**THE FLOOR WAS DERIVED FROM A COUNTING ERROR.** Its table filed arms at 32.9, 33.9 and 40.0 mph as
"tolerated" when all three had provoked a cancel -- they were counted as tolerated because the cancel
later RELEASED. Collapsing "the camera objected and recovered" into "the camera did not object"
is what manufactured the clean 20-25 mph gap the floor was placed in. The two real latches land
directly on those rows. **When a table's rows are the whole argument, check what got binned
together** -- a category that merges the outcome you care about with the one you do not cannot
separate them however clean the gap looks.

**What the cancel is NOT, each ruled out by measurement rather than reasoning:**

- **Not contradiction magnitude.** a9 SURVIVED with the largest deltas of the three (mean -1.27,
  max -2.06 m/s^2). a8 latched with a mean of -0.14, and for its first twelve seconds the override
  matched Ford's own `AccBrkTot_A_Rq` to within 0.01 m/s^2 -- openpilot and Ford were asking for the
  same braking, frame after frame, and the camera cancelled anyway.
- **Not arm speed**, per the table above.
- **Not the low-speed part of the stop.** On a8 the cancel landed at 29.7 mph, 1.6 s after arming,
  long before the car was near a standstill.
- **Not coincidence.** The camera was quiet for at least 4 s before each arm.
- **Not the lead check misfiring.** On a8 the override released to `fallback` at 13.6 mph when a
  VISION lead appeared at 42 m (`radar=False`, modelProb 0.53-0.90) -- but that was 11 s AFTER the
  cancel, so it is downstream of the failure, not its cause.

So the working figure is that the camera tolerates **about 1.5 seconds** of not driving, and a stop
needs five to eight. **Every override will therefore provoke a cancel. That half is inherent and
there is no arming rule that avoids it.**

**BUT THE PERMANENCE IS OURS, AND THAT IS THE HALF WORTH FIXING.** `passthrough_admissible` refuses
any frame carrying `AccCancl_B_Rq`, so from the cancel onward Ford's command never reaches the car
again -- which means the camera can never observe the car obeying it, which means it never gets a
reason to release. A self-sustaining refusal, the same shape as drive A's 70 re-engagement cycles
and pointing the other way.

The evidence that it is only the BIT that is stuck, not the camera: on route a8 he re-engaged at
t+886, and the camera was publishing a sane 46 kph `AccVeh_V_Trg` and ordinary brake values while
still asserting cancel. It was ready to drive and we were throwing all of it away.

**SO THE RECOVERY IS BUILT, 2026-08-22.** After 5 s of continuous cancel, IF the stop override ran
this drive (which is what makes the cancel ours rather than the camera's own judgement) and
openpilot is longActive, Ford's frame is forwarded again with `AccCancl_B_Rq` cleared --
`create_acc_msg_passthrough(..., clear_cancel=True)`, gated by
`passthrough_admissible(..., allow_cancel=True)`. Every other refusal still applies: `AccDeny_B_Rq`,
`CmbbDeny_B_Actl`, the park brake and both panda bands are untouched, because "I will not let ACC
run" is a different statement from "stop the ACC that is running".

Bounded at 30 s, because **whether the camera releases is exactly the unknown** and pretending
otherwise would make the drive unreadable. Two `cloudlog.error` lines are the readout: RECOVERY when
it starts, RECOVERY WORKED with the elapsed time if the camera drops its cancel. Mutation-tested --
both halves were broken on purpose and each failed exactly one test.

**This is the same correction `AccStopStat_B_Rq` needed**, and it is the third time on this list: a
bit went on the refusal list by association with drive A, and refusing it cost the feature more than
the bit ever did. Check what a blanket refusal costs before keeping it.

**DO NOT MOVE `ARM_MIN_SPEED` AGAIN.** That lever has been tried, on evidence that did not support
it, and the cost of the retry was two drives. The next thing that would move this is finding what
the camera actually counts -- consecutive frames, a response-to-command test, something in
`ACCDATA_3` -- not another band.

**DO NOT TELL HIM TO TURN THE OVERRIDE OFF.** That was the first recommendation out of this analysis
and it was wrong -- he had to say so: *"Why would I turn that off if that's what we are building
here?"* Coming to a complete stop IS the feature; switching it off is not a mitigation, it is
abandoning the thing. The cancel was a bug with an unread cause, and reading the cause took one
wire-level diff of our transmitted frame against the camera's. **Diff the wire before recommending a
retreat.**

**And one claim from the same analysis was withdrawn the same hour:** `cruiseState.standstill` going
true at the stop was read as Ford's own hold engaging for the first time on this car. It is not
evidence of that -- `ford/carstate.py:74` OR's `standstill` with actual wheel speed, so on a stopped
car it means "stopped", not "Ford is holding it". The stopped-and-engaged question is still open.

**Ships OFF, and the reason is about the car:** the camera's tolerance for sustained contradiction
is unmeasured. Toggle is `StockAccStopOverride`, "Come To A Complete Stop".

### 2026-08-23: IT COST HIM FORD ACC AGAIN, AND THE RECOVERY NEVER RAN

Four drives, `ae` `af` `b0` `b1`. `INERT` logged four times; **`RECOVERY` logged ZERO**. Route `b0`
he never engaged at all; `b1` was clean (Ford authored 99.7% of engaged frames).

    route  override   authority
    ae     368 fr     ford 22.6%  inert 22.3% (6012 fr, 60 s)  opStop 1.4%
    af     1776 fr    ford 28.7%  inert 23.2% (2738 fr, 27 s)  opStop 15.1%

**THREE OF THE FOUR RECOVERY GATES ARE RULED OUT BY MEASUREMENT**, which is worth keeping because
each cost a wrong theory first:

- **Attribution.** `tools/bp_cancel_attribution.py` times the last `opStop` frame against the first
  `inert` frame. Both drives: **4.99 s**, and `inert` is exactly 5 s of cancel — so the cancel run
  opened the frame the override handed back, `frames_since_override` was 1, and `cancel_is_ours`
  should be True.
- **`CC.longActive`.** `inert` is unreachable without it, by the authority chain's own ordering.
- **The panda bands.** `tools/bp_recovery_blocked.py` replays the real band rule over all 8,750
  camera frames of both inert windows: **0 refused**, old rule and new. The `AccBrkTot_A_Rq` theory
  below was mine, was wrong, and is retracted — the bug is real and it is not this.

**AND NOT ONE OF THE FOUR IS PUBLISHED OR LOGGED**, so the drive cannot say which declined. Third
time in one day, after the hold rule and the TSR baseline. `RECOVERY DECLINED` now logs all four
once per cancel run, at the frame recovery would have started. **That is the next drive's readout.**

**A REAL HOLE WAS FOUND WHILE DRIVING THE MECHANISM OFFLINE, and it is fixed but is NOT the
diagnosis.** Attribution is decided once, on the frame a cancel RUN opens. The counter is only
touched inside `if not override`, so a run already open when the override begins survives it: the
override ends, the counter is still non-zero, the `== 0` test never fires, and `cancel_is_ours`
keeps its pre-override value of False. Recovery is then blocked for the whole drive by a decision
made before the thing it is attributing. Now zeroed on the override edge.

**THE FIRST REPRODUCTION WAS INVALID AND READ AS A FINDING.** It held the cancel without ever
firing the override, so `frames_since_override` sat at its `1 << 30` sentinel, `cancel_is_ours` was
correctly False, and recovery correctly declined — which looked exactly like "recovery is broken".
Fixtures more orderly than reality, and this time the fixture was *missing the thing the rule is
about*. Both tests now fire the override first.

### THE CAMERA SAYS `ACC_Unavailable`, NOT `ACC_Overridden`. ASKED IT DIRECTLY, 2026-08-23.

`ACCDATA_3` carries the camera's own message text, and the DBC names every value. `tools/bp_cancel_reason.py`
decodes it from raw bus-2 CAN alongside `accAuthority` in the same pass:

    route   while the OVERRIDE had the car
    ae      AccMsgTxt ACC_Unavailable=10, No_Text=8   AccWarn Cancel_Warning=4
    af      AccMsgTxt ACC_Unavailable=19, No_Text=67  AccWarn Cancel_Warning=4
    b1      override never fired; IACC_Unavailable only, ZERO ACC_Unavailable, ZERO Cancel_Warning

**`ACC_Overridden` (value 4) appears ZERO times on any drive.** The camera does not believe a driver
is braking over it. It declares ACC **UNAVAILABLE** and raises `Cancel_Warning`, and it does so
while the override has the car.

**That is a different problem from the one this file has been working from.** "It watches the car
decelerate harder than it asked and gives up" predicts `ACC_Overridden`, which is a system waiting
to be handed back. `ACC_Unavailable` is a self-assessment that it cannot run at all — and nothing
about continuing to forward its own frames obviously argues it out of that. **The recovery design
rests on the camera having a reason to release. Its own message text does not say it has one.**

**AND THE RADAR IS FINE, so the obvious alternative is ruled out.** `CadsAlignIncplt_B_Actl` and
`CadsRadrBlck_B_Actl` are **0.0% on all three drives, 4,071 samples**. The B1433 alignment fault an
IPMA as-built write causes — and he was writing as-built on the 21st and 22nd — is NOT present.

**`AccStopStat_D_Dsply` is `NoDisplay` on 100% of every drive measured.** Ford's ACC never enters
its own stop-and-hold on this car, which is the fourth independent confirmation of that.

**AND CANCEL-AND-RE-ENGAGE IS ALREADY TESTED. IT DOES NOT WORK.** Proposed here as the obvious next
step and closed immediately by him: *"I manually try to cancel and reengage after it fails."* He has
been running that experiment by hand on every failure, which is why the fix has always been pulling
over and restarting the ignition. ASK BEFORE DESIGNING AROUND SOMETHING HE DRIVES EVERY DAY.

The log agrees, route `af`:

     75.1 .. 102.4   inert      the camera latched
    102.4 .. 104.4   stock      he disengages
    104.4 .. 107.8   FALLBACK   re-engaged -- Ford's frame STILL refused
    107.8 .. 109.8   stock      disengages again
    109.8 .. 111.4   FALLBACK   re-engaged again -- still refused
    111.4 .. 117.9   stock

`fallback` means openpilot was longActive with the camera's frame inadmissible, so both
re-engagements produced openpilot authoring and never `ford`. In exactly those frames the camera was
still saying **`ACC_Unavailable`, 24 of 24 samples**.

**So the state survives an ACC cycle and only a POWER cycle clears it.** A latched condition, not a
transient the camera is waiting to be talked out of. That is the strongest evidence yet that the
recovery's premise is wrong, and it rules out the whole family of "cycle ACC to clear it" fixes
before any of them are built.

**THE CONSEQUENCE, PLAINLY: every override risks Ford ACC for the REST OF THE DRIVE, unrecoverably.**
Not "a cancel we recover from". That moves the work from RECOVERY to PREVENTION -- the override has
to not provoke the state at all. The one prevention idea never tried is INTERLEAVING: hand Ford a
frame back every N frames during the stop, so whatever the camera counts never runs to completion.
It is testable, it directly separates "consecutive frames" from everything else, and it costs a
lumpier stop rather than a lost ACC.

**Still read `RECOVERY DECLINED` from a drive first.** It costs nothing and it is one drive away.

### THE OVERRIDE STOPS THE CAR OUTSIDE FORD'S OWN STOP PROTOCOL. LEADING HYPOTHESIS, 2026-08-23.

Found by asking what the PCM was doing rather than what the camera was thinking.

    route af, during opStop     AccStopMde_D_Rq   NoStop on ALL 888 frames
    route b1, under `ford`      AccStopMde_D_Rq   Hold on 498 frames

**`AccStopStat_B_Rq` is fed from `stopping`, and `stopping` is `longControlState == stopping`, which
this file already records as a STOPPED-CAR state that is never true above 3 mph.** So the override
brakes the car from 20 to 0 while telling the PCM no stop is in progress, for the entire approach.

**And b1 is the first time this fork has EVER seen Ford enter its own stop mode on this car** --
same car, same evening, override never fired, `Hold` for 498 frames. That retires the standing claim
that Ford never holds a stop here: the handshake works, when Ford is the one driving it.

**The camera receives `CcStat_D_Actl` and `AccStopMde_D_Rq` directly** -- `IPMA_ADAS` is a listed
receiver of both in the DBC. So it watched a car come to a standstill while its own powertrain
reported no stop was happening. `ACC_Unavailable` is a reasonable conclusion from that, and unlike
every other theory tried today **it explains why the state is LATCHED** rather than transient, and
why an ACC cycle does not clear it while a power cycle does.

Fixed by passing `lng.stopping or override`. The override only runs when we are deliberately
stopping, so asserting the bit is honest signalling rather than a trick -- it is the same bit Ford
asserts for the same reason, and panda does not police it.

**NOT PROVEN. One strong correlation, and the drive that tests it is the next stop the override
takes.**

**TWO MORE THEORIES DIED THE SAME HOUR, both by measurement:**

- **The camera does not see our frame.** `src 2` (camera, bus 2) and `src 128` (our bus-0 TX echo)
  are separate and equal on every authority -- 1691/1691 under `ford`, 888/888 under `opStop` -- and
  there is no second ACCDATA stream on bus 2. `safety_fwd_hook` would forward bus 0 to bus 2 and
  nothing in the TX list blocks it for that destination, so this was worth checking and it is not
  happening.
- **It is not contradiction magnitude.** During `opStop` on af the camera was commanding
  -2.12..0.40 m/s^2 and we were sending -2.12..0.31 -- **the same braking**. Second confirmation
  after route a8, where the two matched to within 0.01.

### THE THRESHOLD IS BETWEEN 1.1 AND 2.6 SECONDS. FIVE EPISODES, AND IT IS 4 FOR 4 FATAL.

Four ignition cycles in eleven minutes on 2026-08-23 make this the cleanest experiment in the file:

    21:14:36  ae   ford 60.8s -> opStop  3.67s -> inert 60.1s   DEAD, route ends
    21:19:51  af   ford 33.8s -> opStop 17.75s -> inert 27.4s   DEAD
                   then two re-engage attempts: fallback, fallback, never ford
    21:21:57  b0   never engaged
    21:25:41  b1   ford x8 windows over 403 s, NO override fired   PERFECT

**Ford ACC works from every fresh ignition, dies the first time the override runs, and cannot be
recovered without another ignition.** b1 is the control: 403 seconds, eight separate Ford windows,
no override, no trouble at all.

**EVERY OVERRIDE DURATION ON RECORD, against outcome:**

    1.1 s   (a9)   SURVIVED
    2.6 s   (aa)   latched
    3.67 s  (ae)   latched
    12.6 s  (a8)   latched
    17.75 s (af)   latched

**So the camera's tolerance is between 1.1 s and 2.6 s, and every override longer than that has
killed ACC -- four for four.** The 1.5 s figure this file has carried as an estimate is now bracketed
by measurement rather than inferred, and `ARM_MIN_SPEED` is confirmed irrelevant a second time: ae
armed at a duration, not a speed.

**THIS IS WHAT MAKES INTERLEAVING CONCRETE.** A stop needs 5-8 s and the camera tolerates under 2,
so a single continuous override can never work. Bursts of ~1.0 s with Ford's own frame handed back
between them stay inside the tolerance on each burst -- IF what the camera counts is consecutive
contradiction rather than cumulative. That is precisely the unknown, and interleaving is the
experiment that separates the two. The cost of being wrong is a lumpier stop; the cost of not trying
is that the feature is dead as built.

**Do not raise `ARM_MIN_SPEED` again, and do not shorten the override to 1 s and call it fixed** --
a 1 s override cannot stop the car, which is the entire point of the feature.

### ICBM SHOULD PREPARE THE SET SPEED WHILE e2e DRIVES, NOT MERELY STOP TOUCHING IT

His correction, 2026-08-23, and it is a better rule than the one that shipped that morning:

  *"when e2e is being used ICBM shouldn't be trying to change its speed, but preparing for if we
  recover Ford ACC once we are done with e2e"*

The suppression built earlier that day FREEZES the set speed wherever it happened to be. On a stop
approach that is wherever ICBM had walked it to -- often the 20 mph floor. If Ford's authority ever
returns, it picks up from that, which is the lurch the floor-release rule already exists to avoid.

**Freeze is not prepare.** The prepared value is the DRIVER'S aim -- the hold if there is one, else
SLA's target -- never the transient curve or stop target e2e is already executing. That is the same
idea as "the set speed is prepared while stopped" in the stop-override section, generalised from
the standstill case to every window where openpilot is authoring.

**The plumbing is the obstacle, not the idea.** `acc_authority` is decided in the carcontroller and
ICBM runs in selfdrived, so the controller does not currently know that e2e has the car. Both values
it would aim at are already published (`vBaseline`, and `vSlaTarget` as of this day).

### `AccBrkTot_A_Rq` HAS THE SAME CEILING AS `AccPrpl_A_Rq` AND WAS NEVER CLAMPED

Straight off his swaglog, every line a refused frame handed to openpilot:

    passthrough: AccBrkTot_A_Rq 1.996 / 2.019 / 2.043 / 2.066 / 2.090 / 2.105 / 2.125 / 2.140

`_PANDA_ACCEL_MAX` is 1.9999, so with the margin anything from 1.995 up was thrown away — and
despite the name this field is Ford's TOTAL acceleration request, **positive while accelerating**.
Ford sits on that ceiling pulling away exactly as it sits on the gas ceiling.

**This is the 2026-08-19 launch bug on the other field of the same message, and it sat for four
days.** The note saying `AccBrkTot_A_Rq` is "carried verbatim, where a silent softening would be
indistinguishable from working until it mattered" was written about the BRAKING side and is still
right about it — it was read as covering the whole field. Fixed by the same asymmetry: the top is
clamped (asking for LESS acceleration is conservative), the bottom stays a refusal.

**The lesson: when a field is fixed by clamping one end, check its NEIGHBOURS in the same message
for the same shape.** Three fields here have now needed three different answers, and the fourth was
found only because he reported the symptom a second time.

### ICBM MUST STAND DOWN WHILE OPENPILOT IS AUTHORING. HIS DIAGNOSIS, AND IT MEASURES OUT.

*"Technically, when OP long is being used, ICBM doesn't, right? Or at least when OP long is driving,
we shouldn't affect its speed with ICBM?"* Route `ae`, by authority state:

    authority   frames   ICBM pressing   dash travel   buttons
    ford          6076    41    0.7%         14.0 mph  increase=41
    inert         6012   378    6.3%         84.0 mph  decrease=210, increase=168

**378 button frames and 84 mph of dash travel, hunting, while a latched camera meant openpilot
authored every ACCDATA frame.** The set speed is not in that control loop at all, so ICBM was moving
a number that governed nothing — in full view on the dash. That is his "the speed went up and down".

**`_op_long_drives()` structurally cannot catch it.** It decides whether ICBM runs ONCE, at car
init, and under the passthrough it correctly answers "Ford drives, so ICBM stays". **Authority is a
per-frame fact and it changed hours into the drive.** Anything else keyed on that init-time decision
has the same blind spot — check for others before trusting one.

Suppressed for `inert` and `openpilot` only. `fallback` is a scattered per-frame refusal Ford
resumes from next frame, and suppressing there would stutter the buttons every time a band clipped;
`opStop` is left alone because the override raising the set speed while stopped is deliberate.

### THE HOLD STUCK FOR 87 SECONDS WITH EVERY CONDITION SATISFIED. **SOLVED: A BUTTON TIMER.**

Route `ae`, `tools/bp_hold_clear_audit.py`, which reconstructs the rule's own inputs:

    t+155.7  hold 27  sla 27  live True  source press   *** SHOULD HAVE CLEARED ***
    t+158.4  hold 27  sla 27  live True  source press   *** SHOULD HAVE CLEARED ***
    t+245.6  hold  0                                    CLEARED

All four of the rule's own conditions pass and it does not fire for 87 s -- with cruise ENABLED and
`vTargetRaw` 27, so neither `cruise_enabled` nor `v_target_valid` is the block either.

**THE CAUSE IS `update_manual_button_timers` IN `cruise_ext.py`.** It only zeroes a timer on a
RELEASE event. This car's SCCM clears the button bit between frames, so one physical press arrives
as a burst of PRESS events -- and the release is not reliably among them:

    route 000003ae, t+140..260:  88 events pressed=True, ONE pressed=False
    last press t+154.48          next event of any kind t+269.62

So the `accelCruise` timer sat above zero for 115 s. ICBM re-arms its press-settle stand-down every
frame any manual-override timer is non-zero, so `update_manual_override` returned before the
clearing rule on every one of those frames. **The rule was never reached, which is why three rounds
of fixing the rule did not help.**

Fixed with a 2 s cap. The timer is ALREADY "frames since the last press event" -- every press resets
it to 1 -- so a cap cannot cut a real press short: while a button is genuinely held the bursts keep
arriving and keep resetting it. Longest gap WITHIN a held press on that route was 0.72 s.

**Deliberately NOT a change to the press-settle re-arm**, which is correct and was itself added for
a real report -- a cap expiring mid-hold froze the baseline and walked the set speed back down while
the button was still held. The defect is the INPUT it trusts.

**AND THE 2026-08-22 TEST PASSED BECAUSE IT SENT A RELEASE.** It holds the button on every frame,
which was the fix for the round before -- then ends with `DECEL_RELEASE`, zeroing the timer. It
exercised everything except what his stalk actually does. **When a fixture ends with a tidy-up event
the real hardware may not send, that ending is the next bug.**

**AND THE AUDIT'S FIRST VERSION HID THE ZERO** — it skipped `baseline <= 0`, so it could not tell
"stuck" from "cleared one frame later", the single question it existed to answer. Written by the
session that keeps quoting the rule against exactly that.

### PUBLISHING A DIAGNOSTIC IS NOT A ONE-TIME ACT

The capnp comment above `vTargetRaw` says those fields exist because "neither was published, and
that made a real on-road report undiagnosable from a route". On 2026-08-22 the rule stopped
comparing them and started comparing SLA's own number gated on the limit being live — **and neither
of those was published either.** So the same report, from the same driver, was undiagnosable again,
one struct field away from where the lesson is written.

**A diagnostic is a property of the RULE, not of the module. When a comparison is rewritten,
re-check that its new terms reach the wire.** `vSlaTarget` and `speedLimitLive` now do.

### SHELL QUOTING BIT TWICE MORE

Both cost a wrong result rather than an error, which is why they are here beside the heredoc rule:

- **Backticks inside a double-quoted shell string are command substitution.** A commit message lost
  two phrases that way; bash printed `inert: command not found` and the commit went through anyway.
  Use `git commit -F -` with a single-quoted heredoc delimiter.
- **Nested single quotes in `ssh "bash -lc '...'"`** silently truncate the remote command. Put the
  loop in a `$cmd` here-string with no inner single quotes, or write a script file and run that.

**AND IT REQUIRED FIXING THE 20 MPH FLOOR RELEASE, which he had reported from the road as its own
complaint:** *"occasionally the traffic light thing will set my speed back up after it has gotten
down to 20."* Not occasional. `unconfirmed_lead.py` released the model stop at `ACC_FLOOR_MS`, and
`_release()` goes to `restoring` whenever a restore point was captured -- so it happened every time
the car crossed 20 with a model stop running.

**That was CORRECT before the override existed.** Below the floor the set speed genuinely could not
ask for anything, so the request was spent and handing it back was right. With the override it is
actively wrong: the set speed climbs back while openpilot brakes, and the moment the time bound
expires Ford accelerates away from the stop line. Now gated on `stop_override_available`, which
requires BOTH the passthrough and the override -- with either off, the old behaviour is exactly
unchanged.

**THE HUD SAYS WHO IS DRIVING, because the ACC pill was about to lie.** It reads the CAMERA's
ACCDATA, so during an override it would have shown COAST -- Ford's actual wish -- while openpilot
braked the car to a stop. Same shape as the gap-display bug, and he had already confirmed he sees
COAST on the road. `OP STOP` in violet, from `carOutput.actuatorsOutput.accel` disagreeing with
`brakeLightStatus.accAccelRequest`: what we PUT ON THE WIRE versus what Ford asked for, both already
subscribed, no new signal. Violet rather than another red, because it is a different AUTHOR and not
a different amount of braking. Rendered before shipping, two new scenes in `preview_acc_status.py`.

**RESUMING FROM A STOP WE AUTHORED IS HELD FOR THE DRIVER.** He asked how resume works and the
chain turned out to end somewhere worth stopping:

    controlsd.py:175   CC.cruiseControl.resume = enabled and CS.cruiseState.standstill
                                                 and not longitudinalPlan.shouldStop

`standstill` is Ford's own hold, `EngBrakeData.AccStopMde_D_Rq == 3`. So once the model judges the
intersection clear, openpilot presses RESUME and **the car pulls away from a stop sign with no
driver input**. That is upstream behaviour, not new -- but the override is what makes it REACHABLE
on this car, because a standstill with cruise engaged has never existed here: three drives checked,
**zero stopped-and-engaged frames**, since stock ACC cannot hold a stop without a lead.

"Come to a complete stop" did not ask for "and then go when the model feels like it". So
`resume_allowed` now holds a stop the OVERRIDE authored until he presses resume or the gas -- his own
press never reaches this gate, so it is untouched. The no-lead branch used to mean only "the queue
cleared, nothing to wait for"; it now has a second meaning and they needed separating.

**AND IT IS HIS CHOICE NOW, not ours: `StockAccStopAutoResume`, added 2026-08-22 because he asked
for it by name after having the behaviour explained.** Ships OFF at his request, and the reason
belongs in the "reason about the car" category rather than caution about the code: the "go" signal
is `shouldStop` going false, which means the MODEL stopped wanting to stop, **not that the light
turned green.** openpilot does not read signal state at all. Turning it on is choosing to let the
car decide an intersection is clear on the cheapest evidence there is, which is the exact thing the
evidence rule elsewhere in this file forbids for a lane change -- so it is a toggle, with the reason
written where he reads it, rather than a default either way.

**Scope, and it is narrower than the title suggests:** only stops the OVERRIDE authored. Behind a
lead Ford is holding the stop, `stop_override_stopped_us` was never set, and the ordinary
queue-cleared resume is unchanged with the toggle in either position. Both flags are read through
`getattr(..., False)`, and for the new one the safe fallback is the one that keeps the car STOPPED
-- the opposite of this fork's usual "a missing attribute should not disable a feature" instinct,
because here the feature being missing is what keeps the car where the driver last saw it.

The UI gates it on the override being on, one level below the override's own gate on the
passthrough. Ungated it would read as a general "pull away from stops" setting that does nothing.

**AND THE SET SPEED IS PREPARED WHILE STOPPED, which is his spec:** *"Ideally while stopped at a
stop sign or traffic light, the set speed is restored from 20mph, and when it is time to go it
goes."* Without it the restore waited for `model_slow_down` to clear -- which at a red light is the
moment it turns GREEN. The set speed would only start climbing when he wanted to move, and Ford
would pull away toward 20 while ICBM spent seven seconds pressing it back to 45.

Gated on `v_ego < 0.5 mph AND cruiseState.standstill`, and **`standstill` is the load-bearing
half**: it is Ford's own hold, so a held car waits for resume whatever number it is aiming at.
Stopped WITHOUT the hold means Ford is free to go, and raising the set speed there is exactly the
lurch the floor release existed to avoid.

**Which branch he actually gets is unknown until a drive**: if Ford does not enter its hold mode
without a lead, `standstill` stays false, resume never fires at all, and he re-engages by hand.

**AND HIS OWN EXPERIENCE NARROWS IT, 2026-08-18:** *"when I use OP long fully, it does come to a
complete stop."* That is worth more than the log measurement it corrects. openpilot's authored
ACCDATA CAN stop this car -- the ABS accepts the brake command to zero -- and the override sends
exactly that frame, `create_acc_msg`, for a bounded window instead of a whole drive. **So the
stopping mechanism is proven and only the handoff back to Ford is new.** If it also HOLDS there,
`AccStopMde_D_Rq` is reachable and the standstill branch is the live one.

**The "zero stopped-and-engaged frames on this car" measurement was true and did not mean what it
was used for.** Two of the three routes had the passthrough forwarding Ford's command 91% of the
time, and Ford will not stop without a lead -- so the state was absent because nothing tried to
create it, not because it is unreachable. Absence in a log is evidence about the log's conditions
first. Same shape as the 70.6% denominator error two entries above.

**What is STILL genuinely unknown is narrower and it is the right one:** under full op long there is
no forwarding, so there is no contradiction and the camera has nothing to disagree with. The
override contradicts by construction. That remains the only thing the bound exists for.

**And his own reason for the whole architecture, restated because it is the sharpest one:** *"the
one other thing that always makes me prefer Ford ACC+ICBM over OP long is that it can COAST."* That
is why the override is scoped to below 20 mph only -- it takes the part where coasting was never
available, and leaves every mph above it to the blend Ford picks.

**The structural guard against the documented trap:** `test_it_never_reads_fords_command` parses the
module with `ast` and fails if Ford's signals, `acc_stock_values` or `passthrough_admissible` are
referenced in CODE. Parsed rather than grepped because every explanation of the trap contains the
words -- the same lesson `test_mapd_schema.py` records for `suggestedSpeed`.

**AND THAT DECIDES THE SHAPE OF THE OVERRIDE, WHICH IS THE NEXT THING ANYONE WILL BUILD.**

  THE TRAP: `min(ford_accel, openpilot_accel)` -- "use whichever brakes harder". One line, handles
  stops and ramps and everything else automatically, and it is WRONG. openpilot's planner is more
  conservative than Ford's most of the time, so it would win constantly and the passthrough becomes
  op long again, arriving through a comparison operator. Every reason this idea exists is undone.

  THE RULE: the override is a NAMED, BOUNDED CONDITION, never a comparison. "A stop line ahead and
  within N seconds of needing to brake" fires explicitly, for a few seconds, and falls back to
  Ford's number the moment it is done. Same discipline as the gap lease -- assert while needed,
  silence restores.

**THE DIVISION OF LABOUR, settled 2026-08-17 across several of his corrections. Do not re-litigate
it; DO settle the boundary with drive data.**

  FORD DECIDES **HOW** TO SLOW. WE DECIDE **WHETHER** AND **BY HOW MUCH**.

Three independent arguments converge on it, and the third is his and is the one that would be
easiest to forget:

1. **PERCEPTION.** Ford's radar sees leads and it has years of calibration on following and
   stop-and-go. It has no idea about a mapped corner, a stop sign, a red light, or a car its radar
   has not acquired. Those are ours.
2. **RATE.** The set speed falls at 3.3 mph/s and stops at 20 mph. Anything needing more than that
   cannot be expressed through buttons at all.
3. **ACTUATION VOCABULARY -- his point, and the sharpest.** *"Coasting is a thing Ford ACC can
   do."* Ford chooses between coasting, engine braking, precharge and friction brakes, and the DROP
   LIMITER exists precisely to exploit that -- "stock ACC brakes for one large drop and coasts
   through a series of small ones, so smaller steps trade braking for coasting at the same net
   deceleration." **The set speed is a request for an OUTCOME and Ford picks the means. ACCDATA is a
   command of the MEANS.** Overriding directly means choosing brake-versus-coast ourselves, every
   frame.

**And point 3 has a cost he already measures.** A direct override reaches for friction brakes and
lights the stop lamps where a staged set-speed drop would have coasted there silently. The brake-lamp
readout exists for that, `IcbmMaxTargetDrop` is tuned against it ("lower this if the lamps come on
during routine slowing"), and the preview scenes name the states an override would flatten: COAST,
ENG BRAKE, PRE-BRAKE, BRAKE.

**So the override takes over ONLY where coasting could never have reached anyway:** below 20 mph,
where the set speed cannot ask; and on ramps steep enough to need real braking regardless. Everywhere
else the set speed is STRICTLY BETTER, because Ford's answer to "be doing 45 shortly" is a blend we
do not have to write and could not easily match.

### BOTH "NEEDS A DRIVE" ITEMS WERE ANSWERABLE FROM LOGS ALREADY ON THE DEVICE

2026-08-18. Two things were reported to him as unverifiable without another drive, and both were
settled in minutes by replaying against route 0000038b. **Replaying a real class against a real log
is the tool here; reach for it before saying something needs a drive.**

**1. SLA DOES REACH `active` WITH THE FIXES. Confirmed, not hoped.** Built the real
`SpeedLimitAssist` with his real CarParams, set `pcmCruiseSpeed = False` (what the ICBM fix
produces), and fed it the recorded `longitudinalPlanSP.speedLimit.resolver` frames:

    pcm_op_long        False   (was True -- so no PCM ceiling, no "set your speed to 70")
    states             disabled 4213, ACTIVE 994
    publishing a real speed-limit target   994 of 5207 frames

On the drive as it actually ran, `active` was reached **zero** times. So both changes work and they
work together: `cluster_converging` gets it out of `disabled`, and `pcm_op_long` makes the target the
real limit instead of the ceiling.

**2. THE LANE-CHANGE LEAD THEORY WAS WRONG.** The review flagged that the stop override's lead check
reads `radarState.leadOne.dRel` with no in-path test, and his "red light with no cars, but I changed
lanes right before it" fit perfectly. Measured, and it does not:

    lead inside 60 m while slowing below 20 mph   923 frames  (373 with a blinker on)
    lateral offset yRel   min -2.7   p50 -0.1   max 3.7 m
    |yRel| > 1.8 m -- not our lane                44 of 923

**p50 of -0.1 m is dead centre.** The radar was tracking a real car in his own lane, not one in the
lane he left. An in-path filter would change 4.8% of those frames and would not have opened that
stop. **Do not build the yRel gate on this evidence** -- the override refused because a lead was
genuinely there, which is what it is supposed to do.

**THERE ARE FIVE GATES. The FIFTH is the one that actually cost him two drives, and it is not
about op long at all -- it fires when CarParamsSP has simply not been READ yet.**

`ui_state._enforce_constraints`'s `else` branch -- reached whenever `self.CP_SP is None` -- called
`params.remove("IntelligentCruiseButtonManagement")` unconditionally. That is every UI start, before
a car has been seen. So the UI DELETED the setting on essentially every boot, and `card` then read it
as False at car init.

**One flag, both of his 2026-08-18 complaints.** With the param false,
`_initialize_intelligent_cruise_button_management` never clears `pcmCruiseSpeed`, so:

  - `v_cruise` stops being openpilot's and MIRRORS the dash (`cruise.py`'s else branch). MAX and the
    ICBM number become the SAME NUMBER -- "it's still having me change the ICBM speed instead".
    There is no separate max speed to move, and no hold can exist.
  - `pcm_op_long = openpilotLongitudinalControl and pcmCruise` goes TRUE, so Speed Limit Assist runs
    `update_state_machine_pcm_op_long`, which requires the set speed to sit at
    `PCM_LONG_REQUIRED_MAX_SET_SPEED`. **That is the "set your speed to 70 for it to work"** -- a
    protocol for cars with no button injection, reached because this car was reporting it had none.

Measured on the device 2026-08-18: the param file read `1` early in the session and was simply GONE
afterwards, with `icbm_enabled=False` while every other condition held.

**"Not known yet" is not "not supported", and removing a PERSISTENT param is not a way to say it.**
Report unavailable for display; never destroy the stored setting on missing evidence.

**AND THE THIRD GATE IS THE SETTINGS SCREEN.** Found 2026-08-18 from
"ICBM was grayed out" -- after both `interfaces.py` gates were already fixed. `cruise.py`'s
`_update_state` does not merely disable the toggle under op long, it calls
`params.remove("IntelligentCruiseButtonManagement")` **on every render of the page**. So opening
settings deleted the setting. That is why re-enabling ICBM mid-drive never stuck and why the device
kept reading `unset`. Now gated on the same `op_long_drives` condition.

**The lesson: when a param is being deleted, grep for every `remove()` of it before concluding you
have found the one.** Two of the three were in the file that decides whether the feature RUNS; the
third was in the file that decides whether he can SEE it, and only the screen could report it.

**DONE, 2026-08-18: `_op_long_drives()` in `sunnypilot/selfdrive/car/interfaces.py`.** Both gates now
ask whether op long DRIVES the car rather than whether it is merely on -- with `StockAccPassthrough`
set, Ford is still authoring the command, so ICBM stays. It also fixes something that bit on drive A
independently of the passthrough: the second gate does not ignore the param, it REMOVES it, and the
key has no default, so it returns as OFF. He re-enabled ICBM mid-drive, the gate deleted it again,
and the device still read `unset` afterwards.

**And `interfaces.py` is now testable offline for the first time**, which is why this could be
checked at all. Its module-level `sunnylink.statsd` import wants pyzmq, then `hardware.hw.Paths`,
then `system.version` -- each stub revealing the next. **Stub the MODULE, not its chain**: the file
uses one name from it. Same for `system.sentry`, and note that `import a.b.c as x` binds through the
parent, so a `sys.modules` entry for the leaf alone is not enough -- `openpilot.system` has to exist
and carry the attribute.

**AND ICBM KEEPS WORKING UNDER THE PASSTHROUGH -- the earlier claim that it does not was wrong.**
Both gates in `sunnypilot/selfdrive/car/interfaces.py` key on `CP.openpilotLongitudinalControl` and
encode one assumption: op long is on, therefore openpilot drives, therefore the buttons are
meaningless. **Under the passthrough that is false** -- Ford is still computing, the set speed still
governs, and panda permits button injection in long mode (`Steering_Data_FD1` is in
`FORD_COMMON_TX_MSGS`, which `FORD_LONG_TX_MSGS` inherits; verified). So holds, SLA, both curve
controllers, pinned holds and the gap button all survive. They die today only because of two `if`
statements written for a different mode, and teaching those gates about a third state is the work.

**His other correction, and it is what makes any of this safe:** *"holds shouldn't even be a part of
ICBM, they are a part of SLA."* Correct, and already recorded above as a known misnaming. It matters
more here than as naming hygiene: **a hold is a statement about what speed he wants against a posted
limit, and has nothing to do with how that speed is achieved** -- so it is actuator-independent by
construction and survives any migration. Same for SLA and the curve controllers. These were never
ICBM features; ICBM was merely the only actuator available.

**The cheap first step is a pure passthrough that overrides NOTHING.** If the car drives identically
to stock, the hard half is proven and the stop is a small addition. If the camera faults merely from
being forwarded, it is dead and one drive found out.

### A HEREDOC EATS `
`, AND IT HAS NOW SHIPPED A BROKEN PUSH

Three times on 2026-08-16/17, and the third one went out with a RED SUITE because the push was
chained after the test command with `&&` and the failure scrolled past. Writing Python through a
`<<'PY'` heredoc in this environment, an escape inside a triple-quoted string arrives as a LITERAL
NEWLINE, producing an unterminated string literal. It hit `test_mapd_schema.py`, `bp_mapd_compare.py`
and `cruise.py`.

**Avoid the escape entirely** -- `print()` for a blank line, `", ".join(...)` instead of a joined
newline, or a single spaced sentence. If a newline is genuinely required, use the Edit tool rather
than a heredoc.

**AND THERE IS A SECOND, SILENT FAILURE MODE THAT IS WORSE THAN THE CRASH. 2026-08-19.** The
documented case produces an unterminated string literal, which at least stops. But when the mangled
string is a **`str.replace` ANCHOR**, nothing raises: the pattern simply does not match, `replace`
returns the input unchanged, and the edit is silently dropped. That night a two-part patch to
`bp_stop_override.py` landed its COUNTERS and dropped its PRINT block, because only the second
anchor contained an escape. Ruff passed, `ast.parse` passed, the file was valid Python -- and the
funnel was computed every frame and rendered nowhere.

**That is this fork's oldest bug, for the FOURTH time**: a value computed correctly and never
displayed. It was caught only by reading the tool's actual output and noticing a section header
missing. So: **a multi-part `replace` patch must verify its own application** -- `grep -c` for a
phrase from EACH part, not just one, since one hit reads as success while half the patch is gone.

**AND READ THE SUITE RESULT BEFORE THE PUSH LANDS, NOT AFTER.** `test && commit && push` prints the
failure and then pushes anyway if the chain is written so the push does not depend on it. On a branch
every other branch rebases onto, that is the worst possible place to be sloppy. The rule was already
written down and it was still broken.

## Params, defaults, and his settings

**Settings behave EXACTLY as they do on stock BluePilot, sunnypilot and openpilot.** Decided
2026-08-08: *"I know before that I wanted them to change or something, but that caused issues, so I
want to go back to how upstream does it."* There is no defaults migration on this fork any more.

So the rule is upstream's, and it is simple: `system/manager/manager.py` writes every unset param to
its default on the first boot that knows about the key, and after that **the stored value never
changes**. Not on update, not ever.

**The consequence that governs everything else here: CHANGING A SHIPPED DEFAULT DOES NOT REACH HIS
CAR.** It reaches a fresh flash and nothing else. A changed default is therefore a RECOMMENDATION,
not a change, and the only channel that actually carries it is the settings description --
`selfdrive/ui/bp/settings_defaults.py`'s `recommended()`, which reads `get_default_value()` at
display time and cannot go stale.

So when a road report says a number should move:

- Change the default, and **say so plainly in the message**, because that is how he learns to go
  change it himself. He does this deliberately: *"I will go through each setting, check the
  description for recommended value, and set my value to it."*
- Never claim a default change fixed anything on his car. It did not, until he sets it.
- Do not build machinery to push it. That machinery existed, produced two real bugs in one evening,
  and was 272 lines in a sunnypilot file. It is gone and is not coming back.

What remains in `params_migration.py` is upstream-shaped: a rename migration that carries a value
across a key rename, which preserves settings rather than overriding them. Renames are fine.
Anything that decides what value he *should* have is not.

**Every feature this fork builds ships ON.** Stated 2026-08-08: *"the recommendation for all
features should be on."* A feature defaulting off is a recommendation to not use it, and it is also
how one goes untested for weeks -- `IcbmModelStopEnabled` was unreachable from the UI, then shipped
off, and he twice asked why stop-sign slowing never happened.

This is about FEATURES, not preferences. Upstream's display toggles -- `ShowTurnSignals`,
`StandstillTimer`, `RainbowMode` and that block -- are his to set and are left alone: he has already
chosen the ones he wants, so moving their defaults buys his car nothing and modifies upstream lines
forever.

If something genuinely should not be on, the reason goes in the comment beside the key, and it needs
to be a reason about the car rather than caution about the code.

**Every param ships with a control in the same commit.** A feature that cannot be turned on has not
shipped -- `IcbmModelStopEnabled` was unreachable without SSH and he reported the feature as broken
when it was merely unenableable. Cruise/ICBM controls live in
`selfdrive/ui/sunnypilot/layouts/settings/cruise.py`: define the item in `_initialize_items()` AND
register it in the returned `items` list, because a defined-but-unregistered item silently never
renders. `selfdrive/ui/bp/settings_defaults.py`'s `recommended()` puts the shipped default into the
description automatically. Put *why it defaults off* there too, since that is what he reads when
deciding whether to try it.

**A key declared `JSON` encodes itself.** `PYTHON_2_CPP` has `(dict, JSON)` and `(list, JSON)` and no
`(str, JSON)`, so `put(key, json.dumps(x))` is a `TypeError`, and `get` returns the DECODED object so
`json.loads` on it fails too. Pass the list or dict directly. This shipped pinned holds completely
dead -- enabled by default, storing nothing, for its entire life -- because both directions were
broken and therefore agreed with each other.

## COMMA 4: EVERY SETTING MUST BE REACHABLE FROM SUNNYLINK

Asked for 2026-08-12: *"We need Comma 4 compatibility. We need the menus and all settings available
in SunnyLink for changing."* And the reason, in his words: *"SunnyLink is useful for Comma 4 users
since they have a tiny screen to deal with."*

The comma 4 is `mici` in the hardware layer (`HARDWARE.get_device_type()`), alongside `tici` (comma
three) and `tizi` (3X). Upstream already carries `mici_only` and `hide_on_mici` macros, so the
concept exists -- what was missing was this fork's own settings.

**The state when this started: 6 of 32 fork settings were reachable from SunnyLink.** The other 26 --
every ICBM control, both curve-factor pairs, nine speed-limit controls -- could only be changed by
standing at the car. That is the on-device rule ("every param ships with a control") failing in a new
way: the control exists, but not on a surface a comma 4 owner can practically use.

**settings_ui.json is GENERATED. Never hand-edit it.** Same shape as README.md:

```
sunnypilot/sunnylink/settings_ui_src/pages/*.yaml     author here
sunnypilot/sunnylink/settings_ui_src/_macros.yaml     shared rule fragments, $ref them
python sunnypilot/sunnylink/tools/compile_settings_ui.py     rewrites settings_ui.json
```

**The workflow for any new setting, and the one the other branches must follow:**

```bash
python tools/bp_sunnylink_settings_audit.py     # what is missing, with YAML to paste
# place each item in the right page section, then:
python sunnypilot/sunnylink/tools/compile_settings_ui.py
python tools/bp_offline_test.py
```

`test_sunnylink_settings_complete.py` fails when a fork setting has no SunnyLink entry, and names it.

**Upstream hand-edits `settings_ui.json` and does not know `settings_ui_src/` exists** -- the
generator is ours. So every upstream commit that touches settings arrives as a JSON-only change,
git auto-merges it cleanly, and the *next* run of `compile_settings_ui.py` silently deletes it,
because the YAML source never learned about it. Nothing catches this: the merge is conflict-free
and the suite is green either way.

**After any merge that touches `settings_ui.json`, regenerate and diff.** A non-empty diff is
upstream's new entries about to be destroyed, not drift to accept:

```bash
python sunnypilot/sunnylink/tools/compile_settings_ui.py
git diff -- sunnypilot/sunnylink/settings_ui.json     # MUST be empty
```

If it is not empty, `git checkout --` the JSON, port their entries into the right
`settings_ui_src/pages/*.yaml`, regenerate, and confirm the diff is now empty -- that empty diff is
the proof the port was faithful. This is how the bp-7.0 merge (2026-08-24) kept the unified theme
selector: upstream replaced the `BPRadRacerTheme` toggle with `BPThemePack` / `BPThemeAutoSeasonal`
in the JSON alone, and regeneration would have reverted the whole feature.
It was verified to fail with an item removed -- do not trust it on green alone, that mistake has been
made here before.

**Things learned doing this that will otherwise be re-learned:**

- **`option` IS the numeric widget.** It takes `min`/`max`/`step`. The widget enum reads
  `toggle | option | multiple_button | button | info` with nothing obviously numeric, which invites
  the wrong conclusion that ranges cannot be expressed.
- **`multiple_button` options are `{value, label}` objects, not bare strings**, and the button's
  index is its stored value.
- **An omitted `value_change_step` means 1**, from `option_item_sp`'s own signature. Emitting `None`
  produces a control that validates as a string and cannot be moved.
- **Upstream deliberately shows some params on more than one page.** A duplicate check that does not
  scope itself to this fork's own keys fails on Mads and a dozen others, and policing upstream's
  layout is not this fork's business.
- **The compiled JSON validates against `settings_ui.schema.json`** with `jsonschema`. That catches
  the two mistakes above; the on-device `validate_settings_ui.py` needs `msgq` and cannot run here.

**This is per-branch work.** Passing assist and the radar detector each add their own params, so each
must rebase and run the same loop. The audit only sees what is defined in the branch it runs in.

## COMMA 4 ON-ROAD UI: PORT IT, IMPORTANT THINGS FIRST

**Reversed 2026-08-13.** The earlier instruction here was not to port any UI to the comma 4 -- *"I
don't think we even want to try to display stuff"* -- and that is no longer what he wants: *"Let's
also see how much of the on-road UI we can port to the Comma 4. Try to see what you can include.
Start with the important stuff."* Settings still live in SunnyLink; this is about the ONROAD screen.

**What is there today.** `MiciHudRendererBP` extends mici's own `HudRenderer` -- NOT our
`HudRendererBP` -- and its `_render` draws three things: the torque bar, the set speed, and the
steering wheel (with brake colouring, powerflow gauge and the lateral-control overlay). None of the
ACC status stack is on it.

**What the big screen has that mici does not**, in `_draw_acc_status`'s stacking order, which is
already a priority order:

  1. ~~**HOLD badge**~~ -- **DELETED FROM THE BIG SCREEN 2026-08-22.** mici still draws its own, and
     that is now the DIVERGENCE rather than the shared thing this list assumed. The big screen puts
     the hold in the set-speed box (`max_box_state`); mici's `_draw_set_speed` is mici's own and has
     no such concept, so its badge is the only hold readout it has. **Porting the box rule to mici is
     what would let its badge go** -- that is the work, not copying a badge that no longer exists.
  2. **ACC pill** -- what stock ACC is asking for.
  3. **Brake lamp pill** -- drawn in BOTH states deliberately; an indicator that only appears when lit
     cannot be told from one that is broken.
  4. **TSR pill** -- the camera's reason for having no speed limit.

**The plumbing is already portable, which is the part that makes this cheap.** All the ICBM state
(`_icbm_baseline`, `_icbm_arrow`, `_icbm_pinned`, `_icbm_hold_locked`) is gathered in
`selfdrive/ui/bp/onroad/hud_renderer_bp.py` by reading `selfdriveStateSP` directly -- it is our code
reading a message, not something inherited from the big-screen base class. Extract it into a shared
module and both renderers can consume it. Do that BEFORE writing any mici drawing code; duplicating
the reader is how the two screens start disagreeing about the same hold.

**Do not guess at layout.** `selfdrive/ui/bp/onroad/tools/preview_acc_status.py` renders the shipped
drawing methods to PNG at device scale, and he confirmed after driving that the car looks exactly
like the preview. It is big-screen only today -- **give it a mici scene set as part of this work**,
because the small screen is where a layout mistake is most likely and least visible offline.
Geometry cannot be carried over: the big-screen badge is sized against the MAX box and stacks four
elements under it, which will not fit.

## SUNNYLINK: REACHABLE IS NOT THE SAME AS USABLE

Four bugs in one feature on 2026-08-13, every one found from a screenshot the owner sent, and every
one passing the audit, the schema validation and the full suite the whole time. The checks verify
that the JSON is well-formed and complete. **None of them verifies that a human can reach the
setting**, which is the only thing that matters.

1. **A bare list of rules is an AND.** `{$ref: longitudinal_and_icbm}` requires BOTH capabilities --
   the macro name says so. Upstream wraps the same two in `type: any` to get OR. Using the raw macro
   greyed out all 26 added settings on a car with ICBM and stock ACC, which is his car.
2. **Section-level `option` items do not render.** Every option slider upstream ships lives in a
   `sub_panel`; section-level items are all toggles. Speed limits appeared and SCC/ICBM did not, and
   that was the only difference between them.
3. **SunnyLink must never gate more tightly than the device.** `cruise.py` gates ICBM tunables on
   `has_icbm` ALONE and does not capability-gate SCC or speed limits at all. Anything stricter means
   a setting he can change on the bench reads as UNAVAILABLE remotely. **The device is the spec.**
4. **Fork additions include new CHOICES, not just new params.** `SpeedLimitOffsetType` has four
   buttons on the device -- None / Fixed / % / By Limit -- and SunnyLink had three. A car set to the
   fourth matched nothing and the control rendered BLANK. The audit could not see it twice over: it
   compared presence only, and the device passes `buttons=SPEED_LIMIT_OFFSET_TYPE_BUTTONS`, a NAME,
   which the AST extractor returned None for.

Both audit gaps are fixed -- `_literal` resolves names against module-level assignments, and
`option_mismatches()` compares choices -- and `test_sunnylink_settings_complete.py` covers it. The
compiler also dereferences `trigger_key` unconditionally, so a sub-panel needs one even where the
schema says only `id` and `label` are required; it fails with a bare KeyError.

## REVIEW THE FAILURE PATHS INSIDE THE FAILURE HANDLING

A review on 2026-08-13 of code written HOURS earlier found two real bugs, and both had the same
shape -- an error path inside an error handler, which no test exercises because every test drives
the happy path:

- soundd's retry loop constructed `SounddBP()` OUTSIDE its own `try`. `__init__` loads sound files
  and opens a socket, so a missing audio device raises there and escaped `main()` -- the fix for a
  crash loop contained a path that caused one.
- The mici HOLD badge left `_hold_rect` populated when it latched off, so a badge that threw once
  stopped drawing while its rectangle kept accepting taps. An invisible button.

**When you add a guard, review the guard.** 589 green tests said nothing about either, and the same
blindness produced all four SunnyLink bugs above: structure checked, behaviour unchecked.

## SOUNDD CRASH-LOOPS ON THE COMMA 4, AND THAT LOOKS LIKE A COMMS FAULT

upstream's `soundd_thread` ends its loop on a bare `assert stream.active`. When the output stream
stops -- device suspend, underrun, the comma 4's amplifier changing state -- the process exits,
manager restarts it, and it dies again. **The restart churn is what the driver sees: "low
communication rate between processes", then devicestate and managerstate complaints.** It recovers
after a couple of minutes and correlates with cruise use, because that is when alerts fire.

Not reproducible on the 3X. `selfdrive/ui/bp/soundd_bp.py` now reopens the stream instead of dying,
in the wrapper rather than in upstream's loop, since a wrapper costs nothing on the next merge.

**Why this fork carries an upstream bug**, against the rule above: this fork ships `soundd_bp` AS
the soundd process, and ICBM drives far more engage/disengage cycles than stock, so it exercises the
stream much harder. A latent upstream bug we make LIKELY is ours to survive, even while it stays
theirs to fix.

## Keep only the additions that still earn their place

## Keep only the additions that still earn their place

Stated 2026-08-08: *"I just want to keep additions we have made that actually make a difference."*
This is the upstream-scope rule turned on OUR OWN work, and it has teeth, because a knob we invented
costs the same merge conflict forever as one we borrowed.

**IF UPSTREAM HAS IT, KEEP IT -- even if nothing here uses it.** This rule is only ever about
additions THIS FORK made. Deleting upstream's own code is the maximum merge cost there is: it
conflicts with every future change they make to it, forever, which is the exact opposite of what
this rule is for. An unused upstream param costs nothing, because it is on both sides of the diff
and cancels out. Before deleting anything, check:

```bash
git show upstream/bp-7.0:<path> | grep -c "<the thing>"
```

Zero means it is ours and the rule applies. Anything else means leave it alone. That check is what
established earliness was ours -- upstream has no earliness concept at all.

**The test is whether the REASON still holds, not whether the default is neutral.** Those come apart:

- `SmartCruiseControlVisionEarliness` -- **deleted.** Ours, not upstream's; upstream uses
  `_ENTERING_PRED_LAT_ACC_TH` 1.3 / `_ABORT` 1.1 / `_TURNING` 1.6 directly and we replaced all five
  with properties dividing by a param. It existed because vision was once the only thing that could
  catch an off-ramp. SCC-Map reads ramps from the map now, so the reason expired -- and its
  remaining effect was making gentle interstate sweepers slow hard. Gone, call sites back on the
  constants.
- `SmartCruiseControlMapFactor` -- **kept**, and it defaults to 100, which changes nothing today.
  Neutral, but the reason is live: it is the only way to ask for mapped corners slower than the
  posted advisory, which this car's retrofit PSCM needs.
- `SmartCruiseControlVisionHighSpeedFactor` -- **kept** at a neutral 100. It is one half of a
  speed-blended pair whose other half is not neutral; removing it would break the blend, not
  simplify it.

**When a reason expires, delete rather than park at neutral.** A knob that changes nothing is still
a modified upstream line, and it reads to the next person as load-bearing. And prefer upstream's
constant to our multiplier where the two are equivalent -- their numbers have far more road under
them than ours.

## THE EXIT THAT NEVER SLOWS ENOUGH IS NOT A TUNING PROBLEM

He has reported this repeatedly. Measured on route 00000348, 2026-08-11, and the answer is arithmetic:

  t+24439  63 mph  dash 80  nothing asking
  t+24441  66 mph  dash 78  sccVision fires, asking 71
  t+24443  69 mph  dash 71  sccMap fires, asking 38
  t+24447  66 mph  dash 58  latAcc 6.03   <- already in the corner
  t+24453  46 mph  dash 38  the set speed finally arrives

Two hard numbers bound it:

- ~~**The set speed falls at about 3.3 mph/s and cannot go faster.**~~ **WRONG, AND IT IS QUOTED
  ALL OVER THIS FILE. Corrected 2026-08-26, from his question: *"So were you wrong about how fast
  ICBM could change the speed? So it could slow down more for exits, right?"***

  Measured across today's drives, fastest SUSTAINED travel over a 3-second window with cruise
  engaged:

      000003c9   UP +8.0 mph/s      DOWN -6.7 mph/s
      000003c8   UP +3.7            DOWN -20.1  (a cruise-state jump, not presses -- discount)
      000003c6   UP +3.7            DOWN -1.3

  **-6.7 mph/s, twice the figure this file has carried as a hard ceiling.** The two numbers
  reconcile: 3.3 came from ONE descent on route 00000348 (71 -> 38 in ten seconds) and that was
  measuring the DROP LIMITER, which paces the target down on purpose so Ford coasts instead of
  braking. It was never the buttons' rate.

  **And "it is not a parameter" was wrong too: `IcbmMaxTargetDrop` is one, and it reads 12.**

  WHAT THIS RE-OPENS. "THE EXIT THAT NEVER SLOWS ENOUGH" is built on the 3.3 figure -- *"a 65 -> 38
  exit needs about eight seconds of set-speed travel. It got four."* At 6.7 mph/s that 27 mph takes
  **four seconds**, which is exactly what it got. The arithmetic that closed the exit problem as
  "detection time, and no lever here can touch it" may be wrong at its root.

  **DO NOT ACT ON THAT YET.** Today's drives contain no exit ramp, so the descent rate measured is
  from ordinary slowing, and whether the limiter binds during a real ramp is unmeasured. What is
  established is only that 3.3 is not a ceiling and that a lever exists. Re-measure on a route with
  an actual exit before touching `IcbmMaxTargetDrop`.

  **The general lesson, for the fourth time in this file: a number produced by ONE event, quoted
  afterwards as a property of the car.** The circular-convergence entry and the `t+NNNN` inflation
  entry are the same failure. He caught this one by asking whether the earlier claim was wrong.
- **The map asked four seconds before peak cornering.** A 65 -> 38 exit needs about eight seconds of
  set-speed travel. It got four.

So the deficit is DETECTION TIME, and the levers people reach for do not touch it:

- `SmartCruiseControlMapDecel` is a trigger distance, and at 8 it already triggers earlier than
  stock. The map still only fired 4 s out, so the corner was not in `MapTargetVelocities` until then.
- Commanding Ford's 20 mph floor instead of 38 does NOT help. The dash descends at the same rate
  either way; a lower final number just overshoots later. Worth stating because the hazard path does
  exactly that for a different reason, and the analogy is tempting and wrong.
- The camera cannot cover it either: SCC-Vision fired two seconds before the map, because a ramp bends
  away from where the camera is looking.

The remaining lever is how far ahead `MapTargetVelocities` is populated, which is mapd's, upstream of
this fork. Do not re-derive this from scratch; measure with `tools/bp_missed_curves.py` and compare
the map's fire time against the 3.3 mph/s budget before proposing anything.

## 2026-08-26: SCC-MAP PUBLISHED THE CORNER SPEED TWO SECONDS AFTER PEAK CORNERING

**HE ASKED THE QUESTION THAT REOPENED THIS, and the first answer was wrong.** A 6.50 m/s^2 lateral
event on route 000003c9 was reported to him as "it was you, hands on the wheel, not a bug". He
pushed: *"Yeah, sure, maybe I was steering, but was cruise on and I took over steering because it
couldn't handle it?"*

He was right, and this file already contains the pattern he was invoking: *"hands-on% climbs the
same curve -- 6% low, 90%+ above 3.0 -- so he TAKES OVER exactly where the PSCM starts losing the
line."* **"He was steering" and "it could not handle it" are the same observation, not alternatives.**

    t+762-779   nothing asking     dash 80 (SLA)   car ACCELERATING 66 -> 73 mph
    t+780       sccVision asks 63                  dash starts down from 80
    t+783       dash 65            latAcc 0.65     hands off
    t+784       dash 57            latAcc 2.34     HE TAKES THE WHEEL at 68 mph
    t+785       dash 56            latAcc 4.31
    t+786       dash 54  sccMap ASKS 28            latAcc 4.75  <- peak, and the map arrives NOW
    t+790       dash 28                            corner over, car at 50

**The controller that knew the corner needed 28 mph published it two seconds AFTER peak lateral
acceleration and six seconds after the corner began.** SCC-Vision fired first, ~6 s out, asking 63
against a car doing 73 -- and the car was still ACCELERATING into the bend at that moment, because
SLA had the set speed at 80 and nothing was objecting.

**THIS IS "THE EXIT THAT NEVER SLOWS ENOUGH", WORSE THAN THE WRITE-UP.** That section measures the
map firing four seconds BEFORE peak cornering on route 00000348. Here it fired two seconds after.

**AND THE 3.3 mph/s ARITHMETIC THAT CLOSED THAT SECTION IS ALSO WRONG** -- see the corrected entry
in "Facts that have been got wrong before". The dash came down 80 -> 57 in four seconds here, about
5.75 mph/s, so the set speed was NOT the binding constraint on this event. **Detection time was.**

### WHAT NOT TO DO

- **Do not conclude "he cornered hard" from `steeringPressed` alone.** It is the same split that
  corrected the 3.21 m/s^2 figure, and used carelessly it converts a controller failure into a
  driver anecdote. Ask what the controllers were asking for and WHEN.
- **The steering-angle derivation reads ~35% high at highway speed.** 6.50 from the bicycle model
  was 4.75 on `currentLateralAccel` at the same frames. Print both, on the same frame; that rule is
  already in this file and the first read of this event ignored it.
- **The 2026-08-26 ramp-approach fix does NOT help here** and was never meant to. This corner was
  entered at 73 mph, so `ramp_approach` is true and the conservative horizon still applies --
  correctly. That fix is for phantom corners on surface roads, a different failure.

### THE ONE LEVER, AND ITS COST

`SmartCruiseControlMapDecel` is a TRIGGER DISTANCE (see "Facts that have been got wrong before"),
currently 8. A gentler value lengthens the required distance and makes SCC-Map publish EARLIER.

**But it makes the phantom corners fire earlier and hold longer too**, and there are four of those
on the same two drives (see the ramp-approach entry). Moving it trades a real, measured late
detection against a real, measured false one. **Measure both before touching it** -- and note the
phantom fix and this lever push in opposite directions, so changing them together produces a drive
that cannot say which moved.

## WE ARE PINNED TO THE LAST RELEASE OF A DEAD MAPD, AND UPSTREAM IS NOT COMING

Established 2026-08-16 from the repos, because "mapd is upstream of this fork" was being used as a
reason to stop thinking, and it turns out to be a reason to start.

**mapd is [pfeiferj/openpilot-mapd](https://github.com/pfeiferj/openpilot-mapd)** -- a standalone Go
binary using OpenStreetMap, downloaded from GitHub releases at boot by
`sunnypilot/mapd/mapd_installer.py`, where the version is one constant. Not a library.

**`VERSION = "v1.12.0"` IS THE FINAL v1 RELEASE THAT WILL EVER EXIST.** pfeiferj shipped v1.12.0 and
every release after it is v2.x. Upstream is on v2.3.0 as of 2026-08-12.

**And sunnypilot's move to v2 is abandoned, not in progress.** All of it dates to one day:

  - PR [#1647](https://github.com/sunnypilot/sunnypilot/pull/1647) "prerequisite mapd v2: remove old
    mapd, sla, scc" -- still a DRAFT, `+3/-2567` across 35 files, pure demolition
  - branches `mapd-v2`, `mapd-v2-prebuilt`, `mapd-v2-prerequ` -- last commit 2026-01-14, all three
  - `mapd-v2` is 2 commits ahead of master and **1439 commits behind**
  - zero human comments on the PR in seven months, only a CI bot
  - the author is STILL ACTIVE in the repo (PR #1767, 2026-08-14) -- they did not leave, they moved
    to UI work and never came back to this

So "wait for upstream" is not a plan. There is nothing to wait for, and nothing to collide with
either -- which inverts the usual rule. The demolition draft is 1439 commits behind and would have to
be rewritten by whoever finishes it.

**WHY IT MATTERS TO EVERY FEATURE HERE, not just passing assist.** v1 talks through `/dev/shm/params`,
which pfeifer's own docs call the design's biggest flaw: a BLOCKING operation in the controls loop,
where every new field is a breaking change for every fork, and **none of the data reaches the route
logs**. That last one is why no drive analysis in this fork has ever been able to see what the map was
saying. v2 rewrites comma's msgq in Go, so mapd speaks native cereal -- non-blocking, logged, and able
to read openpilot's state directly instead of us copying GPS back out to it.

The v1 field list is short because its transport made adding fields expensive, not because the data
does not exist. What v2 publishes on `mapdOut` (20 Hz) that we have no access to today:

| Field | Answers |
|---|---|
| `highwayClass` | the raw OSM tag, and it separates `motorway` from `motorwayLink` -- **freeway from on/off-ramp**, which is the exit problem above stated exactly |
| `advisorySpeed` | the yellow curve-advisory sign, an independent number for the corner SCC-Vision currently derives with no cross-check at all |
| `lanes` + `distanceFromWayCenter` + `estimatedRoadWidth` | how many lanes, and which one we are in -- the question the camera structurally cannot answer when paint refuses |
| `oneWay` | divided-highway corroboration for the radar oncoming veto |
| `waySelectionType` (incl. `fail`) | when the map is LOST rather than confident and wrong |
| `tileLoaded` | "no limit here" vs "no map here" -- the distinction behind a hold inferred for 36% of route 00000379 |

**The full inventory is in `bluepilot/MAPD-V2-PLAN.md`** -- every field and setting, sorted by
which feature it serves, with the integration cost and the known collisions. The underrated half
is the SETTINGS: `Curve Target Speed Time Offset` is literally the earliness lever the exit
section above says is "mapd's, upstream of this fork", and `MapdExtendedOut.path` carries the
whole curvature-and-target-velocity profile ahead rather than SCC-Map's single step.

**Do not start this before the California trip.** It is large and it touches the layers this fork has
customized most. But drop "upstream will handle it" as a reason -- it is measurably false.

**THE BLOCKING QUESTION IS ANSWERED, 2026-08-16: v2 CAN RUN ALONGSIDE v1 AS A PURE OBSERVER.** The
SLA/SCC teardown in that draft is sunnypilot's consolidation choice, confirmed rather than assumed --
**mapd v2's `params.go` has no `/dev/shm` concept in it at all**, so every key SLA and SCC read is
written by v1 and cannot be perturbed by a process that never opens that store. v2 takes its own
position from `gpsLocationExternal` over msgq. Two further things that were assumed wrong: there are
not two tile stores (v2's base path IS `Paths.mapd_root()`, same URL, same layout), and the tiles
already on the device already carry `wayId` and `highwayClass` at 100% -- the shipped v1.12.0 binary
reads those very files and has no way to publish either field. Only integration step 7 (clamping
`v_cruise` in the planner) is non-additive, and it is the one the map-is-evidence rule forbids anyway.

Full evidence, the four mechanical collisions, and what still needs the device (CPU and memory with
both running) are in `bluepilot/MAPD-V2-PLAN.md`. `tools/bp_offline_map.py` reads the tile store
directly -- the first tool here that can see what the map says, since v1 puts none of it in the route.

### MAPD V2 IS SHIPPED AND LIVES IN THIS BRANCH. WHAT EVERY SESSION NEEDS TO KNOW.

Built 2026-08-16. It is in `icbm-manual-override-and-tuning` **on purpose**, not on a feature branch:
it touches `cereal/`, `params_keys.h`, `process_config.py` and `sunnypilot/mapd/`, which the base
owns, and every other branch rebases onto this one -- so this is the only place it survives. Passing
assist and the radar detector pick it up by rebasing, which they already do.

**`MapdV2` HAS THREE STATES, and the middle one is the one nobody expects:**

    0  off       the v2 process does not run. Nothing changes, nothing is spent. THE DEFAULT.
    1  observe   v2 runs and its whole view is logged at 20 Hz; Speed Limit Assist still reads v1.
    2  on        SLA reads v2.

State 1 exists because v1 records NOTHING about what it saw, so the only way to compare the two is to
run both and log the new one. `tools/bp_mapd_compare.py` scores one drive; the gate for moving to
state 2 is its "only v1 had a limit" row being near zero.

**THE GATE IS MET. Route 00000383, 2026-08-18, 494 frames where both had spoken:**

    both agree on a limit    318   64.4%
    both say no limit        126   25.5%
    ONLY v1 had a limit        8    1.6%   <- the gate
    only v2 had a limit       33    6.7%   <- v1 was blind here
    differ by >1 mph           9    1.8%

And all eight "only v1" frames are ones where v2's `waySelectionType` was **fail** -- v2 was not
wrong, it said it did not know, and `MapdV2MapData` refuses a limit from a failed match anyway. v2
published 8,740 frames against v1's 598.

Checked before recommending it, because the way this could hurt is v2 INVENTING a low limit rather
than missing one. Every "only v2" limit is class-appropriate: 20 residential, 30 tertiary, 45 on a
motorwayLink, 70 and 65 on motorway, 30/35/40 secondary. And in the disagreements v2 reads HIGHER in
108 of 121 frames -- v1 was serving 20 mph on a secondary road v2 calls 30. Flipping to state 2 is
not a slower car.

**So `MapdV2` 1 -> 2 is his to set, and it is now backed by his own drive rather than by the plan.**

**Default 0 is deliberate and it is about somebody else's car.** Others track this branch for ICBM
alone -- a second map daemon on their device, a fifth of a core and 200 MB, for a migration that is
ours, is not a cost to hand to them. The binary ships either way; what it costs is opt-in.

**Rules for anything touching this, all already paid for:**

- **`mapdOut.suggestedSpeed` IS NOT TO BE CONSUMED.** It is mapd's own arbitration and its integration
  guide has you clamp `v_cruise` to it. It cannot know this car is driven by BUTTON PRESSES at
  ~3.3 mph/s, that a HOLD exists, or that SCC-Map carries four defenses built from measured events --
  and as a clamp it moves the MAX number, which is his. Take the INGREDIENTS instead:
  `speedLimitSuggestedSpeed`, `mapCurveSpeed`, `visionCurveSpeed`, as inputs beside the camera.
  `test_mapd_schema.py` fails if ANY decision-making file reads it -- widened 2026-08-16, because
  the first version checked `longitudinal_planner.py` alone and a read added to
  `speed_limit_assist.py` passed it. The guide names the planner because that is where the GUIDE
  puts it; the bypass is just as total from SLA, SCC-Map, ICBM or a passing-assist gate. It parses
  with `ast` rather than grepping, so prose explaining why we refuse the field stays free while code
  using it is caught by file and line -- necessary, since every such explanation contains the word.
- **The `Mapd*` structs in `cereal/custom.capnp` are THEIRS.** The binary is compiled against its own
  copy and capnp reads by POSITION, so an inserted field does not rename anything -- it makes
  `speedLimit` decode out of other bytes, with no error anywhere. Take mapd's verbatim; put our own
  fields in our own structs.
- **For passing assist: map data MAY REFUSE, MUST NEVER OPEN.** `lanes = 3` cannot authorize a lane
  change on its own. That is what keeps "no map costs coverage, never safety" true.
- **A LIMIT FROM A FAILED WAY MATCH IS REFUSED.** `waySelectionType == fail` means mapd's matcher
  could not decide which way the car is on, and `MapdV2MapData` zeroes both the current and the next
  limit there -- the next one is matched against the same way, so it is exactly as suspect. Added
  2026-08-17. It is OUR confidence policy, not an assumption about mapd: a limit is an instruction to
  change speed, so refusing one costs coverage while honoring a wrong one costs safety, which is the
  map-is-evidence rule applied. Whether mapd zeroes it anyway is answered by
  `bp_mapd_compare.py`, which now cross-tabs fail frames against non-zero `speedLimit` -- so that
  question needed a DRIVE, never a device, and it no longer blocks state 2 either way.
- **A new setting family needs its prefix in `bp_sunnylink_settings_audit.py`.** `Mapd` was missing
  and the audit reported 33/33 reachable while the new control could not be changed remotely at all.

**THREE OPERATIONAL FACTS, learned on the device 2026-08-16, each of which cost a round:**

- **NEVER HAND-RUN `mapd_v2` WHILE MANAGER HAS IT.** Two publishers cannot hold the same msgq
  endpoint, and it is the MANAGED instance that dies -- `managerState` reads `running=False
  exitCode=2`, manager does NOT restart it, and only a reboot recovers. The nasty part: v1 is
  untouched throughout, so the car looks perfectly healthy and the only dead thing is the one being
  debugged. Subscribe to `mapdOut` instead. This is the easiest way to waste an evening here.
- **OFFROAD IT PUBLISHES NOTHING AND THAT IS NOT A FAULT.** Parked, `gpsLocation`,
  `gpsLocationExternal` and `liveLocationKalman` are all silent, so v2 has no position to resolve and
  emits zero frames. **v1 looks alive in the same moment only because `/dev/shm/params` still holds
  `MapSpeedLimit` and `RoadName` from the last drive -- STALE VALUES THAT READ AS LIVE ONES.**
  **Observe mode cannot be verified from a parked car.** What IS checkable offroad: the binary
  exists, the process is in `managerState` with `running=True`, and `MapdV2` is set. Everything else
  needs a drive.
- **That staleness is a trap for the COMPARISON, not just for a human reading params.** At the start
  of a drive v1 confidently serves last trip's limit while v2 correctly serves nothing, which scores
  as a run of "only v1 had a limit" -- THE number the cutover rests on, poisoned toward never
  switching, by v1 being wrong rather than v2 being deficient. `bp_mapd_compare.py` scores only
  frames above 5 mph for exactly this reason.

- **A ROUTE OLDER THAN THE CURRENT BOOT SAYS NOTHING ABOUT A PROCESS YOU JUST ENABLED.** On
  2026-08-17 `mapdOut` was absent from all four newest routes and that nearly went out as "observe
  mode is broken". The newest was 00:25, the device had booted at 03:54, and it had been parked
  since -- so every route predated the build being asked about. Check `uptime` and the segment
  mtimes against each other BEFORE reading anything into a missing message. Combined with the
  offroad-publishes-nothing fact above, a parked device can neither confirm nor deny observe mode,
  and it will happily let you conclude either.

**Setting `MapdV2` right after a flash needs the FILE, not `Params().put()`.** `common/params_pyx` is
compiled from `params_keys.h`, so until scons rebuilds on the first boot the key does not exist and
you get `UnknownKeyName: b'MapdV2'`. Writing the file directly lets ONE reboot do both, since manager
will not overwrite a param that is already set:

```bash
printf "1" > /data/params/d/MapdV2      # 0 off, 1 observe, 2 on
```

Otherwise it is reboot, set, reboot. True of any new param on its first flash, not just this one.

### WHAT THE v2 PATH ACTUALLY CARRIES: CURVATURE. THE VELOCITIES ARE A TRANSFORM OF IT.

Measured 2026-08-18, route 00000383, and it reframes the migration. Across **6,725 path points,
every single one**:

    targetVelocity^2 * |curvature| = 2.200

which is `/personalities/standard/map_curve_target_lat_a` exactly. mapd computes the path's corner
speeds as `v = sqrt(a_lat / curvature)` and nothing else, on the STANDARD personality --
`subscriber/shadow_selfdrive_state` is False, so it never sees openpilot's.

**So `targetVelocity` carries no information `curvature` does not.** SCC-Map at state 2 is fed the
same KIND of number v1 gave it, at 27 points instead of one. The plan document sold the path on its
velocities; **the value is the curvature profile**, and that is what the exit-ramp work should be
built on rather than on a denser supply of corner speeds.

Two dials that look like new levers and are not:

- **`map_curve_target_lat_a` IS `SmartCruiseControlMapFactor`**, in different units:
  `v = sqrt(a_lat / k) * factor`. Two controls for one behaviour. If they are ever consolidated
  mapd's is the better one -- a lateral acceleration rather than a multiplier on somebody else's
  constant -- but that is a SWAP, not an add.
- **`curve_target_speed_time_offset` does not reach SCC-Map at all.** SCC-Map walks the path and
  does its own trigger arithmetic, so mapd's offset only moves mapd's own controller output, which
  this fork does not consume. It was written up in `MAPD-V2-PLAN.md` as the answer to "the exit that
  never slows enough" and **it is not**. That lever is still ours, in the walk.

`sunnypilot/mapd/mapd_settings.py` is the bridge that establishes all of this: it caches mapd's own
settings into `MapdSettings` (declared since v2 landed, never read or written by anything, and no
process had ever published `mapdIn`), and its write path is built, tested and **deliberately empty**.

**SCC-Map ALREADY READS THE v2 PATH at state 2** (`mapd_v2_path.py`, wired in
`smart_cruise_control.py`, `mapdExtendedOut` subscribed in plannerd). It is a pure SOURCE SWAP: the
walk, the trigger arithmetic, the corner-speed factor pair and all four camera defenses are
untouched, and `curvature` per point is parsed and deliberately NOT used yet.

**Do not "finish" that by re-deriving the defenses from reasoning.** They were each bought with a
measured event on his roads and they were built to interrogate a SINGLE corner speed arriving with no
context; against a full profile some of them answer a question nobody is asking any more. Which ones,
and what replaces them, is a question for drive data. Changing the source and the judgement in one
step would produce a drive that cannot say which half moved.

SCC-Map also FALLS BACK to v1 when v2 is selected but silent, which is the opposite of what the SLA
reader does. Deliberate: there a quiet fallback hides a broken install behind plausible speed limits,
here v1 is still the shipped curve source and the failure being avoided is not slowing for a corner.

### REMOVING v1: THE ONLY THING HOLDING IT IN IS SCC-MAP'S FALLBACK, AND THAT IS A NUMBER

Checked 2026-08-18, because "then we should probably remove v1 to save RAM and CPU" needs a list
rather than a guess. The list is one item long:

- **Speed Limit Assist and the road-name HUD are ALREADY on v2 at state 2.** `RoadNameRenderer`
  reads `liveMapDataSP.roadName`, which `base_map_data` fills from `get_current_road_name()`, which
  `MapdV2MapData` overrides to `mapdOut.roadName`. The `/dev/shm` `RoadName` reader is
  `osm_map_data.py` -- v1's own, correctly.
- **`LastGPSPosition` is written by `MapdV2MapData` itself**, deliberately, so it is not a v1
  dependency either.
- **`SmartCruiseControlMap` re-reads `MapTargetVelocities` whenever the v2 path is None.** That is
  the whole of it.

**And `mapd_ready()` never looks at `MapdV2` at all** -- it returns True whenever the map root
exists -- so v1 runs in every state including 2. Its measured cost is ~22% of a core and 204 MB.

**How often the fallback actually fires, from drive A: 9.0% of moving frames, 5 runs, longest 38 s.**
Which was too high, and 8 of those 9 points were a bug in our own reader rather than a gap in v2:

    frames where NO path point carried a targetVelocity   46
       ...and the path had curvature (mapd could not compute)    0
       ...and the path was straight (nothing to compute)        46

`path_from_mapd` returned None for all 46, against its own docstring saying an empty list is "a real
answer meaning no corners ahead". So SCC-Map consulted a second, older map for a question v2 had
already answered. Fixed: a straight path with no velocities returns an EMPTY target list, and only
curvature-present-with-no-velocity still falls back -- which happened zero times, and stays because
zero is a measurement rather than a guarantee.

**So the remaining fallback is the "empty path" case alone, and the next state-2 drive measures
whether v1 can go.** Do not remove it before that drive: the fallback is silent, and a v2 that goes
quiet without it costs curve slowing outright.

**FOR PASSING ASSIST: `bluepilot/MAPD-V2-FOR-PASSING-ASSIST.md`.** Field-by-field availability
measured on route 00000383 -- `oneWay` 100% and trustworthy (motorway and motorwayLink both 100%
True across 41 distinct ways, residential and tertiary 0%), `highwayClass` 98.6%, `lanes` 91.5% and
plausible by class, and `distanceFromWayCenter` with a p90 of 11.58 m that **does not fit any real
road** and must not carry a lane-position gate until it is checked against the camera. Written so
that session consumes rather than re-derives, and so the v1 removal stays here where its one
remaining dependency lives.

**What is left, in order:** the curvature profile itself -- plan a descent against the ~3.3 mph/s the
buttons actually deliver instead of reacting to a step, which is the exit-ramp problem, together with
whatever the defenses become. Then passing assist consuming lanes / highwayClass / oneWay /
distanceFromWayCenter. Then v1 comes out and gives back what the overlap costs.

**TWO MERGE FACTS, both from 2026-08-16 and both cheap to get wrong:**

- **plannerd's SubMaster list is edited by more than one branch and conflicts must be resolved as a
  UNION.** Passing assist adds `liveTracks`, `rearRadarBP` and `selfdriveStateSP`; the base adds
  `mapdExtendedOut`. Taking either side whole silently removes an input a controller already reads,
  and nothing offline notices -- which is the next bullet.
- **THE SUITE PASSES WITH `plannerd.py` FULL OF CONFLICT MARKERS.** 1674 tests green on a file that
  could not possibly have imported, because nothing offline imports plannerd -- it needs `messaging`.
  After any merge that touches it, parse it explicitly:

      python -c "import ast; ast.parse(open('selfdrive/controls/plannerd.py',encoding='utf-8').read())"

  A green run says nothing whatsoever about a file it never read. The same holds for anything else
  requiring compiled extensions.

### FORD'S LAUNCH IS 2.0 m/s^2, WHICH *IS* PANDA'S CEILING. EVERY PULL-AWAY WAS REFUSED.

*"It switched to OP long for acceleration and it went ridiculously slow."* Then the question that
reframed it: *"Why is OP long launching at all? Is that because I told you that Ford ACC has slow
launches?"* **He never said that and nothing chooses openpilot for launches.** `fallback` is not a
choice, it is a REFUSAL -- and refusing hands the WHOLE frame to openpilot, because a 50 Hz message
cannot simply stop.

Route 00000393, 994 fallback frames with a camera ACCDATA, 624 of them under 15 mph:

    AccPrpl_A_Rq  2.000  2.020  2.030  2.050  2.060  2.070  2.080  2.090 ...  all "outside the band"

`_PANDA_GAS_MAX` is exactly **2.0**, and `_PANDA_MARGIN` 0.005 puts even a clean 2.000 outside. Ford's
ordinary pull-away propulsion sits right on the number, so the passthrough abandoned Ford on
essentially every launch.

**CLAMPED AT THE TOP, NOT REFUSED**, in `create_acc_msg_passthrough`. Costs 0.005 m/s^2 and keeps
every other signal Ford authored. Same shape as pinning `AccPrpl_A_Pred`.

**THE ASYMMETRY IS THE ARGUMENT, and it is the part to preserve:**

    clamping DOWN 2.07 -> 1.995    asks for LESS ACCELERATION than Ford wanted   -> conservative
    clamping UP  -0.77 -> -0.495   asks for LESS ENGINE BRAKING than Ford wanted -> NOT

So the low side is still a refusal and still falls back. Quietly under-decelerating a frame nobody
is watching is not a thing to do. That case is 3.5% of fallbacks and none of the launches.

**And -5.0 is never clamped.** It is panda's legal escape and sits BELOW the band, so treating it as
out-of-range would turn "no propulsion request" into "maximum propulsion request". That inversion is
the one way this change could be dangerous rather than merely wrong, so it has its own test.

**THE GENERAL LESSON: a band violation is not automatically a refusal.** Ask which direction the
clamp errs in. Three fields in this message have now needed three different answers -- pin
(`AccPrpl_A_Pred`, an advisory hint), clamp-one-side (`AccPrpl_A_Rq`), and carry verbatim
(`AccBrkTot_A_Rq`, where a silent softening would be indistinguishable from working until it
mattered).

### A LOWER `nextSpeedLimit` ON A MOTORWAY IS AN EXIT RAMP. THAT WAS THE 45 ON I-215.

*"At one point it said the speed limit was 45, even though the speed limit was 70."* Traced through
every layer on route 00000393, 2026-08-19, and EVERY LAYER BELOW US WAS RIGHT:

    the tile on his device   way 31535502, motorway, maxSpeed 31.2928 m/s = 70.0 mph
    mapd published           speedLimit 70.0   nextSpeedLimit 45.0   waySelectionType current
    our resolver used        45

OSM right, tile right, mapd right. Upstream's ease-down then adopted the NEXT limit -- at 70 mph
with `LIMIT_ADAPT_ACC` -1.0 the adopt window is ~288 m -- and the 45 belongs to an exit ramp.

**HE SETTLED IT FROM THE SEAT:** *"I was in the left lane, which can't exit."* So it was not a road
he could physically have taken, and no route prediction would ever have been right about it.

**The root cause is a MISSING FIELD.** `mapdOut` has no `nextHighwayClass` and no `nextWayId`, so
nothing in the message separates "this road slows ahead" from "there is a ramp ahead we are
predicted onto". Upstream's ease-down assumes the first, which is right on a surface street and
wrong on a motorway.

Fixed in `MapdV2MapData.get_next_speed_limit_and_distance`, not in the resolver: the resolver's
arithmetic is upstream's and correct, and what was wrong is the CONFIDENCE OF THE INPUT. On
`motorway`, a LOWER next limit is refused. Narrow on purpose -- `motorwayLink` keeps it (on the ramp
the drop is real), a HIGHER next limit is untouched, and if he does exit the ramp becomes the
CURRENT way and its limit applies at once.

**AND THIS IS THE SECOND TIME A CONFIDENT-LOOKING MAP NUMBER NEEDED A CONFIDENCE POLICY** -- the
first was `waySelectionType == fail`. Both live in the same adapter for the same reason: mapd
reports what it computed, and whether that answer applies TO US is our question, not its.

### THE STOP OVERRIDE CANNOT FIRE. ITS OWN TWO CONDITIONS ARE MUTUALLY EXCLUSIVE.

Route 00000393, 2026-08-19, `tools/bp_fallback_reason.py`. Over the 11,519 frames where the model
asked for a stop:

    <= 20 mph                    9004   78.2%
    plan STOPPING                2689   23.3%
    no lead in 60 m              4703   40.8%
    -- pairwise --
    speed + stopping             2689   23.3%
    speed + nolead               3352   29.1%
    STOPPING + NOLEAD               0    0.0%     <- never, not once
    all three                       0    0.0%

**CORRECTED THE SAME NIGHT, and the correction matters.** The first reading of this was "openpilot's
plan commits to `stopping` ONLY when there is a lead", i.e. a logical contradiction between the
override's trigger and its own carve-out. A sharper measurement says otherwise:

    plan frames                       30735
    shouldStop                         7103
    shouldStop & NO lead               2570     <- the plan DOES want to stop with no lead
    shouldStop & engaged               2894
    shouldStop & engaged & NO lead        0     <- and THIS is the empty set

**The plan asks to stop at an empty stop line perfectly well. What never happens is being ENGAGED
while it does.** Every engaged stop-request on the drive had a lead; every no-lead stop-request came
while he was already braking. So the conditions are not contradictory in principle -- the state just
does not occur, because he takes empty stops himself.

That is the SAME root cause the previous drive found ("he disengages before every stop"), now
confirmed on a drive where Ford held 12,056 standstill frames behind leads. **Do not write it up as
a design contradiction; it is a precondition he has never had reason to satisfy.**

**Both halves were individually well-reasoned, which is how this got built.** "A lead disqualifies it
-- Ford's stop-and-go is better than ours" is right. "The plan must have COMMITTED, not merely
wanted to" is right. Nobody checked whether the two could hold at once, and the funnel that was
added earlier the same evening could not see it either: it reported each condition's own rate and
their four-way intersection, so three healthy-looking percentages hid a pair that is disjoint.
**PAIRWISE IS THE DIAGNOSTIC. A funnel of marginals cannot show an exclusion.**

**Do NOT fix this by dropping the lead check**, and do not rewrite the trigger either. Both were
proposed on the strength of the wrong reading above. With the corrected numbers there is nothing
structurally broken to repair -- the four conditions are individually right and the car has simply
never been in the state they describe.

**WHAT IT ACTUALLY NEEDS IS ONE DELIBERATE APPROACH**: an empty stop line, no car ahead, cruise left
engaged, foot off the brake, all the way to a standstill. That single event moves this from
untestable to measured, and `passthrough_cancel_frames` is its readout -- the camera's tolerance for
sustained contradiction is the real unknown and always was.

**And the diagnostic lesson survives the correction, which is why it is kept:** the funnel printed
each condition's own rate plus the four-way intersection, and three healthy marginals hid an empty
triple. PAIRWISE, AND AGAINST ENGAGEMENT, is what showed it. A funnel of marginals cannot show an
exclusion -- nor can it tell a contradiction from a state that merely never arose, which is exactly
the distinction the first write-up got wrong.

### THE FAN IS NOT A FAULT. MEASURED, TWICE, AND CLOSED.

*"I still feel like my fans are running pretty hard."* Route 00000393, 2026-08-19:

    peak 95 C at t+546   fan 100%   cpu 45-66%   mem 76-78%
    sustained 86-91 C for most of a 26-minute drive
    thermalStatus:  ok on ALL 3,084 frames.  Zero thermal or resource events.

**Nothing throttled and nothing degraded.** `procLog` puts the CPU where it always is -- locationd,
card, ui, loggerd, controlsd, modeld -- with `mapd_v2` NINTH at 76 MB peak. v1 is confirmed gone
(`ps` shows only `mapd_v2` and its manager), so the two-daemon fix took and stayed taken.

So the honest answer is that the fan is loud because it is WORKING, in a car in Utah in August, and
there is nothing to fix. Recorded so the next session does not spend an evening hunting a runaway
process that three separate measurements now say is not there.

**And the earlier `intakeTempC` idea was worthless** -- it reads 0.0 on this hardware, so the
"ambient proxy" added to `bp_drive_checkup` that morning proves nothing. The temperature TRACE over
the drive, beside cpu and mem, is what actually answers the question; a single peak never could.

### LOW-SPEED CURVES: VISION IS STRUCTURALLY UNABLE TO HELP, AND THE MAP RARELY GETS A CHANCE

*"Low speed curves, it isn't slowing down enough."* Attributed on route 00000393, 2026-08-19,
BEFORE touching a single sensitivity -- which is the rule this fork keeps having to relearn.

Every SCC activation under 40 mph, and the pattern is the whole answer:

    t+ 271s  vision doing 35 mph, asking 48 mph   (+13)
    t+ 592s  vision doing 39 mph, asking 49 mph   (+10)
    t+ 598s  vision doing 33 mph, asking 48 mph   (+15)
    t+ 600s  vision doing 32 mph, asking 53 mph   (+21)
    t+1304s  vision doing 27 mph, asking 56 mph   (+29)
    t+ 595s  map    doing 36 mph, asking 31 mph   (-5)   <- the only one that asked for LESS

**SCC-Vision reports `active` and asks for a speed ABOVE the one the car is already doing.** That is
not a tuning error, it is the formula: `v_target = v_ego * sqrt(a_lat_reg_max / max_pred_lat_acc)`
is PROPORTIONAL TO CURRENT SPEED, so once the car is already slow the target scales down with it and
can never demand a meaningful reduction. Lateral acceleration is `v^2 / R`, so at low speed even a
tight radius produces a small number and the ratio stays above 1.

**So vision contributes NOTHING below about 40 mph, and only the map can.** On this drive the map
was active for 146 frames out of a 1542-second drive -- and it asked correctly every time it did.
The deficit is map COVERAGE of low-speed corners, which is the same root cause as the section below:
mapd smooths real tile geometry into nothing, so most corners never reach a controller at all.

**DO NOT FIX THIS BY TUNING THE VISION FACTORS.** That has now been ruled out by measurement rather
than by the old warning: the factors multiply a target that is already above current speed, so any
value of them still asks for more than the car is doing.

**And do not "fix" the `active` flag either.** Reporting `active` while asking for +21 mph is
genuinely misleading, but `scc.vision.active` is read by the ICBM controller as `curve_active`,
which drives `v_curve_target`, the curve ceiling and `curve_exit_frames`. It is not a display flag,
and changing it changes driving.

**The lever that exists TODAY is `SmartCruiseControlMapFactor`** -- his, currently 90, applied to
corners at or below 25 mph. Lower means slower through tight corners, wherever the map sees them.

### THE CURVATURE IS VALIDATED NOW -- AND THE "40x WIN" NUMBER WAS NODE JITTER

2026-08-19. The tile curvature module was validated on "tightest triple = 127 m" against mapd's
5,000 m. **That 127 m was measuring OSM node jitter, not the bend**, and wiring it in would have
demanded a 127 m corner on a road that has none.

The arithmetic, which is worth keeping because it applies to any polyline curvature: for three
points on a chord `L` with sagitta `d`, `R = L^2 / 8d`. At the 12 m median node spacing on his way,
`L = 24 m`, so a REAL 240 m corner offsets the middle node by only

    d = 24^2 / (8 * 240) = 0.30 m          <- BELOW an OSM node's position noise

and inverted, half a metre of jitter alone reads as `R = 24^2 / (8 * 0.5) = 144 m`. Adjacent-node
curvature simply cannot resolve a 240 m corner at 12 m spacing; the two are the same size.

**`curvature_profile_baseline` measures across ~70 m of ROAD instead.** Noise falls as `L^2` while
signal grows as `L^2 / R`, so at 70 m the same corner gives `d = 2.55 m` against 0.5 m of jitter --
five to one instead of worse-than-one. Cumulative distance, not a node count: spacing on that way
runs 6 m to 112 m, so a fixed node offset would be a 6 m baseline in one place and 224 m in another.

**MEASURED ON HIS TILE, and this is the number to trust:**

    way 31532588, I-80, 56 nodes
      mapd published            ~5000 m       21x too loose
      ADJACENT triples   tightest 127 m, median 362 m     2x too tight, and a huge spread
      BASELINE (70 m)    tightest 259 m, median 302 m     53 readings
      the car actually pulled     240 m       (3.46 m/s^2 at 64 mph)

259 m against a measured 240 m is 8%, and the spread collapses from 127-362 to 259-302. **That is
the first curvature number on this fork that has been checked against something the car did.**

**THE SPEED IS MEASURED: THE PSCM HOLDS ABOUT 2.5 m/s^2, AND HE TAKES CORNERS AT 4.**

The first answer was **3.21 m/s^2** and it was WRONG, in the most instructive way available. He
called it twice -- *"So that's how fast I take them?"* then *"I bet that 3.2 is how fast I am taking
them"* -- and he was right both times. `latActive` only means openpilot was PERMITTED to steer; it
says nothing about whose hands were on the wheel. Splitting on `steeringPressed`:

    openpilot alone (no hands)   n=5251   p50 1.09   p90 1.93   p99 2.73   max 3.19
    HIS hands on the wheel       n= 892   p50 1.95   p90 3.09   p99 4.14   max 4.20

**openpilot has never once exceeded 3.2. The 3.21 was his 892 frames leaking into the number.**

**AND THE "CONVERGENCE" WAS CIRCULAR, which is the part to remember.** 3.21 on a 259 m corner gives
64 mph, and he drove that corner at 64 -- reported as two independent measurements agreeing. They
were not independent: one was derived from his driving, so it had to come out at his speed. This is
the file's own rule -- *a number only one tool can produce has never been checked* -- failing in a
new costume, because there APPEARED to be two numbers.

**WHERE THE PSCM STOPS KEEPING UP, which is the question he actually asked** (achieved is not
comfortable -- it can produce 3.2 while tracking badly and running wide):

    lat_acc bin   frames  limiter%  devLim%  hands-on%
    0.5 - 1.0       2511     0.8      0.4       6.2
    1.5 - 2.0       1269     3.1      2.0      19.8
    2.0 - 2.5        491     3.7      1.8      36.9
    2.5 - 3.0        198     9.1      8.1      55.6
    3.0 - 3.5         73    27.4     27.4      90.4
    3.5 - 4.0         25    12.0     12.0     100.0

The deviation limiter is quiet to 2.5 (<= 3.7%), then 9.1%, then **27.4%**. And `hands-on%` climbs
the same curve -- 6% low, 90%+ above 3.0 -- so he TAKES OVER exactly where the PSCM starts losing
the line. Two independent signatures of the same ceiling, and this pair genuinely is independent.

    the PSCM comfortably holds     ~2.5 m/s^2      259 m corner -> 57 mph
    openpilot's own p99             2.73           259 m corner -> 59 mph
    he drives it at                 ~4.1           259 m corner -> 64 mph, measured

**So his original correction was exactly right and is now quantified: he takes corners about 1.5
m/s^2 harder than his PSCM can hold, which is 5-7 mph on that bend.** The corner target belongs near
2.5, NOT at what the car has been observed doing.

This was only measurable because of a find while looking for it: **`MAX_LATERAL_ACCEL` (~2.4) is
applied in `carcontroller.py` and `lateral_curv_ext.py` and appears ZERO times in
`lateral_angle_ext.py`**, which is his car's path. Angle mode has no lateral-accel cap, so every
corner driven with MADS on is already a sample of what the PSCM will do.

**Two honest limits on it.** The driver-steered comparison is empty -- MADS is always on, so
`latActive` is true nearly always and there is no hand-steered baseline to prove openpilot's ceiling
sits BELOW his. And p99 is not a hard limit: max was 4.20 and the 130 limiter-bitten frames ran
HIGHER (p99 3.71), which is the expected shape -- our own clips bite during the hardest cornering.
So 3.2 is "sustained and demonstrated", not "the most the PSCM can do".

**WHAT IS STILL MISSING IS THE SPEED, and it is deliberately not invented.** Turning 259 m into a
corner speed needs `v = sqrt(a_lat * R)`, and `a_lat` is the open question -- his own correction:

    "I want to take the curve as fast as the PSCM can handle with angle steering."

At 2.2 (mapd's constant) a 259 m corner asks for 53 mph. He took it at 64. His 3.46 m/s^2 is what
the car achieved with HIM steering, and he has said the PSCM needs the car slower than that -- so
3.46 is an upper bound on the answer and 2.2 is somebody else's comfort number. **The PSCM's
angle-mode authority limit is a CAR FACT nobody here has measured yet**, and guessing it is exactly
the "somebody else's comfort constants" mistake he already corrected once.

So: the geometry is done and trustworthy, and the controller wiring waits on that one number.

### THE TILES SEE THE CORNER. MAPD DOES NOT. THE CURVE FIX IS OURS TO WRITE.

2026-08-18, and it REVERSES a conclusion stated twice the same evening. He reported a curve on
I-80 ("Dwight D. Eisenhower Highway", wayId 31532588, motorway, 3 lanes) where SCC did nothing. The
first read said the map was blind and the corner was unfixable. **That was checking the wrong
layer.** mapd's own settings were read -- no curvature or smoothing knob exists, only
`map_curve_target_lat_a` which is `SmartCruiseControlMapFactor` in other units -- and the
investigation stopped there instead of opening the tiles.

    mapd published curvature        ~0.0002 1/m      ->  5,000 m radius, a straight
    THE TILE ON HIS DEVICE          0.00790 1/m      ->    127 m radius at the tightest
    node spacing                    min 6 m, MEDIAN 12 m, max 112 m, 56 nodes on the way
    the model measured on the road  3.46 m/s^2 at 64 mph  ->  ~240 m radius

**The tile geometry is accurate and dense.** 240 m sits between the tile's tightest triple (127 m)
and its median (364 m); 12 m node spacing resolves a bend of that size easily. mapd is smoothing
real geometry into nothing, by a factor of forty, and we consume its output rather than the data.

`tools/bp_offline_map.py` ALREADY READS THAT TILE STORE. So the curve fix is computing curvature
ourselves over consecutive node triples -- circumradius of three points, which is what produced the
numbers above -- rather than reading `mapdOut.path[].curvature`.

**AND THE TARGET SPEED IS NOT A COMFORT CONSTANT, WHICH IS HIS CORRECTION AND THE SHARPER HALF:**
*"remember that my PSCM requires slower speeds for curves, so how I take the curve won't be
accurate. I want to take the curve as fast as the PSCM can handle with angle steering."*

So learning the corner from how he drives it -- proposed the same evening, and he pointed out it is
both already built (pinned holds) and WRONG-SHAPED -- would learn a speed set by the PSCM's limits
rather than by what the car can do. Same objection applies to `_A_LAT_REG_MAX` and the vision
factors: somebody else's comfort numbers.

The corner speed wanted here is **the fastest the retrofit Edge PSCM can hold the lane at in angle
mode**. That is CAR FACTS, category 3 of "the model gets what he has no preference about" -- not a
preference, not perception, a property of ONE car with no fleet to learn it from. Written code, no
param. It is the same authority limit the passing-assist curve gate already rests on, and
`FordLowSpeedFactor_ang` / `FordHighSpeedFactor_ang` are the calibration it lives in (see "Ford's
angle gains are a PSCM calibration, not a detune").

**Do not start this by tuning vision thresholds.** `SmartCruiseControlVisionEarliness` was deleted
for exactly that and made gentle sweepers brake hard. On the approach at 74 mph the model predicted
0.34-0.46 m/s^2 against vision's 1.3 entering threshold -- the camera had genuinely not seen the
bend, and no threshold change fixes a sensor that cannot look that far. The far field is the map's
job, which is why the tiles matter.

**One more thing measured and closed: `advisorySpeed` is ZERO on his entire route.** 23,179 mapdOut
frames, not one advisory speed. The yellow-sign shortcut does not exist here; do not plan around it.

### THE MAP IS EVIDENCE, NEVER PERMISSION -- and that is the design, not a caveat

His, 2026-08-16, and it is the sentence to check any map integration against:

  *"Mine works better with the map data we will add, but also works without it. BlueCruise always
   requires map data."*

  *"I almost think it's good that we aren't getting good map data yet, so we can make this work for
   the best and not rely on it."*

**BlueCruise uses the map as PERMISSION.** A Blue Zone is an operational design domain drawn in
advance -- prequalified divided highway, HD-surveyed. No map, no feature. That is why it covers
130,000 miles and why his 2+1 sections on US-6 and US-89 will never be in it.

**Here the map is one more input into a decision the sensors already reach.** The existing rule is
what makes that rigorous rather than a good intention -- *evidence that OPENS a maneuver must never
be cheaper than evidence that refuses one*. Applied to map data:

  MAY REFUSE, freely. `highwayClass` says motorwayLink, do not offer a pass. `oneWay` false with
  radar oncoming, do not offer a pass. A refusal from a stale tile costs a missed pass.
  MUST NEVER BE THE SOLE THING THAT OPENS. `lanes = 3` alone cannot authorize a lane change. A
  wrong tile then puts the car somewhere real, and losing map coverage takes the feature with it.

Hold that and the property he wants is automatic: **no map costs COVERAGE, never SAFETY.**

**THE MOMENT THIS GETS LOST IS ONE LINE LONG**, and it will look like a cleanup: "the map says
three lanes, so skip the camera check." That converts the map from evidence to permission silently,
and every gate downstream inherits it. Whenever mapd v2 lands, check each new map input against the
two bullets above before it touches a gate.

The trade, stated so it is chosen rather than discovered: in the MAPPED case BlueCruise wins. It
knows the lane count and where the gore point is; we re-derive both from a radar and a camera every
frame. We give up that ceiling to work on roads nobody surveyed, which is where he drives.

### THE MODEL GETS WHAT HE HAS NO PREFERENCE ABOUT. WRITTEN CODE GETS THE REST.

His, 2026-08-16, and it is the test to apply to any "should this be learned or hand-written" question
rather than arguing it fresh each time:

  *"Models here are good for things of which I have no preference, like staying in my lane."*

It sorts into three, and the third is the one people miss:

**1. PERCEPTION -- no preference, and the model is genuinely better.** Where the lane lines are, how
wide the lane is, where the road edge sits, is there a lead. Nobody would hand-write these and this
fork does not: `_geometry` consumes modelV2 wholesale. Comma's end-to-end direction is right here and
we free-ride on it.

**2. POLICY -- he has a preference, so it is written code with a param.** Whether to pass at all, how
much slower a car has to be, keep-right, how fussy to be when not making time, follow gap. These are
HIS, and the reason a fleet-trained model cannot serve them is not capability -- **a model trained on
the fleet learns the median driver and averages his preferences away by construction.** That is the
same objection he has to BlueCruise refusing to move into a faster lane: someone else's policy, baked
in where he cannot reach it.

**3. CAR FACTS -- nobody has a preference, but the model cannot know them.** The set speed moving 1
mph on a tap and 5 on a hold. The retrofit PSCM needing the car slowed before it accepts hard
steering. ICBM existing at all. These are not preferences and not perception; they are properties of
ONE car, and there is no fleet to learn them from. Written code, and deliberately with NO param --
they are facts, not choices.

The curve gate is worth noting as both 2 and 3, which is why it is the strongest of the recent gates:
"I don't want to pass on curves" is a preference, and the PSCM authority limit underneath it is a car
fact. When those two agree, the number is not a guess.

**What this costs, stated because it is real:** written code only refuses what somebody thought of.
The center turn lane was not thought of -- the road taught us, twice, and it is still not fully
solved. A model that had seen ten thousand turn lanes would simply not do it. Enumeration is the
weakness of category 2, and the answer is to keep MEASURING (geoLeftTravelProven, exitsBy,
patienceMissed) so the road can keep teaching, rather than to pretend the enumeration is complete.

## THE STOP-SIGN SIGNAL IS ON THE WIRE NOW. IT NEVER WAS.

2026-08-18. He reported stop-sign slowing as inaccurate while traffic lights are fine, and that
complaint was **unattributable**: `dec.has_slow_down()` -- the thing the whole stop-sign path keys
on, and what `unconfirmed_lead.py` drives `IcbmModelStopEnabled` from -- had never been published.
`DynamicExperimentalControl` logged `state`, `enabled` and `active` and nothing else, so no route
has ever said whether the model failed to SEE a sign or saw it and the RESPONSE was wrong.

Now `hasSlowDown`, `slowDownUrgency` and `slowDownEndpoint` are on the struct and set in
`longitudinal_planner.py` from the accessors. `endpoint_x()` is inf when the model's plan is not
full length, and inf is clamped to 0 on the wire -- **0 means "no endpoint", never "stopping right
here"**, which is the one reading that would invert the meaning.

**This is the third time a value in this fork was computed correctly and never rendered.** The test
asserts the WIRING and follows one level of indirection to do it: a field fed from `active()`
instead of `has_slow_down()` would read plausibly and correlate, and the test fails on it.

**AND IT KILLED plannerd ON THE FIRST FRAME OF THE NEXT DRIVE, 2026-08-18.**

    dec.hasSlowDown = self.dec.has_slow_down()
    KjException: Tried to set field: 'hasSlowDown' with a value of: 'False'
    which is an unsupported type: '<class 'numpy.bool'>'

`has_slow_down()` is `urgency_filtered > SLOW_DOWN_PROB` and urgency_filtered is a numpy scalar, so
it returns `numpy.bool`. Python treats that as a bool everywhere except at the capnp boundary. The
`float()` calls beside it were already right; the bool was bare.

**THE STATIC TEST COULD NOT HAVE CAUGHT IT, AND I WROTE ONE ANYWAY.** `test_dec_slow_down_published`
reads the AST -- it proved the field was fed from the right accessor and had no way to notice the
type. Same category as the 2026-08-15 CarController crash: structural and pure-logic tests do not
EXECUTE the boundary, and the boundary is where the process dies.
`test_capnp_accepts_published_types.py` builds the real message and assigns real numpy values into
it, plus an AST guard that every `dec.*` field goes through `bool()` or `float()` -- with `state`,
`enabled` and `active` exempted because each was checked and is plain Python.

**THE RULE, generalized: any numpy-derived value crossing into capnp needs an explicit Python cast**,
and the test for it has to run the assignment rather than read it.

**What it cost.** plannerd died at t+109.9 with exitCode 1 and `processNotRunning` fired 235 times.
It is the whole of what he saw; the 905 exceptions in swaglog that day are all `sunnylinkd` and
`athenad` websocket noise and none of them are plannerd.

**It costs a drive to use.** `has_slow_down` is computed from modelV2 alone, so it is live whatever
DEC is doing -- but it is not retroactive, and no existing route carries it. The measurement it
enables is scoring fire locations against OSM stop and give_way nodes from the tile store
(`tools/bp_offline_map.py` reads that store), which finally separates detection from response.

## THE SET SPEED HUNT: TAP FOR SMALL CORRECTIONS, HOLD FOR LARGE ONES

Reported 2026-08-12: *"it raised and lowered my cruise over and over... when the speed limit changed
to 25."* Found with `tools/bp_setspeed_hunting.py` on route 00000361 at t+2704:

```
18 reversals in 20 s.  SLA 25.  icbmTgt a CONSTANT 27.  dash 26 <-> 29 <-> 26.
```

**The cause.** This car moves the set speed **1 mph for a tap and 5 mph for a held button**. ICBM
asserts the button continuously until the cluster crosses the target -- a hold. So a 1 mph correction
requests 5, overshoots, and requests 5 back the other way, forever. The controller was not failing to
settle on a reachable number; **it had no way to ask for a small change.**

**The fix is the SHAPE of the request, not the state machine.** Within `TAP_BAND` the button is
pulsed; outside it, held exactly as before. No transition changed.

**THREE EARLIER ATTEMPTS ALL BROKE SOMETHING. Do not retry them:**

| attempt | what broke |
|---|---|
| deadband in `v_cruise_equal` + early exit from increasing/decreasing | stalled a curve descent at 63 instead of 40 -- the DROP LIMITER steps its target down 1 mph at a time and needs exact arrival before releasing the next step |
| re-entry keyed on whether the target MOVED since last settling | ICBM overshot a driver press by 6 mph |
| gating the button on a reversal count | broke `TestPressWinsWhileIcbmIsBusy`, did not converge |

All three tried to make the state machine TOLERATE being a mile per hour off. None asked why it was
off. The transitions carry more meaning than they look like they do -- the drop limiter depends on
exact arrival, the press path on immediate reaction -- so leave them alone.

**WHAT IS NOT VERIFIED.** `TAP_ON_FRAMES` / `TAP_CYCLE_FRAMES` are a guess at what this car reads as
a release rather than a repeat, and it cannot be checked offline: **the Drive harness moves the
cluster 1 mph per emitted button frame, so it models tapping and cannot reproduce a held-button
overshoot at all.** That modelling gap is why no test ever caught this, and it contradicts the
button contract below, which says to model 5 mph jumps. The tests therefore assert the DUTY CYCLE --
pulsed within the band, held outside it -- and both were confirmed to fail with tapping disabled.
Whether the gap is long enough is a road question. If the hunt persists, lengthen the gap before
touching anything else.

## EVERY `t+NNNN` PRINTED BEFORE 2026-08-12 IS INFLATED, BY ABOUT 4x

**The cause, measured rather than reasoned about.** Every segment file replays the boot-time header
messages -- initData, carParams and friends -- before its own data, so all thirteen segments of route
00000365 START at monotime 70.0:

    seg 0    70.0 -> 131.3
    seg 1    70.0 -> 191.3          <- steps BACK 61 s at the boundary
    ...
    seg 12   70.0 -> 824.4          <- steps BACK 721 s

Walking the segments in order therefore steps backward at every boundary, by an amount that GROWS
with the drive. Two successive versions of the helper saw those steps and "corrected" them --
first on any backward step over 1 s, then on any over 60 s -- and both accumulated a shift at every
boundary. The drive is **754 seconds** and was being reported past t+3300.

**THE FIX IS TO DO NOTHING.** openpilot starts a new route per ignition cycle, so `logMonoTime` is
already monotonic within a route. There is no reset to compensate for. Subtract the smallest monotime
seen and stop -- `tools/bp_logtime.py`, `DriveClock`.

Two traps that cost a wrong fix each:

- **The first monotime is not the smallest.** Header replay means the minimum may arrive at any point,
  so anchor on the running minimum, not on the first message.
- **A reset cannot be told from header replay by the SIZE of the backward step.** That step equals
  elapsed drive time, so it grows without bound and any threshold on it fails on a long enough drive
  -- a 10-minute drive already clears 60 s and would clear 600 s. Discriminate on the VALUE: header
  replay lands exactly ON the earliest monotime, a real reset lands BELOW it.

**What this does and does not invalidate.** Speeds, targets, radii, plan sources and the ORDER of rows
in a printed table were never affected -- consecutive rows carried nearly the same shift. What was
wrong is every absolute `t+NNNN`, and any duration measured across a long stretch. **Treat a
timestamp quoted anywhere before 2026-08-12 as an event label, not a time.**

**And the lesson that actually matters: a number only one tool can produce has never been checked.**
All four tools shared the helper, so they always agreed with each other, and nothing disagreed until a
one-off script happened to read `carState` alone -- the one stream with no header replay in it -- and
returned 754 s. When a tool's output has no independent cross-check, go and build one.

## 2026-09-02: TWO LANDMINES UNDER EVERY RLOG TOOL

### `initData.params` IS A BOOT SNAPSHOT. IT CANNOT SEE A MID-ROUTE SETTINGS CHANGE.

`bp_settings_timeline.py` and the drive ledger both claimed it carried a fresh snapshot per SEGMENT,
"so mid-route changes are visible". **False, and the ledger was built on it.**

PROVEN on route 0000040e: `FordHighSpeedFactor_ang` was written at 23:12:32, segment 14 closed at
23:12:33, the route ran to 23:28 -- and all 31 segments report the pre-change value. Sixteen minutes
of driving on a different setting were invisible.

**And the ledger's "00000400 seg 19 was a mid-route change" is WITHDRAWN**: segments 0-18 were never
pulled off the device, so the tool was comparing seg 19 against the previous ROUTE. There has never
been a demonstrated mid-route detection from initData.

**`--telemetry` is the fix and it is EXACT, not an estimate.** At or above 70 mph the speed blend is
saturated, so the schedule collapses and inverts:

    gainLowCurv  == FordHighSpeedDampening_ang
    gainHighCurv == anchor_high * FordHighSpeedFactor_ang

Below saturation both terms are speed-blended and the recovery is impossible, so those frames are
EXCLUDED rather than estimated -- an estimate there is indistinguishable from a real settings change,
which is the whole failure being fixed. On 0000040e it found TWO changes initData missed: high went
0.68 (boot) -> 0.714 -> 0.794. **A drive labelled from initData can be labelled wrong.**

### IMPORTING `opendbc.car.*` AFTER `capnp.load()` KILLS THE INTERPRETER. EXIT 127, NO TRACEBACK.

Measured 2026-09-02 while building the above. `opendbc.car.structs` loads its own capnp schema, and
a second schema load in a process already holding `log.capnp` calls `abort()`:

    import angle_gains alone                 ok
    capnp.load(log.capnp) alone              ok
    capnp.load THEN import angle_gains       exit 127, no output, no Python exception

**`try/except ImportError` catches nothing** -- the process is gone. `opendbc.car.structs` is the
innermost trigger; `opendbc.car.ford`, `opendbc.car.ford.values` and anything importing them all
die the same way.

**EVERY rlog tool loads log.capnp, so NO rlog tool can import a car constant.** Parse it out with
`ast` instead -- `bp_settings_timeline._gain_anchors` reads `GAIN_CAN` / `GAIN_CANFD_BOF` /
`GAIN_CANFD_SUV` straight out of `angle_gains.py`, which keeps ONE definition and adds no import.
Hardcoding 1.15 would be wrong on CAN-FD, which is the defect the shared module exists to prevent.

**And a piped exit code hid it twice.** `python ... 2>&1 | tail -3; echo $?` reports TAIL's status,
so an aborting interpreter reads as success with empty output. Check `$?` without a pipe, or
`${PIPESTATUS[0]}`.

### A MEDIAN OVER A HANDFUL OF WINDOWS IS NOT A MEASUREMENT. 2026-09-03.

The damper was reported as working -- "both columns the right way, which a P-gain trade cannot do"
-- off 0.6-2.4 minutes of qualifying road. Two drives later, SAME settings, same tool:

    0000040e   median  65 crossings/min   range [20-190]   24 windows
    0000041c   median 100                 range [10-230]   13 windows
    0000041d   median 180                 range [30-290]    9 windows

**Three brackets that overlap almost entirely: one distribution, no measurable effect.** The spread
across identical settings was wider than the effect being looked for, and that was not checked
before it was reported as a direction.

**THE FIX IS IN THE OUTPUT, NOT IN RESOLVING TO BE CAREFUL.** `bp_lateral_weave` now prints the
median, the min-max ACROSS WINDOWS, and the window count. A thin row now looks thin. Do the same to
any tool whose verdict is a median: the bracket is what stops a 9-window number reading like a
result. **Do not fix this by inventing a minimum window count** -- that is the guessed-bound failure
recorded for MAPD_V2_STALL_S; show the spread and let it disqualify itself.

**AND "HE HAS NOT DRIVEN ENOUGH HIGHWAY" WAS WRONG -- IT WAS THE TOOL. He asked, which is the only
reason it was checked.** Decomposed per route, on carState:

    route        driving   >=65 mph   ...ENGAGED hands-off   ...& straight
    00000412       7.9        0.0            0.0                 0.0
    0000041a      11.9        0.0            0.0                 0.0
    0000041b      15.5        0.0            0.0                 0.0
    0000041c      17.5        7.3            6.4                 3.2
    0000041d      13.3        4.5            3.6                 1.7

**11.8 minutes above 65 mph and he was ENGAGED, HANDS OFF for 85% of it.** Three routes were
genuinely pure surface driving; two were not. The thin samples were `bp_lateral_weave` discarding
road, not an absence of it: 3.1 qualifying minutes on `0000041c` scored as 1.3, because emitting a
6 s window and CLEARING the buffer binned every remainder shorter than the window. Lane-line
probability was never the constraint (median 0.98). Fixed by sliding the window 3 s instead of
clearing it, and the `min` column now reports road that passed every gate rather than window count
times 6 s, which double-counted the overlap.

**BEFORE TELLING HIM THE DATA IS TOO THIN, CHECK WHETHER THE TOOL IS THE ONE THINNING IT.** A gate
that rejects and a window that discards look identical in the output, and only one of them is about
his driving.

**AND THE CURVE METRIC IS IN THE SAME STATE ON THOSE DRIVES.** 2.35 osc/min at 47% swing on
`0000041c` (1.7 min of curve-holding) against 4.29 at 60% on `0000041d` (0.7 min). The historical
range on this car is 3.43-5.78 at 45-54%, so both readings sit inside it and disagree with each
other by more than any effect. **His recent drives are surface roads** -- every route rejected
6,000-13,000 model frames for hands-on-or-not-engaged -- so neither open question can be scored
from them. It needs sustained highway, and saying so is the answer rather than a smaller number.

### THE WEAVE NUMBERS IN THE LEDGER WERE FROM A TOOL THAT NO LONGER EXISTS

The 2026-09-01 weave table (p2p 0.29-0.44 m, 13.7-20.0 crossings/min) came from an ad-hoc script at
an unrecorded sampling rate. A second ad-hoc script the next day returned 3-4x the crossing rate on
comparable road -- not because the car changed, but because the two counted differently.

**`tools/bp_lateral_weave.py` replaces both**, states its own definitions in its docstring, and
samples at the modelV2 rate (20 Hz) because reading lane position off a 100 Hz stream makes the
crossing count a property of the READER rather than of the road. **Every route quoted against
another must be re-run through it.** Two numbers from two instruments are not a comparison.

## Diagnosing a road report: the tools, and the order to use them

Written 2026-08-11 after an evening where three separate wrong controllers were blamed in turn. All
live in `tools/` and all are READ-ONLY; scp them to the device and run from `/data/openpilot`.

| Tool | Answers |
|---|---|
| `bp_why_slow.py` | who GOVERNED the drive (per-source occupancy) and what caused every slowdown |
| `bp_hold_history.py` | every change to the HOLD, with `baselineSource` naming the mechanism |
| `bp_curve_runaway.py` | slowdowns where VISION chased its own output down, plus the raw steering angle beside both lateral-acceleration derivations |
| `bp_setspeed_hunting.py` | bursts of set-speed reversals, with every source's target |
| `bp_dump_exit.py` | the older exit-specific dump; superseded for anything above 55 mph |

The order that works: occupancy first, then the specific event, then the raw fields. Skipping to the
raw fields is how an evening goes to the wrong controller.

**And do not trust the source label.** See "Facts that have been got wrong before" -- it names a
winner even when every candidate is `V_CRUISE_UNSET`.

## SCC-VISION HAS NO DEFENSES AT ALL, AND ITS TARGET CHASES THE CAR DOWN

Found 2026-08-12 on route 00000365, from the owner's own map of where the events happened. He marked
three spots on I-215/I-80 at the Parley's interchange: two where it slowed far too much, one where it
did not slow enough. All three are freeway curves, and the same controller owned all three.

**The target is proportional to current speed**, from `vision_controller.py`:

```
v_target = v_ego * sqrt(a_lat_reg_max / max_pred_lat_acc)
```

If the model's implied curvature is CONSTANT this converges to a fixed corner speed and is correct --
`max_pred` falls as v² so the ratio cancels. It only runs away when the model's implied curvature
RISES while the car slows, because then every frame re-derives a lower target from the lower speed.
That is what happened; back-calculated from the logged targets and speeds:

```
61.5 mph  target 74  ->  implied radius 382 m
54.2 mph  target 57  ->  implied radius ~230 m
44.1 mph  target 46  ->  implied radius 147 m
```

The model's curve got 3x tighter as the car approached it, and 147 m is not a radius that exists on
I-215 mainline. **Note this derivation uses only `v_target` and `v_ego`.**

**Then the device was reachable and the one flagged event turned out to be a RAMP, correctly taken.**
See the section below. So this mechanism is real in the code and no measured example of it has been
found yet -- a shrinking implied radius is the signature of approaching a ramp too, which is exactly
why SCC-Map excludes ramps from its own vetoes. Do not build a bound on vision until an event is
found where the steering column disagrees with the model's radius.

**And nothing stops it.** Read `_update_state_machine`: the only exits are the model's own prediction
falling. There is no cross-check against the map, no plausibility bound on `max_pred_lat_acc`, no
speed-class floor. SCC-Map got three defenses built from measured events; **vision got none, and
vision is the controller that owns the near field**, so it is the one answering for curve complaints.

## `currentLateralAccel` IS FINE. THE 30x DISAGREEMENT WAS A SAMPLING MISTAKE.

Recorded 2026-08-12 as untrustworthy, then measured on the device the same evening and cleared.
`tools/bp_curve_runaway.py` prints it beside a steering-angle derivation on the same frame, and the
two track each other across every descent on route 00000365. The steering figure reads HIGH by
roughly half at highway speed because the simple bicycle model omits the understeer term.

The original 30x came from comparing two tools' numbers at DIFFERENT INSTANTS -- a peak against a
nearby trough. **Before calling a logged field wrong, print it beside its rival ON THE SAME FRAME.**

**And the conclusion it was used to overturn was right after all.** The 68 -> 37 mph slowdown at the
I-80/I-215 interchange was CORRECT: 16 degrees of steering at 37 mph is a 174 m radius, and the
model's implied radius there was 180 m. Real ramp, appropriate slowing -- if anything ~8 mph more
conservative than his measured comfort. Three positions were taken on this event in one day; the one
that held is the one with two independent measurements agreeing on the same frame.

## EVERY BLUEPILOT BRANCH, CHECKED 2026-09-03. TWO COMMITS WE DO NOT HAVE AND SHOULD KNOW ABOUT.

He asked twice -- "did you take anything else", then "check the dev branches too" -- and the second
ask is what found these. **Releases: newest is `bp-7.0` and we are 0 commits behind it. There is no
`bp-8.0`.** Dev branches on the remote: `bp-dev`, `bp-dev-191`, `bp-dev-expedition`,
`bp-dev-f150-mk14.5`, `bp-dev-mici-ui`, `bp-dev-old`, `bp-dev-rl-ui`, `bp-dev-ui`, `bp-jmc-lane`,
`bp-livedelay-icon`, `bp-no-stall`, `bp-sync-*`, plus two archives.

`bp-no-stall` is 0 ahead. `bp-livedelay-icon` is 4 ahead, one trivial. The platform and UI branches
are other people's cars. **Everything on `bp-dev` touching the Ford lateral path we already have BY
CONTENT** -- the StarPilot guards, the correction rate limit, the interpolation update. Two do not.

### `0cb9165427` on `bp-jmc-lane` -- THE TRIM COMPETES WITH THE PLANNER FOR THE DEVIATION BUDGET

Unmerged, John Christman, 2026-08-27, *"avoid lane positioning at limits and takeover oscillation"*.
**WE HAVE THE ONE-SIDED FORM IT FIXES.** Ours adds the trim into `kappa_cmd` (line ~613) and then
clips the COMBINED value:

    kappa_cmd = self.lane_center_trim.update(...)
    if v_ego > 9:
      kappa_cmd = clip(kappa_cmd, current_curvature -+ CarControllerParams.CURVATURE_ERROR)

Theirs gives the planner first claim and hands the trim the remainder, with the reason stated
plainly: *"a one-sided form lets the trim subtract authority while the planner is already clipped
short in a curve."*

**THAT IS EXACTLY THIS CAR'S REGIME.** Delivery is 0.87-0.93, i.e. measured lags desired, so in a
curve `abs(planner - measured)` is already near the budget and the trim can only push further out
of it -- where the clip cuts the sum, not the trim's share of it.

**IT IS NOT A SAFETY PROBLEM AND IT DOES NOT INVALIDATE THE LC 0.35 DRIVE.** The clip bounds the
total either way, so nothing can exceed measured +- CURVATURE_ERROR. And the straight-road weave
test runs where the planner has budget to spare, so the primary question that drive answers is
untouched. **What it can confound is CURVE behaviour at raised strength**, which matters because
`lane_centering_strength_ang` went 0.15 -> 0.35 on 2026-09-03.

It also carries `_PRESS_RELEASE_S = 0.3`, debouncing `steeringPressed` because *"a 30 ms dip inside
a 1.7 s hold fired a pulse (route 00000399 t=172.04)"* -- a takeover-oscillation guard on the stall
blip. This fork has its own history of `steeringPressed` chatter, so that is worth reading before
anyone re-derives it.

### `fc228b4099` on `bp-dev` -- cruise-button event storm on sustained hold

*"ford: fix combo cruise-button event storm on sustained hold."* Not read in detail yet. **It is
adjacent to a failure this file already spent a day on** -- `update_manual_button_timers` only
zeroing on a RELEASE while this car's SCCM sends one physical press as a burst of PRESS events, which
stuck a hold for 87 seconds. Read it against that section before deciding.

**NONE OF THE FOUR (these two plus `a15672fb15` and `77ec55c73e`) HAVE BEEN TAKEN.** All are
behaviour changes to a branch his car auto-pulls, on a day he is driving, with one experiment
already in flight.

## THE PSCM NEVER REPORTS LIMITING ON THIS CAR. THE SIGNAL IS DEAD ON NON-CAN-FD FORDS.

He asked what came of "the PSCM reports when it is limiting". **It does not, on his car**, and the
`_pscm_lim` clamp in `lateral_angle_ext` can therefore never fire. Established three ways:

1. **`FORD_FUSION_MK5.config.flags` is 2** -- `ALT_STEER_ANGLE` only, `CANFD` (1) NOT set. (The
   `flags 18` quoted elsewhere in this file is the RUNTIME value; `TSR` (16) is added from the
   camera-bus fingerprint, not the static config. Both readings agree that CANFD is clear.)
2. **`Lane_Assist_Data3_FD1` (972) is registered only under `if CP.flags & FordFlags.CANFD`**, so
   it is not in his parser at all -- and `carstate.py` carries the reason in its own comment beside
   the one signal it reads from that message: *"this signal is always 0 on non-CAN FD cars."*
3. **Decoded off his raw CAN anyway: 100% `LimitNotReached` across 61,626 frames** on routes
   `0000041c`/`0000041d`, including 757 frames of ENGAGED, hands-off cornering above 2.0 m/s^2.
   `LatCtlSte_D_Stat` in the same message reads `Unavailable` on 100% of frames, which is the
   sanity check failing loudly: openpilot sets `steerFaultTemporary` when that is not in (1,2,3),
   so a real 0 would mean the car never steers. The message is simply all zeros.

**CONSEQUENCES:**

- **`_pscm_lim = getattr(CS, 'lat_ctl_lim_stat', 0)` is ALWAYS 0** -- and not only because the
  message is dead: **nothing anywhere assigns `lat_ctl_lim_stat`.** The attribute does not exist
  outside two test stubs. So `_in_hard_sat` reduces to `_dbc_sat` alone and the `_pscm_lim >= 1`
  branch is unreachable. This file already recorded "`_pscm_lim` is silent in angle mode" and
  attributed it to ANGLE MODE; the real reason is the PLATFORM, and it would be silent in curvature
  mode too.
- **There is no telemetry on this car that says the PSCM hit its authority limit.** Every claim
  about the ~2.5 m/s^2 ceiling rests on indirect evidence -- our own deviation limiter biting above
  2.5, hands-on% climbing past 3.0, delivery sitting at 0.87-0.93. Those agree with each other and
  none of them is the PSCM saying so.
- **So the torque-ceiling hypothesis cannot be confirmed OR refuted from this signal**, and that is
  worth telling anyone building the interceptor: on a non-CAN-FD Ford there is no limit status to
  instrument against, before or after.
- **Do not "fix" this by registering the message.** It is CANFD-gated upstream for a documented
  reason and would return zeros.

## THE TORQUE INTERCEPTOR IS BLUEPILOT'S, NOT OURS. 2026-09-03.

  *"We just need the torque interceptor and then I'll use SCC."*
  *"The torque interceptor will obviously be made and designed and everything from BluePilot and
  their devs, so we just need to wait for that. I will have little involvement in that."*

**So the PSCM authority ceiling has a hardware answer coming from upstream, and it is not this
fork's work.** Do not design around it, do not propose alternatives to it, do not scope software
that tries to buy authority back, and do not ask him about it -- he has said his involvement is
minimal. **There is no torque-interceptor concept anywhere in this tree** (checked 2026-09-03,
zero hits across .py/.capnp/.h/.md), so if it lands it arrives as new upstream code.

**WHAT IT WOULD CHANGE, worth knowing so nobody re-derives it:** if the PSCM held nearer his
4.1 m/s^2, the corner speeds SCC asks for become acceptable, he stops taking over, and the entire
longitudinal curve programme gets a consumer again. Much of the angle-mode gain tuning would also
need re-measuring rather than carrying forward. **None of that is actionable until it exists.**

## bp-dev-191: WE HAVE MOST OF IT BY CONTENT, AND THREE COMMITS MATTER. 2026-09-03.

He asked whether that branch had been looked at. One commit had been taken from it (`ba20937aac`,
2026-08-29) and it was never re-checked; it is 23 commits ahead of us. **Checked BY CONTENT, because
these arrive under different hashes through the release branch.**

**ALREADY OURS, do not re-take:** the StarPilot guards (`_WIDTH_TOLERANCE_BP/V`,
`_STD_TOLERANCE_BP/V`, the confidence `min()`, the isfinite and monotonic-x checks), the correction
rate limit (`_CORRECTION_ROC_PER_TICK`, which a grep for "rate_limit" misses), and the
`curvature_factor` interpolation update -- our tree already has the `[0.0005, high_gain_boundary]`
ramp with `interp(v, [11.18, 31.29], [0.02, 0.0045])`.

**A first pass reported "we have NONE of the guards" off a grep for the wrong constant names.**
Reading the file settled it in one command. Grep for the CONCEPT and then read, or the report is
about your pattern rather than the code.

**NOT OURS, and the first one moves numbers already given to him:**

| commit | what | effect |
|---|---|---|
| `a15672fb15` | `high_gain_boundary` `[0.02,0.0045]` -> `[0.015,0.0035]` | the ramp gets SHORTER, so the same high factor is ~33% steeper |
| `77ec55c73e` | blend `b` tapers to 0 across `_VLT_V_LOW/HIGH_MS`; `_kappa_entering` 1.25 -> 1.1; `_desired_falling` 0.8 -> 0.9 | pure `desired` at highway speed |
| (part of `94bf8144d4`) | `_MAX_APPLIED_CORRECTION = 0.0015` on the POST-gain correction | binds above `lane_centering_strength_ang` 0.375 |

**THE BOUNDARY CHANGE, quantified at 85 mph with his damp 0.78:**

    high 0.794   slope +33 (ours today)  ->  +44 with the commit
    high 0.850   slope +49               ->  +66
    high 0.900   slope +64               ->  +85
    the MEASURED-BAD +68 arrives at high 0.915 today  ->  0.856 with the commit

So the "stop at 0.90" ceiling handed to him on 2026-09-03 is correct for the code his car runs and
becomes ~0.85 if that commit is taken. **Taking it silently would invalidate a number he is tuning
against.**

**AND THE 0.0015 CAP QUALIFIES THE LANE-CENTERING ADVICE GIVEN THE SAME DAY.** Applied correction is
`_MAX_RAW_CORRECTION` (0.004) times gain:

    LC 0.15 -> 0.0006      LC 0.35 -> 0.0014      LC 0.50 -> 0.0020      LC 0.55 -> 0.0022

**0.35 sits just under the cap; 0.5 and 0.55 exceed it.** Upstream's own comment says it "stops a
fake stuck steering rack from happening, because it is under the curvature error and stall gap" --
a failure mode, not a comfort number. He ran 0.55 for the whole 600-mile drive with no complaint,
so it is not obviously dangerous on this car, but **"0.5 is well supported" was said before this cap
was known and should not be repeated without it.**

## HE IS NOT USING SCC-VISION OR SCC-MAP. 2026-09-03, unprompted.

  *"I am not really using SCC Vision or even map. They work great, but since the PSCM needs to go
  so slow, I just take over, if you get what I mean."*

**HE IS NOT REPORTING A BUG. He is saying the feature is correct and unusable, which is a harder
problem and a different one.** Do not respond to this by tuning a sensitivity, adding a defense, or
touching `SmartCruiseControlMapFactor`. The controllers are doing what a car with this PSCM should
do; the PSCM is why the answer is unacceptable.

**THE CHAIN, entirely from measurements already in this file:**

    the PSCM comfortably holds       ~2.5 m/s^2      a 259 m corner -> 57 mph
    openpilot's own p99               2.73                        -> 59 mph
    HE drives that corner at          ~4.1                        -> 64 mph, measured

So SCC slows correctly for a bend the PSCM cannot hold at his speed, he does not want to be that
slow, and he takes the corner himself -- longitudinally AND laterally, because at his speed the
PSCM cannot track it either.

**IT IS VISIBLE IN THE DRIVE DATA AND WAS MEASURED BEFORE HE SAID IT.** Hands-on above 65 mph:
13% on `0000041c`, 19% on `0000041d`. That is this, and it is the same signature already recorded
years-ago-in-fork-time as *"hands-on% climbs the same curve -- 6% low, 90%+ above 3.0 -- so he TAKES
OVER exactly where the PSCM starts losing the line."*

**WHAT IT MEANS FOR PRIORITY, and this is the point of the entry:**

- **SCC-Map and SCC-Vision tuning currently has NO CONSUMER.** The veto defenses, the corner-speed
  factor pair, the late-detection work, the exit-ramp problem -- all of it improves a feature he
  drives around. Do not spend effort there and do not open a session with it.
- **THE LATERAL WORK IS THE THING THAT UNLOCKS THEM.** If the PSCM held a corner nearer his 4.1,
  SCC would be asking for a speed he would accept, and he would stop taking over. That makes the
  angle-mode tracking work upstream of the entire longitudinal curve programme rather than a
  comfort project running beside it.
- **Do not switch SCC off for him.** He said it works and he did not ask. It is his toggle, and a
  feature he ignores costs him nothing while a setting changed under him costs trust.

**AND IT RETIRES A STANDING QUESTION.** "The PSCM angle-mode authority limit is a CAR FACT nobody
here has measured yet" has been open since 2026-08-19 as the number blocking the tile-curvature
corner-speed work. That work is now pointless until the authority itself moves: a better corner
speed still lands under his comfort, and he still takes over.

## SCC-Map has four defenses now, and they are deliberately different questions

Built up across 2026-08-10 and 2026-08-11 from measured events. They stack, and the split between
them is what keeps exits working:

1. **The corner-speed factor pair.** `SmartCruiseControlMapFactor` (tight, <= 25 mph) and
   `SmartCruiseControlMapHighSpeedFactor` (highway, >= 45 mph), blended on the CORNER's speed, not
   the car's. A ramp is a 25 mph corner entered at 75 and a sweeper is a 50 mph corner entered at 75;
   keying on vEgo cannot separate them.
2. **The camera veto, absolute.** The model sees no curve at all (`< MODEL_DISAGREE_LAT_ACC`). Ramps
   keep a conservative 4 s horizon; highway corners get the model's real 10 s reach, because
   `max_pred_lat_acc` is a percentile over the whole modelV2 plan and 4 s made the veto unreachable
   at highway speed.
3. **The camera veto, relative.** The model sees a curve, but a far gentler one than the map claims.
   Highway corners only.

4. **The camera has not been able to look yet.** A HIGHWAY corner still beyond the model horizon is
   suppressed until it comes into view. Added 2026-08-12 because defenses 2 and 3 were
   STRUCTURALLY UNREACHABLE, not merely quiet: SCC-Map publishes the corner speed exactly when
   braking must BEGIN, so on route 00000365 a 50 mph corner was acted on at 467 m against a 353 m
   horizon, and `_model_disagrees` returned False at its distance gate before either test ran. It
   walked the set speed 79 -> 64 with nothing able to question it.

   The dead band was wide. A veto is only reachable when the braking distance fits inside the
   horizon -- `(v1^2 - v2^2) / 2a <= 10 * v1` -- so at 79 mph it protected corners of 58 mph and
   faster, while being off below 45 mph as ramp-like. **45-58 mph, the band that produces the
   biggest slowdowns, had no protection at all.** The cost of waiting is bounded: 79 -> 50 within
   353 m needs 1.06 m/s^2 instead of 0.8, which ICBM delivers.

`_MAP_FACTOR_V_BP[1]` (45 mph) is the single definition of "highway corner" for all four --
referenced, never duplicated.

**Why ramps are excluded from 2 and 3.** On an exit the model predicts the path it expects to drive,
straight down the highway, so a ramp's curvature may never enter the plan until the car is on it.
Camera silence there is blindness, not evidence. That is also why vetoing is safe where it does
apply: it removes only the MAP's contribution, and SCC-Vision keeps running as the near-field expert.

## Facts that have been got wrong before

Each of these was asserted confidently from reasoning and turned out to be false. Check the source.

- **There are two "set speeds" and they legitimately disagree.**
  `carState.cruiseState.speedCluster` (m/s) is the car's dash number, the one ICBM's buttons move.
  `carState.vCruiseCluster` (kph) is openpilot's own v_cruise, and under ICBM
  (`pcmCruiseSpeed` False) it tracks DRIVER button presses only. Reading the second as "the set
  speed" produced three wrong conclusions in a row. In any diagnostic, print both, labeled.
- **`SmartCruiseControlMapDecel` is a TRIGGER DISTANCE, not a rate.** `map_controller.py` publishes
  the corner speed as a step, at exactly the moment braking must begin. A gentler value makes the
  target appear EARLIER, not the slowing gentler. Anything that re-paces it downstream spends road
  that was already budgeted -- which is what ICBM's drop limiter did on freeway exits.
- **There is no MS-CAN on this car.** openpilot can read body state on bus 0 but can never command
  it. Route anything needing body actuation to FORScan instead of designing around it.
- **The Fusion IPC is LKA-only.** TJA, LCA, BlueCruise and Driver Alert draw nothing on his cluster;
  the LKA states are the entire vocabulary available.
- **"Too slow in curves" was SCC-MAP, not SCC-Vision.** Measured on route 00000338, 2026-08-10:
  sccMap was the plan source and had driven the dash to 43 mph before vision said anything. Three
  separate changes to the vision factors were aimed at a controller that was not asking. Attribute
  the source before touching a sensitivity -- `tools/bp_why_slow.py` does it, and vision was only
  7.9% of that whole drive.
- **The vision factors barely apply between 30 and 60 mph.** `_SENSITIVITY_V_BP` blends
  `SmartCruiseControlVisionLowSpeedFactor` into `...HighSpeedFactor` across 30-60 mph. With low at
  100 and high at 80, a bend taken at 45 mph gets ~0.90, not 0.80. Changing only the high factor does
  almost nothing to a 40-55 mph corner, which is the range most complaints have been about.
- **A single vision target frame can be a large outlier.** On that bend vision asked 59, then 36, then
  55 on consecutive samples. ICBM chases the minimum, so one frame set the number. Do not read a
  single logged target as the controller's estimate.
- **`longitudinalPlanSource` NAMES A WINNER EVEN WHEN NOBODY ASKED.** It is
  `min(targets, key=...)` over every candidate, so when they are all `V_CRUISE_UNSET` it still
  reports one -- on route 00000348 it read `sccVision` for 40 s while vision was inactive and asking
  for nothing. Check `smartCruiseControl.<x>.active` AND the published `vTarget` before believing the
  source label. 570 mph in a diagnostic is 255 m/s, which means "not asking".
- **A diagnostic that prints `--` for both "inactive" and "active with no target" hides the only
  distinction that matters.** Two tools were written that way and both pointed at the wrong
  controller. Print the raw fields.
- **Speed Limit Assist stays `is_active` on a road with NO speed limit data.** It is not a proxy for
  "SLA has a number". Anything gating on it must also check that its target is real, or a stretch
  with no map coverage silently removes the cruise baseline from the planner.
- **Ford's angle gains are a PSCM calibration, not a detune**, and the take-over alert that looks
  like they cause is a tracking-lag false positive.

**Tests do not substitute for reading.** A review found four real bugs under 931 green tests, and
each hid where a fixture held something constant. Stubs are the specific danger: `FakeParams.put`
accepted any type, so the suite was more permissive than the device and a `TypeError` firing on
every real write was invisible. A stub laxer than the thing it stands in for hides exactly the bugs
it was built to catch.

## SIGNALLING ALREADY SUPPRESSES FORD'S ACC BRAKING -- MEASURED, AND IT MATTERS TO PASSING ASSIST

Measured 2026-08-14 across routes 00000365, 0000036b and 0000036f -- 92,000 frames with a lead
inside 80 m, engaged, above 18 mph:

                    frames    ACC braking %    mean gap (s)
    blinker ON       9,277           4.3%            2.00
    blinker off     82,550          18.9%            2.14

**Stock Ford ACC brakes about four times less often while the blinker is on.** The owner suspected
this from the seat and was right. It is correlational -- he signals when he intends to pass, and
passes are situations he is accelerating through anyway -- but a 4.4x difference is far too large to
be selection alone, and Ford documents overtake-aware ACC behaviour on some models.

**WHY PASSING ASSIST CANNOT JUST ASSUME IT GETS THIS FOR FREE**, which is the owner's own point and
the sharp part: passing assist ACTUATES the blinker itself, over CAN. The measurement above is of
the DRIVER moving the stalk. If Ford's ACC keys off the stalk position or a body-module signal rather
than the lamp state, a CAN-injected blinker may produce the lamps without the ACC behaviour -- the
suppression would silently not happen, and the pass would be made into a car that still brakes for
the vehicle being overtaken.

**That is a measurement, not a guess to make.** Compare braking rate during passing-assist-commanded
blinker against driver-stalk blinker, using the same query shape as above. If they match, the gap
button may be unnecessary. If they do not, that is the strongest argument for it.

**And the owner's judgement is that it is not enough on its own:** "I don't think it closes the gap
enough." So the likely answer is BOTH -- signal for the lane change, and reduce the gap for the
duration of the maneuver -- with the caveat below.

**THE GAP BUTTON IS CLOSED-LOOP, and an earlier version of this section said otherwise.** It claimed
`AccTGap_D_Dsply` came from the GWM and was therefore unreadable, so commanding a gap meant counting
presses open-loop against a state nobody could see. **That was wrong twice over**, and the passing
assist session caught it:

- `BO_ 394 ACCDATA_3: 8 IPMA_ADAS` -- the CAMERA sends it, not the gateway. GWM is a receiver on that
  signal line, which is what the DBC line was misread as.
- `opendbc/car/ford/carstate.py` **already registers ACCDATA_3 at 5 Hz** and reads other signals out
  of it. Nothing new has to be subscribed.
- And the owner's GWM ruling was about FLASHING FIRMWARE AND AS-BUILT, never about reading a
  broadcast frame. Stretching it to cover a message we already parse was my error, not his rule.

So: press, read the resulting setting, repeat. The "one missed press leaves the car following closer
than the driver chose" objection dissolves entirely -- the loop closes on the next ACCDATA_3 frame.
Five settings (`Time_Gap_1..5`) and a single cycling button on his current wheel are still true, and
still mean a specific gap takes up to four presses, but that is bounded work rather than an
unverifiable assumption.

**Before scoping the gap button at all, though, try his own idea:** *"we may not have to pass cars as
far away. We can get a little closer and wait for ACC braking and then pass."* The 63% figure above
makes ACC's own deceleration a reliable trigger, and once the car is laterally clear the radar drops
the lead and ACC accelerates on its own. No gap command, no press counting, no new signal.

**AND THE SUPPRESSION QUESTION IS NOT MOOT DURING THE MOVE -- I argued it might be and was wrong.**
The reasoning was that the driver's stalk would be involved by then. It will not be, ever: *"LANE
CHANGES WILL NOT BE STARTED BY MY STALK"* and *"if I had to manually do anything, then I might as
well just keep using the SunnyPilot nudgeless lane changes."* The finished maneuver decides, signals
and crosses with no stalk input, so the crossing is precisely where the ONLY blinker is the injected
one.

What that costs, stated as passing assist put it: **a worse pass, not a dangerous one.** Once
laterally clear the lead drops and ACC accelerates regardless. The exposure is the crossing itself,
and the failure is a slow overtake. But "better passes than I can make" is the goal, and a pass that
decelerates through its first half is worse than his own.

**AND THERE IS NO ROUTE OUT. THE OWNER HAS CLOSED THE ONLY ONE.** An earlier version of this
section, written hours before this one, called the SCCM stalk-contact tap from `blinker_test_ext.py`
"the route out" and noted the rear-radar board has a spare switched output, so the hardware might
arrive anyway. He has since ruled steering-column wiring too invasive. That is a decision, not an
obstacle to route around: **do not propose it again**, and do not treat the suppression finding as
temporary. The injected blinker is what this feature has, permanently.

So the honest state of it: passing assist actuates a blinker the camera does not see as a stalk, ACC
therefore keeps its full following distance through the crossing, and the first half of a commanded
pass is slower than the same pass made by hand. That is the cost, it is fixed, and the only lever
left is the follow gap itself.

**EXCEPT THAT "THE CAMERA DOES NOT SEE IT AS A STALK" WAS NEVER MEASURED** -- caught 2026-08-17. The
measurement is named twenty lines above, as a thing to do, and no result was ever recorded. The
paragraph that reads like the closing argument rules out the stalk-contact TAP, which is a hardware
decision, not evidence about the camera. A conservative assumption hardened into a stated fact, and
it is the premise for "the only lever left is the follow gap".

The mechanism makes it genuinely open: we send the same field the stalk sends, and the lamps prove
the BCM accepts our frames -- but the DBC lists `IPMA_ADAS,PSCM` as the other receivers of
`TurnLghtSwtch_D_Stat`, and they see the SAME alternation with the gateway. Whether that reads as
"signalling" is a debounce question, not a signal-authenticity one.

And it is answerable from logs, not a drive: `ford/carstate.py:232` reads the blinker back off bus 0
through a parser that updates on every frame, ours and the gateway's. **Compare the duty cycle of
`carState.leftBlinker` during a COMMANDED blink against a stalk blink.** Near 100% kills most of the
hypothesis; near 50% quantifies exactly what a fix would have to beat.

Full write-up, including the two gap-controller contract changes: `bluepilot/BLINKER-ACC-SUPPRESSION.md`.
Either way it stays a supplement -- 2.14 -> 2.00 s is 4 m, and he has already said that is not enough.

**Which is why the gap button matters more than it looked.** It is the one remaining way to buy room
before a pass on this car, and it is closed loop -- see below.

### The follow-gap button (built 2026-08-14)

`opendbc/sunnypilot/car/ford/gap_control.py`, pressed from `ford/icbm.py`, requested via
`longitudinalPlanSP.accGapRequest` -> `selfdriveStateSP...gapTarget` -> `CC_SP`. Off by default
(`IcbmGapControl`).

Three things about it are worth remembering because each replaced a guess:

- **`Steering_Data_FD1` carries THREE gap signals**, not one: `AccButtnGapIncPress`,
  `AccButtnGapDecPress` and `AccButtnGapTogglePress`, all received by IPMA_ADAS. His wheel only has
  the cycling button -- but the wheel is not what authors this message, we are. Whether the camera
  honours inc/dec from an injected frame is UNPROVEN, so the controller probes it on its first press
  of a drive and falls back to toggle if nothing moves. Same for which direction the numbers run.
- **Panda does not gate these bits.** `ford.h`'s Steering_Data_FD1 tx_hook checks only cancel and
  resume, so gap presses go out regardless of `controls_allowed`.
- **The lease is ASSERTED, never timed.** The requester asks every frame it still wants the gap;
  silence restores. A dead planner or a dead selfdrived therefore restores by itself, which no
  stored deadline could guarantee. `MAX_LEASE_FRAMES` exists only for a request wedged ON, and after
  it fires the request must drop to zero before another is honoured.

The driver outranks all of it: their own press, or any gap movement we did not command, ends the
lease on the spot and is NOT pressed back over.

**What is still unknown is the only thing that matters:** whether the camera accepts an injected gap
press at all. It cannot be settled offline and there is no requester yet, so the first real passing
assist request is the experiment. The controller diagnoses itself and gives up safely, and every
transition is `cloudlog.warning`ed as `ICBM gap: mode=... result=...` so the answer is readable off
a route rather than inferred.

**And nobody knows what gaps 1-5 ARE.** He set his by feel and thinks 3/5 is about two seconds.
`tools/bp_gap_seconds.py` measures it from any route -- headway during steady following only, since
frames where the set speed binds contain no information about the setting and averaging them in
produces a plausible-looking number that means nothing. `carStateBP.accGap` now logs the setting;
the tool falls back to decoding address 394 byte 4 low-3-bits for routes recorded before that.

## Capnp field numbers across branches -- the tiebreaker is WIRE HISTORY, not base branch

Two branches added fields to the same structs on 2026-08-14 and both collided. I had written that
ICBM owns the numbering because it is the base the others rebase onto. **That is wrong, and passing
assist renumbered mine instead. They were right:**

  - `accGapRequest @9` collided with `passingAssist @9`  -> mine moved to @10
  - `accGap @4` collided with `blisLeft @4`              -> mine moved to @9

**capnp reads by POSITION.** `passingAssist` and `blisLeft` are already in every route log on the
device, so renumbering THEM makes every recorded drive decode as garbage. My fields had never run
anywhere. A field with wire history outranks a field on the base branch, every time.

**Git will not catch this.** Neither branch touched the other's lines, so the merge is clean and
silent. And capnp does not raise on a bad numbering space -- it calls `abort()`, so the whole suite
dies at import behind a traceback that names pytest and never mentions a schema. Verified here: a
struct with an ordinal GAP kills the interpreter with no Python-level exception at all, exit 127,
`except` never runs. `test_capnp_ordinals_unique.py` on the passing-assist branch guards it.

**The numbers CANNOT be mirrored back onto this branch, and that is not stubbornness.** capnp
ordinals must be contiguous from 0. `CarStateBP` here ends at @3, so a new field can only be @4;
@9 is reachable only once blisLeft and its neighbours exist, which happens at rebase. So the
renumber is inherently a rebase-time operation.

**Therefore: declare new shared capnp fields on the branch that will OWN them, and let the consumer
rebase onto it -- but never renumber a field that has already been recorded.** When a collision is
unavoidable, the branch whose field has never been written to a log is the one that moves.

## 2026-08-23/24: THE NIGHT I PUT HIM IN THE ROAD. READ THIS BEFORE SHIPPING ANYTHING.

*"It took an exit so fast I almost slid off the fucking road!"* — 5.20 m/s^2 lateral at 70.6 mph on
route 000003b6. For scale, from this file's own measurements: openpilot's p99 is 2.73 and HIS p99
with hands on the wheel is 4.14, max 4.20. **5.20 is harder than anything ever recorded on this
car.** Then: *"Are you tried to kill me, claude?!"*

Four separate defects were live in one build, three of them mine from that same day.

### THE RULE THAT WOULD HAVE PREVENTED ALL OF IT

**HE STATED THE CORRECT RULE BEFORE I SHIPPED THE WRONG ONE, AND I WROTE HIS RULE DOWN AND SHIPPED
THE OPPOSITE ANYWAY.**

  *"when e2e is being used ICBM shouldn't be trying to change its speed, but preparing for if we
  recover Ford ACC once we are done with e2e"*

I recorded that in this file, called it "a better rule than the one that shipped this morning", and
left the freeze in place. The freeze is what stuck the set speed at 25, 27 and 35 against SLA
targets of 35, 35 and 40 — and then stuck it HIGH on the approach to an exit.

**When he states a rule, implement THAT rule or ship nothing.** Recording his correction and
shipping the opposite is the worst failure mode in this file, because it converts his review into
documentation of a bug rather than prevention of one.

### FREEZE IS NOT A SAFE DEFAULT FOR AN ACTUATOR

The suppression was measured and well-motivated — 378 ICBM button frames and 84 mph of dash travel
in one inert window, hunting a set speed that governed nothing. Suppressing it was still wrong,
because **an actuator that stops actuating does not go neutral, it holds its last value.**

Then the "fix" made it worse: blocking only DOWNWARD presses cured being stuck low and left being
stuck high completely unaddressed — and down is the direction an exit ramp needs.

**Any change that can stop an output from moving must be checked in BOTH directions, and the
dangerous direction is whichever one the road needs urgently.** Reverted entirely; the cost is
hunting on an inert drive, which is annoying, and the thing it buys back is the set speed always
being able to come down, which is not.

### DO NOT SHIP TO HIS BRANCH ON THE DAY HE DRIVES

I pushed to `passing-assist-phase1` repeatedly while he was driving, including a change that made
the failure worse. **The pushes did not reach him** — the reflog is unambiguous, the device pulled
at 01:58 and not again until 03:10, and all four drives ran `a4eb6127b1` — so it was reckless
rather than harmful. That is luck, not judgement.

**And I flip-flopped on this twice in one conversation** before checking `git reflog` on the device,
which settles it in one command. He had to tell me to stop. **Check the reflog; do not reason about
the updater.**

The rule: on a day he is driving, a behavior change to the branch his car tracks needs a drive of
evidence behind it, not a green suite. Commit locally, hand him a SETTINGS-level mitigation, and
push when he is parked.

### THE TWO DEFECTS THAT WERE NOT MINE, AND HOW THEY COMBINED

- **`SpeedLimitPolicy` -> 4 (Combined) at 02:24, mid-drive.** Combined is `min(car, map)`, which
  admits the CAR source. Reasonable of him: we had spent the day on TSR and he thought it was done.
- **The IPMA as-built write made TSR emit a phantom 80.** `vLimit1 = 80` for 16,171 frames on b6,
  read at 42 mph on **S 2165 E, a surface street**. He is certain he passed no 80 sign, and there
  is none there. Across the previous 20 drives TSR was never anything but 255 or 30.

Neither is dangerous alone. Together, on roads where the map is quiet, the car source won outright
and SLA took **80** — so ICBM drove the set speed up toward 80 and he entered an exit with it still
unwinding through 57. **That is the "TSR 80 leak" this file closed on 2026-08-19, re-opened.**

**TSR STAYS QUARANTINED BEHIND `SpeedLimitPolicy = 1` UNTIL IT EARNS ITS WAY OUT.** The bar is
several drives of readings that are CORRECT, not merely present. A rare correct value is useless; a
wrong one is worse than useless the moment anything consumes it. I left "then we work out the 80" as
a someday item instead of saying that plainly, and this is what someday cost.

### AND THE INSTRUMENTATION EARNED ITS KEEP IN ONE DRIVE

`RECOVERY DECLINED` printed `cancel_is_ours=False` — the gate that has blocked the cancel recovery
every time, unknown for two days. **On the same drives I had measured the opStop-to-inert gap at
4.99 s and concluded FROM THAT TIMING that attribution passed, and published it as a finding
twice.** An interval measured either side of an event does not tell you what a counter inside it
did. **When a rule cannot be explained from a drive, add the log line rather than a third
inference.**

## 2026-08-24: WHAT THE INSTRUMENTATION ANSWERED, AND TWO MEASUREMENT TRAPS

### THE CANCEL RECOVERY WAS BLOCKED BY ATTRIBUTION. IT PRINTED THE ANSWER.

    RECOVERY DECLINED: cancel_is_ours=False longActive=True
                       stop_override_stopped_us=False recovery_frames=0/1500

**On the same drives I had measured the opStop-to-inert gap at 4.99 s and concluded FROM THAT TIMING
that attribution passed -- and published it as a finding twice.** An interval measured either side
of an event does not tell you what a counter inside it did. One log line settled in a single drive
what two rounds of inference got wrong.

Route `b5` had THREE inert episodes, not the one I had been reasoning about:

    ep 1  override  8.99 s -> inert 70.3 s   gap  4.99 s  attribution PASSED
    ep 2  override  2.29 s -> inert 31.3 s   gap 20.93 s  attribution REFUSED
    ep 3  override 11.91 s -> inert 45.6 s   gap 18.17 s  attribution REFUSED

Two refusals, two DECLINED lines, matching exactly -- which is what makes the fix evidence rather
than a guess. The 3 s window is too brittle because ANY non-cancel refusal resets the run, so one
band clip restarts the clock and the next run opens too late to be attributed.

**`override_since_camera_clean` replaces it**: set while the override has the car, CLEARED the
moment the camera's own frame is admissible again. The permanent `override_ran` bool was tried and
rejected years-ago-in-fork-time for latching a whole drive; this is that idea with the missing half,
so it cannot span a healthy period and cannot mask a later independent cancel.

**AND THE NARROWER VERSION IS WRONG -- it was written, tried, and reverted the same hour.** Clearing
on "the camera is not asserting cancel" instead of "the frame is admissible" fails because between
the override and the cancel run he is DISENGAGED, where `passthrough_admissible` returns "openpilot
longitudinal inactive" -- not a cancel. The narrow rule clears the flag there and re-blocks exactly
the episodes it was meant to rescue. `test_a_late_cancel_run_after_our_override_is_still_ours` is
what caught it. **Do not narrow it again.**

**EPISODE 1 IS STILL UNEXPLAINED.** Attribution passed, the bands were clean across all 7,032 camera
frames of the window, every gate satisfied -- and recovery never ran and logged NOTHING, because a
refusal from `passthrough_admissible(allow_cancel=True)` INSIDE the recovery body was silent. That
path now logs `RECOVERY BLOCKED BY THE FRAME`. Likely one of the unpoliced bits; the next drive
names it.

### `cluster_moved_since_press` IS A PAIR WITH `v_cluster_at_press`. RESET THEM TOGETHER.

The latch means "the cluster has moved since THIS anchor". The press path reset both; the inferred
fallback and the pinned-hold path each re-anchored WITHOUT clearing it, so a stand-down armed by
either began with `moved` already True and `settled` could fire on the first stable frame -- ending
the stand-down mid-gesture, which is verbatim the failure it exists to prevent.

Found in code review hours after shipping the latch, in the hold path he had reported four times.
`test_the_moved_latch_resets_when_the_anchor_moves` asserts the INVARIANT per function rather than a
symptom, because the symptom needs an exact interleaving and the invariant does not.

### TWO MEASUREMENT TRAPS, BOTH SELF-INFLICTED THE SAME NIGHT

**`vTarget` IS POST-BASELINE. MEASURING ITS TRAVEL MEASURES HIS THUMB.** While a hold is active it
EQUALS the hold by construction. A tool reading it reported "27x jitter" under `ford` authority that
was him pressing buttons while `vTargetRaw` sat steady at 22 and both curve controllers were
inactive. The capnp comment above `vTargetRaw` states this in as many words. **Any question about
what the PLAN is doing reads `vTargetRaw`; `vTarget` only answers what ICBM is aiming at.**

**A TEST THAT COUNTS ASSIGNMENTS MUST COUNT THE RIGHT VALUE.** The latch invariant test was vacuous
twice: first a line-proximity heuristic that flagged two correct sites because a comment sat between
anchor and reset, then a per-function tally that counted the latch's own `= True` as a reset, so
deleting a real one still passed. **Mutation testing is the only reason either was caught** -- both
were green against the bug they were written for.

## Working with the owner

- **READ THE MODULE BEFORE EXTENDING IT.** These files carry long design docstrings recording what
  was tried, what failed on the road, and what was settled -- `blinker_test_ext.py` opens with a
  summary that answers most of what a new feature needs to know about commanding the signal. On
  2026-08-09 I asked him a series of questions and proposed a design that were all already answered
  in the tree, and he escalated three times before: *"I'm really mad you don't remember any of what
  we did before, even though all the code is right in front of your fucking face."* The failure was
  searching for CONFIRMATION of what I was about to build rather than reading what was already
  built. grep for the concept before adding a field, param or constant, and check whether a later
  section of a long plan document supersedes the one you are editing. When he says "we already did
  this", stop and go read.
- **He reports, I tune.** On-road reports are tuning input, not complaints to work around. His
  observation of his own device beats my inference about it every time.
- **Don't check in.** He has given open-ended permission; pick the work and report it done rather
  than closing with "want me to...".
- **AND DON'T LEAVE WORK PARKED**, which is the same failure from the other side. Stated on
  2026-08-10: *"After I ask something like 'Is there anything else you want to do before my next
  drive?', you should say no, because you should have done everything. You shouldn't wait for me to
  follow up. Why aren't you just doing everything?"* That question should always be answerable with
  a plain no. Before reporting, sweep: is every finding acted on, is every number that was added
  actually rendered somewhere, is the device updated, is anything still sitting in a note as "worth
  doing later" that could be done now. Finding a real problem and describing it instead of fixing
  it is not a report, it is a handoff he did not ask for.
- **Shell commands are fine.** What he dislikes is running them *in the car*, in 100-degree heat.
  Diagnostics he can SSH into at home are welcome; "run this on your next drive" is not.
- **Talk about the finished system.** Do not preface answers with what does not actuate yet; he is
  always describing the finished behavior. For passing assist that system **decides, signals and
  makes the lane change with no stalk input** -- *"LANE CHANGES WILL NOT BE STARTED BY MY StALK"*,
  and *"if I had to manually do anything, then I might as well just keep using the SunnyPilot
  nudgeless lane changes"*. The decision is the whole feature; nudgeless already does a crossing
  the driver chose. Describing it as advisory is not the cautious version of it, it is a different
  product he already has. Note this does not change the SAE level: automating the decision leaves
  it Level 2, because the level is set by who monitors and who carries liability, not by how much
  the car does. **This is about answering HIM, and does not extend to documentation** -- see the
  README section above, where the opposite applies, because a stranger reading the README has no
  way to know what is scaffolding.
- **KEEP REPLIES SHORT.** 2026-08-19: *"Wall of text..."* Lead with the answer in a sentence or two.
  The reasoning, the measured tables and the ruled-out alternatives belong in the COMMIT MESSAGE and
  in this file, not in the chat reply -- he reads those when he wants them. A finding is not more
  credible for being longer, and burying the one thing he has to do inside six paragraphs means he
  has to go looking for it.
- **23 controls on the settings screen is not a usability problem.** Do not consolidate unless asked.
- **DISK SPACE IS NOT A FINDING. STOP REPORTING IT.** *"Dude, you tell me this all the time. Of
  course it won't get filled up."* -- 2026-08-26, after it was raised twice in one afternoon and
  then "corrected" into a third framing that was still about disk. `deleter.py` removes oldest
  routes to hold 5 GB free and has never failed to. A `df` reading is not evidence of anything, and
  neither is "you only have N hours of logs" -- retention is total space over the write rate, which
  is a constant of the device, not news. Same shape as the athenad websocket churn: **a check that
  fires every drive forever is how a real finding gets scrolled past.** Do not open with it, do not
  close with it, do not derive an urgency from it.
- **Report test results only when the result is news.** No sign-off with a suite total every message.
- **Changes made on one branch reach the others because he rebases every time.** So CLAUDE.md is the
  channel that actually travels between sessions; per-directory memory is not.

## 2026-08-26: THE SET SPEED WENT TO 80 AT A STOP. A `possible` WAY MATCH DID IT.

*"On my second most recent drive today at one point at the beginning my speed went to 80 while it
was at a stop and then went back down to 35."*

Route 000003c4, t+74, stopped at 1 mph on 1300 East -- secondary, 30 mph posted:

    t+72-73   waySelectionType fail       unknown      no limit
    t+74      waySelectionType POSSIBLE   MOTORWAY     "Dwight D. Eisenhower H"   70 mph
    t+75      waySelectionType fail       unknown      gone
    t+78      waySelectionType possible   secondary    "1300 East"                30
    t+79+     waySelectionType current    secondary    "1300 East"                30

**ONE SAMPLE matched him to I-80 while he sat still on a surface street.** The resolver took the 70,
`vTargetRaw` and MAX went to 80, his 35 hold CLEARED, and ICBM pressed `increase` -- dash 35 -> 48 in
three seconds -- before the map corrected itself and it walked back to 35.

**IT WAS NOT PINNED HOLDS, AND HE REASONABLY SUSPECTED THEM** because he had turned them off that
morning at 07:38 and the drive began at 07:37. Ruled out by reading `match()`: it returns 0 when
`self.pins` is empty, and `IcbmPinnedHolds` has been `[]` since 2026-08-11. **A pin has never once
been created on this car.** It was not TSR either -- `SpeedLimitPolicy = 1` excludes the camera.

### THE FIX: `possible` JOINS `fail` AS AN UNTRUSTED MATCH

`MapdV2MapData` already refused `fail`. `possible` is mapd saying it is not sure which way this is,
and the real road never settles there -- 1300 East went `possible` -> `current` four seconds later
and stayed. Measured across c4 and c5, 25,556 mapdOut frames:

    current    21,660   81.7% carry a limit    <- untouched
    fail        2,311    0.0%                  <- already refused
    predicted   1,080   79.2%                  <- untouched
    possible      309   46.3%                  <- REFUSED NOW
    extended      196   58.2%                  <- untouched

**143 limit-carrying frames of 17,952 -- 0.8% of coverage.** `predicted` and `extended` are
deliberately NOT refused: they are mapd projecting along a way it HAS matched, and refusing on
suspicion costs coverage for nothing.

**AN EXISTING TEST BLESSED `possible` AND HAD TO BE REVERSED.**
`test_every_confident_selection_still_passes_through` read "the gate must catch `fail` and nothing
else -- `extended` and `possible` are still matches", written before any drive said otherwise. One
did. A test changed to permit new behaviour must say why, or the next reader cannot tell a fix from
a regression.

**AND THE DAMAGE OUTLIVES THE LIMIT, which is the general lesson and the third instance.** The map
was wrong for half a second; the SET SPEED was wrong for ten, because ICBM had already converted the
limit into button presses and those do not come back. Same shape as the TSR phantom 80 on 000003b6.
**Rejecting a bad read matters far more than un-latching one.**

### AND HIS RESUME REPORT IS NOT OURS

*"On the most recent drive, auto resume after the lead vehicle departed didn't work, leaving my
cruise cancelled."* Route 000003c5:

    t+173   0 mph   standstill True    lead 4 m, lead speed 0     engaged
            ...20 s stopped, lead stationary...
    t+193   0 mph   resume     True    lead 4 m, lead speed 2     <- lead moves, WE PRESS RESUME
    t+194   0 mph   standstill False   lead 5 m, lead speed 5
    t+194           engaged    FALSE                              <- Ford drops cruise

**The resume gate did NOT withhold it** -- that was the first suspicion and it is wrong. The lead was
stationary the whole time and openpilot asserted resume in the same second it moved. Ford declined
after a 20-second standstill. Consistent with his own read: *"I've seen this happen before, without
passthrough, and it is rare. It might not be fixable."* The open question is whether there is a
STANDSTILL DURATION threshold; short stops resume fine on these same drives.

## THE DEVICE RUNS IN UTC. HE DOES NOT. CONVERT BEFORE REASONING ABOUT A TIME.

2026-08-25, and it inverted a TSR conclusion inside an hour. `date` on the comma reports UTC, and
so does every `stat` mtime, every route directory time and every `%y` in a diagnostic. **Utah is
UTC-6 in August (MDT), UTC-7 in winter (MST).**

Reported to him were "you flipped the GPS toggle at 7:21 PM" and "three night drives at 11:12 PM,
1:20 AM and 3:09 AM". The real local times are **1:21 PM**, and **5:12 PM, 7:20 PM, 9:09 PM** --
two of those three are broad daylight. The conclusion drawn from them ("night alone does not bring
TSR reads back") was the reverse of what the data says: both reads on record are after dark.

He spotted it in four words: *"Your timezones or something are wrong."*

**Where this bites hardest is TSR**, where the open question is literally light level, and where
`TSR-INVESTIGATION.md` records every timestamp in UTC. It also bites any statement about what he
was doing at the car, and any correlation with sunset, rush hour or his working day.

```bash
ssh comma@comma-34b959b "bash /tmp/bp_times.sh"     # tools/bp_times.sh -- routes + params, both zones
TZ=America/Denver date -d @<epoch>                   # the one-off conversion
```

Never quote a device time to him without converting it, and never reason about daylight from one.

## Language and units — US

The owner is in the United States (Utah). Everything written here is **US English**: comments,
docstrings, commit messages, settings labels, alert text.

This was not being followed. A single pass on 2026-08-04 corrected 60 instances of *behaviour*,
*metres*, *colour*, *signalling*, *minimise*, *grey*, *dialled* and friends that had accumulated
across this branch. It reads as someone else's codebase, and upstream openpilot is US English
throughout, so the mixture is worse than either convention alone.

Units have a split that matters, and it is **not** a style choice:

- **Internally, SI.** Speeds are m/s, distances metres, accelerations m/s². That is openpilot's
  convention end to end and changing it would break every interface. Do not "fix" it.
- **Anything a driver reads, US customary.** mph, feet, miles. The conversion happens at the
  display boundary via `CV.MS_TO_MPH` and the `is_metric` param.
- **Comments describing driver-facing behavior should use mph**, even when the code beside them is
  in m/s — "below 6 mph" is the useful statement; "below 2.68 m/s" is not. Give both when the
  constant itself is SI.

Dates in comments: **ISO `YYYY-MM-DD`**. Not because it is US style — it is not — but because
`08-04` is genuinely ambiguous across readers and this file already records dated decisions.

## TSR: read bluepilot/TSR-INVESTIGATION.md before touching anything

Several hours of in-car work on 2026-08-11 is written up there: the exact as-built field positions,
a full restore point for both modules, what was tried and refused, and the next steps in order. It is
the difference between continuing and starting over.

**He wants this working.** It is not a curiosity -- Speed Limit Assist has no camera speed limit
source, and on the drive that same evening the set speed froze on a road with no map coverage. TSR is
the second source for exactly those roads. Treat it as live work, not a closed file.

Three things from that session that will otherwise be re-learned the hard way:

- **FORScan decodes an Edge IPMA through a 2020 Fusion profile, and its friendly names are wrong.**
  It reports "wheel arch height 1338 mm / 1856 mm" for a block that actually holds
  `FeatureCfg_DAS_GSR`, and "TSR: Enabled" for a byte that is not the TSR field. Use raw as-built.
- **Writing IPMA as-built invalidates the RADAR's calibration** (`B1433`, MIL on). Reverting the IPMA
  clears it with no alignment drive. The two modules are calibrated as a pair.
- **Do not run the IPMA firmware update to `CF`.** It moves Strategy `KT4T-14F397-AE` -> `-AF`, which
  is the `FORD_EDGE_MK2` fingerprint in this repo rather than `FORD_FUSION_MK5`, and away from the
  software a known-working car runs.

## TSR, and the region change that is not worth repeating

**Setting the region in FORScan produced a lot of DTCs, and it is now back to UNSPECIFIED.** His
words, 2026-08-09: *"when I set region and stuff, I got hella DTCs"* and *"the region is set to
unspecified or something like that."* That path has been tried and it cost more than it returned.
Do not propose it again as a way to make TSR work, and do not treat the region as an unexplored
lever -- it is explored, and the answer was no.

**U0253 IS STILL HAPPENING. IT WAS NEVER FIXED.** `U0253 - Lost Communication With Accessory
Protocol Interface Module`, logged by the IPMA, recurring. The APIM is the SYNC module and the source
of navigation data, so `NoNavDataAvailable` is literal: the camera cannot reach the module that would
supply it.

**Enabling TSR in the APIM at `7D0-09-02` did NOT fix it.** That was recorded here as a fix on
2026-08-11 and it was wrong -- a DTC read back as "Previously Set - Not Present at Time of Request"
was taken as resolved, when it means only "not present at this instant" and the same read said "Test
not complete". He said repeatedly that it keeps coming back. Believe the owner over a status byte.

**AND THE MECHANISM IS NOW MEASURED, 2026-08-21.** The IPMA is a listed receiver of THREE APIM GPS
messages. Decoded across a whole 27-segment drive, on every bus the comma can see:

    0x462  APIMGPS_Data_Nav_1   lat/lon                      3494 frames
    0x463  APIMGPS_Data_Nav_2   UTC date+time, PDOP, compass     0 frames
    0x464  APIMGPS_Data_Nav_3   heading, HDOP, VDOP, altitude    0 frames

One of three arrives. That IS the `U0253 Missing Message`, precisely. The APIM's Nav Repeater format
and conformance are already set correctly (Motorolla / Current), Navigation is enabled, and units are
Miles/Feet -- so it is configured to repeat and does not. Android Auto was tested and is not the
cause. Whether it is the APIM or the gateway is open; the gateway is not to be written to.

**BUT THIS IS NOT WHAT STOPS SIGN READS, and saying otherwise cost most of 2026-08-21.** The camera
read a sign with `U0253` asserted and `NoNavDataAvailable` on every frame. Two separate faults; do
not merge them again.

**AND "TSR IS SWITCHED OFF AT `706-01-01` third character" IS WRONG.** That mapping came from a Ford
reference for a different vehicle line, and section 4d killed it: **nibble 3 is `1` on the friend's
car too, and TSR works there.** The field that IS named, by a Mondeo MK5 owner on the FORScan forum,
is **nibble 8** -- `9` = Disable, `A` = Reading + GPS. This car reads `B`, the friend's reads `A`.

**See `bluepilot/TSR-INVESTIGATION.md`.** The gateway is the most likely place a retrofit routing
fault would live. **He does not want its FIRMWARE OR AS-BUILT CHANGED** -- *"I don't think I should
touch the GWM. It took me forever to get where it is."* That is about WRITING to it, and nothing else.

**STOP QUOTING THIS AS "THE GATEWAY IS OFF LIMITS."** It was written that way, it was then cited in
conversations that had nothing to do with the gateway -- including, on 2026-08-16, as a reason map
data could not come from the car -- and he had to say so twice: *"that was completely unrelated to
openpilot... I didn't want to change firmware on it once and now you just keep taking it out of
context."* Reading it, reasoning about what it broadcasts, and anything that does not modify it are
all fine. This is the SECOND time the same overreach is recorded here: the ACCDATA_3 entry further up
says "the owner's GWM ruling was about FLASHING FIRMWARE AND AS-BUILT, never about reading a
broadcast frame. Stretching it to cover a message we already parse was my error, not his rule."

A narrow no is not a standing rule. When his decision is quoted, quote what he actually decided.

**THE CAMERA READS SIGNS. It has done it once, and it is VERIFIED. Corrected 2026-08-21 (late).**

This section said "the camera is NOT reading signs at all" earlier the same day, which was true of
every route measured up to that point and is no longer true.

    route 000003a7, segment 6, seven consecutive frames
      TsrVLim1MsgTxt     255 NoLimit  ->  30 Message30
      TsrVl1PrmntMsgTxt  DoNotShow    ->  ShowPermanentlyWithoutSupp
      position, from the car's own 0x462:  40.725463, -111.829903
      = 2011 2100 S, Salt Lake City.  Street View shows a SPEED LIMIT 30 on a pole there.

Two semantically coherent fields moving together, and byte 3 of the payload went `ff` -> `1e`, a
whole byte rather than a packed field. He confirmed the location: *"Bingo. Literally right there."*

**So the question is no longer whether the camera works. It is why it is BAD at it: one detection
across 50 segments of driving.**

**AND "ONE DETECTION" IS ALSO WRONG, 2026-08-23. IT READS ON DRIVES NOBODY HAD CHECKED.** Measured
with `bp_drive_checkup` check 15, which counts rising edges of `trafficSignData.vLimit1` out of the
0/255 sentinel:

    000003a7   1 read   30 mph at t+404.6     544 frames at 30, of 73,846
    000003ad   1 read   30 mph at t+481.9   5,171 frames at 30, of 56,375

Route `ad` is 2026-08-23 -- days after the "one verified read ever" line above was written, and it
was never re-checked. **The camera reads more than this file says.** Both figures came from the
first two routes looked at, so the real rate is unmeasured, not one-in-fifty.

**THE FULL BASELINE, `tools/bp_tsr_baseline.py`, 7 routes / 90 segments / 512,376 frames.** This is
the BEFORE for the as-built change -- run the same command after it and compare:

    000003ad  10 seg   56,375   1 read   1 return   [30 x5171]
    000003ac  11 seg   60,465   1 read   1 return   [30 x12424]
    000003ab  19 seg  109,633   0        0          [none]
    000003aa  16 seg   95,019   0        0          [none]
    000003a9   5 seg   25,910   0        0          [none]
    000003a8  16 seg   90,928   0        0          [none]
    000003a7  13 seg   73,846   1 read   1 return   [30 x544]

```bash
ssh comma@comma-34b959b "bash -lc 'cd /data/openpilot && /usr/local/venv/bin/python tools/bp_tsr_baseline.py 8'"
```

**IT IS NOT LATCHED, AND THAT WAS THE OPEN QUESTION.** Every read returns to the sentinel -- reads
1, returns 1, on all three. `Traffic_RecognitnData` keeps updating and the parser is not serving a
stale value, so this is NOT the shape of the v1 mapd `/dev/shm` staleness or of the 80 mph "sign".
The camera reads a sign, holds it, and correctly gives it up. **The mechanism is healthy; the recall
is terrible.**

**AND THE REAL SIGNAL IS THAT IT ONLY EVER READS 30.** Three reads, three 30s, across drives
covering interstate, arterial and city -- never a 25, 35, 45, 65 or 70. That is a much narrower
failure than "bad at reading signs", and it is the thing an as-built change has to move.

**IT IS NOT ONE MAGIC SIGN -- the three reads are three different places**, which was the
alternative and is now ruled out:

    000003a7   40.725463, -111.829903   (2011 2100 S, verified against Street View)
    000003ac   40.747404, -111.853911
    000003ad   40.752015, -111.855317

`ad` and `ac` are ~530 m apart; both are ~3.5 km from `a7`. Three distinct signs, all Salt Lake
City surface streets, all 30. (The first coordinate is the 2026-08-21 `0x462` measurement recorded
above; the other two came from `bp_tsr_baseline.py`, whose run was cut short by the laptop losing
hostname resolution before it reached `a7` -- rerun it to have all three from one source.)

**SO THE HYPOTHESIS THAT FITS IS RANGE, NOT SIGN SET, and it lines up with the open "TSR detection
range ~0 m" item.** Every read is a slow urban street where the sign is close to the lane and the
car is going 30; nothing is ever read on the interstate, where signs are far off the shoulder and
pass quickly. A recognizer that only resolves a sign it is nearly beside would produce exactly this
-- reads only where the car is slow and the sign is near, which in this city is 30 mph roads, which
is why every value is 30.

**MEASURED, AND THE PREDICTION HELD -- every read is slow:**

    000003ad   40.752015, -111.855317   doing 31 mph
    000003ac   40.747404, -111.853911   doing 34 mph
    000003a7   40.725446, -111.829907   doing 16 mph

**Not one read above 34 mph, across 512,376 frames of drives that include interstate.** The `a7`
position also lands within ~2 m of the 2026-08-21 `0x462` measurement, which is an independent
confirmation of both that reading and this tool.

**BUT IT IS CONFOUNDED AND MUST NOT BE WRITTEN UP AS PROVEN.** In this city a 30 mph road IS a slow
road, so "only reads when slow" and "only reads the value 30" are the same three samples and cannot
be separated by them. What would separate them, and neither has been observed yet:

  - a **25 mph** read (slow, not 30) kills "only 30" and leaves range standing
  - a read on a **45+ mph** road kills "only slow" and leaves the sign set standing

What the data does support is narrower and still useful: whatever the cause, it has never once
produced a limit for a highway, which is exactly where Speed Limit Assist has no map coverage and
wanted a second source. **So do not expect the as-built change to be judged by "does TSR work" --
judge it by whether any read appears above 35 mph, or at any value other than 30.** Both are
one-line reads of `bp_tsr_baseline.py` output.

**Hold duration varies wildly and is unexplained: 544, 5,171 and 12,424 frames.** A long hold is
only correct while the limit still applies, so the 12,424-frame one is worth a look -- it is minutes
of a 30 being served. It reaches nothing today (`SpeedLimitPolicy` is `map_data_only`, which
excludes the car source entirely) but it would the moment that policy changed.

Re-measured on routes 0000039f and 000003a1, decoding `Traffic_RecognitnData` (0x3CD) off bus 2:

    000003a1   909 frames, TWO distinct payloads
               372320FFFCC80220   x878  (96.6%)
               172220FFFCC80220   x31   (3.4%)
    0000039f   1175 frames, four payloads, 97.2% the same dominant one

**`TsrVLim1MsgTxt` is 255 in BOTH payloads, on every frame of both drives.** 255 is the no-data
sentinel. The only fields that move between them are `TsrMsgTxt` (3 -> 1) and `TsrStatMsgTxt`
(3 -> 2) -- status enumerants, not a limit. The camera publishes "I have no speed limit"
continuously.

The old claim -- "a real value for roughly 10% of it, so the camera does read signs", from route
00000333 on 2026-08-09 -- holds on no recent drive, and it CONTRADICTED the TSR 80 leak entry
earlier in this same file, which says TSR is "stuck at a constant 80". Both were wrong, and the
contradiction sat here unnoticed until he said flatly *"the signs it's reading are wrong. Those
aren't actually signs."* He was right, and I had quoted one of the two halves back to him as settled.

**What it changes, and four theories it kills.** `KT4T-19H406-CE` is a **Cx** camera -- Ford's own
part scheme has Bx adding AHB, TSR and tiredness alert, Cx adding autonomous braking on top -- and it
demonstrably recognised a US sign. So:

- **Hardware is fine.** Do not propose replacing the camera, and do not propose replacing the IPC.
- **Fusion mode is NOT required.** The read happened in `Available_CameraOnly` with
  `NoNavDataAvailable` asserted on all 747 frames including those seven.
- **Android Auto is not the cause** of anything here. Tested by driving with the phone off USB.
- **Firmware is not the issue.** EU and US IPMA firmware are byte-identical -- same SBL, strategy
  and calibration, confirmed by an owner who obtained both.

**A separate, real, measured defect: the APIM sends `0x462` (position) 3494 times a drive and
`0x463`/`0x464` ZERO times.** Those two are addressed to `IPMA_ADAS` and their absence is exactly the
`U0253 Missing Message` the camera raises. Its Nav Repeater settings are already correct. **This is
NOT what stops sign reads** -- conflating the two cost most of 2026-08-21. openpilot can synthesize
both from the comma's own GPS (built, tested, ships on, stands down if the car ever sends them), and
that feature must never be described as the TSR fix.

**DO NOT chase a US-market as-built.** A 2020 US Edge ST owner reports sign recognition was removed
from 2019+ US firmware, and this car's whole IPMA configuration came from a Brazilian file off the
internet with no original kept. So a "correct" US config could plausibly turn off the one thing that
works. Two other owners of this exact part number, with textbook configurations, get zero signs --
**this car is ahead of both of them.**

**Three confident framings about MARKETS were wrong in one day** -- "the friend's car is a different
market so his nibbles are dangerous", "`FF` is an unset region", "get a US as-built". Each was a
plausible story reasoned from a search result with no control to check it against. What survived is
what was measured on this car.

The next write, and its limits, are in `bluepilot/TSR-INVESTIGATION.md` section 4k.

**Two places in one file disagreeing about one measured fact is how a line of investigation gets
closed on the wrong evidence.** If a claim here is contradicted anywhere else in this file,
re-measure before quoting either.

**The IPC is a separate question and an irrelevant one.** He asked and was told "the US IPC does not
support TSR, you have to replace it." That is about the CLUSTER drawing a sign, which he does not
want: *"I don't care about TSR being on my IPC."* The camera reading signs and the cluster
displaying them are different modules, and the first is the only one Speed Limit Assist needs.

There is also an on-screen TSR status readout already built. Check what it shows before writing new
diagnostics for the same question.

## Car

2020 Ford Fusion Titanium AWD with retrofitted Edge ADAS parts (Edge PSCM, rack, IPMA camera, CCM
radar; Fusion ABS, IPC, steering column). Platform `FORD_FUSION_MK5`, `flags 18` =
`ALT_STEER_ANGLE | TSR`, **not** CAN FD — that combination is what exposed the duplicate-message
bug, so keep it in mind when reasoning about flag-gated branches.

## 2026-08-24: THE "SET SPEED CHANGED" SPAM WAS A SHAKING TARGET, NOT A HUNTING CONTROLLER

His report was *"it keeps telling me set speed changed and the max speed is flashing fast"*, and the
press count on route `000003ae` looked like the documented tap-vs-hold hunt. It was not.

**Measured, from his rlogs:**

| route | `vTargetRaw` changes | rate | driver button events in the window |
|---|---|---|---|
| `000003ae` | 363 over 129 s | **2.82 / s** | **none** — all 378 presses were ICBM |
| `000003b5` | 401 over 574 s | 0.70 / s | the holds there were HIS thumb |

On `ae` the plan target flipped 27 -> 30 -> 27 with a ~0.1 s high leg inside a ~0.5 s cycle, for the
whole `inert` window, while the baseline, `vTarget` and the dash all sat still at 27. ICBM was not
failing to settle on a reachable number; it was tracking a number that would not sit still.

**Two traps this walked into, both worth not repeating:**

1. **`ae` cannot answer WHY the target shook.** `vSlaTarget` and `speedLimitLive` were added AFTER
   those drives -- they read 0 on all 27,139 frames of `ae` and are populated on 61,967 of `b5`.
   Re-querying `ae` for the source is wasted work. `b5` and later routes can answer it.
2. **My first conclusion — "the jitter was his thumb" — was right for `b5` and wrong for `ae`.**
   One route's answer is not the other's. Check the driver button events per route, every time.

**The fix, and the design rule it produced.** The first version made every step UP wait out a
settle window. It broke five tests in `test_manual_override`, including the two defending the
owner's own rule that *behind a car the set speed may go anywhere, because that car is probably
driving correctly*; an earlier placement (above the `v_target_raw` capture) also DESTROYED a driver
hold in `test_icbm_own_recovery_is_never_adopted`. Both were caught by running the FULL suite, not
the new file. The shipped filter is aimed at the one shape that was actually measured -- a bounce
back up to a level just left -- and:

- a FALL is adopted on the frame it arrives, always, with no exception, ever;
- a rise to a level the target has NOT just left is adopted immediately;
- only a bounce back inside `REVERSAL_MEMORY_S` has to be asked for continuously for `SETTLE_S`.

It sits BELOW the `v_target_raw` capture on purpose. Holds, overrides and the divergence latch all
compare against `v_target_raw`, which is still exactly what the planner published. The stated reason
for putting it above -- that a shaking raw value could arm the override -- was arithmetic I never
did: `DEFAULT_BASELINE_RESET_DELTA` is 10 mph and the shake was 3.

**Generalisation, and it has now cost twice in two days: a filter that makes the car SLOWER to speed
up is cheap, and a filter that makes it slower to slow down is never acceptable. Put the delay only
on the permissive direction, and prove the placement against the whole suite before believing it.**

## 2026-08-25: SCC-MAP IS INVENTING HAIRPINS AT STATE 2, AND NOTHING CAN VETO IT THERE

Two road reports from one drive, and they turned out to be unrelated:

  *"It seemed to be emulating pressing and holding because I was dropping and increasing by 5 miles
  per hour at a time."*
  *"It also decided to drop down to 20 for no reason with no warning. I kept overriding with my gas
  pedal."*

`tools/bp_icbm_steps.py`, routes 000003c0 / c1 / c2 / c3.

### THE 5 MPH STEPS ARE HIS OWN THUMB. ICBM TAPS CORRECTLY.

    who                    n     1mph    5mph    med gap
    ICBM in-band         244      230       3      0.14-0.50s
    ICBM out-of-band     631      617       8      0.10-0.30s
    driver / other        46       16      10      0.56-8.5s

**869 of ICBM's 875 dash steps were 1 mph.** Every meaningful 5 mph step is in the DRIVER column,
which is this car's own press-and-hold behaviour with `CustomAccIncrementsEnabled = 0`. So the tap
duty cycle works on the road, and `TAP_ON_FRAMES` / `TAP_CYCLE_FRAMES` -- recorded for weeks in "THE
SET SPEED HUNT" as an unverified guess that "cannot be checked offline" -- are now VERIFIED on the
road. Do not re-tune them off this report.

**AND IT SETTLES A CONTRADICTION THIS FILE WAS CARRYING.** "THE SET SPEED HUNT" says ICBM holds the
button and the car moves 5 mph per hold; the `buttons-cannot-hold` note says the SCCM clears the bit
between frames so ICBM can only tap at 1 mph. **Both give 3.3 mph/s, which is why no tool measuring
RATE ever caught the disagreement.** They differ only in the STEP the driver sees, and the step is
1 mph. The second note is right about what reaches the dash.

### THE DROP TO 20 IS SCC-MAP, AND THE RADIUS IS THE PROOF

Comparing the map's corner SPEED against the car's speed proves nothing -- the controller exists to
slow the car before the bend, and it publishes the target at the moment braking must BEGIN. The
invariant that survives both is the GEOMETRY:

    SCC-Map:  v_target = factor * sqrt(a_lat / k)   ->   R_map   = v_target^2 / (a_lat * factor^2)
    the car:                                             R_drove = v^2 / a_lat measured at the peak

    route  t+       target   R_map    R_drove    verdict
    c0     150      17 mph    32 m     753 m     23.2x too tight
    c2     486      12 mph    16 m     320 m     19.8x too tight
    c0     469      17 mph    32 m     282 m      8.7x too tight
    c0    1178      12 mph    16 m     126 m      7.8x too tight
    c1     409      13 mph    19 m      28 m     map agrees   <- a REAL hairpin
    c1     414      13 mph    19 m      28 m     map agrees

**Five of seven SCC-Map floor episodes demanded a corner 8-23x tighter than the road.** A 16 m radius
is a parking-lot turn. `stop?` was 0-4% on every one of them, so no stop sign or light was involved
and the screen had nothing to say -- which is exactly *"no reason with no warning"*.

**THIS IS THE OPPOSITE OF THE I-80 FINDING AND BOTH ARE TRUE.** "THE TILES SEE THE CORNER. MAPD DOES
NOT." records mapd v1 smoothing a real 240 m corner into a 5,000 m straight -- 21x too LOOSE. He is
now on `MapdV2 = 2`, so SCC-Map reads the v2 curvature profile (`mapd_v2_path.py`), and it errs the
other way. **A fix aimed at one of these makes the other worse; they are different sources.**

### WHY NO DEFENSE STOPPED IT -- AND THE FIRST VERSION OF THIS SECTION WAS WRONG

**IT SAID "THEY ARE ALL HIGHWAY-ONLY" AND THAT IS FALSE. Corrected within the hour, by reading
`_model_disagrees` instead of remembering it.** Defense 2 -- *the camera sees no curve at all* --
runs at EVERY speed. `ramp_like` selects the HORIZON (4 s against 10 s), not whether the veto
applies. Only defense 3 is highway-only by condition, and defense 4 by its `v_target >=
_MAP_FACTOR_V_BP[1]` gate.

So the code-level reading is narrower and different: defense 2 is reachable at low speed and is
gated by `target_distance > v_ego * 4`, while SCC-Map publishes its target at the moment braking
must BEGIN -- which for 40 -> 12 mph is on the order of 180 m against a 71 m horizon. **That would
make defense 2 unreachable for exactly these corners, and defense 4, which exists precisely because
a corner can be acted on beyond the model's reach, is highway-only.**

**THAT IS AN INFERENCE AND IT IS NOT MEASURED. DO NOT ACT ON IT.** `target_distance` and
`model_lat_acc` are the two numbers those gates compare and NEITHER HAS EVER BEEN ON THE WIRE, so no
drive can say which gate declined. This is the third instance here of publishing a decision without
its inputs, and the rule already written down is: when a rule cannot be explained from a drive, add
the log line rather than a third inference. `targetDistance`, `modelLatAcc`, `modelVetoed` and
`cameraNotSeen` are published as of 2026-08-25. **The next drive names the gate; until then this
paragraph is a hypothesis.**

**AND THE TWO VETOES WERE MERGED BEHIND AN `or`**, whose own comment says they are "deliberately not
merged... folding them into one predicate makes the log unreadable". `or` short-circuits, so
`_camera_has_not_seen_it` was never even EVALUATED when `_model_disagrees` was true. Separated.

Whatever the gate turns out to be, **the fix is NOT a factor change**: `SmartCruiseControlMapFactor`
scales a corner speed that is already derived from a radius off by a factor of twenty.

**Do not fix this by tuning the vision factors** -- "LOW-SPEED CURVES" already rules that out by
measurement, and vision was right on every episode here (its own rows are excluded from the radius
column, see below).

### WHAT THE TOOL GOT WRONG FIRST, BECAUSE EACH IS A TRAP WORTH KEEPING

- **It counted float chatter as steps.** `speedCluster` sitting on a rounding boundary flips between
  two integers on adjacent frames. First run: median gap 0.10 s = 10 mph/s, three times this car's
  documented ceiling, which is the tell. Debounced at 0.08 s.
- **It applied SCC-MAP's formula to SCC-VISION rows.** Vision's target is
  `v_ego * sqrt(a_lat_reg_max / max_pred_lat_acc)` -- proportional to CURRENT SPEED, a completely
  different derivation -- so inverting it with the map's formula produces an authoritative-looking
  number that means nothing. All six vision rows read "map agrees", which is the most dangerous way
  for a wrong column to be wrong. The radius check is now `sccMap`-dominant only.
- **Its first floor-episode pass never closed an episode**, so episode 2 was episode 1 plus a second,
  and it printed 100 Hz context around frames that were not at the floor at all.
- **The harness set `cc.propulsion_blend` directly** in a sibling test, which `update()` overwrites
  from Params every frame -- so the toggle-OFF case ran with the toggle ON and passed.

### THE ONROAD GUARD IS IN THE TOOL, NOT IN A NOTE

An rlog scan on this device cost him engagement mid-drive once, and the lesson was written down as
"copy the logs off and decode locally". A note cannot stop the next scan. `bp_icbm_steps.py` reads
`/data/params/d/IsOnroad` before EVERY segment and stops with a loud partial-results banner, because
a drive can start at any point in a run that takes minutes.

## THE FORD CAR CODE IS NOT LINTED BY THE REPO'S OWN CONFIG

`pyproject.toml` `[tool.ruff] exclude` lists `opendbc` AND `opendbc_repo`. **Every file that actually
runs the car -- carcontroller.py, carstate_ext.py, lateral_angle_ext.py, radar_interface.py, the
Ford smoke tests -- is invisible to `ruff check .`.** A clean run of the repo lint says nothing
about them.

Checked explicitly on 2026-08-24 it found eight things, all real and all in code that runs:

    ruff check --no-cache --isolated --select F,E9 opendbc_repo/opendbc/car/ford/                                                    opendbc_repo/opendbc/sunnypilot/car/ford/

- `steer_alert` computed in `carcontroller.update` and never passed anywhere (HudExt derives its own)
- `calc = 1` in `radar_interface`, `d_ref = pscm_d_ref_m(v_ego)` and `LP = self.lp` in
  `lateral_angle_ext` -- discarded results, one of them a function call made every frame
- two unused imports, and a **vacuous smoke test**: it named the three gap signals in a variable it
  never used and instead counted every frame at 0x083, so it passed on the periodic all-zero frames
  while asserting in its own message that "the gap request reached the wire"

**Run that command as part of any review that touches Ford code.** `--isolated` is what bypasses
the exclude; without it ruff silently checks nothing. And note the repo carries ~900 pre-existing
style errors elsewhere, so lint is not enforced anywhere here -- `--select F,E9` is the filter that
separates real findings (undefined names, unused results, syntax) from that noise.

**The same audit on instance state, which lint does NOT do:** an AST pass for `self.X` attributes
that are stored and never loaded found `scc_map_requesting` in the ICBM controller -- assigned the
identical expression as `deadline_requesting` one line apart, read by nothing, with a comment three
hundred lines away claiming the drop limiter reads it. The limiter reads `deadline_requesting`.
Cross-check any hit repo-wide before believing it: most write-only attributes are read by another
module, and a per-file scan cannot see that.


## 2026-08-24: WHY TSR BARELY READS SIGNS -- THE CAMERA SAYS SO ITSELF

`Traffic_RecognitnData` (0x3CD) carries twelve signals. `carstate_ext.update_traffic_signals` read
TWO of them -- the number and the unit -- and the other ten were subscribed, parsed and discarded.
Decoding them off routes 000003b6/b7 answered both halves of the TSR problem in one pass.

**WHY SO FEW READS -- and it is not the camera being blind:**

    TsrMsgTxt_D_Rq    NoNavDataAvailable      100% of b7, 95% of b6
    TsrStatMsgTxt     Available_CameraOnly    100% of b7, 95% of b6
                      Available_FusionMode      5% of b6

Ford's TSR is a FUSION system. Without nav data it falls back to camera-only, which is the weak
mode, and that is where this car lives. On the wire 0x462 arrives from the real APIM 584-715 times
a drive while **0x463 and 0x464 are zero frames from any source**. `FordSynthesizeApimGps` exists to
send exactly those two.

**HE TURNED IT ON HIMSELF AT 13:21 MDT ON 2026-08-24, and this paragraph said `0` for a day after
that.** On 2026-08-25 that stale line was quoted back to him as current state -- "it is off on your
device, that is the untried lever" -- and he caught it: *"I thought I turned Send GPS To The Camera
on?"* He had. Read live:

    FordSynthesizeApimGps = 1   written 2026-08-24 13:21 MDT
    routes c1 / c2 / c3         2026-08-25 2:15 PM / 4:09 PM / 8:14 PM -- ALL after it

**NEVER QUOTE A PARAM VALUE OUT OF THIS FILE. READ IT OFF THE DEVICE WITH ITS MTIME.** That rule is
already written here twice -- once for `SpeedLimitPolicy` and once for `AlphaLongitudinalEnabled` --
and this is the third time it has been broken, this time by trusting a note that was 24 hours old.

**So fusion mode is NOT an untried lever any more; it has been on for two days.** With it on, c1 and
c2 produced ZERO reads across 266,000 frames and c3 produced two, both the value 30, at 33 and
34 mph. `tools/bp_tsr_fusion.py` is the before/after and asks the two questions in the order that
matters: did 0x463/0x464 actually reach the wire, and did `TsrStatMsgTxt` move off
`Available_CameraOnly`. **The second is meaningless without the first**, and a stale device build
makes it a null test that reads exactly like "the feature did not help".

**WHY THE READS IT DOES GET ARE UNTRUSTWORTHY, and the trap in fixing it:**

`TsrVl1StatMsgTxt_D_Rq` is the camera grading its own value -- LimitReliable / LimitChanged /
LimitOutdated / Null. It is now gated on, so an OUTDATED limit no longer reaches the car.

**But check the JOINT distribution before believing a gate fixes an episode.** The phantom 80 on
b6 -- an I-80 route shield read near 2100 S that walked the set speed to 90 for 13 minutes -- broke
down as 96 frames LimitReliable, 62 LimitOutdated, 7 LimitChanged. The marginals (16% reliable
overall) suggested the gate would kill it. The joint says it would NOT: the camera was confident and
wrong on 58% of those frames. A confident wrong read needs corroboration against the map source,
which is a resolver change nobody has made yet.

**AND THE DAMAGE OUTLIVES THE READ.** At t+389.8 the camera withdrew the 80 and the resolver went
`valid=False, lastValid=False, source=none` correctly -- but the resolved 90 persisted to t+486.6,
97 seconds later, because ICBM had already PRESSED the dash up to 90. A bad limit is converted into
button presses, and those do not come back when the limit does. Rejecting a bad read matters far
more than un-latching one.

### 2026-08-25: HIS ONE-LINE DIAGNOSIS. IT READS 30 MPH SIGNS AND INTERSTATE SHIELDS.

  *"Bro, it loves reading 30 mph signs and interstate signs."*

That is a far sharper claim than "TSR is unreliable", and it explains the only read that has ever
hurt him. **The 80 is not a hallucination and not a speed limit sign -- it is the I-80 ROUTE SHIELD**,
a badge with a large `80` on it, posted all over Salt Lake City on surface streets that feed the
freeway. The camera resolves the number correctly and misclassifies what KIND of sign it is on.

**AND IT REAPPEARED THE SAME DAY, on route 000003c3 (2026-08-25, 8:14 PM):**

    000003c3   14 seg   81,670 frames   2 reads   [30 x29504, 80 x3436]
       read 30 at 40.747585, -111.853906   doing 33 mph
       read 30 at 40.729188, -111.853962   doing 34 mph

The 80 ran for **3,436 frames** and was NOT counted as a read, because it came out of the 30 rather
than out of the sentinel -- so any edge-counting baseline undercounts exactly the value that is
dangerous. It reached nothing only because `SpeedLimitPolicy = 1` excludes the camera source.

**WHY THIS CANNOT BE FIXED BY VALUE.** 80 is a legal US limit and Utah posts it on I-15 and I-80, so
no plausibility filter can reject it. What separates a shield from a sign is WHERE it is read: a
surface street the map calls residential at 30. That is the corroboration-against-the-map resolver
change named above as "nobody has made yet", and his observation is what makes it specifiable --
**refuse a camera limit that grossly exceeds the mapped limit for the road we are on**, which is the
map REFUSING, never opening, and so is allowed by the map-is-evidence rule.

`tools/bp_tsr_shields.py` prints every camera-limit run with its position, the car's speed, and the
MAP's limit and road name at the same moment, and flags runs whose value is an interstate route
number on a road the map calls far slower. **Confirm each against Street View at the printed
coordinate before believing it** -- that is how the 2026-08-21 read of 30 was verified and it is the
only check that has ever settled one of these.

**AND HE MAY SIMPLY BE RIGHT THAT IT IS BROKEN.** *"I'm also leaning towards it's broken entirely
because how has it only detected these."* Across roughly a million frames now measured, TSR has
produced about five reads, every one the value 30, every one on a slow surface street, and never
once on a highway -- which is the only place Speed Limit Assist actually wanted a second source.
"The mechanism is healthy, the recall is terrible" is a distinction that buys him nothing. The
decision criterion is already written above and has not moved: **any read above 35 mph, or any value
other than 30.** If several more drives produce neither, retire TSR rather than keep paying it
attention.

## 2026-08-24: WHAT MAKES A CAMERA CANCEL STICK -- AND TWO WRONG ANSWERS I PUBLISHED FIRST

Twenty cancel runs across routes 3b4, 3b5, 3b6, 3b7, 3b8, 3ba, classified by whether cruise was
ENGAGED at the moment `AccCancl_B_Rq` went high:

    raised while DISENGAGED   16 runs   all cleared in seconds (or were route-end artifacts)
    raised while ENGAGED       4 runs   3 never cleared; the 4th took 365 seconds

**A cancel raised while cruise is OFF is not a cancellation.** It is the camera's idle state
flickering, and it clears on its own. The only real cancels are the ones raised while engaged, and
those essentially never relent. Any future count of "cancel runs" that does not split on this is
counting mostly non-events -- which is exactly how both wrong answers below happened.

**WRONG ANSWER 1: "the camera relents almost every time."** Published off the raw run counts
(7 of 8 clearing on 3b5). All of those were cruise-off runs. Genuine cancels do not relent.

**WRONG ANSWER 2: "a MAIN press recovers a stuck cancel."** Three measured clears within 0.7 s of a
MAIN press -- 3b7 t+82.6, 3b8 t+177.1, 3ba t+373.1 -- and it reached the ALERT TEXT before being
re-checked. All three were cruise-off runs that would have cleared anyway. Against a real cancel
MAIN failed five times for five: 3b5 t+596.4, 3b8 t+587.3, t+588.4, 3ba t+469.7, t+470.9.

**AND THE MECHANISM IS NOT DISENGAGEMENT.** The obvious next theory -- the camera holds the cancel
while engaged and releases when you drop out -- is false: **0 of 15 clears were preceded by cruise
going off within 5 s**, and cruise DID go off during all three of the stuck runs without releasing
them. Being engaged at the moment of assertion is what makes it latch; disengaging afterwards does
not undo it.

**WHAT THIS LEAVES.** There is no known recovery from a real cancel. Ruled out by measurement so
far: the camera not seeing our frame, the magnitude of the disagreement, radar health,
cancel-and-re-engage, dropped TX frames, waiting for the camera to relent, disengaging, and MAIN.
The remaining direction is PREVENTION -- what the camera detects during an override that makes it
cancel an ACTIVE session -- and nothing measured yet distinguishes that.

## 2026-08-24: A LEAD IS WHAT DECIDES WHETHER AN OVERRIDE SURVIVES

Nine override episodes across six routes, classified by whether the radar had a lead during them:

    CANCELLED   3b5 t+379.1   9.0 s    lead present   0%
                3b8 t+581.9   2.9 s    lead present   0%
                3ba t+420.6   2.8 s    lead present   4%
    CLEAN       3b7 t+666.1   0.7 s    lead present  61%
                3b7 t+670.6   1.4 s    lead present 100%
                3b8 t+227.5   1.8 s    lead present 100%
                3ba t+603.4   0.8 s    lead present 100%
                3b5 t+463.4   2.3 s    lead present   0%
                3b5 t+517.9  11.9 s    lead present   0%

**Every override with a lead survived (4/4). Every cancel was leadless (3/3).** Leadless is not
certain death -- two survived, one of them for 11.9 s -- but a lead has never once failed to protect.

**IT IS NOT DURATION AND IT IS NOT MAGNITUDE, and both were believed at various points today.**
11.9 s leadless survived while 2.9 s leadless cancelled. And the accel channel is ruled out
entirely by a matched pair: 3b8 t+227.5 (clean) and 3ba t+420.6 (cancelled) have near-identical
integrated disagreement (1.066 vs 1.039 m/s), peak gap (1.68 vs 1.81) and peak command (-1.95 vs
-1.94). The only material difference between those two frames is the lead: 60-80 m versus none.

**WHY IT MAKES SENSE.** Ford ACC is a lead-following system. With a target ahead, hard braking is
explicable. With nothing there, the car decelerates for a reason the camera cannot account for.

**AND HERE IS THE PROBLEM WITH THE FEATURE.** The stop override exists to stop for red lights and
stop signs -- which is precisely the leadless case. Its primary purpose is the one condition the
camera does not tolerate. Behind a car, where it costs nothing, Ford would usually have stopped
anyway. That is a design fact, not a bug to fix, and it should be stated to him plainly rather than
worked around quietly.

Also ruled out today, each by measurement, so nobody re-derives them: instantaneous disagreement
(the camera had CONVERGED onto our command 3.2 s before cancelling on 3b5), accumulated
disagreement, dropped TX frames, waiting for the camera to relent, disengaging, and a MAIN press.

## 2026-08-24: WHY THE MODEL-STOP PATH ARMED TWELVE SECONDS LATE -- AND A FIX THAT WAS WRONG

Route 000003bb, the approach he reported as "it started slowing at the right time then seemed to
stop slowing" and which ended in emergency braking.

**What was ruled out, all by measurement:**

    ICBM rate limit      NO. The set speed went 45 -> 20 in 3.0 s (~8 mph/s).
    Ford under-braking   NO. Ford COMMANDED -3.16 m/s^2, harder than openpilot's own -2.84 ask.
                             Actual decel reached -4.88. The "Ford tops out near 1.6" belief was wrong.
    an unconfirmed lead  NO. `hasLead: False`, `trigger: modelStop`, `dRel: 0`. It was a light or
                             sign, and the screen said so correctly: "Stop sign or signal ahead /
                             Slowing to 20 mph -- the stop is yours."

**What actually happened:** the model had the stop at t+138.0 with 138 m to run at 39 mph. That
needs 1.10 m/s^2 against an `IcbmModelStopMinDecel` threshold of 1.0, so the gate was satisfied
IMMEDIATELY. The path did not arm until t+150 -- twelve seconds and most of the braking distance.

The arming accumulator is the suspect: `_model_stop_s += DT_MDL` while `model_candidate` holds, and
`= 0.0` on ANY false frame, against `MODEL_STOP_PERSISTENCE_S = 0.3`. Its own comment says it is
"only here to reject a single-frame glitch", and one glitch destroys it instead. `dec.hasSlowDown`
was alternating true/false frame to frame across t+138.6..141.5, and stop_override.py documents that
same signal as chattering near the threshold by design.

**AND THE OBVIOUS FIX IS WRONG. Do not re-derive it.** Tolerating a short gap before zeroing the
accumulator took the suite from 60 passing to 11 failing in that file alone, including
`test_a_stop_reachable_by_coasting_does_not_trigger`. The reason is structural: `model_candidate` is
an AND of the chattering flag AND `a_required >= min_decel`, so a blanket gap tolerance cannot tell
"the flag glitched" from "this stop does not need braking yet" -- and it arms on stops reachable by
lifting off, which is the failure that gate exists to prevent.

Any real fix has to debounce the FLAG term alone, before it is ANDed with the physics term. That is
a change to what `model_candidate` is made of, not to how its result is accumulated.

## 2026-08-27: THE SLC -> YOSEMITE TRIP. 17 ROUTES, 338 SEGMENTS, DECODED ON THE LAPTOP.

The first validation drive for the ramp-approach horizon fix, which was on the car the whole way.
**Decoded off-device**, and that is now the only acceptable way to do this -- see the guard section
below, which is a rule this session broke and had to be stopped on.

### THE HEADLINE: DEFENSE 4 SUPPRESSED A REAL CORNER AT 84 MPH

Route 000003d1, t+1988. 100% of the vetoed frames were `cameraNotSeen`:

    t+1987.5   84.5 mph   straight    VETOED   maxPred 2.48
    t+1988.5   84.5 mph   angle 4.5   VETOED   maxPred 3.60   <- the camera plainly sees it
    t+1989.0   83.5 mph   angle 9.9   VETOED   latAcc 2.40
    t+1989.5   82.5 mph               acts     latAcc 3.00    <- ~2 s late
    t+1990.5   80.6 mph               HANDS ON latAcc 3.35
    t+1991.0   79.2 mph   angle 21.9           latAcc 5.53    peak 5.91 over the event

**5.91 m/s^2 is worse than the 5.20 that nearly put him off the road on 000003b6.**

`_camera_has_not_seen_it` decided "the camera cannot see it" from DISTANCE ALONE, so it fired while
the model was describing the corner in detail. **Fixed**: at or above `MODEL_DISAGREE_LAT_ACC` the
camera has looked and found something, so the blindness claim is false and defenses 2 and 3 -- which
are the right tools for "is it as tight as the map says" -- get asked instead. One-directional by
construction: it can only ever REMOVE a suppression, which is why it landed on a single event.
Mutation-tested, 4 mutants, 0 survivors.

**STILL UNFIXED, and it is the other half of the same event:** SCC-Map published the corner speed
about two seconds LATE. That is "THE EXIT THAT NEVER SLOWS ENOUGH" again, and the veto fix does not
touch it.

### THE VETO IS NOT INERT, AND THE COST COLUMN IS UNMEASURABLE

    map WANTED to act (active + veto)   2163
      ...got through                    1776   82.1%
      ...VETOED                          387   17.9%
         of which cameraNotSeen          117

**`modelVetoed` frames are DISJOINT from `active` frames** -- the veto sets `is_active = False` --
so the denominator is `active + veto`. Scoring the veto against `active` alone gave 21.79% and is
the fourth instance of the denominator error in this file.

**AND "0 REAL CORNERS SUPPRESSED" WAS A VACUOUS NUMBER I NEARLY PUBLISHED AS A SAFETY RESULT.** The
episode detector only opens an episode while the map is `active`, and a suppressed corner has
`active = False` by construction -- so that column could never have been anything but zero. Same
shape as the structurally-unreachable defenses this file already records.

**The underlying data has the same hole:** `get_v_target_from_control()` returns `V_CRUISE_UNSET`
once `is_active` is cleared, so **the speed the map wanted is discarded before it is logged**. What
the veto COST cannot be recovered from any recorded route. Publishing the suppressed target is the
one-field change that would fix that, and it is not done.

### HIS THREE ROAD REPORTS, ALL ATTRIBUTED

**"It wasn't moving me up to SLA sometimes."** 43 sustained windows where the dash sat >= 2 mph
below SLA for >= 5 s. **29 were his own holds** (`press`, manual) and **14 were curve controllers
actively slowing** -- zero unexplained. The mechanism is the GAS PEDAL:

    965.4   dash 37   vEgo 31   gas TRUE    <- he presses the gas on a climb
    965.6   dash 31   vEgo 31   gas TRUE    <- Ford drops the set speed to CURRENT SPEED

then ICBM walks it back at ~1.3 mph/s. On a grade where he overrides repeatedly the dash sits below
SLA most of the time, and nothing is broken. **The first version of that tool called all 14 curve
windows "unexplained" because it never asked what the curve controllers were requesting** -- the
attribute-the-source rule, failing again in a tool written by the session that quotes it.

**"It said setting speed to speed limit when it was actually setting it to a hold."** Real, and
fixed. `speed_limit_auto_set_alert` renders SLA's number while ICBM drives the car to the HOLD, so
a map limit change announced a speed the set speed would never reach, and re-fired on every limit
change while nothing moved. `plannerd` now subscribes `selfdriveStateSP`, the planner passes
`vBaseline` down, and `update_events` defers when a hold DIFFERS from the new limit. A hold EQUAL to
it is about to be cleared and that announcement is true, so it is kept.

**The hunting.** 17 reversal bursts, almost all on the mountain routes `3dc`/`3de`, up to 50 dash
steps in 20 s. **All 14 analysed bursts were the PLAN TARGET shaking; zero were ICBM oscillating on
its own** -- `vTargetRaw` changing up to 11.8 times/s across a 20+ mph range with SCC-Vision active.
ICBM asked `increase` 15,779 frames and `decrease` 12,381. **The buttons are faithful; the number
they are told to chase is not.** Not fixed.

### WHAT IS HEALTHY, MEASURED RATHER THAN ASSUMED

- **Holds: 217 created, 100% `press`.** Not one inferred `fallbackIdle` or `counter` hold in 2,000
  miles. The route 00000379 failure (36.5% of a drive governed by an inferred hold) is gone.
- **The tap band works: 95.8% of ICBM's 1,228 dash steps were 1 mph.** 40 were 5 mph -- but the
  attribution calls a step "ICBM" whenever ICBM was pressing at that moment, so a press of his
  landing inside an ICBM window is misfiled. That number is not a finding yet.
- **Pins: 0 suggestions offered, 0 pinned holds.** And he closed the concept on 2026-08-27: *"I
  turned pins off and never want them."* Not a preference that might change -- stop treating the
  2026-08-25 "suggestions will get noisier" wart as a thing to solve.
- **Resume tail: 2 events**, both inside 0.3 s. The guard had something real to suppress.

### DECODE OFF-DEVICE. AN `IsOnroad` GUARD INSIDE THE SCAN IS NOT ENOUGH.

This session launched a 12-minute rlog scan ON THE DEVICE while he was parked and about to leave,
with a guard that re-checked `IsOnroad` before every segment and a docstring citing the incident it
was protecting against. **The passing-assist session stopped it at 97.7% CPU.**

The guard checks BETWEEN segments and one segment decode is many seconds of solid CPU, so it
silently downgrades "never compute on a driving comma" to "never START a segment on one". The
uncovered window -- he turns the key, openpilot starts, the job still holds a core -- is exactly the
window that cost him engagement the first time. **A guard whose granularity is coarser than the harm
it prevents is worse than none, because it stops anyone looking harder.** Having written the guard
myself is what made me stop checking it.

Off-device is cheap and is now the rule:

```bash
scp -6 "comma@[fe80::20a:f5ff:fee4:4abc%11]:/data/media/0/realdata/<seg>/rlog.zst" .
```

338 segments, 3.6 GB, 10.7 minutes at ~5-7 MB/s, zero failures. **`openpilot.tools.lib.logreader`
will NOT import off-device** (it pulls `fcntl` through the hardware layer) -- load `cereal/log.capnp`
directly and iterate `Event.read_multiple_bytes(raw, traversal_limit_in_words=2**32)`.

### AND THE DEVICE HAD NO IPv4 ADDRESS AT ALL

Full port-22 sweeps of the hotspot `/24` and both halves of the lodge `/23` found nothing, ARP was
empty, and only the laptop answered ping -- while the comma was up and tethered exactly where he
said it was. Its only IPv4 address was `127.0.0.1` on `lo`.

    ssh -6 comma@fe80::20a:f5ff:fee4:4abc%11

mDNS resolves to the link-local v6 address ONLY, which gives a distinctive signature: **the name
resolves and `-4` times out.** That is this case, not a dead car. `%11` is the WINDOWS INTERFACE
INDEX of the hotspot adapter, not part of the address. `scp` needs brackets. **This inverts the
"force `-4`" rule elsewhere in this file** -- `-4` is right on a normal network and is the thing that
guarantees failure here.

**On that hotspot the device has SSH but NO DNS** (`getent hosts github.com` fails, `git ls-remote`
dies), so the auto-updater is inert and a push cannot reach the car. Code moves only by hand-carried
bundle. `UpdateFailedCount` reading 0 means it has not tried since the network changed.

**And `/tmp` is tmpfs (150 MB)** -- a detached job's output does not survive a reboot. Write to
`/data`.

### ONE MORE PROCESS FAILURE WORTH RECORDING: A TIMEOUT IS NOT A DEAD CAR

One SSH timeout was reported to him as "the car's off the network now, he's moved". It answered on
the first retry. This file already says the laptop's resolver is the usual culprit, and the rule was
still broken -- **retry before narrating a cause.** He put it plainly: *"It's never off the network,
dude, you're really driving me mad."*

**And a commit hash was quoted to a peer session without ever being read** -- invented, then
corrected. `git rev-parse` is one command. Same family as the "check content, not hash" rule.

## LATERAL: IT OVERSTEERS AND PING-PONGS ON GRADUAL TURNS. HIS REPORT, 2026-08-27.

  *"we have a weird thing where it oversteers and then corrects itself on more gradual turns and
  ping pongs while doing it"* ... *"It was driving me absolutely crazy today, too."*

**HE WANTS THIS ON ITS OWN BRANCH.** Do not start it opportunistically in an ICBM session, and do
not touch it alongside longitudinal work -- a lateral change landing next to a curve-speed change
produces a drive that cannot say which one moved.

**IT IS NOT NEW, AND THAT IS EVIDENCE.** He corrected an earlier version of this note that implied
it started on the Yosemite trip: *"No, it's been like this for a bit."* So it predates every
longitudinal change in this file, which independently rules out anything recent as the cause and
agrees with the code reading below -- the mechanism is upstream's and has presumably always been
there. It also means MANY routes contain it, not just the trip, so the sample is large and the
first pass needs no device and no drive.

### RULED OUT ALREADY -- DO NOT RE-DERIVE THESE

- **His settings.** He said flatly: *"my settings are right. Don't question that."* They are
  `FordLowSpeedFactor_ang` 0.912, `FordHighSpeedFactor_ang` 0.828, `FordHighSpeedDampening_ang`
  0.85 against upstream's 1.0/1.0/1.0. Not the suspect, and not to be re-litigated.
- **The fingerprint.** `FORD_FUSION_MK5` IS ours -- the whole platform config is a `+` against
  upstream, including `steerRatio=17.07`, `wheelbase=2.85` and the `ALT_STEER_ANGLE` flag. But
  **`steerRatio`, `wheelbase` and `VehicleModel` appear NOWHERE in `lateral_angle_ext.py`.** The
  angle path returns `LateralResult(apply_curvature=0.0, ..., path_angle=path_angle)` -- it commands
  a PATH ANGLE, and steerRatio is not in that chain. It shapes only the curvature FEEDBACK.
  Checked rather than assumed, and he agreed to leave it alone.
- **Our code.** `lateral_angle_ext.py` is upstream bp-7.0's; our entire diff is TWO DELETED DEAD
  LINES (`d_ref` and `LP`, both F841). Verified `d_ref` was assigned and never read in upstream
  either, so the deletion changed nothing.

### THE MECHANISM THAT FITS, AND IT IS UPSTREAM'S

```python
low_gain_calc  = interp(v_ego, [13.5, 26.82], [1.0, path_angle_gain_lowC_highV * user_dampening_factor])
high_gain_calc = interp(v_ego, [13.5, 26.82], [1.30 * low_speed_curv_factor, path_angle_gain_highC_highV * high_speed_curv_factor])
curvature_factor = interp(abs(kappa_cmd), [0.0007, 0.001], [low_gain_calc, high_gain_calc])
path_angle = kappa_cmd * v_ego * curvature_factor
```

**The gain schedule switches across a razor-thin curvature band, and that band lands exactly on
gradual turns.** `κ = 0.0007` is a **1429 m** radius; `κ = 0.001` is **1000 m**. So the controller
blends between two different gains over 400 m of radius, out in the gentle-curve regime.

On a sharp corner `kappa_cmd` is pinned above 0.001 and the gain is constant. On a GRADUAL turn it
sits inside or near the transition, so ordinary curvature noise swings the gain -- which MULTIPLIES
the command -- and the commanded angle moves more than the road did. Oversteer, correct, ring. That
predicts the symptom **specifically on gradual turns and nowhere else**, which is how he described
it unprompted.

**THIS IS A HYPOTHESIS FROM READING THE CODE. IT IS NOT MEASURED.** Do not tune anything off it.

### HIS OWN READ: *"It almost seems like a latency thing, too."* AND IT HAS TWO CANDIDATES

Take this seriously -- under-compensated lag in a closed loop IS oversteer-correct-ring, and it
bites hardest where the correction is small relative to the lag, which is gradual turns.

**AND IT CORRECTS SOMETHING SAID ABOVE.** "Our code is not in the lateral path" was based on
reading `lateral_angle_ext.py` ALONE. `interfaces_ext.py` was never checked, and it holds:

    ret.steerActuatorDelay = 0.22  # upstream: 0.2

**That is OURS, and it is the lag-compensation constant.** So the ruled-out list above is right
about the fingerprint and about `lateral_angle_ext.py`, and was WRONG to generalise to "nothing of
ours". Check every file in a path before clearing the path.

**The two measurable candidates, neither needing the car:**

1. **THE LOOKAHEAD IS CLIPPED BELOW WHAT WE DECLARE THE DELAY TO BE.**

       _t_base = float(clip(self.sm['liveDelay'].lateralDelay, 0.1, 0.15)) + _DT_MDL

   The lookahead floor is capped at **0.15 s** while `steerActuatorDelay` says the actuator takes
   **0.22 s**. If `liveDelay` learns anything above 0.15 the clip truncates it silently and the
   controller under-compensates. `liveDelay.lateralDelay` is published -- read its distribution on
   his routes first. If it sits at or above the 0.15 rail, that clip is binding on every frame and
   the two numbers are describing different cars.

2. **THE SOFT ROC CLIP.** `bp_angle_rate_limited` is published per frame and says whether the
   path_angle rate limiter actually bit. A command that is rate-limited lags the desired one, the
   error grows, then it catches up and overshoots -- latency-shaped ringing from a limiter rather
   than from a gain. Cross-tab it against the ping-pong episodes before touching either.

**Do not change `steerActuatorDelay` or the 0.15 clip on this reasoning.** Two numbers disagreeing
is a reason to measure, and one of them is ours, which makes it likelier we introduced the
disagreement than that upstream did.

### CANDIDATE 4, AND THE STRONGEST: THE COMMAND AND THE PSCM DISAGREE ABOUT "HOW FAR AHEAD"

Found by reading the whole command path instead of stopping at the first plausible mechanism. **This
is a READING-level finding. It is NOT measured. Do not tune on it.**

The module's own docstring says the geometry is

    path_angle = 1/2 * kappa * d_ref          # d_ref = the PSCM's short lookahead

and the file contains `pscm_d_ref_m()`, a 6-point speed table for exactly that:

    speed m/s   0.0   4.17   27.78   41.67   50.0   55.56
    d_ref m     0.5   0.95    1.4     2.075   2.75   3.875

**`pscm_d_ref_m()` IS NEVER CALLED.** It is dead code -- and the one line that referenced it was an
assigned-never-read `d_ref` that this fork deleted as an F841. What actually ships is

    path_angle_calc = kappa_cmd * v_ego * self.curvature_factor

`v_ego * curvature_factor` has units of DISTANCE, so `curvature_factor` (0.85-1.30) is an effective
lookahead TIME and the command is built on a lookahead of ~13 m at 30 mph and ~23 m at 60 mph.

**THE PROBLEM IS NOT THE MAGNITUDE, IT IS THE SPEED SCALING -- and that is why no gain fixes it.**

    30 -> 60 mph      PSCM's own d_ref grows   1.128 -> 1.381 m   = 1.22x
                      the shipped command      13.5 -> 22.8 m     = 1.69x

The three tuning factors can absorb the constant offset at ONE speed. They cannot absorb a
different rate of growth, because they are constants blended over the same 30-60 mph band the
mismatch lives in. So a value tuned until 45 mph feels right makes 70 mph wrong, and vice versa.

**HIS EVIDENCE FOR THIS, AND IT IS THE STRONGEST KIND:** *"I did try changing my models about half
way to California today. I also did change the angle parameters. Nothing really made steering
perfect."* He has already swept the tuning space. A driver who has tried the knobs and found no
setting that works is describing a SHAPE error, not a magnitude error, and that is exactly what a
speed-scaling mismatch is.

**WHY IT FITS "GRADUAL TURNS" SPECIFICALLY.** On a sharp corner the command is large, the PSCM is
near its authority limit, and the error is a small fraction of the input. On a gentle curve the
command is small, so a proportional geometry error is a LARGE fraction of it -- the controller
commands past, the error inverts, it comes back. That is his ping-pong.

### THE FOUR CANDIDATES, RANKED, AND WHAT SEPARATES THEM

1. **Lookahead geometry mismatch (this one).** Predicts: tracking error grows with SPEED in a way
   the gain blend cannot flatten, and ringing exists at all curvatures rather than only inside
   [0.0007, 0.001]. **Distinguishing test: does the error/ringing scale with v_ego?**
2. **Under-compensated lag.** Predicts: `liveDelay.lateralDelay` sits at or above the 0.15 s clip.
   **One number settles it and it is published.**
3. **The gain band.** Predicts: ringing concentrates INSIDE the 1000-1429 m radius band and is
   quiet outside it.
4. **The rate limiter.** Predicts: ringing is dense in `angleRateLimited` frames.

`tools/bp_lateral_ringing.py` measures 2, 3 and 4 directly and 1 by the speed split. **They are
mutually distinguishable, which is the point of measuring rather than arguing.**

### MEASURED 2026-08-27: IT IS HIS LATENCY READ. CANDIDATES 1, 3 AND 4 ARE DEAD.

Routes 000003db and 000003de, 44,960 qualifying frames, hands off, latActive, gradual curves:

    liveDelay.lateralDelay   min 0.381  p50 0.381  p90 0.382  p99 0.382  max 0.382
    AT OR ABOVE THE 0.15 s CLIP:  309,273 of 309,273 samples  = 100.0%

**The car learns a 0.38 s lateral delay and the controller compensates for 0.15 s of it.** Three
numbers exist for one physical quantity -- 0.38 learned, 0.22 declared as `steerActuatorDelay`,
0.15 actually used -- and the smallest wins.

    gain band     1.05 revs/s INSIDE [0.0007, 0.001] vs 0.96 OUTSIDE   -> no effect. DEAD.
    rate limiter  angleRateLimited never fired on either route          -> DEAD.
    speed scaling revs/s 1.19 / 1.03 / 0.77 / 1.18 by speed bin         -> not monotonic. DEAD.

The gain band was MY hypothesis and it is wrong. His was right.

**AND HIS MID-TRIP TUNING IS VISIBLE AND DID NOTHING**, which is the confirmation:

    000003db   low 0.912  high 0.818   0.69 revs/s
    000003de   low 0.92   high 0.83    0.74 revs/s

Different gains, same ringing. *"Nothing really made steering perfect"* -- because gains were
never the variable.

### BUT DO NOT RAISE THE CLIP. THE COMMENT ABOVE IT IS A BUG REPORT.

    # liveDelay can calibrate up to ~420ms on some runs, which inflates VLT to 0.6s and pushes the
    # model lookahead 5m into the curve. At that depth the model sees full peak curvature,
    # kappa_entering stays True, and the exit-biased blend is permanently disabled -- causing the
    # car to command max path_angle through the entire apex.

Somebody already hit the failure raising it causes, and **the measured 0.381 s is exactly the
"~420 ms" they were defending against.** Raising the clip trades the ping-pong for max steering
through every apex, which is far worse.

**THE ACTUAL DEFECT IS THAT ONE NUMBER IS DOING TWO JOBS.** `lateralDelay` is used both to
compensate ACTUATOR LAG and to choose HOW FAR DOWN THE MODEL PATH TO SAMPLE. 0.15 s is correct for
the sampling depth and badly wrong as lag compensation. A single clip cannot serve both, so it
serves the one whose failure was noticed first.

**THE FIX IS TO SEPARATE THEM**, not to move the clip: keep the sampling depth capped where it is,
and compensate the real delay somewhere that does not move the model lookahead. That is a design
change, and it is the whole job of this branch.

**NOT ATTEMPTED TONIGHT, deliberately.** Writing it at 1 am and handing it to him before a long
drive is the 5.20 pattern with a different controller. The measurement is the deliverable; the
redesign needs a rested day and a deliberate test drive.

### AND THEN THE LAG STORY FELL OVER TOO. THE COMPENSATION IS ALREADY CORRECT.

Chased one layer further the same night, and the "we under-compensate the delay" conclusion above
is WRONG. Three checks killed it:

1. **The 0.381 s is genuinely learned, not a seed.** `lagd` publishes `self.initial_lag` whenever
   status != estimated, and `initial_lag = CP.steerActuatorDelay + 0.2` = 0.42 here. Measured:
   `status = estimated` on 8,250/8,250 samples, `validBlocks = 50` (the maximum), `estimateStd`
   0.0067, value 0.3806-0.3819. Converged, tight, and not 0.42.

2. **His car already compensates with that value.** `get_lat_delay()` returns `LagdValueCache` when
   `LagdToggle` is set. Read off the device: **LagdToggle = 1, LagdValueCache = 0.38063**. So
   `modeld` and `controlsd` are using 0.3806 -- the learned number. `steerActuatorDelay = 0.22` only
   seeds `initial_lag`; it is not the compensation.

3. **MY RINGING METRIC WAS COMPARING TWO DIFFERENT INSTANTS.** `controlsState.desiredCurvature` is
   LAG-ADJUSTED -- its own comment says so -- so it is the curvature wanted ~0.38 s from now.
   Comparing it against `curvature` on the SAME frame is the "print both on the same frame" trap
   arriving as a TIME offset instead of a units one. Shifted by 38 frames at 100 Hz:

       NAIVE  (same frame)      1.85 revs/s   mean |err| 0.000321
       SHIFTED (like-for-like)  2.37 revs/s   mean |err| 0.000231

   The magnitude drops 28% -- so part of what was measured WAS the lookahead -- but the reversals
   RISE. **The oscillation is real**, about 1.2 Hz, which is the natural frequency of a loop
   carrying 0.38 s of delay. So the delay is real, correctly compensated, and the loop still rings.

### WHERE IT ACTUALLY SITS: DECODE THE WIRE. AND THE ADDRESS WAS WRONG FIRST.

`bp_path_angle_final` is never published to capnp, so the command is only visible on CAN. First
attempt decoded `LateralMotionControl2` (982) and found **zero frames**; a histogram of `sendcan`
showed 970/979/394/984 and no 982. **His car sends `LateralMotionControl` (979), and the start bit
differs too -- 31, not 28.** Checked rather than assumed, after assuming wrong once.

    planner desiredCurvature   2.37 reversals/s     the noisiest
    command on the wire        1.17 reversals/s     smoothed by rate limit + 0.0005 rad quantisation
    car's response             1.60 reversals/s     ROUGHER than the command

**READ THAT AS A CHAIN, NOT A CULPRIT.** The first version of the tool printed "THE PSCM IS THE
OSCILLATOR" off a threshold, on 1.17 vs 1.60 -- a 27% difference it called "far smoother". That
verdict is removed. Both a wobbly desired signal AND an amplifying PSCM are consistent with these
numbers, and **separating them needs a step input no ordinary drive contains.**

### SO THE STATE OF IT, HONESTLY

- his settings, the fingerprint, our two deleted lines, the gain band, the rate limiter, the speed
  scaling and the lag compensation are **all cleared by measurement**
- the ringing is **real**, ~1.2 Hz, and survives every correction applied to the metric
- the planner's desired curvature is the noisiest signal in the chain and the PSCM amplifies what
  it is given -- **neither is separated yet, and neither is tuned by anything in this repo**
- three of my own hypotheses died in one night. His two calls -- "it's been like this a while" and
  "it almost seems like a latency thing" -- were both closer than mine, even though the latency one
  turned out to be correctly compensated. The delay is real; the compensation is not the bug.

### AND THEN EVERY AGGREGATE ABOVE TURNED OUT TO BE MEASURING NOISE

He pushed back on being asked to drive a test pattern -- *"I just feel like I've driven 300+ miles
today, how is that not enough information"* -- and he was right twice over. The conditions were
already in the logs (an interstate trip is full of straights and gentle curves at every speed), and
filtering for them exposed that **the whole night's metric was the wrong size**:

    condition       des/s   cmd/s  resp/s     cmd p2p      steer p2p
    STRAIGHT         1.20    0.67    0.99   0.0015 rad      0.30 deg
    gentle 30-45     0.47    0.31    0.18   0.0010 rad      0.10 deg
    gentle 45-60     0.57    0.37    0.29   0.0010 rad      0.10 deg

**0.10-0.30 DEGREES of steering.** Nobody feels a third of a degree at the wheel. Every reversal
rate computed above -- 1.2 Hz, the lag-shift comparison, the wire diff -- was characterising
background dither, not his symptom. Rates over 300 miles average episodic events into invisibility,
which is exactly what happened. **Ask how BIG before concluding from how OFTEN.**

### THE REAL EVENTS, FOUND BY LOOKING FOR EPISODES INSTEAD OF RATES

`tools/bp_lateral_episodes.py`: windows of >= 2 deg swing with >= 3 reversals in 2 s, hands OFF,
latActive. **301 episodes on two routes**, 20-45 deg of swing, peaking at 45-60 mph:

    by speed   15:13   30:69   45:112   60:72   75:35

and the unmistakable ones are on GENTLE radii, which is his report exactly (*"It's on larger curves
too, yes"*):

    000003de  t+1751.7   25.7 deg   8 reversals   55 mph   radius 1327 m
    000003db  t+134.8    23.1 deg   8 reversals   58 mph   radius  894 m

A 1327 m curve at 55 mph needs ~3-4 deg of steering and got 25.7 with eight reversals.

### WHAT A CLEAN HANDS-OFF EPISODE ACTUALLY SHOWS: THE CAR UNDER-DELIVERS

Route 000003db, t+134, 57 mph, hands off, curve tightening 333 m -> 269 m:

    t+134.01   desired 0.00300   actual 0.00235   err 0.00065
    t+134.29   desired 0.00372   actual 0.00267   err 0.00104

**One-signed and GROWING. Actual curvature is ~72% of commanded and the gap widens as the curve
tightens.** That is not ringing -- it is the car failing to deliver the commanded curve on entry.
Under-steer first, then whatever catches up produces the swing that reads as oversteer.

**AND THE FIRST DUMP OF THIS WAS CONTAMINATED**: `bp_lateral_dump.py` does not filter
`steeringPressed` the way the episode finder does, so the first window pulled was 100% hands-ON --
his own steering, read as the controller's. Same split that corrected the 3.21 m/s^2 figure. **Any
lateral window must be checked for hands before a single number is read off it.**

### WHERE THIS LEAVES IT

The question is no longer "why does it oscillate" but **"why does the car deliver only ~72% of the
commanded curvature on entry, and what closes that gap afterwards"**. That is a different and much
more tractable question, and it points at the actuation chain -- the command scaling
(`FordHighSpeedFactor_ang` is 0.828 at this speed, deliberately reducing the command) and the PSCM's
own response -- rather than at any oscillation mechanism.

### AND HIS FRIEND'S CAR SETTLES IT: THIS IS NOT TUNING, ON ANY CAR

*"Remember, my friend gets the same behavior and he has tried changing all his settings."* Plus, on
his own: *"Who knows if my settings are right!? I don't know!"* -- so the earlier instruction not to
question them is lifted, and it no longer matters, because a SECOND CAR WITH DIFFERENT SETTINGS HAS
THE SAME SYMPTOM. That single fact rules out his settings, his fingerprint, his steerRatio and his
retrofit PSCM in one stroke, and it explains why two independent tuning sweeps both failed.

**MEASURED, and the factor is exonerated on his car too.** Delivered vs commanded curvature,
steady-state only (desired stable for 0.5 s, so this is what the car SETTLES at rather than how
fast it gets there), 13,022 qualifying frames:

    speed        median delivery   where the gain blend sits
    30-40 mph        0.890              16% toward the high-speed factor
    40-50 mph        0.875              50%
    50-60 mph        0.652              83%     <- worst
    60-70 mph        0.807             100%
    70-80 mph        0.930             100%

**If the gain factor were the cause the ratio would fall as the blend completes and STAY low.** It
does not -- it dips hard at 50-60 and recovers to 0.93 by 70-80, while the blend is saturated across
both. Not the factor. `FordHighSpeedFactor_ang` is cleared by measurement, not by deference.

**AND 50-60 MPH IS ALSO WHERE THE EPISODES PEAK** -- 112 of 301, from the independent episode
finder. Worst delivery and most ping-pong in the same band, found two different ways.

**WHAT IS ESTABLISHED:** the car delivers a median ~87% of commanded curvature with enormous spread
(p25 0.39-0.73), worst at 50-60 mph, on two different cars with different settings. **It is in the
shared angle-control code.**

**WHAT IS NOT:** which part. The `path_angle = kappa * v_ego * curvature_factor` geometry against
the PSCM's own `1/2 * kappa * d_ref` remains the standing suspect (see candidate 4 above, and
`pscm_d_ref_m()` is still dead code) -- but the arithmetic there predicts the command is ~15x the
PSCM's implied geometry, which would over-steer rather than under-deliver. **That contradiction is
unresolved and is the next thing to chase.** Do not patch the formula until it is.

**WORTH ASKING HIM:** what car the friend drives. If it is not a retrofit, hardware is out entirely;
if it is not a Ford, the bug is above the Ford layer and the search moves to openpilot's own lateral
planner.

### THE UNDER-DELIVERY IS REAL -- CONFIRMED AGAINST THE GYRO, AND steerRatio IS CLEARED

`controlsState.curvature` is NOT measured; it is the steering angle through the vehicle model, which
uses our DERIVED `steerRatio = 17.07`. So a ~15% steerRatio error would manufacture the entire
under-delivery finding. `livePose.angularVelocityDevice.z / v` owes nothing to steerRatio:

    vehicle-model / desired   0.840
    IMU yaw-rate  / desired   0.899      <- ground truth
    IMU / vehicle-model       1.044      <- they agree within 4.4%

**steerRatio is fine, and the car genuinely delivers only ~90% of commanded curvature** -- 79% at
50-60 mph by the gyro. Cleared by measurement, not by deference.

### 50-60 MPH NOW APPEARS THREE INDEPENDENT TIMES, AND THERE IS A CONSTANT SITTING IN IT

    episode finder     112 of 301 episodes peak at 45-60 mph
    delivery ratio     worst at 50-60 (0.652 vehicle, 0.790 IMU)
    IMU confirmation   same band, same dip

    _VLT_V_LOW_MS   = 25 mph    full extra lookahead
    _VLT_V_HIGH_MS  = 55 mph    NO extra lookahead at or above

**The pre-steering lookahead tapers to exactly zero at 55 mph**, dead centre of the band. Below it
the controller looks ahead up to `_VLT_T_EXTRA_MAX` 0.10 s; at 55 it stops. That is independent of
the gain schedule, which is why no gain explained the dip.

**NOT ACTED ON, and the reason matters:** delivery RECOVERS to 0.92 by 70-80 mph, which "no
lookahead above 55" does not predict -- it predicts staying bad. The recovery may be confounded
(70-80 mph is interstate, where curves are gentler and lookahead matters less). **Separate the
confound before touching `_VLT_V_HIGH_MS`:** compare delivery at matched CURVATURE across the 55 mph
line, not pooled by speed.

### WHAT PR #192 DOES AND DOES NOT DO, MEASURED ON HIS OWN 847k FRAMES

    whole drive   gain swing -74%   COMMAND swing  -2%
    in its band   gain swing -91%   COMMAND swing -11%

It calms the command where it acts and **does not address the under-delivery at all**. Two separate
problems; this is a partial fix for one of them.

**AND THE HARD LIMIT ON ALL OF THIS:** offline replay shows what the COMMAND does and can never show
what the car does back. `actual curvature` is the PSCM physically responding and no model of it
exists here. Whether desired comes to match actual is a DRIVE question.

**ALSO NOTED, and not yet chased:** `FordPathAngleBlendRatio` (default 0.50) blends PREDICTED
curvature into the command, `pred * b + desired * (1-b)`. He raised blending himself. A high blend
follows the model's prediction rather than the lag-adjusted target, and prediction and reality
diverge most on gentle curves. It is a fifth candidate and it is a PARAM, so it is his to move --
name it, do not change it.

### THE FIRST MEASUREMENT, WHICH NEEDS NEITHER THE CAR NOR A DRIVE

From the trip rlogs already on the laptop:

1. During ping-pong episodes, does `kappa_cmd` sit in or near `[0.0007, 0.001]`? If it is pinned
   above 0.001 the whole time, this mechanism is wrong and the band is innocent.
2. Is `curvature_factor` oscillating there? It is not published -- **`bp_path_angle_gain_*` and
   `bp_path_angle_final` are**, so check what actually reaches the wire before adding a field.
3. Characterise the ringing itself: sign changes of (commanded − actual) steering angle on curves
   of 300-1500 m radius, hands OFF, `latActive`, split by speed across the **13.5-26.82 m/s**
   (30-60 mph) gain blend. If it rings on only one side of that blend, the blend is implicated.

**Do not conclude "he cornered hard" from `steeringPressed`** -- same split that corrected the
3.21 m/s^2 figure. And print the steering-derived lateral acceleration beside `currentLateralAccel`
ON THE SAME FRAME; the bicycle model reads ~35-47% high at highway speed.

## 2026-08-28: THE BLEND AVERAGES THE ROAD IN TWO DIFFERENT PLACES. THAT IS THE PING-PONG.

*"Yes, fix that. But also steering wasn't that great... It still did the turn to far and then
over-correct."* Routes 000003eb and 000003ec, his FINAL settings, decoded off-device.

### FIRST: THE 55 MPH CONSTANT IS EXONERATED. DO NOT "FIX" IT.

He authorised that fix and the evidence refused it, which is worth recording as a result rather
than as a thing left undone. Pooled by speed, delivery dips hard at 50-60 mph, exactly where
`_VLT_V_HIGH_MS` tapers the extra lookahead to zero. At MATCHED CURVATURE the dip is gone
(`tools/bp_lateral_matched.py`, hands off, steady state):

    radius          35-45 mph   45-55 mph   55-65 mph   65-80 mph      step at the line
    2000-1000 m       0.288       0.599       0.920       0.884        +0.292  BETTER above
    1000- 500 m       0.885       0.857       0.901       0.932        +0.045  no step
     500- 286 m       0.997       0.854       0.878       1.054        -0.041  no step

**No band is delivered worse above 55 mph.** The pooled dip is road type -- 50-60 mph is canyon and
arterial, 60-80 is interstate -- which is precisely the confound the standing note in this file
warned about, and the reason it said to match on curvature before touching the constant. The IMU
rows agree with the vehicle model throughout (0.70/0.70/0.90/0.90), so this is not a steerRatio
artifact either. The taper's stated justification -- "PSCM responds faster at high speed" -- remains
an assumption nobody has measured, but it is not costing him anything.

### THE MECHANISM, AND IT EXPLAINS BOTH HALVES OF WHAT HE FEELS

`actuators.curvature` is already lag-compensated by modeld:

    lat_action_t = get_lat_delay(...) + DT_MDL + DT_MDL/2 = 0.393 + 0.075 = 0.468 s on his car

`lateral_angle_ext` then blended in a model sample of its OWN, taken at
`clip(liveDelay, 0.1, 0.15) + DT_MDL` = **0.20 s at highway speed**, at a hardcoded b = 0.50:

    requested = predicted(0.20 s) * 0.50 + desired(0.468 s) * 0.50

**Those are not two estimates of one quantity. They are the road in two different places.**
Averaging them drags the aim point back to ~0.33 s while the PSCM still takes 0.468 s to arrive.

Measured, hands off (`tools/bp_lateral_horizon.py`):

    horizon gap                     0.215 s at 25-45 mph rising to 0.268 s at 65-80 mph
    road TIGHTENING ahead  n=6989   command 5.9% SHORT   p90 16.6%   -> turns in late, then overshoots
    road OPENING OUT       n=3033   command 3.5% LONG                -> slow unwind

and because it is a HORIZON error it concentrates exactly where the road is changing:

    curvature change rate      median loss    p90
    < 0.0005 (straight)           0.9%        5.8%
    0.0005-0.002                  7.0%       17.5%
    0.002-0.005                   9.6%       28.1%

**A gain cannot fix this, which is the whole reason two tuning sweeps failed.** A gain scales the
stale component along with the correct one. That is why he went from 0.912/0.828 all the way up to
**1.197/1.163** and changed the feel without touching the symptom, and why his friend's car does it
on entirely different settings: the constants are upstream's and identical on both.

### THE FIX: SEPARATE THE TWO JOBS. THE CLIP STAYS.

This file already prescribed it -- *"keep the sampling depth capped where it is, and compensate the
real delay somewhere that does not move the model lookahead"* -- and the place that does not move
the model lookahead is the BLEND, because `desired` was never the thing that was wrong.

    _t_base         DECISION depth, feeds _kappa_entering.  STILL clip(delay, 0.1, 0.15) + DT_MDL.
    _t_blend_base   SAMPLING depth for the blend.           clip(delay, 0.1, 0.45) + DT_MDL*1.5.

**The documented apex failure runs through `_kappa_entering`, and `_kappa_entering` does not read
the new value.** That is what makes this safe to do at all: raising the base wholesale is the bug
the 0.15 clip exists for (kappa_entering latches True, the exit-biased blend is disabled, the car
commands max path_angle through the whole apex), and the entry decision is untouched here.

Mutation-tested, and both halves matter: restoring the old base kills 5 tests, deleting the apex
clip kills exactly the guard test written for it. `test_lateral_blend_horizon.py`.

### AND THE EXIT-BIASED BLEND HAS NEVER RUN. IT IS THE BAND-AID FOR THE OTHER HALF.

`_desired_falling` asks for `abs(desired) < abs(last) - 0.010` between consecutive angle-path calls.
Measured over the interval that comparison actually spans -- **0.05 s, because `update_angle_strategy`
runs inside `STEER_STEP = 5`** -- across 239,038 intervals:

    would fire            129   0.054%
    threshold             0.0100 1/m
    p99 fall observed     0.0019 1/m        <- the threshold is 5.4x it

With `_pscm_lim` silent in angle mode and `_dbc_sat` needing ~26 degrees of path angle, **`b_blend`
is 0.50 on essentially every frame** and a mechanism the code describes as dropping model weight to
~15% on exits does not run on the curve exits it was written for. Its comment records being scaled
x5 (0.002 -> 0.010) to preserve a 0.2 (1/m)/s trigger rate; that rate came from another branch and
is 5.4x anything this car's planner does.

**Deliberately NOT changed in the same commit.** The horizon fix removes the over-command on exit at
its root -- which is the slow unwind the exit bias was compensating for -- so enabling the band-aid
in the same breath would produce a drive that cannot say which one moved. Re-measure the exit side
after this drive before touching the threshold.

**And a first pass at this measured 100 Hz frame-to-frame falls against a 20 Hz threshold and made
the trigger look five times deader than it is.** Same family as comparing a lag-adjusted signal
against a same-frame one: right numbers, wrong interval. Check what interval a comparison spans
before scoring it.

### TWO COMMENTS THAT NAMED PARAMS THAT DO NOT EXIST

`FordPathAngleBlendRatio` and `FordVLTExtraMax` are named in `lateral_angle_ext` comments, in this
file, and in two diagnostic tools' watch lists. **Neither is in `params_keys.h` and nothing reads
either one.** Both are hardcoded constants. The note in this file calling the blend ratio "a PARAM,
so it is his to move -- name it, do not change it" was wrong on the facts, and it is the reason the
blend went unexamined for a week: it was filed as his setting when it was our constant. Comments
drift from defaults; this is the same rule one layer out.

### WHAT THE REVERSAL SPLIT SAYS, AND WHY IT IS NOT A CULPRIT

`tools/bp_lateral_blame.py` attributes every tracking-error sign reversal to whichever side moved
more than twice as far across it. 708 qualifying reversals, hands off:

    command reversed   222   31.4%
    car reversed       250   35.3%
    coupled            236   33.3%

and flat across every speed band. **Neither side leads.** That is what a loop looks like, and it is
why "is the planner jittering" and "is the PSCM overshooting" were both the wrong question -- each
was live as a hypothesis and the data refuses both. The 2x dominance threshold is deliberately
blunt, because a verdict rendered off a 27% difference is what produced the withdrawn "THE PSCM IS
THE OSCILLATOR".

## 2026-08-29: THE BLEND FIX WORKS. BIG EPISODES DOWN 65%, SMALL DITHER UNCHANGED.

*"I think lateral was better, but still not great."* Both halves of that are in the data, and they
are different phenomena.

**THE COMPARISON IS ALMOST PERFECTLY MATCHED, by luck.** Route 000003ed (191 segments, 159 minutes
hands-off) ran gains **1.197/1.143** with lane centering 0.55 and the blend fix. Yesterday's
000003eb/ec ran **1.194/1.146-1.163**, lane centering 0.55 (0.25 for part), no fix. Same settings,
so the fix is very nearly the only variable. His later sweep down to 0.957/0.829 happened in
000003ee-f1, AFTER the long drive -- do not mix those in.

**COUNT EPISODES PER MINUTE OF EXPOSURE, NEVER RAW.** Raw counts read 1425 vs 254 and look like a
massive regression; 1352 of the 1425 are at 60-75 mph because that drive was interstate while
yesterday's was mixed surface roads. `tools/bp_lateral_rate.py` divides by minutes actually spent
hands-off and latActive in each band. **Fifth instance of the denominator error in this file.**

    >= 2 deg swing (every small wobble)        >= 8 deg swing (what he would feel)
    speed      no fix   fix    change          speed      no fix   fix    change
    30-45       19.40  16.93    -13%           45-60        1.78  0.80     -55%
    45-60       15.37  13.58    -12%           60-75        0.53  0.18     -66%
    60-75       11.13  12.07     +8%           75-95        0.42  0.30     -29%
    75-95       14.77  13.30    -10%           ALL          0.95  0.33     -65%
    ALL         13.63  12.83     -6%

**So the felt events -- turn in too far, correct back -- are down about two thirds, and the constant
small dither is unchanged.** That is exactly the shape of his report, and it says the remaining
problem is a DIFFERENT mechanism from the one fixed. Do not expect more from the horizon.

The 8-30 and 30-45 rows in the >= 8 deg table are 1.2 minutes of exposure each and are noise; do
not read the +3.22 as a regression.

**STEADY-STATE DELIVERY IS UNCHANGED AND THAT IS CORRECT.** Matched-curvature delivery moved 0.87
-> 0.88 overall. The fix targets the TRANSIENT; delivery at steady state measures GAIN. A metric
that cannot move is not evidence either way, and quoting it as "no effect" would have been wrong.

**HE INDEPENDENTLY WALKED THE GAINS BACK DOWN**, from 1.197/1.143 to **0.957/0.829** -- essentially
his pre-excursion 0.912/0.828 -- during 000003ee-f1. That is the predicted consequence: the
inflated gains were compensating for the shortfall the fix removed, so with it fixed the car was
over-eager and he dialled them out. He was not told to do this.

**WHAT IS LEFT.** ~13 episodes/minute of >= 2 deg wobble, unchanged. That is the next target and
it is not the blend. The reversal attribution (31% command / 35% car / 33% coupled, flat across
speed) says it is a loop rather than one side, so the candidates are the PSCM's own response and
the planner's desired curvature -- and `_desired_falling` being effectively dead (0.054% of
intervals, threshold 5.4x the p99 fall) means the exit-biased blend still never runs.

### AND EVERY PRE-FIX TIMING CONCLUSION IS SUSPECT

From the passing-assist session, confirmed on the road: before `2106064495`, drives threw 103
commIssue events with 96 carrying the all-three-plannerd-outputs-invalid signature; on the four
drives since there are 12, all in a startup cascade in the first two minutes, then **zero across
~113 segments and two hours**. `path_from_mapd` rebuilding the map path at 20 Hz against a 1 Hz
message really was it.

**So plannerd was stalling up to 17.6 ms per frame on curvy roads for every route up to 000003ec.**
Anything measured there that looked like a controller being LATE was measured through that stall.
The specific claim at risk is *"SCC-Map published the corner speed two seconds after peak
cornering"* on 000003c9. Re-check it on a post-fix drive before quoting it again.

### OPERATIONAL: WHAT COST AN HOUR TONIGHT

Three separate things, each of which looks exactly like "the car is offline":

- **A stale host key.** ICS re-leased the comma a new address and the old one was in known_hosts.
  With `BatchMode` the failure is silent -- TCP connects, banner exchanges, then it hangs at
  "Authenticating". `ssh -v` says `Host key verification failed` in one line. Fix with
  `ssh-keygen -R <ip>` then `-o StrictHostKeyChecking=accept-new`. **Prefer the hostname**, which
  follows the address.
- **Bitwarden locked.** `ssh-add -l` returning `agent refused operation` is the tell, and it is
  distinguishable from the above: a locked vault refuses, a bad host key hangs.
- **Hotel wifi.** His hotspot is ICS'd off it, so when the hotel drops, ICS resets the hotspot and
  the comma's link dies mid-transfer. A single-connection 3 GB stream died twice at ~1.2 GB.
  **Pull in small batches that resume from what is already on disk** -- 12 segments per batch
  survived it; one big stream did not.

### LATENCY IS NOT WHAT IS LEFT. THE PLAN ITSELF REVERSES.

*"Nothing it did wrong today felt small... make sure you look at everything related to latency."*
Both halves answered on 000003ed, `tools/bp_lateral_lag_residual.py`.

**THE COMPENSATION IS CLOSED.** Shifting `curvature` against `desiredCurvature` and scoring only
turning, hands-off frames:

    learned lateralDelay          0.392 s
    modeld aims (lat_action_t)    0.467 s
    best-fit shift, all turning   NO minimum -- error rises monotonically 0.00248 -> 0.00290
                                  across 0 to 0.9 s, so no shift improves the fit
    best-fit inside >= 8 deg      0.500 s, residual +0.033 s

33 ms, and even that is suspect: the wobble is ~1.2 Hz (0.83 s period) so a 0.5 s shift sits near
antiphase and can manufacture a minimum. **There is no uncompensated lag left to find.** Do not
spend another evening on `steerActuatorDelay`, the 0.15 clip, or LagdValueCache.

**AND HIS "NOTHING FELT SMALL" RETIRES THE >= 2 DEG METRIC.** The 12-17 episodes/minute of >= 2 deg
wobble is not what he feels; every conclusion about the remaining problem must be drawn from the
>= 8 deg population, which runs 0.32/minute on 000003ed.

**WHAT THE BIG ONES ACTUALLY ARE: the DESIRED curvature reverses sign.** t+9115.7, 83 mph, on a
steady ~500-600 m bend:

    9114.0   desired -0.00188   R  532 m   left
    9115.6   desired -0.00003   R  huge    straight
    9116.2   desired +0.00077   R 1298 m   RIGHT -- opposite lock
    9117.8   desired -0.00199   R  502 m   back to left

`actual` tracks it, lagging but faithful. **The car is not overshooting a clean command; it is
correctly executing an S-curve the road does not have.** That is `controlsState.desiredCurvature`,
which is `modelV2.action.desiredCurvature` through `clip_curvature` -- UPSTREAM of every Ford-side
gain, trim and limiter. Period ~4.7 s, far slower than the 1.2 Hz dither, so it is a slow limit
cycle: the model commands, the car arrives 0.47 s later, the model reads the late position and
corrects the other way.

Nominal steering for that curve at that speed is ~2.2 deg. The episode swings 15.3.

**SO THE NEXT SUSPECT IS A POSITION LOOP, NOT AN ACTUATOR ONE.** The candidate worth checking first
is the lane centering trim, because it is the only thing in the stack that closes a LATERAL POSITION
feedback loop, and he raised it from 0.25 to 0.55 immediately before this drive. Its correction is
computed against where the plan will be versus lane centre, applied to `kappa_cmd`, and therefore
changes the car's path, which changes what the model sees, which changes the plan. With ~0.47 s of
delay in that loop it can ring, and a slow limit cycle is exactly the signature. **An earlier note
here called it "not a feedback loop that can wind up" -- that was wrong. It is not a loop through
the CONTROLLER; it is a loop through the ROAD and the model.**

**Do not act on that yet. It is a hypothesis from one dump.** The test is cheap and he can run it:
one drive at `lane_centering_strength_ang` 0.0 with everything else unchanged, scored with
`bp_lateral_rate.py --swing 8`. If the >= 8 deg rate falls, it is the trim.

### AND THE 65% FIGURE IS FIX **PLUS** TRIM, NOT FIX ALONE

Stated because it would otherwise be quoted as the fix's own number. 000003eb/ec ran
`lane_centering_strength_ang` **0.25**; 000003ed ran **0.55**. He raised it between the two drives,
so the 0.95 -> 0.33 episodes/minute improvement is both changes together. The gains were matched
(1.194/1.15 vs 1.197/1.143) and the fix is the larger and better-understood of the two, but the
trim is not controlled for and the honest attribution is "the two changes together".

### HIS NEW LOWER GAINS ARE WORSE. BOTH METRICS, SAME DAY, FIX HELD CONSTANT.

He dropped to 0.957/0.829 with dampening 0.69 after 000003ed, and 000003ee-f1 ran on them:

                        >= 8 deg/min    >= 2 deg/min
    3ed  1.197/1.143        0.32           12.93
    3ef  0.957/0.829        0.51           14.37

Worse on both, and worst where he drives -- 60-75 mph went 0.18 -> 0.33. **Tell him to put
1.197/1.143 with dampening 0.81 back**, and note it is a recommendation he applies, not a default
to push. Route mix is the usual caveat, but both routes are highway-dominated (3ed 156 min above
60 mph, 3ef 61 min), so this is better matched than most comparisons here.

### THE CURVE PING-PONG IS IN THE MODEL'S PLAN. NOTHING HE CAN SET REACHES IT.

*"It was just ping ponging so much on curves."* And, correcting me: *"But gains that high would make
it go too far, right? I started the drive with that and backed out."*

**HE WAS RIGHT AND I HAD RECOMMENDED THE OPPOSITE.** All 199 segments of 000003ed ran 1.197/1.143
with zero changes, so he drove 3.3 hours on those gains, felt it ping-pong on curves, and lowered
them afterwards. I had just told him to put them back, off a metric that ranked them better.

**WHY THE METRIC WAS WRONG: A 2 SECOND WINDOW CANNOT SEE A 4.7 SECOND CYCLE.**
`bp_lateral_episodes.py` and `bp_lateral_rate.py` use `WIN_S = 2.0`. The limit cycle found on this
same drive has a ~4.7 s period. Those tools measure fast wobble and are structurally blind to the
thing he reports, so ranking two settings with them was meaningless -- the same shape as the
episode detector that could only ever return zero for suppressed corners. **When the driver and the
instrument disagree, check the instrument's window against the phenomenon's period before believing
the instrument.**

`tools/bp_lateral_curve_cycle.py` uses 6 s windows, requires the road to be genuinely bent for the
whole window (|desired| above 6e-4 and never changing sign), hands off, >= 45 mph, and measures how
far `desired` swings about the curve's own mean and how often it re-crosses it. **It qualifies on
DESIRED, not on steering angle: angle scales with gain, so testing on angle marks any high-gain
setting worse by construction.**

**AND EVERYTHING HE CAN SET IS EXONERATED:**

    build / setting                          curve osc/min   median swing
    Yosemite 3d1  no lane centering, no fix       3.43            45%
    Yosemite 3dc  no lane centering, no fix       5.78            54%
    today 3ed     LC 0.55, blend fix, gains hi    3.91            48%
    today 3ef     LC 0.55, blend fix, gains lo    3.91            52%

**3.91 against 3.91 across his entire gain change.** Gains scale the command AFTER the plan is
made, so they cannot touch an oscillation that is in the plan -- which is also why no tuning sweep
has ever fixed this and why his friend gets it on different settings.

**AND IT KILLS THE LANE-CENTERING HYPOTHESIS I RAISED ONE ENTRY AGO.** The Yosemite rows predate
the bp-dev cherry-pick, so that build has no lane centering code in it at all, and it oscillates at
the same rate and the same amplitude. Do not re-raise the trim as the cause; the phenomenon is older
than the feature. (Yesterday's 3e2-3ea cannot referee it either way -- surface roads, 0.4 min of
qualifying curve-holding across all of them, and zero for the lane-centering-off group.)

**WHAT IS ACTUALLY LEFT.** `controlsState.desiredCurvature` is `modelV2.action.desiredCurvature`
through `clip_curvature`. It swings a MEDIAN 48-54% of the curve's own value while holding a steady
bend, on every build measured. On a 500 m curve the plan wanders between roughly 330 m and 1000 m
of equivalent steering. That is upstream of every gain, trim, limiter and blend this fork owns.

The one lever that could plausibly reach it is the MODEL ITSELF -- `ModelManagerSelectedBundle` is
unset on his device, i.e. the default bundle, and he has said he tried other models on the
California trip and *"nothing really made steering perfect"*. Scoring bundles with
`bp_lateral_curve_cycle.py` is the first measurement that could tell them apart on this specific
failure, and it needs one drive per bundle on roads with sustained curves.

**DO NOT propose another gain, dampening, blend-ratio or lane-centering change for this symptom.**
Four separate settings and one code fix have now been measured against it and none of them move it.

### AND HIS TIMELINE FACT IS THE DIAGNOSIS: ANGLE MODE HAS NO CLOSED LOOP ON CURVATURE.

*"Its been like this since path angle steering was added."* That is the sentence that explains the
whole thing, and it survives the objection that the oscillation lives in `desiredCurvature`, which
is computed upstream of the Ford angle code. **The loop closes through the ROAD**: the angle path
steers, the car moves, the camera sees a different view, the model re-plans. So the model's own
output is downstream of how well angle mode tracks.

    curvature mode   sends apply_curvature. The PSCM closes ITS OWN loop on curvature and keeps
                     correcting until the car is at the commanded value. Delivery ~1.0 by design.
    angle mode       sends path_angle = kappa_cmd * v_ego * curvature_factor. An OPEN-LOOP
                     feedforward conversion. There is no feedback term anywhere in the path.

And the conversion is measurably wrong in one direction: delivery is **0.87-0.93 everywhere, never
centred on 1.0**, confirmed against the gyro so it is not a steerRatio artifact. A persistent bias
inside a loop closed by something else is exactly what forces that something else to hunt: the
model sees the car drifting wide, asks for more curvature, the car still falls short, the model
asks harder, the correction lands 0.47 s late, the model backs off hard. Period ~4.7 s.

Weak but correctly-signed support on 000003ed: windows with delivery below 0.80 swing a median
**71%** against 46-50% for 0.80-1.00 (n=15 in the worst bin, so it is a hint, not a proof).

**THE FIX DIRECTION, AND IT IS NOT GOING BACK TO CURVATURE** (which he has refused twice and does
not need to): give angle mode the loop curvature mode gets free. A SLOW, CLAMPED feedback term on
(desired - actual) curvature, added to `kappa_cmd` upstream of the existing limiters the way
`lane_center_trim` already is, so the steady-state shortfall goes to zero and the model has nothing
left to chase.

**Do not rush it.** An integrator in a path carrying 0.47 s of delay oscillates worse than no
integrator if its gain is too high, and shipping a lateral change on one evening's reasoning is the
5.20 m/s^2 pattern. It needs: a rate limit, a magnitude clamp, freezing while `steeringPressed` or
during lane changes, and a bench check that it cannot wind up when the PSCM is saturated.

`tools/bp_lateral_curve_cycle.py` is the instrument for judging it -- curve oscillation per minute
of curve holding, which is the only metric so far that tracks what he reports.
### THE "SNAP-BACK" IS THE CAR LAGGING A RELEASE. I HAD IT BACKWARDS, AND THE WIRE SAID SO.

He asked for more examples before a fix, which is what caught this. `bp_lateral_snapback.py` found
126 events on 000003ed, 8.4 per minute of curve holding, median 38% of delivered curvature lost --
and a suspiciously identical 36-38% median on every other build and setting measured. That
constancy read as one clean mechanism. It is an artifact.

**Decoding LateralMotionControl (0x3D3) off sendcan for the worst event reverses the order:**

    t+4793.0   path_angle -0.110   desired 3.96   actual 3.82   steer -15.3 deg
    t+4793.8   path_angle -0.067   desired 2.53   actual 4.07   steer -16.2 deg
    t+4794.4   path_angle -0.034   desired 1.47   actual 3.07   steer -12.3 deg
    t+4794.6   path_angle -0.030   desired 1.48   actual 0.83   steer  -3.9 deg

`desired` collapsed FIRST. `path_angle` tracked it down faithfully. The car tracked `path_angle`.
Nothing abandoned a curve, and no limiter of ours fired -- measured, not assumed: inside those
windows `curvatureDeviationLimited` 0.13%, `humanTurnLateralPaused` 0.00%, `angleRateLimited`
1.14%, `stallBlipActive` 1.98%, so ~97% of frames have no limiter at all. The deviation clip is
also ruled out by arithmetic: `CURVATURE_ERROR` is 2.0 1/km and the gaps in these events are ~0.9.

**THE DETECTOR'S GUARD COULD BE SATISFIED BY THE OPPOSITE OF THE PHENOMENON.** `a0 >= 0.75 * d0`
was meant to mean "the car had reached the curve". At t+4794.1 actual was 4.07 against desired
2.01 -- ABOVE desired, lagging a release that had already happened -- and that passes a `>=` test
exactly as well as genuinely holding a curve does. The window then began after the collapse, so
`desired` looked steady across it.

So those 126 events are the EXIT HALF of the limit cycle already described: the plan swings, the
command follows it exactly, the car arrives ~0.4 s late, and that lag carries `actual` past
`desired` on every reversal. One mechanism, not two.

**AND IT KILLS THE FEEDBACK FIX THE PREVIOUS ENTRY PROPOSED.** There is no delivery shortfall to
integrate away here -- the car delivered 4.07 when asked for 2.01. An integral term on
(desired - actual) would push harder into precisely the excursions that hurt. **Do not build it.**
The steady-state under-delivery (0.87-0.93) is real and is a different regime from these
transients; any future feedback term has to be shown not to fire during a release before it ships.

**Third instance in this file of the same shape:** a detector whose qualifying test can be met by
the opposite of the phenomenon (after the SCC veto that could only return zero, and the funnel of
marginals that hid an exclusion). **Check a top-ranked event against raw data before believing a
rate.** `tools/bp_lateral_wirewin.py` decodes path_angle for one narrow window and is the cheap way
to do it.
## 2026-08-29: THE LAG IS THE CAR, THE PLAN DOES NOT REACT TO IT, AND WE OVER-COMPENSATE.

He asked for both open questions measured before any fix: *"Go measure both of these. I need this
fixed, I am driving 600 more miles soon."*

**Q1 -- THE LAG IS THE PSCM, NOT OUR CADENCE.** `tools/bp_lateral_loop.py` shifts the commanded
path_angle ON THE WIRE (0x3D3 off sendcan) against the steering angle, so the interval contains only
the PSCM and the mechanics -- everything upstream is already baked into path_angle by then.

    levels fit, turning frames, hands off     0.310 s   (peak r 0.979, but the curve is FLAT:
                                                         0.966 at zero shift, 0.965 at 0.80 s)
    DERIVATIVES, transients only              0.230 s   (r 0.083 -> 0.178 -> 0.048, a clean peak)

Our command cadence is `STEER_STEP = 5` -> 20 Hz, i.e. ~0.075 s of quantisation plus zero-order
hold. **Going to 100 Hz buys back under a fifth of the delay.** Use the derivative figure; the
levels fit is soft because both signals have nearly the same shape at every shift.

**Q2 -- THE PLAN DOES NOT REACT TO THE CAR BEING LATE.** Correlating the tracking error e(t) with
d(desired)/dt at t+lag peaks at **r = +0.09** (strongest at 0.80 s; it is -0.20 at zero lag, which
is the algebraic relation, not a response). **Reducing the lag will not quiet the plan.** The
oscillation originates in the model and the lag only amplifies it into what he feels.

**AND THAT KILLS THE "ANGLE MODE HAS NO CLOSED LOOP" FIX DIRECTION FROM THE ENTRY ABOVE.** If the
plan were hunting a persistent under-delivery, error would lead plan change. It does not.

### AND THE ACTUAL FINDING: WE AIM ROUGHLY TWICE AS FAR AHEAD AS THE CAR NEEDS

    modeld compensates   lat_action_t = LagdValueCache + DT_MDL + DT_MDL/2 = 0.468 s
    the PSCM responds in                                                     0.230 s
    over-compensation                                                       +0.238 s

The wire agrees: at t+4793.8 on 000003ed, `desired` was 2.53 1/km while `actual` was **4.07** -- the
car doing MORE curvature than the plan currently wanted, which is what aiming too far ahead
produces. It reaches the future curvature early, overshoots, and then the plan falls away.

**`FordBlendHorizonScale` ships at 1.0 (no change) with a settings control.** It scales
`_t_blend_base` as a fraction of `lat_action_t`; ~0.55 lines the sample up with the measured
response. It is a road question and he has 600 miles to answer it with.

### THREE THINGS RULED OUT ON THE WAY, EACH BY MEASUREMENT

- **`clip_curvature` is not distorting the plan.** Its ISO jerk limit is `MAX_LATERAL_JERK / v^2`
  = 0.0045 1/m/s at 75 mph. Observed |d(desired)/dt| reaches it on 1.7% of frames with NO pinning
  signature (0.12% in the 99-100% band). And its returned `curvature_limited` flag does **not**
  include the rate limit at all -- only the lateral-accel and max-curvature clamps -- so the jerk
  limiter is silent and must be measured by its signature, never read off a flag.
- **Averaging the model's PATH does not help.** Point sample at `lat_action_t` vs the mean over
  +-0.45 s around it: frame-to-frame wobble 0.0000375 -> 0.0000341, **-9.1%**. The whole path moves
  together each frame; the model is re-planning the road, not jittering one point. So no smoothing
  WITHIN a frame can fix it.
- **A first pass reported `clip_curvature` modifying 26.66% of frames.** That was pairing noise --
  modelV2 is 20 Hz and controlsState 100 Hz, and pairing one action with the next controlsState
  frame manufactures differences during transients. The median difference is exactly 0.

### AND THE SUNNYLINK AUDIT WAS BLIND TO AN ENTIRE SETTINGS SCREEN

Adding `FordBlendHorizonScale` surfaced it. `bp_sunnylink_settings_audit.py` reported **35/35
reachable, 0 missing** while the new control was not in `settings_ui.json` at all. Two causes,
stacked, and the second is the serious one:

- `OUR_PREFIXES` had `FordSynthesize` and `FordPref` but not the lateral family. Widened to `Ford`.
- **`ITEM_CALLS` listed only sunnypilot's four `*_item_sp` constructors.** `UI_DIRS` has always
  included `selfdrive/ui/bp/layouts/settings`, but that screen builds controls with
  `float_control_item` / `int_control_item` / `toggle_item` -- so the audit walked the file,
  recognized nothing, and reported compliance. **A scanner that reads the right files and
  understands none of their calls is indistinguishable from a clean audit.**

That is why `FordLowSpeedFactor_ang`, `FordHighSpeedFactor_ang`, `FordHighSpeedDampening_ang` and
the lane-centering trio were in `settings_ui.json` only because somebody hand-edited the JSON --
and **regenerating from the YAML source would have silently deleted three of them**, which is the
exact failure the COMMA 4 section warns about. Verified by regenerating with my YAML change stashed:
60 lines deleted. All three are now ported into `settings_ui_src/pages/vehicle.yaml`, the generator
is idempotent, and the audit reads **39/39**.

**Check the audit by adding a setting and watching it FAIL first.** It has now been green through
two different structural blind spots.
### SCC-MAP VETO, RE-MEASURED POST-FIX: 13 REAL CORNERS SUPPRESSED, ZERO PHANTOMS -- AND IT IS COMFORT, NOT SAFETY

The pre-fix veto numbers were measured through plannerd stalls and were flagged as suspect. Route
000003ed (post-fix, `bp_scc_veto_cost.py`):

    veto episodes where the map was inventing a corner :  0
    veto episodes that suppressed a REAL corner        : 13
    ratios                                              0.9-1.0x  (the map's radius MATCHED the
                                                                   radius he actually drove)

So the camera veto is now wrong in every case it fires here -- the opposite of the Yosemite picture,
where phantoms were the problem. **But check what it cost before treating it as urgent**, which the
3.0x verdict column cannot do:

    t+5640    peak lat 2.98 m/s^2 @ 73 mph, radius 353 m, hands OFF
    t+5662    peak lat 2.84        @ 71 mph, radius 357 m, hands OFF
    t+10409   peak lat 2.68        @ 70 mph, radius 367 m, hands ON

openpilot's own p99 on this car is 2.73 and his hands-on p99 is 4.14; the 2026-08-23 event that
nearly put him off the road was 5.20. **These are firm corners taken at the controller's normal
ceiling, not the 5.91 pattern.** It is a comfort cost and it does not warrant a change before a long
drive -- do not hand him a second setting to move while the lookahead scale is the live experiment.
### THE OVER-COMPENSATION FINDING WAS WRONG. `FordBlendHorizonScale` IS REMOVED. HIS QUESTION CAUGHT IT.

He asked *"What is the live learn steering latency thing? Wouldn't that fix this?"* -- and reading
`lagd.py` to answer him properly is what exposed the error, one setting away from a 600-mile drive.

**THE TWO NUMBERS WERE MEASURED BETWEEN DIFFERENT ENDPOINTS:**

    commanded path_angle (wire, 0x3D3) -> STEERING ANGLE     0.230 s   <- what I measured
    commanded curvature -> YAW RATE (the car rotating)       0.370 s   r=0.988, same drive
    lagd's own learned lateralDelay                          0.393 s   <- agrees

`lagd.update_points()` correlates `la_desired = desiredCurvature * v^2` against
`la_actual_pose = yaw_rate * v` -- the command against the CAR ACTUALLY ROTATING, taken from
`livePose`. My figure was the command against the WHEEL MOVING. The ~0.14 s between them is tire
slip and the vehicle's own yaw response, and **the compensation must aim at the second one.**

So `lat_action_t = 0.393 + DT_MDL + DT_MDL/2 = 0.468` is self-consistent and correct. There is no
over-compensation, the "we aim twice as far ahead as the car needs" claim is withdrawn, and
`FordBlendHorizonScale` -- param, UI control, SunnyLink entry -- is removed rather than parked at a
neutral default, because its whole reason expired. Setting it to the recommended 0.55 would have
UNDER-compensated and partly undone the blend-horizon fix that measured well.

**COMPARE ENDPOINTS BEFORE COMPARING LAGS.** This is the "print both on the same frame" rule
arriving as a choice of WHAT to correlate rather than WHEN, and it is the second time in two days a
lag conclusion has died on it -- the first was comparing lag-adjusted `desiredCurvature` against
same-frame `curvature`. Before quoting any delay: name both signals and check the other figure spans
the same pair.

**AND lagd ONLY LEARNS WHILE TURNING, which is his other question and is correct design.** Its gate
is `np.abs(self.yaw_rate) >= self.min_yr`. On a straight both signals are flat and every time-shift
correlates equally, so a straight-road sample teaches nothing and would drag the estimate toward
noise.

**WHAT SURVIVES FROM THAT ROUND**, because it stands on its own evidence:

- Q1's real content: the lag is the CAR, not our 20 Hz cadence. `STEER_STEP = 5` is ~0.075 s of the
  ~0.39 s, so a 100 Hz command rate buys back under a fifth. Still true.
- Q2: the plan does NOT react to the tracking error (r = +0.09). Still true, and still the reason
  the "angle mode has no closed loop" fix direction is dead.
- `clip_curvature` is not distorting the plan; averaging the model's path buys 9%.
- The SunnyLink audit blind spot and the three orphaned controls -- both independently real, both
  kept. Audit now reads 38/38.

**AND THE REMAINING LEAD IS UNCHANGED:** the model re-plans the road every frame and disagrees with
itself by ~50% of the curve, on every bundle he has tried. The idea worth building is a CURVE-HOLD
filter -- damp hard only while the road has been bent, same sign, for several seconds; release
instantly on a large or sign-changing move so entry and exit are untouched. Not built; it needs to
be the only variable on a drive.
### CHERRY-PICKED ba20937aac FROM bp-dev-191 -- AND IT CONFIRMS THE DEAD-THRESHOLD FINDING

He asked for the whole BluePilot repo checked. One commit touching the angle path is not in our
history and it targets exactly what this file has been measuring: **`ba20937aac` "Predicted_Curvature
Weight Blending"**, Praeuner, 2026-08-25, on `bp-dev-191`. **We took PR #192 off that branch and
missed the commit underneath it.** He approved taking it: *"Yes, take it. I trust bp-dev."*

Three changes, and the first is independent confirmation from the person who owns the file:

    _desired_falling   abs(des) < abs(last) - 0.010          -> abs(last) > 0.001 and
                                                                abs(des) < abs(last) * 0.8
    _kappa_entering    kappa_at_t_base > abs(des)            -> abs(des) > 0.001 and
                                                                kappa_at_t_base > abs(des) * 1.25
    b_blend            snapped between b and b*0.25          -> ramps 0.1/call toward a target of
                                                                b*0.25 (exit) / b*0.35 (straight) / b

**The `_desired_falling` change is the fix for the bug measured here two days earlier** -- 0.010 1/m
is 5.4x the p99 fall across 239,038 intervals, so the exit-biased blend fired on 0.054% of them.
Upstream reached the same conclusion and fixed it the right way: a RELATIVE threshold is scale-free
and cannot go stale against a cadence change the way the absolute one did. That is the second time
this file has recorded an absolute per-call delta rotting; prefer ratios.

**b_blend IS NOW PERSISTENT STATE ON THE CarController**, which is the category that once made this
car undrivable. `LateralAngleExt.__init__` IS called explicitly from carcontroller.py:89, it is
seeded there, and it is reset at all three early-return sites so a re-engage cannot inherit the last
drive's weight.

### AND IT IS INERT ON THE GENTLE CURVES HE ACTUALLY COMPLAINS ABOUT

**Both new guards carry an `abs(...) > 0.001` floor -- a 1000 m radius.** Found by picking 0.0010 as
a test value and watching the test fail on `>` rather than `>=`. His reported episodes include
1271 m, 1327 m and 2514 m radii, i.e. kappa 0.0004-0.00079, all BELOW that floor. On those curves
neither `_kappa_entering` nor `_desired_falling` can fire, so the exit-biased blend still never runs
and the ramp never leaves `b`.

So: a real improvement on curves tighter than ~1000 m, and **nothing at all on the gentle sweepers
where he first described the symptom** (*"It's on larger curves too, yes"*). Do not report it to him
as a fix for the whole complaint. `test_blend_weight_ramp.py::test_IT_IS_STILL_INERT_ON_CURVES_
GENTLER_THAN_1000_M` pins this so it is not rediscovered.

### AND ONE OF MY OWN TESTS WAS VACUOUS UNTIL MUTATION TESTING SAID SO

`test_blend_weight_ramp.py` MIRRORS the ramp arithmetic rather than executing it, so it passes
whether or not the shipped code matches. Setting `b_step = 1.0` (snap instead of ramp) left it
green. `test_lateral_blend_horizon.py::TestTheBlendWeightRampRunsForReal` drives the real
`update_angle_strategy` and reads `ext.b_blend` back, and the same mutation now fails it.

**The scenario is what made it real.** The first attempt drove a hard exit, but on call one
`_desired_curvature_last` is 0 so `_desired_falling` cannot fire, the default branch was selected,
the target equalled the seed, and the weight had nowhere to move -- a test that could not observe
the thing it was named for. A STRAIGHTAWAY (`desired` 0.0005, below the 0.00125 gate) selects
`b*0.35` on the very first call with no history needed, which is the cheapest scenario where target
differs from seed.
### AND ba20937aac IS SUB-PERCEPTUAL ON HIS CAR. HE SAW IT IN ONE GLANCE AT THE CHART.

Replayed both the old and new blend logic over recorded frames (`tools/bp_lateral_blend_replay.py`)
and plotted the two commands against each other. His response: *"Those lines are the same."* They
are. Converting the difference to steering angle, which is the only unit he can judge:

    window            command diff (1/km)              as STEERING ANGLE
    gentle 1773 m     med 0.011  p90 0.046  max 0.130  med 0.031  p90 0.128  max 0.362 deg
    tight  447 m      med 0.000  p90 0.009  max 0.026  med 0.000  p90 0.025  max 0.072 deg

    his ping-pong episodes                    7 - 15 deg of swing
    dither already measured as imperceptible  0.10 - 0.30 deg

**The whole cherry-pick moves the wheel by 0.03 deg typically and 0.36 deg at its worst -- inside
the noise floor this file already proved he cannot feel.** It is a correct upstream bug fix and it
will not change anything he perceives. Do not tell him the merge is worth waiting for, and do not
let a later session quote "180 of 240 frames differ" as evidence it did something.

**THIS IS THE "ASK HOW BIG BEFORE CONCLUDING FROM HOW OFTEN" RULE, VIOLATED IN THE FILE THAT
RECORDS IT.** The first pass reported frames-differing and a mechanism and a direction, and never
once computed a magnitude. The same failure produced the retracted 1.2 Hz ringing analysis, where
every aggregate turned out to be characterising 0.10-0.30 deg of dither. **Convert to the unit the
driver judges in -- degrees at the wheel -- before reporting that a change does anything.**

**Also corrected in the same breath:** the earlier note that the fix is "inert on gentle curves" was
wrong in the other direction. It DOES act there, through the `_on_straightaway` branch (`abs(des) <
0.00125`, an 800 m radius), which drops the weight to b*0.35 -- so the 1000 m floor on
`_kappa_entering` / `_desired_falling` is not the whole story. The action is real; the magnitude is
what makes it irrelevant.

**And the framing "trusts the wobbling model less and the planner more" was imprecise.** BOTH blend
inputs are the model's: `desired` is `action.desiredCurvature` after `clip_curvature`, and
`predicted` is a raw interpolation of `orientationRate.z`. The change leans on the more PROCESSED
model signal, not away from the model.

**So the lateral problem is exactly where it was**, and the honest position for his 600-mile drive
is: nothing to set, nothing to wait for, expect no change. The open lead remains the plan's own
~50%-of-curve oscillation, and the untried idea is the curve-hold filter -- damp only while the road
has been steadily bent, release instantly on a real change. Any future candidate gets converted to
degrees at the wheel BEFORE it is described to him.
### WHICH CURVES IT ACTUALLY STRUGGLES ON -- MEASURED, AFTER HE HAD TO ASK FOR IT

*"You should know what curves it struggles on because you have all the logs."* He was right, and
asking him was the wrong move. He also named the real process failure: *"I keep saying one thing and
then it throws you completely off your conclusion."* True -- several reversals this session were
re-reasoning from his last sentence rather than from data already on disk.

`tools/bp_lateral_by_radius.py`, route 000003ed, hands off, per MINUTE OF EXPOSURE:

    radius                    minutes    eps   per min   med swing   x nominal
    under 200 m                   0.3      3      9.25      29.3 deg      1.6x
    200-500 m                     2.5     38     15.50       4.0 deg      0.6x
    500-1000 m                   13.2    259     19.69       3.3 deg      0.9x
    1000-2000 m                  18.7    332     17.78       2.5 deg      1.3x
    over 2000 m                 122.8   1521     12.39       2.2 deg      7.6x

**The worst rate is 500-2000 m -- fast sweepers through large highway curves.** His instinct
("really it is tighter ones") beat my framing, which had drifted to 1271-2500 m.

**'x nominal' is what stops the table being read wrong**, and a count alone cannot say it:

- under 200 m looks dramatic at 29 deg but is 1.6x what the curve REQUIRES -- real cornering, and
  0.3 minutes of a 3.3-hour drive
- over 2000 m has a wild 7.6x but a 2.2 deg absolute swing on near-straight road: that is the dither
  band this file already established as barely perceptible
- 500-2000 m is the only place both are true at once -- highest rate AND swings at or above what the
  road asks for

**So the target band is 500-2000 m.** Anything proposed for this symptom should be evaluated there,
not at the extremes.

## 2026-09-01: THE GAIN SCHEDULE IS A SLOPE, AND HIS THREE SYMPTOMS ARE THREE POSITIONS ON IT

He tuned all through a 600-mile drive, landed on `low 0.981 / high 0.51 / damp 0.78 / LC 0.15`, and
the next morning could not take curves. **Nothing changed overnight** -- and both halves are true.

**READ THE SCHEDULE AS LEVEL + SLOPE, not as three independent knobs:**

    low_gain  = interp(v, [11.18, 31.29], [1.00, 1.00 * damp])           <- the LEVEL
    high_gain = interp(v, [11.18, 31.29], [1.30 * lof, 1.15 * hif])
    boundary  = interp(v, [11.18, 31.29], [0.02, 0.0045])
    curvature_factor = interp(|kappa_cmd|, [0.0005, boundary], [low_gain, high_gain])

`damp` sets the level at low curvature. The GAP between `damp` and `1.15 * hif` sets the slope, and
the slope is what he has been feeling all along. Incremental gain `cf + kappa*(dcf/dkappa)` at
85 mph -- how hard the wheel reacts when the PLAN moves, which is what oscillates:

    damp 0.85, high 0.845         straight 0.850  gentle 0.875  sweeper 0.911  tight 0.987  slope +30
    damp 0.70, high 0.845         straight 0.700  gentle 0.757  sweeper 0.836  tight 1.006  slope +68
    damp 0.78, high 0.51  (HIS)   straight 0.780  gentle 0.740  sweeper 0.683  tight 0.562  slope -48
    damp 0.78, high 0.68  (flat)  straight 0.780  gentle 0.780  sweeper 0.780  tight 0.780  slope   0

**His three reports map onto that column exactly, and he found all of it by driving:**

- *lowering dampening fixed straights and caused aggressive ping-pong ON CURVES* -- it drops the
  level, which STEEPENS the slope (+30 -> +68). Reaction on a 400 m curve rises to 1.006.
- *lowering the high factor killed ping-pong everywhere but it cannot take tight curves* -- slope
  inverted to -48. **The quiet curves and the weak curves are the same fact**: the car reacts LESS
  the more the road bends.
- *straights still ping-ponged, so I lowered dampening again* -- the level, which is the only thing
  that acts below the ramp start.

**THE FLAT POINT IS `damp / 1.15` = 0.678 for him, AND IT MOVES WITH DAMPENING** (0.70 -> 0.61,
0.85 -> 0.74). Changing one knob silently re-tilts the ramp, which is the trap he fell into.

**WHY IT DID NOT TRANSFER, MEASURED:** the whole 600-mile drive was 90-93% curves over 2000 m radius
at 75+ mph. The morning drive was 68%, with 18% at 1000-2000 m and 11% at 500-1000 m -- **14x the
sub-1000 m exposure**. He tested `high 0.51` for 600 miles on road where it barely participates: at
a 2000 m radius, high 0.51 and high 0.87 produce an IDENTICAL 0.780.

**AND HIS STRAIGHT-ROAD PING-PONG WAS NEVER THE HIGH FACTOR.** `curvature_factor` interpolates from
`|kappa| = 0.0005`, so below that the high factor is OUTSIDE the interpolation and has no effect at
all. Measured across 254 minutes of straight road at 70+ mph on three routes:

    median |kappa| on straights   0.000053 - 0.000073   (13,600 - 18,900 m radius)
    peak excursion per window     p50 0.00017-0.00025   p90 0.00030-0.00049  <- still under 0.0005
    FRAMES above the ramp start   1.0% / 1.2% / 1.9%

**98-99% of his straight-road frames are below the line where the high factor starts**, so 0.51 and
0.68 are bit-identical there. Straights are the LEVEL (dampening) and the lane-centering loop, and
nothing else.

### THE STRAIGHT-ROAD WEAVE IS A POSITION LOOP, AND IT IS THE ONLY ONE IN THE STACK

His hypothesis, and it measures out. `lane_center_trim` is a PURE PROPORTIONAL controller --
`raw = 2*error / lookahead**2`, no derivative term -- with a 0.4 s smoothing filter stacked on the
car own ~0.39 s lag, added into `kappa_cmd` upstream of every limiter. On straight road at
70+ mph, hands off:

    route      LC     straight min   median off-centre   p2p swing   crossings/min
    00000400   0.55       33.0            0.05 m           0.29 m        17.5
    00000402   0.55       87.9            0.04 m           0.31 m        20.0
    00000405   0.55      118.0            0.06 m           0.44 m        13.7
    00000406   0.15        1.1            0.21 m           0.36 m         6.5

**29-44 cm peak-to-peak, crossing lane centre 14-20 times a minute.** For scale, the steering dither
chased for days elsewhere in this file is 0.10-0.30 DEGREES and provably imperceptible; a third of a
metre of lane position is not. At LC 0.15 the crossing rate halves and the median offset goes
4-6 cm -> 21 cm: the classic P-gain trade, less hunting for worse centring.

**So the three knobs have three separate jobs**, and treating them as interchangeable is what made
the tuning circular:

    weaving on straights    lane_centering_strength_ang   it is the position loop causing it
    overall firmness        FordHighSpeedDampening_ang    the level
    curve ping-pong vs
      tight-curve authority FordHighSpeedFactor_ang       the slope; damp/1.15 is flat

**Dampening was the expensive way to fix a straight-road problem** -- it works only by lowering gain
everywhere, and it drags the ramp bottom down, which is what produced the +68 curve ping-pong.

**THE DURABLE FIX IS CODE, NOT A SLIDER:** the trim is P-only with ~0.8 s of loop lag. Adding a
derivative/lead term or cutting `_SMOOTH_TAU_S` would let him run real centring authority without
the weave, instead of choosing between hunting and sitting 21 cm off centre. Not built.

### AND NONE OF IT WAS ANSWERABLE FROM A ROUTE. THE GAIN IS NOW PUBLISHED.

Every number above needed `LateralMotionControl` (0x3D3) decoded off `sendcan` plus the gain
schedule RE-IMPLEMENTED in a tool, because `curvature_factor` -- the value that multiplies the
command -- was computed every frame and published nowhere. `bp_path_angle_final` carried a comment
saying exactly that and a tool was built around the gap instead of closing it.

**That is this fork oldest bug for the FIFTH time** (after the SCC veto suppressed target, the
three ICBM readouts, the DEC slow-down fields, and the stop-override funnel). `ControllerStateBP`
now carries `pathAngleFinal @55`, `kappaCmd @56`, `curvatureFactor @57`, `laneCenterCorrection @58`,
`gainLowCurv @59`, `gainHighCurv @60`, `blendWeight @61`, so the whole ramp is reconstructible from
a drive. `test_lateral_telemetry_published.py`, 13/13 mutants killed.

**ORDINALS: `@55+` IS SAFE HERE AND IT WAS CHECKED, NOT ASSUMED.** `ControllerStateBP` is identical
at `@0..@54` on `icbm-manual-override-and-tuning`, `passing-assist-phase1` AND `radar-detector`, so
passing assist rebases onto this with no collision -- unlike `CarStateBP`, where the same move would
have hit `blisRight @5`, which has wire history in every route on the device. **`route-intent` has
its own `@55` (`accAuthority`) and must renumber at rebase**: its field has no recorded history on
the car and that branch is not what he drives.

**THREE THINGS THE BUILD ITSELF TAUGHT:**

- **capnp REFUSES `np.float64` AND `np.float32`, verified by assignment.** `curvature_factor` is the
  return of `numpy.interp`, so it IS an np.float64 -- publishing it uncast is the 2026-08-18
  plannerd death exactly. The first version of that test used `np.array`, which any binding refuses,
  and would have passed while the realistic failure went unchecked.
- **A `hasattr` test cannot tell a wired value from a constant zero**, which is the entire failure
  being fixed. Deleting the capture in `lateral_angle_ext` survived mutation testing until a test
  compared each published field against the controller OWN live variable. Non-zero is not enough
  either: deleting the blend-weight capture leaves the `__init__` seed of 0.5, which is non-zero.
- **`conftest.py` is not available here** -- `bp_offline_test.py` runs with `--noconftest`, so the
  pytest fixture-import idiom needs `# noqa: F811` rather than the idiomatic fix.

### WHAT SHIPPED, 2026-09-01: HIS SET IS NOW THE DEFAULT, AND THE FLAT POINT IS ON SCREEN

He asked for it directly -- *"see if you can change the settings yourself and definitely make these
the defaults on this fork"* -- which lifts the standing "settings are his, name the toggle, never
change it" rule FOR THIS CHANGE ONLY. It is not a general licence.

**ON HIS CAR:** `FordHighSpeedFactor_ang` 0.51 -> 0.68, written directly to
`/data/params/d/`. No reboot needed -- `update_angle_params` is called inside `CarController.update`,
so the gains are re-read every frame. `IsOnroad` was checked first.

**THE SHIPPED DEFAULTS ARE NOW HIS MEASURED SET, with High at the flat point:**

    FordLowSpeedFactor_ang       0.912 -> 0.981
    FordHighSpeedFactor_ang      0.828 -> 0.68     (= 0.78 / 1.15, the zero-slope value)
    FordHighSpeedDampening_ang   0.85  -> 0.78
    enable_lane_positioning_ang  0     -> 1
    lane_centering_strength_ang  0.25  -> 0.15

Upstream ships 1.0/1.0/1.0 for the three gains and we were ALREADY overriding all three, so this
costs no new merge surface. The two lane-centering keys are entirely ours.

**AND THE FLAT POINT IS PLATFORM-SPECIFIC, WHICH MATTERS BECAUSE HIS FRIEND RUNS THIS BRANCH.** He
pointed that out -- *"my friend uses this branch and not passing assist"* -- and it turned a shipped
default into a defect: the high-curvature anchor is 1.15 on CAN, 0.95 on a CAN-FD truck and 1.05 on
a CAN-FD unibody SUV, so `0.78 / 0.68` is flat on a Fusion and INVERTED on an F-150 or a Mach-E.
That is the exact failure the defaults exist to fix, shipped to somebody else's car.

A single default cannot be flat everywhere, so the fix is that the SCREEN now tells each owner their
own number: `opendbc/sunnypilot/car/ford/angle_gains.py` holds the anchors and
`flat_high_speed_factor(damp, fingerprint)`, imported by both `lateral_angle_ext` and the settings
screen. It deliberately imports only `CAR`, so the UI process is not dragging the car layer in to
render a description, and there is no duplicated constant left to drift. CAN-FD owners set High to
0.82 (truck) or 0.74 (unibody SUV) at dampening 0.78.

**The settings screen hardcoded 1.15 for exactly one commit.** It would have printed a confident
flat point that was nothing of the sort, and an owner would have tuned to it -- worse than printing
nothing. Caught only because he said who else uses the branch.

**AND THE COUPLING IS NOW VISIBLE INSTEAD OF A TRAP.** The two settings descriptions are computed at
display time -- `float_control_item` takes a lambda, so this is free -- and the High Speed
Adjustment Factor now names its own flat point for the CURRENT dampening, while the Dampening
description warns that changing it moves that number. The divisor is duplicated in the UI rather
than imported (the UI process should not pull in the car layer) and
`test_flat_point_matches_the_gain_schedule` fails if it ever drifts from `_GAIN_CAN[1]`.

### `lane_centering_damping_ang` -- BUILT, SHIPS AT 0.0, AND ONE DRIVE SETTLES IT

The trim acts on `error + damping * d(error)/dt` instead of `error` alone. `damping` is a lead TIME
in seconds, so the sum is still a position and goes through the existing geometry and every existing
limiter untouched.

**IT SHIPS INERT AND THAT IS THE POINT.** How this loop responds to a lead term is unmeasured, and a
lateral change shipped on reasoning alone is how the 5.20 m/s^2 event happened. Same reasoning that
shipped `StockAccStopOverride` off. `test_zero_damping_is_bit_identical_to_the_old_controller`
proves 0.0 reproduces the pure-P controller EXACTLY, and `test_the_SHIPPED_DEFAULT_is_zero` fails if
a later edit turns it on without a drive behind it. 10/10 mutants killed.

**Try 0.3.** If the straight-road weave falls without the centring offset going back to 21 cm, the
damper is right and `lane_centering_strength_ang` can go back up toward 0.5. Score it with the
weave measurement (lane-centre offset p2p and crossings per minute on straight road at 70+ mph),
not with `bp_lateral_rate.py` -- a 2 s window cannot see a 6 s position oscillation.

**TWO THINGS THE TESTS GOT WRONG FIRST, both corrected by looking at the numbers:**

- **`abs(damped) < abs(undamped)` IS THE WRONG ASSERTION for a closing error.** A strong lead
  legitimately drives the command PAST zero and out the other side -- that is what a derivative term
  does -- so measuring with `abs()` reads correct behaviour as a failure. Assert the SIGNED value.
- **The window cannot be chosen by where the ERROR turns.** `_error_rate` is filtered
  (`_DERIV_TAU_S` 0.2 s) and lags the error by a few frames, so the first frames after the turn are
  still being led the old way. Gate the assertion on the rate the controller ACTUALLY USED.

### THE SUNNYLINK AUDIT HAD A THIRD BLIND SPOT, FOUND THE WAY THE COMMENT SAYS TO FIND ONE

Adding `lane_centering_damping_ang` and watching the audit report **38/38, 0 missing** is what
surfaced it. `OUR_PREFIXES` is a list of CAPITALISED names -- `Icbm`, `Ford`, `Mapd` and friends --
and the whole angle-mode lane positioning family is lowercase:

    enable_lane_positioning_ang   custom_path_offset_ang
    lane_centering_strength_ang   lane_centering_damping_ang

**None of them matched any prefix, so the audit has never once checked them** -- including through
the entire period this file was recording that three of them reached `settings_ui.json` only by
hand-editing. Widened; the audit went 38/38 -> 41/42 (1 genuinely missing) -> 42/42 after the YAML
entry. **Third structural blind spot in that tool, and the third one found by adding a setting and
watching it stay green rather than by reading it.**

### AND `FordHighSpeedDampening_ang` IS THE LARGEST LEVER FOUND ALL SESSION

It multiplies the LOW-CURVATURE high-speed gain only (`low_gain_calc = interp(v, BP, [1.00,
GAIN_LOWC * user_dampening_factor])`), so it acts on exactly the open end of the target band. He runs
**0.69** against a 1.0 default. At 75 mph with his 0.957/0.829, raising it to 1.0 is:

    2500 m  +45% command     1271 m  +41%     800 m  +34%     500 m  +25%

For scale, the ba20937aac cherry-pick moves the wheel 0.36 deg at its absolute worst. This is tens
of percent of the command. **It is his setting, it is reversible from the seat, and it is the first
thing to try** -- 0.85 before 1.0, so the direction is felt before the magnitude.

The honest risk, and it must be said with it: gain scales whatever it is given, and the plan
oscillation is still there, so this may trade "weak and late" for "busier". He has described the
current setting as weak, so he is on the wrong side of that trade today.
## 2026-08-30: MAPD V2 DIED MID-DAY AND A TOGGLE DID NOT RECOVER IT. HE REPORTED IT; I FIRST SAID "HEALTHY".

*"I thought at one point I didn't have a speed limit and I turned mapdv2 off and then turned it back
on and then it got the speed limit."* He was right, the toggle did NOT fix it, and my first answer
was wrong in a way this file already has a rule against.

    route   segs   mapdOut   liveMapDataSP   gpsFrames   movingFrames
    3f2      11      6810         644           583         52308
    3f3      15     13326         873           849         64494
    3f4      15     15647         865           831         77760
    3f5      15         0         892           867         78670   <- died here
    3f6       9         0         506           504         25827
    3f7       6         0         312           277         22436
    3f8       6         0         336           308         26059
    3f9       9         0         491           474         32616

**Zero mapdOut across 45 segments and ~185,000 moving frames, with GPS present the whole time and
`MapdV2` reading 2 in every segment's initData.** So it is not offroad-silence, not a lost fix, and
not the setting: the process simply stopped publishing and never resumed within the drive.

**AND I REPORTED IT AS HEALTHY FIRST.** The total was 35,783 mapdOut frames, 82% `current`, 70%
carrying a limit -- all of which came from the first three routes. **A total is not a distribution**,
and that is the same failure as reporting "180 of 240 frames differ" without a magnitude, twice in
two days. **Always break a health metric down BY ROUTE before calling a subsystem healthy.**

**THE TOGGLE CANNOT FIX IT.** Toggling `MapdV2` off and on is what he reached for and it is the
natural thing to reach for. What he saw come back was not v2 -- `liveMapDataSP` kept publishing at
300-900 frames per route throughout the outage, carrying NO speed limit (0/892, 0/506, 0/312,
0/336, 0/491). He had no speed limits for five consecutive drives.

**AND "REBOOT, DO NOT TOGGLE" WAS WRONG. IT WAS ABOUT TO BE HANDED TO HIM BEFORE A 600-MILE DRIVE.**
It came from this file's own mapd v2 section -- *manager does NOT restart a dead mapd_v2, only a
reboot recovers* -- which describes a DIFFERENT failure. `logMonoTime` is since boot, and routes
3f5, 3f7 and 3f8 each START ~62 s after boot:

    3f5   62.6 .. 954.4    fresh boot, mapdOut 0
    3f6   9617 .. 10124    same boot as 3f5
    3f7   62.2 .. 374.4    FRESH BOOT, mapdOut 0
    3f8   62.2 .. 397.6    FRESH BOOT, mapdOut 0

**Three fresh boots, three fresh mapd_v2 processes, all publishing nothing.** A reboot does not fix
this. Quoting a recovery from a neighbouring section because the symptom rhymed is the same failure
as the `SpeedLimitPolicy` and `AlphaLongitudinalEnabled` entries: **check that the recorded cause
matches THIS failure before quoting the recorded fix.**

**AND `restart_if_crash=True` CANNOT SEE IT EITHER.** That flag was added 2026-08-24 for route
000003b4 (441 "not running: mapd_v2" events) and it watches for the process DYING. Here
`managerState` read `running=True` with no exit code on every sample of all five drives.

**RULED OUT BY MEASUREMENT**, each one cheap and each one worth not re-deriving: the build (same
commit `4adc7ff69b` in every route's initData), his settings (the per-segment params snapshot diff
between 3f4 and 3f5 shows only the lateral gains moving), GPS (`gpsLocation` present throughout --
and note `gpsLocationExternal` is ZERO on the WORKING routes too, so this file's claim that v2 reads
that service is wrong; the binary subscribes to both), the network (`networkType` was `none` on 100%
of frames on the working routes as well), the tile store (524 MB, region `US`, intact, no truncated
files -- and the dead routes never crossed a tile boundary, all of it inside 34.25-34.50), geography,
and the clock reset (every boot starts at the same stale RTC and corrects on GPS fix, working routes
included).

**THE CAUSE IS STILL UNKNOWN.** What is built is a RECOVERY, not a diagnosis -- see the stall
watchdog below.

**THE FIX: A STALL WATCHDOG.** `MapdV2MapData` times how long `mapdOut` has been silent WHILE THE
LOCALIZER HAS A VALID POSITION (offroad it is legitimately silent, so counting that would bounce the
process in his driveway). When it crosses the threshold `mapd_manager` sets `MapdV2RestartRequest`;
`mapd_v2_ready` clears it and returns False, so manager stops mapd_v2 and starts it on the next
pass. `test_mapd_v2_stall_watchdog.py`, 17/17 mutants killed.

**AND THE FIRST VERSION OF IT WOULD HAVE MADE HIS 600-MILE DRIVE WORSE. A REVIEW CAUGHT IT.** It
shipped a single `MAPD_V2_STALL_S = 60.0`, chosen from the assumption that startup was "some
seconds". Measured on the three routes where mapd v2 WORKED -- gap from `liveLocationKalman` going
valid to the first `mapdOut` frame:

    3f4    -37.0 s   (mapd was already publishing before the localizer converged)
    3f3    +93.8 s
    3f2   +195.8 s

**A healthy mapd v2 is silent for over three minutes while it loads tiles from the 524 MB offline
US dataset.** The 60 s watchdog would have bounced a WORKING process on two of those three routes,
and every bounce restarts tile loading -- so it could have stopped mapd ever finishing while
spending the whole restart budget in the first minutes. Strictly worse than no watchdog.

    MAPD_V2_COLD_START_S = 420.0   before the first frame this process ever publishes (2.1x the
                                   measured worst case)
    MAPD_V2_STALL_S      =  60.0   after it has published once, silence is a real fault
    MAPD_V2_BUDGET_RESET_S = 1800  continuous health that refills the restart budget

**The budget refills**, because `MAPD_V2_MAX_RESTARTS = 5` held as a per-BOOT cap means a long drive
that stalls six times goes silently blind after the fifth.

**THE GENERAL LESSON, and it is this file's own rule failing in the file that records it: the number
came from reasoning when the logs that could settle it were already on disk.** Worse, the test
asserted `30 <= MAPD_V2_STALL_S <= 180` -- an invented window that BLESSED the broken value and
would have failed the correct one. A test whose bound is guessed does not defend a threshold, it
freezes the guess. `test_THE_COLD_START_GRACE_CLEARS_THE_MEASURED_HEALTHY_STARTUP` now carries the
195.8 s measurement.

**THREE MORE HOLES THE REVIEW FOUND, all of the same family -- the guard disabling what it guards:**

- **The watchdog released its own request on a later tick.** `mapd_manager` has no
  `restart_if_crash` and `NativeProcess.start()` returns early while `self.proc` is not None, so a
  mapd_manager that died between setting and releasing left mapd_v2 STOPPED FOR THE DRIVE. It is
  fire-and-forget now; `mapd_v2_ready` clears the request as it acts on it, depending on no other
  process. Safe as a predicate side effect because `ensure_running` calls `should_run` exactly once
  per process per pass and stops it in the same pass.
- **`mapd_manager`'s startup `put_bool(False)`** could erase a request it had just set that manager
  had not yet seen -- a lost recovery, silently. Removed; `CLEAR_ON_MANAGER_START` covers the boot.
- **One broad `try/except: return True`** wrapped the opt-in read as well, so a transient params
  failure started mapd v2 on a device whose owner had switched it OFF. One param call per try now.

**And `isinstance` was the wrong guard**: this repo is importable as both `sunnypilot.x` and
`openpilot.sunnypilot.x`, which makes TWO class objects for one class, so an isinstance gate rejects
a genuine `MapdV2MapData` depending on how the caller imported it. Duck-typed on the attribute.

**THE FALLBACK THAT NEEDS NO CODE, and it is his to set:** `MapdV2 = 1` (observe). `mapd_manager`
line 129 is `MapdV2MapData() if use_v2 else OsmMapData()`, and `mapd_ready` runs v1 in states 0 and
1 -- so observe hands Speed Limit Assist to **v1**, a different binary on a different transport
(`/dev/shm/params`), which cannot share whatever failed here. v1's binary is still installed. It
costs a little coverage (measured on route 00000383: only-v1 1.6%, only-v2 6.7%) and a second daemon
running, which is heat.

**TWO TEST LESSONS FROM BUILDING IT, both caught by mutation testing and neither by a green run:**

- **`pytest.importorskip` HID SEVEN TESTS.** `mapd_manager` does not import offline (it reaches
  `openpilot.system.micd` through alertmanager), so the whole watchdog class SKIPPED while the run
  reported "8 passed". Stub the MODULE chain -- micd, `system.hardware.hw`, `system.version`,
  `system.sentry`, `common.spinner` -- and IMPORT, so a broken chain fails loudly. A skip that
  removes a whole class is worse than a failure.
- **NEVER ASSERT A CLOCK-DERIVED FLOAT IS ZERO.** The monotonic clock has ~15 ms granularity on
  Windows, so a set and a read inside one test return the SAME value and `stalled_s == 0.0` passed
  against a mutant that deleted the reset. Worse, a mutant that re-anchored the clock every tick --
  which makes the threshold unreachable, i.e. **the watchdog never fires at all** -- survived two
  rounds. Both needed the `_now()` seam and a fake clock. Assert on the STATE (`_stall_since is
  None`) or on an injected clock, never on elapsed real time.

### AND THE DEVICE CLOCK HAS RESET AGAIN

`date +%s` reads 1780675437 while `/data/params/d/MapdV2` has mtime 1788115346 -- the param is
stamped ~86 days AFTER "now". No NTP without DNS, and it has not held a GPS time fix. Consequences:
route directory names carry wrong dates, and **mtime-vs-route ordering is unusable** until it syncs.
The rule in this file about converting UTC to MDT still applies, but there is now a second failure
mode above it: the clock can simply be wrong, so do not order events by timestamp at all right now.
## 2026-09-04: WHAT LETS IT TAKE A REAL TURN -- AND WHAT ACTUALLY CAPS IT

He reported a large left turn before a freeway on-ramp and asked what enabled it: *"I couldn't
believe how far it turned the wheel."* Then, crucially: *"No, it did it well! I want it to be able
to do that in the future!"* -- **a capability report, not a safety report.** The first reading of it
here was as an incident and that was wrong.

### THE FIRST ANSWER WAS WRONG. THE PEAK OF THAT TURN WAS HIS HANDS, NOT OPENPILOT.

`find_turn.py` ranked route 00000423 seg 13 t+49.9 as "94.8 deg ENGAGED hands-off" and it was
reported to him that way. Splitting `latActive` from `steeringPressed` and dumping the window
reverses it:

    t+46.15  lat Y  hands .    -0.7d      openpilot has it
    t+46.25  lat .  hands .     2.2d      LATERAL DROPS OUT
    t+47.85  lat .  hands Y   220.5d      HIS turn, openpilot not steering
    t+48.86  lat Y  hands Y   177.0d      openpilot back, cf 1.309
    t+49.95  lat Y  hands .    84.4d      hands off, driving the exit

**`latActive` false for 2.5 s across the entire peak.** A tool that ANDs the two flags into one
"engaged" column cannot show this, and the ranked row it produced read as a controller achievement.
**Print `latActive` and `steeringPressed` as separate columns in any lateral event dump** -- this is
the same split that corrected the 3.21 m/s^2 figure, arriving through a tool's output format rather
than through a filter.

### THE REAL ANSWER: 102.4 DEGREES, BUILT FROM ZERO, HANDS OFF

Route 00000423 seg 5 is the biggest turn openpilot has taken BY ITSELF in this pull:

    t+2.01   18.8 mph      0.1d   latacc 0.00   cap 3.07   cf 1.000   hands off
    t+4.56                32.2d   latacc 1.21   cap 3.45   cf 1.309   hands off
    t+5.76   18.8 mph    102.4d   latacc 2.97   cap 3.51   cf 1.309   hands off
    t+7.46 .. 8.36                latacc 3.52   cap 3.51              PINNED for 0.9 s

**The ISO lateral-acceleration clamp is what caps it, and on that turn it BOUND.** Not the wheel,
not the PSCM, not any Ford gain. `clip_curvature` limits desired curvature to
`(MAX_LATERAL_ACCEL_NO_ROLL - roll * g) / v^2` with `MAX_LATERAL_ACCEL_NO_ROLL = 3.0`; that road was
banked, so the cap read 3.51 and the plan sat welded to it for nearly a second.

**Measured ceiling on this pull, engaged, under 20 mph, n=44,219 frames:**

    p50 0.107   p90 1.255   p99 3.032   p99.9 3.518   max 3.535 m/s^2

The max exceeding 3.0 is the roll term, not a violation -- do not read it as the clamp failing.

### THE THREE THINGS THAT MAKE LOW-SPEED TURNS WORK, AND THEY ARE NOT THE HIGHWAY KNOBS

1. **`FordLowSpeedFactor_ang` sets the ceiling of the ramp, and he raised it himself** 0.981 ->
   1.007 on 2026-09-03. `high_gain = 1.30 * lof` at low speed = **1.309**, which is exactly the
   `curvatureFactor` observed saturated through both turns.
2. **The ramp is SHORT at low speed.** `boundary = interp(v, [11.18, 31.29], [0.02, 0.0045])`, so at
   or below 25 mph full gain arrives by kappa 0.02 -- a **50 m** radius -- against **222 m** at
   75 mph. A turn saturates the schedule almost immediately; a highway sweeper never does.
3. **The 3.0 clamp is permissive at low speed.** It is a CURVATURE limit, so the slower the car the
   more wheel it permits: 15 m radius at 15 mph, 23 m at 18.8, 82 m at 35, 330 m at 70.

**SO `FordLowSpeedFactor_ang` IS THE TURN-AUTHORITY KNOB AND `FordHighSpeedFactor_ang` IS NOT.**
They govern different regimes that barely overlap, and every tuning conversation in this file until
now has been about the high one. **Do not lower the low factor while chasing highway behaviour** --
it is what makes turns like this possible.

### AND THERE IS NO SETTING THAT MAKES A TURN GENTLER

He asked whether that turn was "a little tight". It is a fair read -- 3.5 m/s^2 is above openpilot's
own p99 on this car (2.73) and near his hands-on p99 (4.14). But the commanded curvature comes from
the model and `clip_curvature`, both UPSTREAM of every Ford gain, trim and limiter this fork owns.

**Lowering `FordLowSpeedFactor_ang` does not plan a wider turn. It under-delivers the turn already
planned**, which is running wide rather than turning gently -- and delivery at that peak was already
0.88. Anyone reaching for that knob to soften a turn is reaching for the wrong end of the loop.

### THE TRIM WAS NOT INVOLVED, AS DESIGNED

`laneCenterCorrection` was **exactly 0.00000** on every frame of both turns, at LC 0.45. That is
`_SPEED_RAMP_BP = (0.0, 9.0, 15.0)` -> `_SPEED_RAMP_V = (0.0, 0.0, 1.0)` working: the lane-centering
trim is OFF below 20 mph and full at 34. **A lane-centering setting cannot explain anything he
reports below 20 mph**, in either direction.

## 2026-09-04: THE LATERAL CHAIN, DECOMPOSED. THE PSCM CEILING IS MEASURED AT LAST.

`ControllerStateBP` has published `kappaCmd`, `curvatureFactor` and `laneCenterCorrection` since
2026-09-01, and this is the first analysis to use all three together. **It splits the long-standing
"delivery is 0.87-0.93" number into three independent links, and they have completely different
causes at different speeds.** Steady state (desired within 5% for a full 0.5 s), hands off,
latActive, 44 segments of routes 0000041e / 0000041f / 00000423.

    desired --[trim + clip]--> kappa_cmd --[our gain]--> command --[PSCM]--> actual

          radius      n    mph   trim+clip  our gain    PSCM   = delivery
    500-  1000 m    552   70.1       1.055     0.834   0.952       0.865
    286-   500 m    861   39.3       1.071     0.973   0.889       0.862
    100-   286 m    906   40.7       1.041     1.005   0.784       0.859

**THE DELIVERY COLUMN IS FLAT AT 0.86 AND MEANS THREE DIFFERENT THINGS.** Pooling it, which every
previous measurement here did, hid that entirely:

- **Highway curve, 70 mph: the shortfall is OURS.** Gain 0.834, PSCM 0.952. `FordHighSpeedDampening_ang`
  and `FordHighSpeedFactor_ang` close this, and the car will deliver what they ask for.
- **Turn, 40 mph: the shortfall is the PSCM.** Our gain is already 1.005 -- the schedule is at unity
  -- and the car returns 0.784. **No setting in this fork reaches that column.**

(The three columns are medians of ratios and do not multiply to the fourth exactly; the pattern is
the finding, not the arithmetic identity.)

### AND THE PSCM CEILING IS SOFT AND PROGRESSIVE, NOT A CLIP. SPEED-CONTROLLED.

Binned by commanded `pathAngleFinal` WITHIN each speed band, because a first pass pooled by command
size put 39 mph and 75 mph frames in adjacent rows and manufactured a trend:

    30-45 mph   0.830  0.808  0.871  0.765      (command 0.021 -> 0.228 rad)
    45-60 mph   0.892  0.800  0.772  0.743      (0.026 -> 0.171 rad)  <- monotonic
    60-80 mph   1.069  0.933  0.983  0.945      (0.015 -> 0.068 rad, small commands only)

**The PSCM tracks essentially perfectly when asked for little and degrades steadily as the command
grows.** 45-60 mph is the cleanest run and falls 0.892 -> 0.743 monotonically -- and that is the
same band this file already flags three independent ways as the worst on this car.

**THIS IS THE FIRST QUANTITATIVE MEASURE OF THE AUTHORITY LIMIT.** The section above records that
`LatCtlLim_D_Stat` is dead on non-CAN-FD Fords, so the PSCM never reports limiting and every earlier
claim about ~2.5 m/s^2 rested on indirect signatures (our deviation limiter biting, hands-on% rising,
delivery sitting at 0.87-0.93). **The tracking ratio against command size, speed-controlled, is a
direct measure and it does not need the dead signal.** Use it to score the torque interceptor when
it arrives: the same table, before and after.

**WHAT IT MEANS FOR TUNING, and it is a diminishing return rather than a wall:** at 0.17 rad of
commanded path_angle the PSCM returns 74 cents on the dollar, so commanding 35% more nets about 26%
more actual. Not nothing -- but the gain amplifies the plan's own ~50%-of-curve oscillation at the
same time, so this is a real trade and not a free win.

### HIS TUNING STEPS ARE TWO ORDERS OF MAGNITUDE TOO SMALL TO JUDGE

The wire recovered his change as `FordHighSpeedFactor_ang` 0.794 -> 0.804. In the unit he judges in:

    radius   nominal   cf .794   cf .804    delta at the wheel
     1000m     2.79d     0.797     0.798        0.004 deg
      500m     5.57d     0.830     0.834        0.025 deg
      350m     7.96d     0.858     0.865        0.055 deg

**0.025 degrees.** The dither this file proved imperceptible is 0.10-0.30 deg. To move the wheel by
even 0.30 deg at 500 m he needs 0.794 -> **0.90**. Every A/B he has run at this step size was
unscoreable before the drive started, and telling him a drive "showed" anything at this resolution
would be inventing a result. **Quote the step in degrees before recommending a drive to test it.**

### THE TRIM IS NOT EATING THE DEVIATION BUDGET. `0cb9165427` IS REFUSED ON EVIDENCE.

The deviation clip (`measured +- CURVATURE_ERROR`, 0.002 1/m) binds hard on tight curves:

    radius        n     % clipped   budget p50   p90
    500-1000 m   13798      0.54%        0.17   0.42
    286- 500 m    8302      2.67%        0.26   0.54
    100- 286 m    5674     16.90%        0.44   1.04
      0- 100 m     311     54.66%        0.72   4.41

That looked like the one-sided-budget bug `0cb9165427` fixes -- *"a one-sided form lets the trim
subtract authority while the planner is already clipped short in a curve"* -- and this file had it
flagged as a real risk. **Re-running the clip with and without the trim's contribution refuses it:**

    radius        clipped   planner ALONE   TRIM-CAUSED   % of frames
    500-1000 m         80              80             0         0.00%
    286- 500 m        207             207             0         0.00%
    100- 286 m        606             566            59         1.04%
      0- 100 m         88              88             0         0.00%

**The clipping is the planner's own, in every band.** The trim causes it on 1.04% of frames in one
band, and there it destroys 0.000076 1/m of planner curvature -- **0.21 degrees of wheel, below the
dither floor.** Do not take that commit for this reason; if it is ever taken it must be for a
different one, measured.

**And the trim is NOT neutral on curves** -- signed with the bend it is +16.6% of commanded curvature
at 500-1000 m, +9.4% at 286-500, +2.2% at 100-286. It is silently compensating for the shortfall
above, which is very likely why raising `lane_centering_strength_ang` fixed his "hugging some edges".
**Any measurement of delivery that does not subtract the trim is measuring the trim.**

### AND HIS Q3/CAN CAR IS THE *LESS* RESTRICTED FORD, NOT THE MORE. 2026-09-04.

He said *"I know my Q3 CAN car is supposed to be worse than a CAN FD one"* -- that is the common
belief and for LATERAL AUTHORITY it is backwards. Upstream's own comment states the trade:

    # Ford Q4/CAN FD has more torque available compared to Q3/CAN so we limit it based on
    # lateral acceleration.
    if CP.flags & FordFlags.CANFD:
      curvature_accel_limit = MAX_LATERAL_ACCEL / (max(v_ego_raw, 1) ** 2)   # ~2.4 m/s^2

**CAN FD has more torque and is therefore CAPPED. His car is not.** Three places it lands:

    carcontroller.py:64   the ~2.4 m/s^2 clip is inside `if CP.flags & FordFlags.CANFD`
    ford.h:245/804        FORD_STEERING_LIMITS = FORD_LIMITS(false, ...) for him
                          FORD_CANFD_STEERING_LIMITS = FORD_LIMITS(true, ...)
                          -- the flag IS `limit_lateral_acceleration`, and it drives
                          `.angle_is_curvature` too
    angle_gains.py        GAIN_CAN high anchor 1.15  vs  CANFD_BOF 0.95, CANFD_SUV 1.05

So on the turn measured above his car reached **3.52 m/s^2 bank-adjusted -- above the 2.4 any CAN FD
Ford is allowed at all**, and it runs the highest gain anchor of the three platforms. The only limit
it met was `clip_curvature`'s ISO 3.0, which binds every openpilot car on every platform.

**WHAT HE ACTUALLY GIVES UP IS TRACKING, NOT AUTHORITY.** Torque-controlled platforms close their
loop inside openpilot and are tunable there; angle mode hands the loop to the PSCM, which returns
0.78-0.95 of what it is told (see the decomposition above). That is a different axis from how much
the car is permitted to ask for, and conflating the two is what makes "CAN is worse" sound obvious.

**DO NOT extend this into numbers for other platforms.** Nothing here has measured a non-Ford car,
and openpilot's 3.0 clamp being universal is a code fact, not a measurement of what those cars
deliver.

### SLOWING DOWN BUYS BOTH RADIUS AND TRACKING

    certain, arithmetic:  the ISO clamp is 3.0 / v^2, so the tightest permitted radius scales with
                          v^2 -- 107 m at 40 mph, 27 m at 20 mph, 15 m at 15 mph
    measured:             the PSCM tracks SMALL commands better, and the same corner asks for a
                          smaller path_angle when slower (path_angle = kappa * v * gain)

    100-286 m band     0-25 mph  cmd 0.047 rad  PSCM 0.894
                      35-45 mph  cmd 0.074      PSCM 0.849
                      25-35 mph  cmd 0.107      PSCM 0.806
                      45-60 mph  cmd 0.126      PSCM 0.777

**Monotonic in COMMAND SIZE, not in speed** -- which is the same relationship the speed-controlled
table above found, arrived at from the other direction.

**One row refuses it and is reported rather than dropped:** 286-500 m at 0-25 mph reads 0.566 with
p25-p75 of 0.35-0.68. Its median command is 0.019 rad -- about one degree of wheel -- where
`controlsState.curvature` (steering angle through the vehicle model) is noise-dominated and the
ratio means little. It is not evidence against, and it is not evidence for. **Do not quote a
tracking ratio computed on a command under ~0.03 rad.**

### AND THE STEADY-STATE DECOMPOSITION ABOVE MUST NOT BE TUNED ON. HE CAUGHT THIS.

The table above says delivery is 0.86 and the gain is short, and a "raise the gains" recommendation
was built on it and given to him. **He refused it from the seat: *"Changing those settings higher
will lead to more oversteer, though. It oversteered on that tight turn today."*** He is right, and
the wire says so within minutes of being asked:

    tight  <500 m, any speed   turning in 0.664   holding 0.868   UNWINDING 1.017   53% over 1.0
    highway 500-2000 m, 60+    turning in 0.500   holding 0.861   UNWINDING 1.076   65% over 1.0

**The car turns in lazily and carries PAST on the unwind.** Steady state is one point in the middle
of a spread that runs 0.50 to 1.08, and the spread is the 0.39 s lag, which this file already
records as correctly compensated (learned 0.393, aimed 0.468, residual 33 ms).

**A GAIN MULTIPLIES THE WHOLE RATIO, ENTRY AND EXIT ALIKE. IT CANNOT NARROW A SPREAD.** Raising
`FordLowSpeedFactor_ang` 1.007 -> 1.20 to fix the 0.66 turn-in takes the unwind from 1.017 to ~1.21
and its p90 from 1.33 to 1.58 -- worsening exactly the thing he reported. The same argument kills
the `FordHighSpeedDampening_ang` 0.78 -> 0.92 recommendation, and the overshoot is WORSE on highway
curves (1.076, 65% of frames over 1.0) than on tight ones.

**HIS SETTINGS ARE AT THE RIGHT POINT AND ARE NOT TO BE MOVED.** 1.007 / 0.804 / 0.78 put the EXIT
nearest 1.0, which is the correct end to protect: exit overshoot is what reads as oversteer and is
the direction that costs a lane. He arrived there by driving.

**THE RULE: never recommend a gain change off a steady-state number alone.** Steady state measures
where the middle of the distribution sits; the driver feels the ENDS. Print turning-in / holding /
unwinding before any gain recommendation leaves this repo. This is the same family as "ask how big
before concluding from how often" -- here it is *ask across what phase* before concluding from a
median.

### AND THE LONGITUDINAL WORK IS WHAT UNLOCKS TIGHTER TURNS. HIS PLAN, AND IT IS CORRECT.

  *"unless you see anything I need to change in my settings, we need to work on the longitudinal
  parity project to take tighter turns"*

The chain is arithmetic and it holds:

    tighter turn        needs lower speed -- clip_curvature permits 3.0 / v^2
    ICBM + Ford ACC     floors at 20 mph (FORD's floor, he confirmed it) -> 27 m minimum radius
    op long             has NO floor -> 15 m at 15 mph, 10 m at 12 mph
    op long is unusable ONLY because it cannot coast -- which IS `ford-acc-parity`

**And it helps TWICE, which is not obvious:** lower speed also means a smaller commanded path_angle
for the same corner, and the PSCM tracks small commands better (0.894 at 0.047 rad vs 0.777 at
0.126). Clamp headroom AND tracking, from one change.

So `ford-acc-parity` (`../bluepilot-ford`) is not a side project to the lateral work -- **it is the
lever on tight turns**, and the torque interceptor is the lever on fast ones. Different regimes,
different fixes. Longitudinal authoring still does not belong on this branch.

### THE EXIT-BIASED BLEND IS STILL DEAD AFTER `ba20937aac`, AND FOR A SECOND REASON NOBODY MEASURED

His complaint is exit overshoot. **The one mechanism in this file aimed at exit overshoot has run to
completion ONCE in 44 segments.** Measured 2026-09-04 with `blendWeight @61`, which is why this was
findable at all.

    population           phase        n   med blend    p10    p90   % below 0.45
    all curves      turning in    44161       0.175   0.17   0.40          90.1%
    all curves       UNWINDING    47397       0.175   0.17   0.40          90.6%
    tight <500 m    turning in     3036       0.500   0.50   0.50           0.9%
    tight <500 m     UNWINDING     2101       0.500   0.50   0.50           1.3%

**On anything actually bent, `b_blend` is pinned at the 0.500 seed.** The 0.175 on the pooled row is
`b * 0.35` -- the `_on_straightaway` branch (`abs(des) < 0.00125`) firing on near-straight road. The
exit branch's `b * 0.25` = 0.125 is never reached anywhere: the p10 across every population is 0.17.

**TWO INDEPENDENT REASONS, AND THE SECOND IS NEW.**

**1. The relative threshold is at the 1st percentile of falling intervals.** `ba20937aac` replaced
the absolute 0.010 1/m delta with `abs(des) < abs(last) * 0.8`, compared once per `STEER_STEP = 5`
(0.05 s). Measured on falling intervals:

    band                    n   med ratio    p10    p01    min   % under 0.80
    tight <500 m         2561      0.9801  0.931  0.777  0.384          1.41%
    highway 500-2000 m   2006      0.9683  0.876  0.658  0.244          3.44%

A 20% drop in 50 ms is the ~1st percentile of what this planner does. The absolute form fired on
0.054% of intervals; the relative form fires on 1.1-3.4%. **Twenty times better and still dead.**
The relative threshold IS the right shape -- scale-free, cannot go stale -- it is simply set five
times too strict for this car's planner.

**2. EVEN WHEN IT FIRES, IT FIRES FOR ONE CALL, AND THE RAMP NEEDS FOUR.** `ba20937aac` also added
`b_step = 0.1` per call to stop the weight snapping between 0.5 and 0.125 -- a good change on its
own. But 0.500 -> 0.125 is 0.375 of travel, so it needs **four consecutive firing calls**:

    consecutive calls   episodes   share
                    1         80    87.9%
                    2          9     9.9%
                    3          1     1.1%
                    4          1     1.1%

    gate fired on 105 of 9484 calls (1.11%)
    episodes long enough to reach the exit weight (4+):  1 of 91

**87.9% of firings are a single isolated call, and the blend reached its exit target exactly once.**
Neither half is wrong by itself; the product of a 1-in-90 trigger and a 4-call ramp is zero.

**DO NOT FIX THIS TONIGHT, AND DO NOT FIX IT BY MOVING ONE NUMBER.** A threshold loosened without
the run-length problem still needs four in a row; a ramp sped up without the threshold still almost
never triggers. The shape that would work is a LATCH -- once an exit is detected, hold the exit
state for a bounded number of calls so the ramp can traverse -- and that is a design change to the
lateral path on a branch his car auto-pulls. **This is the 5.20 m/s^2 pattern if it is written in an
evening and handed to him.** Record it, measure it against `bp_lateral_by_radius` and the phase
table above, and ship it on its own drive with nothing else moving.

**And note what it does NOT explain.** The exit overshoot in degrees is median +0.16d on tight
curves -- imperceptible. It is the tail that hurts: p90 +3.21d, p99 +10.81d. The gate's 1.4% firing
rate lands on the fastest falls, which are plausibly those same tail frames, so a working exit blend
targets the right events. That is an argument for fixing it, not evidence that it would have.

## 2026-09-05: UPSTREAM SURVEY. ONE COMMIT WE ALREADY HAVE, AND PR 191 REWROTE THE GAIN SCHEDULE.

    releases        bp-7.0 newest, we are 0 behind, still NO bp-8.0
    bp-dev          exactly ONE new commit since 2026-09-03: fc228b4099
    bp-dev-191      nothing new (a15672fb15 / 77ec55c73e / ed251bfb85, all already assessed)
    deleted         bp-dev-188, bp-dev-193, bp-dev-ALP, bp-dev-UISAD

**`fc228b4099` (combo cruise-button event storm) IS ALREADY OURS BY CONTENT.** Verified rather than
assumed: theirs adds `self.prev_button_signal` keyed by `can_msg`; ours is `self.combo_states` keyed
by `group`, and `group = COMBO_GROUPS.get(button.can_msg)` -- the same granularity. Both also fix
the `processed_signals` scoping. Ours additionally documents that `processed_signals` is an
early-out and `combo_states` is the correctness guard, with the mutation that proves it. Nothing to
take.

**HE IS NOT A STAKEHOLDER IN 191/192 AND SAID SO.** Praeuner apologised to him on #192 for pushing
commits "before seeing you had also made commits on your end"; he replied *"I'm just messing around
with this stuff on my own fork... I don't have any stake in how 191/192 shake out."* Do not treat
those PRs as his to steer, and do not draft comments for them unasked.

### PR 191's NEW HEAD `fd221cd05c` (2026-09-04) REWRITES THE WHOLE SCHEDULE

    low_gain   unchanged:  interp(v, [11.18, 31.29], [1.00, damp])
    high_gain  1.30*lof -> 1.40*lof   AND   anchor*hif -> 1.20*anchor*hif
    boundary   interp(v,[11.18,31.29],[0.02,0.0045]) -> interp(v,[8.94,13.41,16.54,31.29],
                                                               [0.02,0.0195,0.018,0.0035])
    plus       low-pass filters (0.80/0.20) on _speed_factor, _kappa_factor and b_blend
    plus       _kappa_entering 1.1 -> 1.05, _desired_falling 0.9 -> 0.95

**AT HIS SETTINGS (1.007 / 0.804 / 0.78), IN DEGREES AT THE WHEEL:**

     75mph  500m   cf 0.834 -> 0.945   +13.3%   +0.59d
     75mph  350m   cf 0.865 -> 1.039   +20.1%   +1.31d
     40mph  193m   cf 1.010 -> 1.038    +2.8%   +0.33d
     18mph   50m   cf 1.309 -> 1.410    +7.7%   +5.00d
     18mph   27m   cf 1.309 -> 1.410    +7.7%   +9.25d

**IT PULLS BOTH WAYS ON HIM AT ONCE.** The low-speed half is exactly the turn authority he asked for
on 2026-09-04 (*"I want it to be able to do that in the future"*). The highway half raises a command
whose EXIT OVERSHOOT he reported the same night, and a gain multiplies the whole ratio:

    highway 500-2000 m   unwind median 1.070 -> 1.160    p90 1.59 -> 1.72
    tight    <500 m      unwind median 1.018 -> 1.039    p90 1.33 -> 1.36

**AND HIS FLAT POINT MOVES 0.678 -> 0.565**, so his 0.804 becomes a far steeper slope than he tuned
it to be. Keeping his current slope would mean High 0.804 -> **0.670**. That is the retune burden
alan-polk objected to on #192 in as many words -- *"Changing the interpolation range means everyone
on bp-7.0 has to retune."*

### THEIR EXIT-GATE FIX DOES NOT REVIVE THE DEAD BLEND. MEASURED ON HIS OWN LOGS.

`_desired_falling` 0.9 -> 0.95 is the right direction and close to the value the 2026-09-04 analysis
derived independently. But it is paired with an exponential filter that is SLOWER than the step ramp
it replaces, and the compound defect survives:

    gate    fires   % of calls   runs   4+ consecutive   7+
    0.80      107        1.03%     93       1 (  1%)      0     <- ours (b_step 0.1, needs 4)
    0.90      430        4.13%    331       7 (  2%)      0
    0.95     1173       11.27%    800      32 (  4%)      2     <- PR191 (0.80/0.20, needs ~7)

**Eleven times more firings and still 96% of exit detections last under four calls.** It moves the
mechanism from "reached its target once in 44 segments" to "moves meaningfully on 4% of exits" --
a real improvement to something that was dead, not a fix.

**VERDICT: DO NOT TAKE IT.** It is an open PR taking commits daily, its own reviewer has objected to
the retune burden, and on this car it worsens the symptom he reported tonight unless he
simultaneously drops High to 0.670. **Watch it; the 1.30 -> 1.40 low-speed anchor is the half worth
having, and it is separable.** If it is ever taken, every number quoted to him on 2026-09-04 --
the flat point, the 0.90 ceiling, the degrees-per-step table -- is invalidated and must be re-derived
before he tunes against it.

## 2026-09-05: THE LC 0.35 vs 0.45 A/B DOES NOT RESOLVE -- AND TWO ERRORS ON THE WAY

### I QUOTED A DEVICE TIMESTAMP WITHOUT CONVERTING IT. FOURTH INSTANCE.

`lane_centering_strength_ang` was reported to him as written at **16:37**. That is the raw `stat`
output, which is **UTC**. Local is **10:37 MDT**. This file already carries the rule -- *"NEVER QUOTE
A PARAM VALUE OUT OF THIS FILE. READ IT OFF THE DEVICE WITH ITS MTIME"* and *"THE DEVICE RUNS IN UTC.
HE DOES NOT"* -- and it was broken anyway, by reading a `stat` line straight into a sentence.

It mattered: at 16:37 the change looked like it came AFTER every route pulled, so the absence of any
0.45 label read as a telemetry gap. At 10:37 it lands 9 minutes into route 00000423, which is
exactly why the earlier session split that route at segment 10.

    LC 0.45 written           2026-09-04 10:37:32 MDT
    0000041e 09-03 12:09  0000041f 09-03 17:00  00000420 09-03 19:30   <- 0.35
    00000421 09-04 08:58  00000422 09-04 09:08                          <- 0.35
    00000423 09-04 10:28  <- STARTED 9 MIN BEFORE THE CHANGE, spans it
    00000424 09-04 17:30  00000425 09-04 18:40                          <- 0.45

### AND I CHECKED "DOES THE DEVICE STILL HAVE THE SEGMENTS" WITH THE WRONG QUESTION

The pull was reported as complete at 72/72, with the earlier 114 figure dismissed as stale. **The
query only listed the three routes already on disk.** The device had EIGHT routes and 115 segments;
43 were missing, including both 0.45 drives. `ls` restricted to what you already have can only ever
tell you that you have it. **Enumerate from the DEVICE side and diff, never from the local side.**

### THE RESULT: NO CONCLUSION, AND THE REASON IS THE SAMPLE

Matched on radius AND speed (a first pass matched radius only, and the 0.45 pool's "500-2000 m" is
gentle surface-street curves at 30 mph against interstate at 70 in the 0.35 pool):

      radius      speed     LC      n   ratio    p90   med deg   p90 deg
     100-286     20-35     0.35    727   0.999   1.25    -0.02d     3.78d
     100-286     20-35     0.45     79   0.900   2.46    -1.17d    19.54d
     286-500     20-35     0.35   1129   1.108   1.51     0.85d     3.43d
     286-500     20-35     0.45    143   1.070   1.46     0.60d     4.05d
     500-2000    20-35     0.35   4112   1.140   1.83     0.37d     2.22d
     500-2000    20-35     0.45   1073   0.977   2.23    -0.03d     2.68d

**Every median is equal or slightly better at 0.45.** The 100-286 m p90 going 3.78d -> 19.54d is the
only dramatic row and it is **n=79** -- under a second of road, one or two turn exits. Not a finding.

**THE STRAIGHT-ROAD WEAVE IS UNSCOREABLE FOR A PLAIN REASON, NOT A TOOL ONE:** 00000424 and 00000425
have **ZERO** qualifying windows, rejected as "too slow" on 2853 and 3607 frames. He has not driven
highway since changing the setting.

    LC 0.35   n=83669 curve frames   p50 39 mph   p90 76
    LC 0.45   n= 8932                p50 21 mph   p90 32

**Nine to one, and no overlapping road type.** VERDICT: keep 0.45; nothing argues against it. What
settles it is one ORDINARY drive at 0.45 on highway plus 35-50 mph curves -- not a test pattern.

**AND THE BOOT SNAPSHOT DOES NOW READ 0.449 ON 424/425**, which confirms the time mapping
independently -- so `initData` labels a route correctly whenever the route does not SPAN the change.
The 2026-09-01 telemetry fields added today make the spanning case readable too, from the next drive.

## FOR THE ICBM SESSION: mapd v2.3.1 FIXES THE PUBLISH-RATE COLLAPSE. WE ARE PINNED TO v2.3.0.

Left here rather than sent as a message because cross-session messaging was unavailable in the
passing-assist session when this was found, and because a message dies with the session while this
file does not. He asked for you to be told; this is the telling.

**`sunnypilot/mapd/__init__.py` has `MAPD_V2_VERSION = "v2.3.0"`. pfeiferj released v2.3.1 on
2026-08-21.** Its notes, verbatim:

    * message publishing is now on its own thread that ensures a constant 20 hz publish rate
    * Large performance improvements in main loop
    * bump to latest gomsgq with additional shadow subscriber safety
    * fix for maps generation near coordinates limits

**THE FIRST LINE IS THE HALF OF HIS commIssue BUG WE DID NOT FIX.** Measured on the Yosemite drive:
`mapdOut` collapsed from its declared 20 Hz to **1.6 Hz** on Tioga Road, tracking map path size
(652 points there against ~50 on open highway), and the collapse correlated with every commIssue
segment. The cause was mapd publishing from inside its own map calculation loop.

We fixed the DOWNSTREAM cost -- SCC-Map rebuilding that path 20x per message (2106064495). That was
real and it is what was stalling plannerd. But mapd's own publish rate collapsing is a SECOND,
independent defect in the same chain, and it is fixed upstream rather than here.

**HISTORY WORTH KNOWING BEFORE BUMPING.** They needed three passes:

    2026-08-18  PR #128 merged  "Publish mapdOut independently of map calculations"
    2026-08-19  PR #134         REVERTED #128
    2026-08-19  PR #132/#135    re-landed with a strict send rate, then slice caching
    2026-08-21  v2.3.1          released

So the first attempt was wrong enough to back out within a day. It is the shipped release now, but
that is not a version to take on the release note alone.

**WHY THIS IS YOURS AND NOT PASSING ASSIST'S.** The pin is in `sunnypilot/mapd/`, which the base
branch owns, and bumping it swaps the BINARY running on his car -- a different class of change from
the Python around it. Nothing here has driven v2.3.1.

**WHAT WOULD MAKE IT MEASURABLE, and we now have the instrument for it.** `mapdOut` publish rate is
readable straight from a route, and the pre-fix baseline is on disk: 1.6-8.7 Hz on the curvy
segments of 000003dc/000003de against a declared 20. A post-bump drive on comparable road either
holds 20 Hz or it does not, and that is a one-number answer.

**ALSO OPEN UPSTREAM, relevant to him:** PR #136 "Reduce non-intersecting map archive downloads",
opened 2026-09-04, not merged. His comma downloads tiles over a phone hotspot with no DNS, so
download volume is not free for him.

**AND HIS FOUR ISSUES ARE STILL OPEN AND UNANSWERED SINCE 2026-08-17** -- 127 (lanes:forward /
lanes:backward), 129 (change / change:lanes), 130 (hov:lanes), 131 (highway=stop nodes). pfeiferj
engaged constructively on 127 and 129 and has not returned. `currentDirectionLanes`, the field he
proposed on 127, does not exist in the repo. Nothing to consume yet.

## 2026-09-05: THE TACO BELL QUESTION -- CAN FIRMWARE GET THIS CAR TO INTERSECTION TURNS?

His long-term goal, stated plainly: *"I want to be able to recreate the Taco Bell drive from Comma
one day with my car, which requires more steering... I am looking into the future."* So the target
is INTERSECTION TURNS (10-15 m radius at 10-15 mph), not lane keeping. He asked whether
`ghostdev137/ford-pscm-re` could get him there and let him drop angle mode.

### ANGLE MODE IS THE RIGHT MODE FOR THIS. CURVATURE MODE COULD NEVER DO IT.

    ceiling                          tightest radius it permits
    LatCtlCurv_No_Actl signal        48 m at ANY speed   (+-0.02094 1/m; panda FORD_CURVATURE_MAX)
    LatCtlPath_An_Actl signal        scales with SPEED:  5.6 m @ 5 mph, 11.2 @ 10, 16.8 @ 15
    clip_curvature ISO 3.0           1.7 m @ 5 mph, 6.7 @ 10, 15.0 @ 15, 26.6 @ 20

**The curvature interface caps at a 48 m radius, forever.** An intersection turn is impossible in
curvature mode on this car, at any speed. The PATH ANGLE ceiling divides by speed, so it opens up
exactly where turns happen.

**PROVEN ON HIS OWN CAR TONIGHT**, route 00000423 seg 5, hands off:

    23 m radius at 18.8 mph  ->  kappa 0.0435 1/m  -- ABOVE the 0.0209 curvature-signal cap
                                 path_angle 0.478 rad = 91% of the 0.5235 signal maximum

**He has already commanded turns tighter than the curvature interface can encode.** So "move on
from angle steering and be like other cars" would be moving the WRONG WAY for his stated goal.

### THE WIRE CAN ALREADY DO THE TACO BELL DRIVE. THE PSCM CANNOT.

At 10 mph the path-angle signal permits 11.2 m and ISO permits 6.7 m -- an intersection turn fits.
What does not fit is DELIVERY: measured 2026-09-04, the PSCM returns **0.83** of what it is told at
40 mph and degrades to **0.743** as the command grows. **Worst exactly where he would need it best.**

**So the gap is authority, not interface** -- and authority is precisely what a calibration patch
addresses. That is what `LKA_FULL_AUTHORITY.VBF` is in that repo: a 12-entry u16 bell curve at
`cal+0x1660`, peak `44 -> 32` between two builds, drive-confirmed +184% column torque. **Nobody
wrote firmware.** They edited a lookup table, after months of RE with the module on a bench.

### WHY THE TORQUE INTERCEPTOR EXISTS, AND WHAT WOULD REPLACE IT

**His PSCM has NO torque input.** Verified in his own DBC: `LateralMotionControl` (979) carries
`LatCtlPath_An_Actl` (rad), `LatCtlCurv_No_Actl` (1/m) and `LatCtlPathOffst_L_Actl` (m) -- geometry
only. The sole `DesiredTorq*` messages are `DesiredTorqBrk` from ABS_ESC, which is BRAKE torque.
There is no byte on his bus meaning "apply N Nm to the steering."

That is exactly why an interceptor is needed -- it injects torque where no bus path exists. **The
Transit does not need one**: it accepts `0x213 DesTorq` directly, which is why that repo ships an
openpilot page saying "drive 0x213 continuously, you do not need Ford LCA."

**A cal patch and the interceptor buy the SAME THING for his goal -- authority -- and the cal patch
needs no hardware.** Neither lets him leave angle mode, and neither should: see above.

### WHAT WOULD ACTUALLY HAVE TO HAPPEN, AND WHAT IS NOT POSSIBLE

**Not possible:** vibe-coding EPAS firmware. Not for me and not for anyone -- that repo built a
patched Ghidra SLEIGH spec and pushed a Binary Ninja v850 lifter to 99.81% decode coverage to find
*which bytes to change*. I have no dump, no bench, no flash path and no way to verify.

**Not covered:** documented vehicles are Transit 2025/2026, Escape 2022/2024, F-150 2021/2022. **No
Edge, no Fusion.** F-150 is explicitly "not cross-compatible" with Transit/Escape -- different
vendor, different MCU, different cal layout. His retrofitted Edge PSCM is a FOURTH platform.

**The cheap, non-destructive first step:** read the Edge PSCM calibration over UDS (`0x730`/`0x738`)
with FORScan and see whether it has recognisable table structure -- the bell-curve authority family
is a Ford EPAS design pattern and appears at three sites in one F-150 cal. That is a READ. It
answers "is this a project or a dead end" without touching the car.

**And keep the stock VBF before anything else.** Their `backups/` exists for that reason, one of
their own patches reverts AS-built on a power cycle, and his PSCM is a retrofit he has already tuned
into place -- bricking it means sourcing another Edge module and redoing all of it.

### HIS CORRECTION: WE USE LCA, NOT LKA -- AND IT RETIRES THE WHOLE PATCH TABLE

*"But we aren't using LKA, we are using LCA."* Correct, and it is the most important thing in this
whole thread. Every shipped patch in that repo solves a TRANSIT problem this car does not have:

    LKA_NO_LOCKOUT.VBF          Transit's LKA gives ONE 10 ms pulse then 10 s of nothing.
                                HIS CAR HAS NO LOCKOUT -- he drives hands-off for minutes.
    LKA_FULL_AUTHORITY.VBF      raises the 0x213 DesTorq cap on the LKA path.
                                HE IS NOT ON THE LKA PATH AT ALL.
    LKA_APA_*                   parking assist. Unrelated feature.
    LCA_ENABLED.VBF             they are TRYING to reach the interface he already has,
                                and it only half-works ("AS-built reverts on power cycle").

**Transit has LKA and is fighting to get LCA. He has LCA.** He is on the destination of their
hardest open problem. Do not describe their patch table as applicable to this car.

### AND A FIRMWARE TABLE WOULD NOT LET HIM TAKE CURVES FASTER. MEASURED.

He asked directly: *"Would modifying a table make it steer more at higher speeds so I can take
curves faster?"* Measured over 56,707 engaged hands-off frames on real curves at >= 55 mph:

    PSCM tracking                             p50 0.979
    commanded lateral accel                   p50 0.96  p90 1.92  p99 2.61  max 3.37 m/s^2
    frames within 10% of the ISO 3.0 clamp    284  (0.50%)

**The rack already delivers 98% at highway speed, and openpilot's own clamp is binding on half a
percent of frames.** Neither is the constraint. **There is nothing at highway speed for a firmware
authority patch to buy** -- which also agrees with the already-retracted "PSCM cannot hold 2.5"
story (commit 585ef47afb: the knee was the gain ramp, not the rack).

What DOES cap curve speed is above the rack entirely: SCC's corner-speed formula (which he does not
use), and our own gain schedule sitting at 0.834 -- and raising THAT is the recommendation he
correctly refused, because a gain multiplies the unwind overshoot by the same factor.

**Firmware would help in exactly ONE regime: low-speed tight turns**, where the PSCM returns 0.784
and falls toward 0.743 as the command grows. That is the Taco Bell case and nothing else.

### CURVATURE vs ANGLE IS A REAL TRADE, AND HE IS ALREADY ON THE RIGHT SIDE FOR HIS GOAL

    curvature mode   PSCM closes its OWN loop -> delivery ~1.0 by design, and it would unwind
                     under closed-loop correction. BUT the signal caps at a 48 m radius, at any
                     speed, so an intersection turn is impossible. Taco Bell drive: dead.
    angle mode       open-loop feedforward, delivery 0.78-0.98. Ceiling scales with 1/speed, so
                     it reaches 11.2 m at 10 mph. Taco Bell drive: possible.

Angle mode already measures **0.979 at highway**, so switching to curvature would buy ~2% there and
cost the tight-turn future outright. **He is on the right mode.** (He has also refused going back to
curvature twice; this is the measured reason he was right, not a reason to re-raise it.)

### "CAN'T YOU VIBE-CODE A TORQUE INPUT?" -- NO, AND HE DOES NOT NEED ONE

Adding a torque command means authoring a CAN handler into a 1 MB AUTOSAR V850 binary with no
source, no toolchain for the target, no bench, and ASIL-D supervision that would trap it -- when
that repo built a patched Ghidra SLEIGH spec and a 99.81%-coverage lifter merely to find which
EXISTING bytes to change. But the stronger answer is that the feature would be redundant: his LCA
geometry path already achieves 0.979 at highway. **The torque interceptor exists for cars with no
working lane-centering interface. He has one.**

### IS CURVATURE MODE DEAD? NO -- IT IS BETTER AT THE ONE THING HE COMPLAINS ABOUT

His question, and it deserves a straight answer instead of the one-line dismissal it got first.

**What curvature mode has that angle mode structurally cannot:** the PSCM closes ITS OWN loop on
commanded curvature and keeps correcting until the car is there. Angle mode is open-loop
feedforward -- it commands and hopes. **On the UNWIND that is exactly his complaint:** measured
2026-09-04, angle mode carries PAST on exits (highway unwind ratio 1.070, p90 1.59, p90 +3.18 deg),
and an open loop has no mechanism to pull it back. A closed loop would.

**HONEST LIMIT ON THAT CLAIM: there are ZERO curvature-mode frames in any pulled log** -- 119,450
angle, 0 curvature. So it is a code-reading claim about his car, NOT a measurement, and it must be
labelled that way until a curvature drive exists. What IS measured is that angle mode already
tracks **0.979** at highway, so the STEADY-STATE gain from switching is about 2%. Any real benefit
would be in the transient, which nothing on disk can score.

**What curvature costs: the 48 m radius cap, at any speed, forever.** Intersection turns become
impossible. So it is not "better" -- it is better ABOVE ~48 m and unusable below.

**AND THE MODE IS RE-READ EVERY FRAME, WHICH MAKES A SPEED-SCHEDULED SWITCH MECHANICALLY POSSIBLE.**
`carcontroller.py:173-174` calls `update_lateral_params` / `update_angle_params` inside
`CarController.update()`, so `self.primary_lateral_control` refreshes per frame and is consumed at
lines 260/268/324 to choose the message. **Curvature above a speed threshold, angle below**, would
give closed-loop tracking on highway curves AND angle-mode reach at intersections. Not built, not
trivial (the two modes send different messages and the transition needs designing), but the
plumbing does not forbid it. **This is the one genuinely new architectural idea from this thread.**

### WHAT ELSE IN THAT REPO APPLIES, ITEM BY ITEM -- HE ASKED AND IT WAS NOT ANSWERED

    LKA 10 s lockout        SOLVED there (LKA_NO_LOCKOUT.VBF, flashed+confirmed) and IRRELEVANT
                            here: his car has no lockout. Tonight's logs show continuous latActive
                            across whole segments; he drives hands-off for minutes.
    APA high-speed          parking assist. Unrelated feature, no bearing on lane centering.
    APA standstill          same.
    LCA enable              them reaching for the interface he already has. Half-works.
    min-speed floor         theirs is ~40 kph on stock LKA. His LCA has no such floor.

**"Or any other modifications like that?"** Conceivable on a cal: authority tables, speed floors,
rate limits -- all NUMBERS in existing tables. Not conceivable: a new CAN handler, a torque input,
or any new BEHAVIOUR, because that is authoring code into a 1 MB AUTOSAR V850 image with no source,
no toolchain and ASIL-D supervision. **The line is "change an existing number" vs "add a feature",
and everything that repo shipped is on the first side of it.**

### CORRECTION: "NOTHING FOR FIRMWARE AT HIGHWAY SPEED" WAS TOO NARROW. HE CAUGHT IT.

*"I get steering exhausted warnings all the time so I don't think it's doing as well as I could on
highways."* Measured `steerSaturated` across the 2026-09-04/05 pull -- 1545 frames, 33 episodes,
0.44% of latActive frames:

    speed     p10 28.9   p50 35.2   p90 55.6 mph
    radius    p10  111   p50  152   p90  286 m
    delivery  p10 0.67   p50 0.76   p90 0.85
    lat accel p10 1.15   p50 1.67   p90 3.40 m/s^2
    hands ON during saturation: 1%

**The saturation is at 29-56 mph on 111-286 m curves, hands off, delivering 0.76.** The "PSCM tracks
0.979, nothing for firmware to buy" finding was measured at >= 55 mph on 500-2000 m radii. **Both
numbers are true and the wrong one was generalised.** He was told there was nothing there; there is.

**SO THE REGIMES ARE THREE, NOT TWO:**

    >55 mph, >500 m      PSCM 0.979    fine. Firmware buys nothing. Limit is our gain + ISO clamp.
    29-56 mph, 111-286 m PSCM 0.76-0.83, 33 saturation episodes  <- HIS ACTUAL COMPLAINT
    <25 mph, tight       PSCM 0.78     the Taco Bell case

**A firmware authority patch would help in the middle band too**, which is the one he drives daily
and the one where `bp_lateral_by_radius` already put 112 of 301 episodes (45-60 mph). Median lateral
accel during saturation is only 1.67 m/s^2 -- **modest cornering with poor delivery, not physics** --
so it is an authority/tracking shortfall, not the tyres or the ISO clamp.

**AND THE FILTER LESSON, for the sixth time in this file:** the highway analysis gated on
`v >= 55 mph`, which excluded the entire population where the failure lives. `mapd_v2_path.py`
already records the sharper version of this -- *"SATURATION IS NOT THE FAILURE. RUNNING WIDE IS"* --
and delivery during these episodes is 0.76, so the car IS running wide. **Before concluding a
subsystem is healthy, check whether the gate excluded the frames the driver is describing.**

### CURVATURE MODE IS CLOSED. THIRD REFUSAL. STOP RAISING IT.

*"No, we shouldn't go back to curvature."* -- 2026-09-05, after two earlier refusals already
recorded in this file. It was raised a FOURTH time that night on the strength of the closed-loop
argument below, and he said no again.

**The technical case does not matter any more and must not be re-litigated.** Same shape as
`pinned-holds-rejected`: he has decided, the decision is his to make, and re-deriving a good reason
is not new information. Do not propose a curvature drive, a curvature A/B, or a speed-scheduled
mode switch. The analysis stays in this file as a record of what was considered, not as a pending
idea.

**AND IT NARROWS THE 29-56 mph SATURATION PROBLEM TO ONE LEVER.** With curvature off the table:

    raise the gain          trades running-wide for MORE unwind overshoot -- the same knob, and
                            he refused that on 2026-09-04 for exactly that reason
    shorten the boundary    same trade, narrower band (this is a15672fb15 on PR 192)
    curvature mode          REFUSED, permanently
    PSCM authority          the ONLY lever that raises delivery WITHOUT raising the command

**So the firmware cal read is not one option among several -- it is the only remaining path to the
thing he actually complains about.** Everything in software trades saturation against overshoot,
because both scale with the same multiplier.

### CORRECTION: THE F-150 WORK IS A CLOSE TEMPLATE FOR THIS CAR, NOT A DISTANT ONE

He asked whether the whole repo had been read. It had not -- only the README, architecture,
openpilot notes and one findings file. The `analysis/f150/` directory is most of the repo and it
contains the two most relevant files in it for this car. **Reading them revises the "fourth
platform, months of work, from scratch" answer given earlier the same night.**

**ALL FOUR MESSAGES IN THEIR F-150 LCA TABLE ARE IN HIS DBC:**

    0x3D3  LateralMotionControl      979  <- his primary command, the one openpilot drives
    0x3D6  LateralMotionControl2     982
    0x3D7  Steer_Assist_Data         983
    0x3CC  Lane_Assist_Data3_FD1     972

So the F-150 PSCM speaks the SAME lateral protocol as his Edge unit. The Transit does not -- it is
the `0x3CA LKA` + `0x213 DesTorq` architecture. **The earlier framing ("F-150 is not
cross-compatible with Transit/Escape, so Edge is a fourth platform") is true and was used to imply
the wrong thing: it is the F-150 work that transfers, and it transfers well.**

**AND HE HAS LKA AND APA TOO** (Edge PSCM is a full ADAS module), so the F-150 feature-envelope
block -- `cal+0x00B8..0x0147`, 144 bytes of float32 defining the speed/angle/torque window for LKA,
LCA/BlueCruise and APA together -- is directly analogous rather than partly applicable.

### TWO PATCHES, TWO DIFFERENT PROBLEMS OF HIS

**1. THE SHARED ANGLE SCALER -- `analysis/f150/angle_scale_patch.md`, a TWO-BYTE patch.**

    0x1009690e   movhi   0x4480, r0, r11   ; r11 = float 1024.0   <- the scale factor
    0x10096912   mulf.s  r11, r17, r12
    0x10096916   trncf.sw r12, r10

One function decodes the wire-domain angle command and converts it to the controller's integer
domain, and **every one of the six steering modes goes through it -- LKA, LDW, LCA, TJA, APA,
BlueCruise.** The scale is a single `movhi` immediate because float32 1024.0 has zero low bits.
Change `0x4480` and the whole car's angle interpretation scales.

**WHAT THAT WOULD BUY HIM: it breaks the SIGNAL CEILING.** `LatCtlPath_An_Actl` maxes at 0.5235 rad,
which caps him at an 11.2 m radius at 10 mph and is 100% consumed by a 10 m turn. With a 2x scaler
openpilot commands half the angle for the same result, so the Taco Bell radius comes back inside the
signal range with headroom. **It does NOT fix saturation** -- tracking degrades WITH command size
(0.892 -> 0.743), which is authority, not scale; scaling just reaches the authority wall sooner.

**2. THE AUTHORITY BELL CURVE -- `cal+0x1660`/`0x25B4`/`0x350C`, 12-entry u16.** This is the
saturation one, and therefore the one for the 29-56 mph / 111-286 m band where he actually gets
steering-exhausted warnings.

### AND IT MAY BE AN AFTERNOON, NOT MONTHS

**F-150 RE was "a one-afternoon job" and Transit is a multi-day slog, for ONE reason:** F-150 PSCM
is baseline Renesas V850, which stock Ghidra 12 lifts cleanly; Transit is RH850-extended, whose
extension opcodes break the decompiler. **His Edge PSCM is 2019/2020 -- the same generation as the
2021 F-150 -- so baseline V850 is the likely case**, which would put it on the easy path.

**The method is fully documented and transferable** (`angle_scale_patch.md`, "the xref trick"):
locate the CAN RX handlers for `0x3CA`, `0x3D3` and `0x3A8`, follow each call chain to physical
units, find the function appearing in ALL THREE -- that is the shared angle reader -- then read its
last basic block for the `movhi` float constant.

**So the honest revision: this is not "reverse-engineer a module from scratch." It is "port a
documented procedure to a new image of the same protocol family."** Still needs a firmware dump, a
bench, Ghidra and someone to flash and drive it -- none of which I can do -- but the earlier "months
of work" framing was wrong and was based on not having read the repo.

### FULL READ OF ford-pscm-re: TWO CORRECTIONS, AND THE TEST THAT DECIDES IT

Read the project files (the 254-markdown count is mostly vendored Binary Ninja / unicorn / fmt docs;
the actual project is ~65 files). **Two things said earlier the same night are now withdrawn.**

**WITHDRAWN 1 -- "his Edge is probably the easy Ghidra path". THE REPO CONTRADICTS ITSELF.**

    README.md + angle_scale_patch.md   Transit = RH850 (hard), F-150 = baseline V850 ("one-afternoon
                                       job", stock Ghidra lifts it clean)
    verdict.md                         F-150 = RH850, Transit = the OLDER V850E2M

**Both cite the same evidence string `AH850S54GxxxxxV101`, and they are exactly inverted.** So the
claim that an Edge PSCM would land on the easy path rests on contradictory ground and must not be
repeated. Which MCU his module runs is unknown and is answered by the dump, not by inference.

**WITHDRAWN 2 -- the +184% headroom figure does NOT apply to this car.** The actual tables:

    Transit stock LKA        [0,   0.2, 0.4, 0.7, 1.0, 1.5, 2.0, 7.0 ]  Nm
    F-150 LCA/BlueCruise     [0.0, 0.7, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5 ]  Nm   <- the patch TARGET
    speed axis (+0x0404)     [0,   10,  30,  50,  70,  90,  130, 250 ]  kph

**`LKA_FULL_AUTHORITY.VBF` sets Transit's table TO the F-150 lane-centering envelope.** His Edge is
a BlueCruise-class module running LCA, so **his table is plausibly already at or near that envelope
-- the patch's destination is his starting point.** The +184% was Transit going from crippled to
normal, not normal to enhanced. Going ABOVE the LCA envelope is possible (their 2X file proves the
table accepts it) but nobody has drive-confirmed above it.

**WHAT SURVIVES AND IS GENUINELY USEFUL:**

- **No runtime signature verification of the calibration.** `verdict.md`: 1.5 MB of strategy has zero
  SHA/RSA constants and no embedded public key; the SBL's hardware SHA-256 verifies only ITSELF.
  **A patched cal flashes and runs.** That was the biggest open "will this even work" question.
- **The cal is mostly empty:** 195,584 bytes, **only ~15.7% live data**, 84% reserved zero. And only
  ~0.5% is statically attributable to a reader function -- everything else goes through AUTOSAR
  `Rte_Prm` pointer tables, so table ROLES come from shape and cross-vehicle diffing, not xrefs.
- **Ford made BlueCruise LESS aggressive than baseline in places** -- the BDL->EDL diff REDUCES the
  bell-curve authority peak 44->32 and HALVES the ramp schedule. Counterintuitive, and a caution
  against assuming the BlueCruise cal is the maximum.
- The angle-scaler method and the concrete F-150 offsets (`+0x0120` LCA engage-min = 10.0,
  `+0x0114` LKA min-speed, `+0x07ADC` LKA arm timer) are a real template.

### THE ONE TEST THAT DECIDES WHETHER ANY OF THIS IS WORTH DOING

**Dump his cal, find the torque-vs-speed table, and compare it to
`[0.0, 0.7, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5]`.**

    his table is LOWER   -> real headroom, and the saturation at 29-56 mph has a firmware answer
    his table is AT/ABOVE-> the firmware idea is much weaker than tonight implied, and the
                            remaining lever is the ANGLE SCALER for signal range, not authority

**Do not spend effort on this before that comparison.** It is a read, it is cheap, and it is the
difference between a project and a dead end. Everything else in this thread is downstream of it.

### THE STEERING-EXHAUSTED ALERT IS MOSTLY NOISE, AND IT IS GATED NOW (the 88% below is WITHDRAWN)

*"Those steering exhausted warnings drive me crazy."* Measured lane position during every
`steerSaturated` frame in the 2026-09-04/05 pull:

                         n        p50      p90     p99     max
    during saturation   1537    0.18 m    0.68    1.56    1.67
    normal driving    294093    0.10 m    0.36    0.93    1.93

    33 episodes, WORST lane offset in each:
      never exceeded 0.30 m   26 of 33  (79%)
      ever exceeded  0.50 m    4 of 33  (12%)
      ever exceeded  0.75 m    3 of 33  (9%)

**When the alert fires the car is typically 18 cm off centre in a 3.7 m lane -- barely worse than
its normal 10 cm.** The alert is right 9-12% of the time.

**AND HE ALREADY TOLD US THE RIGHT RULE.** `mapd_v2_path.py` records his own words -- *"I just
ignore most steering saturated errors until it starts to stray enough from my lane"* -- and the
conclusion drawn there, **"SATURATION IS NOT THE FAILURE. RUNNING WIDE IS."** That principle was
used to tune SCC's corner speed and **never applied to the ALERT ITSELF**, which is where he
actually experiences it.

**THE COST OF LEAVING IT: he is trained to ignore it, and 3 of the 33 episodes reached 0.75-1.67 m
-- nearly half a lane.** Those are the ones worth seeing, and they arrive in the same costume as the
30 that are not. An alert with a 1-in-10 hit rate is worse than no alert, because it destroys the
signal it exists to carry.

**THE FIX, SPECIFIABLE FROM THIS DATA: gate the alert on lane deviation, ~0.5 m. 33 alerts -> 4**,
keeping every episode where he genuinely went wide. No firmware, no hardware, no driving change --
it changes only what he is TOLD, not what the car does.

**BUILT 2026-09-05, and the 88% in this heading is WRONG -- see the correction below.**

## 2026-09-05: THE ALERT GATE IS SHIPPED. AND THE 88% WAS A SMALL-SAMPLE NUMBER.

`bluepilot/selfdrive/selfdrived/steer_saturated_gate.py`, one condition in `selfdrived.py:479`,
toggle **Only Warn When Out Of Lane** (`SteerAlertLaneGate`), ON.

**THE FIRST THING TO FIX IS THE FIGURE THIS FILE HAS BEEN CARRYING.** "88% noise, 33 alerts -> 4"
came from 33 episodes on ONE pull, scored as "worst offset never exceeded 0.30 m", and it never
asked what the gate does with an episode whose lane it cannot measure. Re-run across every route on
disk -- **701 segments, 24 routes, 61 episodes, 3446 alerting frames**:

                          n         p50      p90     p99     max
    while alerting       2998     0.24 m    0.67    1.43    1.67
    normal driving    2403770     0.06 m    0.22    0.70    1.83

    threshold   0.20   0.30   0.40   0.50   0.60   0.75   1.00
    shown         44     35     29     24     22     16     14
    silenced      17     26     32     37     39     45     47

**61 alerts become 24, not 4. A 61% cut, not 88%.** Still the largest free improvement on the
lateral list, and still worth having -- but the number he is told has to be the one from the whole
sample, and the small-sample version overstated it by nearly a third. **Fifth instance in this file
of a rate published before the denominator was checked.**

**14 OF THE 61 ARE UNMEASURABLE AND ALWAYS FIRE.** That is the fail-open path, it is 23% of all
alerts, and most of it is 15-40 mph on unmarked streets and intersections where the model has no
lane lines. It is the remaining noise and it STAYS: the only other position source is `roadEdges`,
which measures past the shoulder and has already caused one bug here. Anyone tempted to shrink that
number should read the road-edge rule first.

**WHY IT LIVES WHERE IT DOES.** `steerSaturated` is upstream's event, so the fork owns a GATE rather
than the event: `should_alert(self.sm)` is one term added to upstream's existing `if`, and the
arithmetic sits in a fork-owned module that imports nothing but `math`. That import list is
load-bearing -- `tools/bp_steer_saturated.py` IMPORTS `lane_deviation` from it, and an rlog tool has
already called `capnp.load()`, so a module reaching `opendbc.car.*` would abort the interpreter.

**NO NEW CAPNP FIELD, AND THAT IS NOT THE "COMPUTED AND NEVER RENDERED" BUG.** Every input is
already logged -- `modelV2.laneLines` / `laneLineProbs` for the deviation, `controlsState` and
`carState` for the trigger -- and the tool runs the SHIPPED function over them, so a drive explains
every suppression exactly. Publishing a field would have added wire surface to duplicate what the
route already carries.

**THE TOOL RECONSTRUCTS THE ALERT AT 100 Hz AND MUST KEEP DOING SO.** The first version read
`onroadEvents` and undercounted by thirty: selfdrived logs that stream "every second or on change"
(selfdrived.py:666), so a 3 s alert leaves ~3 samples and a `max()` over them is a max over three
arbitrary instants. It prints the raw `onroadEvents` count beside the reconstruction as the
cross-check (122 against 3446 across the pull, which is the expected ratio).

**MUTATION-TESTED, 11 mutants, 0 survivors** -- including the two that matter: dropping the
`deviation is None` branch (unmeasurable would go quiet) and deleting the latch reset (one wide
corner would alert for the rest of the drive).

### AND THE SUNNYLINK AUDIT HAD A FIFTH BLIND SPOT. FOUND THE WAY THE OTHER FOUR WERE.

Adding the param, adding `SteerAlert` to `OUR_PREFIXES`, and running the audit BEFORE writing any
YAML -- it reported **42/42, 0 missing**.

`collect_ui_settings` required a `param=` kwarg. The BluePilot settings screen's `toggle_item` does
not take one: it takes `initial_state=self._safe_get_bool(self._params, "X")` and
`callback=lambda state: self._toggle_callback(state, "X")`. **So every toggle on that screen has
always been invisible to the audit** -- `enable_lane_positioning_ang` and
`enable_lane_positioning_curv` included, during the exact period this file was recording that three
members of that family reached `settings_ui.json` only by hand-editing.

`_toggle_param` now reads the name out of those two kwargs and takes it only when both name the SAME
string -- a toggle that reads one key and writes another is a bug, not a setting. The audit went
42 -> 49 known settings, six of the seven newly-visible ones were already reachable, and the new one
was correctly reported missing. **The check is still the same one: add a setting and watch it FAIL.
This tool has now been green through five different structural blind spots.**

## 2026-09-05: THE SPEED FLOOR IS THE WRONG LEVER, AND I NAMED IT AS THE RIGHT ONE

He asked for the cost of raising `sat_check_min_speed` to be measured. `tools/bp_steer_alert_floor.py`
re-simulates `_check_saturation` at arbitrary floors and dwell times over recorded routes, and
**validates against the flag the car published: 99.99% agreement at the shipped 5.0 m/s / 1.0 s over
4,080,086 angle-mode frames.** A counterfactual that cannot reproduce the factual is not a
measurement, so that line is printed first and everything under it is worthless without it.

**MY CLAIM THAT IT WAS "THE ONE CHANGE THAT WOULD TAKE THE REMAINING COUNT DOWN MEANINGFULLY" IS
WITHDRAWN.** It rested on "11 of the 14 unmeasurable episodes are under 40 mph", which is true and
says nothing: the base `LatControl` floor is **22.4 mph**, and only TWO of those 14 are below it.
Under 40 mph is not under 22. **A shape argument was let stand as a magnitude argument** -- the same
failure as the ba20937aac write-up, where a real mechanism moved the wheel 0.03 degrees.

    floor   mph    episodes  on screen   removed   ...that were on screen   supervision lost
     5.0   11.2       60        23          -              -                      -    SHIPS
     8.0   17.9       59        22          1              1                    1.3%
    10.0   22.4       57        20          3              3                    3.7%
    12.0   26.8       57        20          3              3                    5.8%
    14.0   31.3       42        16         17              7                    9.1%
    16.0   35.8       36        13         24             10                   11.6%
    18.0   40.3       30        10         30             13                   13.9%

**Every alert a defensible floor removes is one the lane gate was SHOWING**, because the alerts that
survive the gate ARE the low-speed unmeasurable ones. That is not a coincidence, it is the two
mechanisms being anti-correlated by construction. Going far enough to matter (18 m/s, 40 mph) takes
two MEASURED-WIDE episodes with it -- 0.82 m and 0.76 m, both at 36 mph -- and stops watching 14% of
engaged driving.

**A FLOOR REMOVES SUPERVISION, NOT JUST ALERTS.** Below it `sat_time` never accumulates, so the car
can saturate indefinitely and nothing can ever fire. That column is in the tool output because a
count of alerts cannot show it.

### THE DWELL TIME IS BETTER AIMED AND STILL NOT FREE

`sat_limit` is `CP.steerLimitTimer`. Grepped across the whole tree it reaches exactly one line --
`latcontrol.py:9` -- and its capnp comment reads "time before steerLimitAlert is issued", so it
**cannot change how the car drives**; it only decides how long a saturation must persist before he
is told. Ford already carries 1.0 s, the longest of any brand (most are 0.4).

    dwell   episodes   on screen   removed   ...that were on screen
     1.0       60         23          -             -                SHIPS
     1.5       30         16         28             7
     2.0       20          9         39            12
     3.0       10          6         50            17

1.5 s halves everything and costs no supervision at any speed -- **but three of the seven on-screen
alerts it removes were measured genuinely wide: 0.76 m at 36 mph, 0.75 m at 75 mph, 0.53 m at
71 mph.** Those are the events the alert exists for.

### VERDICT: SHIP NEITHER. THE LANE GATE IS THE ONLY LEVER HERE THAT DISCRIMINATES.

A floor asks how fast the car was going and a dwell asks how long it lasted; neither asks whether
anything was wrong. The gate asks the only question that separates the two populations, which is why
it cuts 61 alerts to 24 without losing a single wide one, and why both of these cut real warnings the
moment they cut anything. **Do not raise either on the strength of an alert count alone.**

**AND THE TWO TOOLS DISAGREE BY ONE EPISODE (61/24 against 60/23), WHICH IS EXPECTED AND IS STATED
RATHER THAN RECONCILED AWAY.** `bp_steer_saturated.py` reads the flag the car published;
`bp_steer_alert_floor.py` re-simulates it and counts angle-mode frames only. 476 frames of 4.08 M
differ. Quote whichever tool produced a number, and never mix them in one table.

**A MATCHING BUG IN THE FIRST VERSION DOUBLED THE COST COLUMN.** Episodes were matched across
settings by rounded start time -- but raising either lever DELAYS accumulation, so the same
saturation fires later and reads as one episode lost plus one new. Now matched on the two windows
being within 3 s of overlapping. The 1.5 s row went from "19 lost" to "13 lost" on the first pull
that exposed it.

## 2026-09-05: THE EXIT-BLEND LATCH IS BUILT. IT IS A TAIL FIX AND IT SAYS SO.

The mechanism this file has called dead since 2026-08-29 now runs. `_EXIT_LATCH_CALLS = 10` in
`lateral_angle_ext.py` holds the exit state for 0.50 s after the gate last fires, so the 0.1-per-call
ramp can actually walk `b_blend` from 0.500 to 0.125 instead of taking one step and springing back.

**THE MAGNITUDE WAS CHECKED BEFORE ANYTHING WAS WRITTEN, and that was the promise made to him.**
`tools/bp_blend_latch_scale.py` recovers `predicted` by inversion from published telemetry
(`predicted = (kappaCmd - laneCenterCorrection - desired*(1-b)) / b`) and reports what the latch can
reach in the only unit he judges in:

    reachable swing   p50 0.34 deg   p90 1.54   p99 4.66   max 10.04
    at or over the 0.30 deg dither floor:  162 of 295 firings (55%)

**The median is barely over the floor. This is a TAIL fix and must be described as one** -- which is
the right shape, because the exit overshoot it targets is itself a tail: median +0.16 deg, p90 +3.21,
p99 +10.81. A median-vs-median comparison would have called this pointless and been wrong.

**`_EXIT_LATCH_CALLS = 10` IS MEASURED.** Gaps between consecutive fires inside one unwind, n=176:
p50 4 calls, p75 7, p90 15. A 4-call latch bridges 57%, 8-call 79%, **10-call 86%**, 12-call 89%,
16-call 93%. Ten sits at the knee, and four is the hard floor because the ramp needs four calls to
traverse -- anything shorter reproduces the defect exactly.

**THE RELEASE IS THE SAFETY-CRITICAL HALF.** `_kappa_entering` zeroes the latch on the frame it
appears, ahead of any decrement, so a new turn-in can never be served a stale exit weight. **Only
mutation testing caught that the two branches must be tested in that order**: swapping them left all
nine other tests green, because `_on_exit_near_limit` carries its own `not _kappa_entering` and hides
the bad arm until the following call. 12 mutants, 0 survivors.

### THE SAMPLING INTERVAL CAUGHT ME TWICE IN ONE HOUR, AND IT MOVED THE ANSWER THREE TIMES

`controllerStateBP` is published from card at **100 Hz**, but `update_angle_strategy` runs inside
`STEER_STEP = 5` -- so every value repeats five times. `_desired_falling` compares consecutive
angle-path CALLS. Three ways of sampling gave three different magnitudes for the same question:

    every 100 Hz frame          fires 0.11% of calls, reachable p50 1.04 deg   <- selects only the
                                                                                  sharpest falls
    "the published values changed"  fires 0.56%, reachable p50 0.07 deg        <- under-counts calls
                                                                                  (10.6 frames each)
    RESAMPLED AT 20 Hz          fires 0.54%, reachable p50 0.34 deg            <- correct

**I reported the first number to him before catching it.** This file already carries the rule --
*"check what interval a comparison spans before scoring it"*, written on 2026-08-28 about this exact
gate -- and it was broken anyway. **Resample on TIME; do not try to detect the call**, because two
consecutive calls on steady road publish identical values and an edge detector silently drops them.

### WHAT IS NOT YET KNOWN

It has never been driven. The replay says what the COMMAND does and cannot say what the car does
back. Score it on a drive with nothing else moving, against `tools/bp_lateral_phases.py` (the
UNWINDING row and its degrees column) and `tools/bp_lateral_by_radius.py` in the 500-2000 m band.
The alert gate shipped the same day cannot confound it -- that changes nothing the car does -- but if
the steering feels different, it is this.

## 2026-09-05: THE LATCH IS DRIVEN. IT RUNS, AND THE UNWIND TAIL HALVED.

Route `00000427`, 13 segments, 7.5 minutes hands-off, first drive with both the alert gate and the
exit-blend latch. **Verified BY CONTENT on the device** (`grep -c` for `steer_saturated_gate` and
`_EXIT_LATCH_CALLS`), not by hash, because the branch was rebased.

**NOTHING LATERAL MOVED, which is what makes it scoreable.** Params read off the device with mtimes
converted to MDT: the gains, both lane-centering keys and the damper are all unchanged since 09-01
to 09-04. `SteerAlertLaneGate` was written 12:44 MDT -- manager storing the new key's shipped
default on the first boot after the update, four minutes before the drive.

**ONE OTHER VARIABLE, and it is his:** `SmartCruiseControlMapFactor` 90 -> 100, written 12:40 MDT,
five minutes before the route. It scales SCC-Map's corner speed at or below 25 mph. The two drives'
speed profiles are nearly identical (p50 24 vs 27 mph, p90 34 vs 34), so it did not visibly move
what the car did -- but it is a second change on the drive and is stated rather than ignored.

### IT RUNS. 13.5x MORE TIME AT THE EXIT WEIGHT.

    BEFORE  424/425, LC 0.45, no latch    34 of  6470 calls at b_blend 0.125   0.53%
    AFTER   00000427, latch                901 of 12585 calls at 0.125         7.16%

901 is almost exactly the 87 qualifying gate firings times the 10-call hold, so the mechanism is
doing precisely what it was built to do and nothing more.

**I REPORTED "THE LATCH DID NOT WORK" FIRST, AND IT WAS A PERCENTILE READ AS A MINIMUM.**
`blendWeight` p10 was 0.175 on both drives, and that was quoted as "0.125 is never reached". The
exit weight occupies ~1-7% of calls, which is BELOW p10's resolution by construction. **A percentile
cannot answer a question about a rare value; count it.** Same family as "ask how big before
concluding from how often", one layer over.

**AND THE HYPOTHESIS BUILT ON THAT WRONG READING WAS ALSO WRONG.** `_kappa_entering` was accused of
zeroing the latch constantly. Recomputed from `modelV2.orientationRate.z` and `liveDelay` exactly as
the module does: it is true on 1.0% of calls and blocks only **16% of gate firings**. 84% select the
exit branch, which is what the 901 calls show. The release is not the problem and was never the
problem.

### THE OUTCOME: THE HIGHWAY UNWIND TAIL IS DOWN ~50%, ON A MATCHED SAMPLE

`tools/bp_lateral_phases.py`, same flags, comparable roads:

    band / phase                    BEFORE (no latch)            AFTER (latch)
    tight <500 m   UNWIND ratio     0.825  p90 1.46             0.907  p90 1.39
    tight <500 m   UNWIND degrees   med -1.35  p90 +3.13  p99 +41.51    med -0.83  p90 +4.04  p99 +21.03
    hwy 500-2000 m UNWIND ratio     1.020  p90 2.28   n=1520    1.012  p90 1.69   n=3415
    hwy 500-2000 m UNWIND degrees   med +0.05  p90 +3.18  p99 +8.98     med +0.03  p90 +1.39  p99 +5.60

**The 500-2000 m band is the one to read** -- this file already established it as the target band --
and it improved on every tail measure with a real sample behind it: p90 overshoot **+3.18 -> +1.39
degrees (-56%)**, p99 **+8.98 -> +5.60 (-38%)**, p90 ratio 2.28 -> 1.69. The medians barely move,
which is exactly what a tail fix should look like and is what the pre-build magnitude check
predicted (p50 0.34 deg, p90 1.54, p99 4.66).

**The tight <500 m band is mixed and thin.** p99 halves (+41.51 -> +21.03) but p90 rises slightly
(+3.13 -> +4.04), and with n=451/703 unwinding frames a p99 is the fourth-to-seventh worst sample.
Do not quote that row as a result either way.

**ONE DRIVE, 7.5 MINUTES HANDS-OFF.** The exit-weight occupancy is robust (thousands of calls); the
outcome is one matched pair on comparable surface roads. It wants a highway drive before the tail
numbers are treated as settled.

### THE ALERT GATE IS UNSCORED AND THAT IS ARITHMETIC, NOT A FAILURE

**Zero `steerSaturated` episodes on the whole route**, reconstructed and raw. The base rate is 61
episodes across 701 segments, so 13 segments expects about ONE. Zero is uninformative -- it is not
evidence the gate works and not evidence it does not. **Do not report an absence at this sample size
as a result**; it needs a drive with the 29-56 mph curves that produce the alerts.

## 2026-09-05: "SET SPEED CHANGED" WAS 99% NOISE. TWO GUARDS, MEASURED ACROSS FOUR PULLS.

*"It's still telling me set speed changed to the speed limit all the time now, even when the set
speed didn't change at all."* Then, narrowing it himself: *"I think we were on SLA and not a hold
and it just kept telling me it changed even though it didn't."*

**IT IS A DIFFERENT ALERT FROM THE ONE FIXED ON 2026-08-27, WHICH IS WHY HE SAID "STILL".** That fix
gated `speedLimitAutoSet` on a hold. This is `speedLimitChanged` / `speedLimitActive`, fired from
`update_active_event` on the ENTRY EDGE into an active state -- **a trigger that never consulted the
set speed at all**, and which picks its wording purely from whether the cluster is under
`CONFIRM_SPEED_THRESHOLD`. Chasing the 2026-08-27 alert again would have found nothing wrong.

### THE MEASUREMENT, AND IT IS THE WHOLE ARGUMENT

`speedLimitChanged` rising edges, with the two SHIPPED guards replayed over the recorded frames:

    pull                        fires   dash ALREADY at target   inside 5.0 s   survive
    00000427                       17            17                    5           0
    2026-09-04 lc_035_vs_045       54            50                   48           0
    2026-09-03 post_damper         11            10                    9           0
    2026-09-01 damper_and_gain     13            12                    6           1
    TOTAL                          95            94                   68           1

**Ninety-five announcements, ninety-four of them with the dash already sitting on the number.**

**AND THE ONE SURVIVOR IS THE PROOF THE GUARDS ARE SHAPED RIGHT, not merely aggressive:**
`t+14384.5, dash 48, target 40` -- SLA about to bring the set speed down eight miles an hour. That
is the sentence the alert exists to say, and it is the only time in four drives it was true.

**SAY THE COST OUT LOUD RATHER THAN LETTING HIM FIND IT: this alert now fires about once in four
drives.** That is not a bug, it is what "only when it is true" costs on his roads -- ICBM has
usually already walked the dash to the limit before the entry edge happens, because the entry edges
are him cycling cruise at lights (35.5% of 00000427 had cruise off).

### THE TWO GUARDS

1. **`target_set_speed_confirmed`** -- the dash already equals the target, so nothing is going to
   happen. Rewording it to `speedLimitActive` would be equally untrue and chimes identically.
2. **A re-announce cooldown that IS THE ALERT'S OWN 5.0 s DURATION**, not a number anyone picked: a
   second announcement inside that window lands while the first is still on screen. Route 00000427
   t+375 fired three times in 1.5 s off one `active -> inactive -> active` flicker.

**Guard 1 returns BEFORE the counter is zeroed**, so a suppressed no-op cannot spend the cooldown
and mute a real announcement behind it. Swapping the two kills a test, deliberately.

### `speedLimitAutoSet` WAS AUDITED AND IS LEFT ALONE -- IT FIRES ONCE IN TWELVE MINUTES

It carries the SAME `promptSingleHigh` chime and has NO cooldown, so it looked like the same bug one
file over. Measured on 00000427: **1 fire in 12.1 minutes, 0 with a motionless dash.** The
2026-08-27 hold gate plus the resolver's `fail`/`possible` refusals already hold it. Adding a
cooldown there would be **refusing a bit by association**, which this file already records as
costing more than the bit ever did.

### THE SAMPLING TRAPS, BOTH HIT IN ONE HOUR

- **`Alert.duration` IS IN CONTROL FRAMES.** `int(duration / DT_CTRL)`, so the shipped alert reads
  **500** where the cooldown constant reads **5.0**. The test that ties the constant to the alert
  asserted `duration == ANNOUNCE_COOLDOWN_S` and failed `500 == 5.0`. Two fields both called a
  duration, on different clocks -- the units half of "compare endpoints before comparing".
- **THE FIRST REPLAY USED `assist.vTarget`. THE CODE USES `resolver.speedLimitFinalLast`.** Caught
  by reading line 261 rather than trusting the name. It happened to give the same answer here
  because SLA was tracking the limit exactly, which is precisely how this trap survives.

### AND THE 20 "ERRORS" IN THAT FOLDER ARE PRE-EXISTING AND EXPECTED

`bp_offline_test.py sunnypilot/.../speed_limit/` reports **20 errors at HEAD as well** -- the folder
holds `test_speed_limit_assist.py`, which needs the device stack and is deliberately excluded from
`DEFAULT_TARGETS` by NAME. Running the DIRECTORY pulls it back in. Run the named files, or the
whole suite; a directory run in that folder is not a signal.

### THE `__new__` FIXTURES ARE FINE. DO NOT BUILD MACHINERY FOR THEM.

Adding one field to `__init__` broke seven tests in `test_the_announcement_defers_to_a_hold.py` with
`AttributeError` -- those fixtures use `SpeedLimitAssist.__new__` because `__init__` needs a real
`CP`/`CP_SP` the runnable suite does not have. **That is the system working**: it failed loudly, in
seven places, and was diagnosed in minutes. The dangerous version is a `getattr` default that would
have hidden it. Seed the field in the fixture and move on. (The other three `__new__` fixtures in
that folder never reach `update_events` and were checked, not assumed.)

**MUTATION-TESTED, 9 mutants, 8 killed.** The survivor is `<` -> `<=` on the cooldown boundary --
one model frame in a hundred, 50 ms on a 5 s window. Immaterial, and stated rather than chased with
a contorted test.

## 2026-09-05: THE SHIFTER READ "SPORT" AT A LIGHT AND ARMED A SOFT DISABLE SIX TIMES

Found sweeping route 00000427 after he asked what else was on the drive. Everything else in the
event histogram was startup (`parkBrake`, `wrongGear`, `controlsMismatch`, `reverseGear`,
`radarTempUnavailable`, `commIssue` -- ALL inside the first 8.1 s, parked, cruise off, gear going
park -> reverse -> drive as he backed out) or him parking at the end. **Print event timings before
calling any of them a finding; a histogram alone would have had me chasing a park-brake ghost.**

The one mid-drive item is real:

    t+299.2 .. 313.8   gearShifter flickers drive -> unknown -> drive, SIX times
                       engaged True and latActive True throughout, stopped at a light

**DECODED OFF THE WIRE RATHER THAN GUESSED**, per the rule that already paid twice here.
`TransGearData.GearLvrPos_D_Actl` (560, bits `12|4@0+` -> `(byte1 >> 1) & 0xF`) over the drive:

    3  Drive               35572
    0  Park                  579
    4  Sport_DriveSport      262      <- ALL of them inside that one window
    1  Reverse                 4
    2  Neutral                 4

**Value 14 `Unknown_Position` and 15 `Fault` appear ZERO times.** So this is not a signal dropout
and not a TCM fault -- the lever genuinely read S. That distinction is the whole finding, and only
the wire could make it.

**AND IT IS TWO SEPARATE GAPS STACKED, WHICH IS WHY FIXING THE OBVIOUS ONE DOES NOTHING:**

1. `GEAR_SHIFTER_MAP` has `'SPORT'` but the DBC string is `Sport_DriveSport`, which uppercases to
   `SPORT_DRIVESPORT` -- not a key. So a legitimate gear falls through to `GearShifter.unknown`.
2. **Even mapped correctly it would still fire.** `car_specific.py:108` raises `wrongGear` unless
   the gear is `drive` or in `CI.DRIVABLE_GEARS`, and **Ford's is `(low, manumatic)` -- no sport.**
   GM and Honda list sport; Ford does not.

`wrongGear` is `ET.SOFT_DISABLE`, so each flicker armed a disengage countdown at a light.

**IT COST HIM NOTHING AND THAT WAS MARGIN, NOT DESIGN.** Six runs, lengths 1.20 / 1.14 / 1.22 /
0.72 / 0.52 / 0.32 s against `SOFT_DISABLE_TIME = 3` -- the longest used 41% of the budget. A
flicker three times longer disengages him mid-intersection.

**NOT FIXED, DELIBERATELY.** One window, on one drive, and the fix touches gear handling, which is
the category this file says not to ship on an evening's reasoning. **And the question that decides
it is his, not mine: does he ever actually drive in S?** If yes, Ford's `DRIVABLE_GEARS` is simply
wrong for this car and both gaps want closing. If he does not -- if the lever was merely brushed --
then the honest fix is nothing at all, and a mapping change would only make a real S engagement
silent. ASK BEFORE DESIGNING AROUND SOMETHING HE DRIVES EVERY DAY.

### AND THE REST OF THE DRIVE IS HEALTHY, MEASURED PER SEGMENT

- **mapd v2 held 20 Hz on all 13 segments** (~1200 `mapdOut` per segment, 1199-1221). The
  2026-08-30 stall did not recur. Broken down BY SEGMENT deliberately, because a total is how that
  death got reported as healthy.
- **`modelStopBraking` fired twice and both look timely** -- t+136.0 at 31.5 mph decelerating
  monotonically to 21.6, and t+236.4 at 33.7 down to 29.1. Neither shows the ~12 s late arming of
  route 000003bb. The first bottoming near 21.6 is the 20 mph Ford floor, not a failure.
- `modeld` dropped 57 / 9 / 10 frames in three bursts, all in segment 0 (1123 `modelV2` against
  1200 elsewhere) -- boot, not a running fault.
- 65.1% engaged, 7.0% hands-on, 24.3% brake pressed, 24.9% standstill, peak 36.8 mph. A surface
  drive, and the braking share is his documented "he takes every stop himself".
