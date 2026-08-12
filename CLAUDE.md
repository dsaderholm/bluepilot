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

## START HERE if the owner asks to update

They will open a fresh session and say something like *"update BluePilot"*, *"get the latest
version"*, or *"there's a new BluePilot"*. That is the whole request. Handle it end to end:

```bash
python tools/bp_merge_upstream.py
```

Then:

1. **No conflicts, tests green** → show them the summary, commit, push, and give them the
   copy-pasteable device command below. Done.
2. **Conflicts** → resolve them yourself. The script prints what *ours* is in each file. Keep our
   change, re-apply theirs around it, `git add`, re-run the script. Then as above.
3. **Tests fail** → say so plainly and do not tell them it is safe to flash. Diagnose it.

Then give them exactly this, and nothing more complicated:

```bash
cd /data/openpilot && git pull && sudo reboot
```

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

## Diagnosing a road report: the tools, and the order to use them

Written 2026-08-11 after an evening where three separate wrong controllers were blamed in turn. All
live in `tools/` and all are READ-ONLY; scp them to the device and run from `/data/openpilot`.

| Tool | Answers |
|---|---|
| `bp_why_slow.py` | who GOVERNED the drive (per-source occupancy) and what caused every slowdown |
| `bp_hold_history.py` | every change to the HOLD, with `baselineSource` naming the mechanism |
| `bp_dump_exit.py` | the older exit-specific dump; superseded for anything above 55 mph |

The order that works: occupancy first, then the specific event, then the raw fields. Skipping to the
raw fields is how an evening goes to the wrong controller.

**And do not trust the source label.** See "Facts that have been got wrong before" -- it names a
winner even when every candidate is `V_CRUISE_UNSET`.

## SCC-Map has three defenses now, and they are deliberately different questions

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

`_MAP_FACTOR_V_BP[1]` (45 mph) is the single definition of "highway corner" for all three --
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

## Working with the owner

- **He reports, I tune.** On-road reports are tuning input, not complaints to work around. His
  observation of his own device beats my inference about it every time.
- **Don't check in.** He has given open-ended permission; pick the work and report it done rather
  than closing with "want me to...".
- **Shell commands are fine.** What he dislikes is running them *in the car*, in 100-degree heat.
  Diagnostics he can SSH into at home are welcome; "run this on your next drive" is not.
- **Talk about the finished system.** Do not preface answers with what does not actuate yet; he is
  always describing the finished behavior. **This is about answering HIM, and does not extend to
  documentation** -- see the README section above, where the opposite applies, because a stranger
  reading the README has no way to know what is scaffolding.
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

**ROOT CAUSE, 2026-08-11: the IPMA cannot talk to the APIM.** `U0253 - Lost Communication With
Accessory Protocol Interface Module`, logged by the IPMA, constantly. The APIM is the SYNC module and
the source of navigation data, so `NoNavDataAvailable` is literal: the camera cannot reach the module
that would supply it.

**This is a network fault and no as-built value can fix it.** Do not edit the IPMA configuration
chasing it -- two DTCs were matured on that module in one evening doing exactly that. Dead theories,
all tested: Ford nav instead of Waze (no change), TSR data source Camera Only (it was already set
that way), Camera + APIM (rejected, reverted), region (U2101 Configuration Incompatible, twice).

It is an Edge IPMA in a Fusion, so the likely cause is gateway routing or which network the retrofit
put each module on. That is vehicle wiring work, not anything measurable from the comma device.

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
