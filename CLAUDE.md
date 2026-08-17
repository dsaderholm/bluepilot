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

**Known violations, deliberately left alone for now.** `IcbmPinnedHolds*` and
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
| `SET −` (`CcAslButtnSetDecPress`) | `decelCruise` — creates or lowers a HOLD | `setCruise` — engages, **clears** the hold, SLA takes the speed |
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

Two things that decided the design:

- **SET does not hold the current vehicle speed**, even though Ford's PCM briefly sets it there.
  If it did, every engagement would create a hold and SLA would never manage a limit unless the
  driver explicitly handed it back each drive. `+` is already the deliberate "I want a different
  number" gesture, so SET is left meaning "engage and manage it".
- **Tap moves the set speed 1 mph, press-and-hold moves it 5 mph** — the car's behavior, not
  openpilot's. Model set-speed movement as 5 mph jumps with stationary gaps, never a 1 mph ramp.

## NO POSTED LIMIT MEANS NO HOLD. THE MAX SPEED IS THE WHOLE INTERFACE THERE.

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

The fix is three pieces, and the middle one is the one that is easy to miss:

1. `no_limit_hold_speed` keeps the baseline as it stood one frame before clearing. It is still the
   DELIBERATE press it always was; observing `v_cruise_cluster` instead would record every number he
   passes through and drown the signal that makes a suggestion mean anything.
2. `worth_showing` is now also true for a BARE PIN SUGGESTION with no hold, and `display_value`
   draws the offered speed -- taking `baseline` there rendered a badge reading `0`.
3. `pinSuggestion` is read OUTSIDE the hold branch. It had been read inside it, so it was
   unreachable in the exact case it exists for.

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

**Also learned: NO route on the device has ever had op long enabled**, so the camera question below
cannot be answered from existing data. It needs a drive.

**THE UNKNOWN THAT DECIDES IT, and it cannot be settled offline:** while we forward faithfully the
camera's loop stays closed -- it commands, the car responds, its model stays consistent. During an
override it commands "hold 20" and watches the car stop anyway. Does it re-plan, fault, or drop ACC?
Nobody knows, and the answer arrives on the first attempt.

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

  1. **HOLD badge** -- the driver's own set speed, and the tap target for pinning. The most important
     one: it is the number ICBM returns to, and nothing else on screen shows it.
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

- **The set speed falls at about 3.3 mph/s and cannot go faster.** 71 -> 38 took ten seconds. ICBM
  already HOLDS the button rather than tapping (the state machine asserts `decrease` continuously),
  so that is the car's own repeat rate for a held button, roughly 5 mph every 1.5 s. It is not a
  parameter and nothing in this fork can raise it.
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

**Checked against the industry in `bluepilot/MODEL-VS-CODE.md`**, and it mostly holds. Mobileye's
RSS is this rule formalized -- five verifiable mathematical rules sitting OVER the learned perception,
operating on assumed bounds on perception error. Waymo BUILT the end-to-end version (EMMA), kept it
as research, and ships modular, their co-CEO calling monolithic "wildly inadequate to go to full
autonomy safely and at scale". Tesla bets the other way and replaced 300k lines of C++ with one
network. And his own car already runs the standard shape -- a learned path inside a written envelope,
since panda enforces torque and accel limits regardless of what the model asks for.

**What this costs, stated because it is real:** written code only refuses what somebody thought of.
The center turn lane was not thought of -- the road taught us, twice, and it is still not fully
solved. A model that had seen ten thousand turn lanes would simply not do it. Enumeration is the
weakness of category 2, and the answer is to keep MEASURING (geoLeftTravelProven, exitsBy,
patienceMissed) so the road can keep teaching, rather than to pretend the enumeration is complete.

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
- **23 controls on the settings screen is not a usability problem.** Do not consolidate unless asked.
- **Report test results only when the result is news.** No sign-off with a suite total every message.
- **Changes made on one branch reach the others because he rebases every time.** So CLAUDE.md is the
  channel that actually travels between sessions; per-directory memory is not.

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

So the blocker is a COMMUNICATION fault between two modules, not a feature flag. TSR is also switched
off in the IPMA at `706-01-01` (third character of the first group: `1` = Off, `5` = SLIF) and SLIF is
disabled in the cluster at `720-09-01`, but neither matters while the camera cannot reach the APIM.

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

**And it does not need to be set**, which is the part worth noticing. Everything below was measured
with the region UNSPECIFIED. The camera reads signs anyway; what the region appears to gate is the
STATUS enumerants, not the detection.

What the camera actually does, measured from route 00000333 on 2026-08-09 rather than assumed:

- `Traffic_RecognitnData` (0x3CD) IS on the bus -- 366 frames on bus 2, forwarded to bus 0. The
  IPMA transmits it.
- `vLimit1` is NOT constant: 255 (the no-data sentinel) for most of the drive, and a real value for
  roughly 10% of it, with `vLimit1Permanent` flipping in lockstep. So the camera does read signs.
- Everything else in the message is pinned across 36,000 frames -- `tsrStatus`, `vLimit1Status`,
  `vLimit2`, `vLimitUnit`, the overtake and warning fields. One field doing real work, the rest idle.

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
