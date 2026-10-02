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

## Radar detector (Valentine One Gen2, wired ESP bus)

Branch `radar-detector`. Nothing here has met hardware yet -- everything is verified against
Valentine's published spec and their own example packets, which is the strongest offline evidence
available and is not the same as a bench test.

**Why the V1 Gen2 and not his Uniden R4.** The R4's single Bluetooth link is its only data path, so
openpilot can never read it. The V1 has a wired Accessory jack carrying ESP as a second, independent
channel. That is the whole reason for the swap; range and directional arrows are a bonus. The cost
is real and was accepted knowingly: the V1 has NO GPS, so it loses the R4's auto mute memory, its
speed-camera database and its low-speed muting. openpilot replaces the first and third.

**It is an SLA feature, not an ICBM one.** The module emits a speed-limit OFFSET OVERRIDE -- it
replaces `SpeedLimitOffsetValue` while an alert holds -- and the resolver's
`speed_limit_final = speed_limit + offset` does the rest. So it composes with his usual offset
(+5 becomes -1, a 6 mph change from a 1 mph margin), and it carries **no ACC floor and no rate
limiting**, because Ford's 20 mph minimum belongs to the button layer and disappears under alpha
longitudinal.

**Waze is deliberately out of scope.** Its live-map endpoint 403s to plain curl, to curl with full
browser headers, and to a real Chrome same-origin fetch. Highway Radar evidently gets through, so it
is reachable only by defeating bot protection -- an arms race that fails silently, and the failure
mode is the car quietly not slowing. He keeps Highway Radar for Waze on a second phone.

**The trap this feature keeps setting for itself: manufacturing its own evidence.** Same shape as
"is Ford braking" in `unconfirmed_lead.py`, and it has come up twice here already:

- muting a learned false alarm makes the detector quiet, which would count as a quiet pass, which
  erodes the record that caused the mute. A suppressed pass is now discarded, not counted.
- `update_pass` recorded an observation every 1 Hz cycle while alerting, so one drive-through logged
  five alerts and five passes. Alerts and passes inflated together, pinning the hit ratio at 1.0 --
  the definition of a false alarm -- so every real speed trap would have been muted. One pass is now
  one observation, settled on the way out.

Anywhere this feature observes something it also influences, check for this first.

**Asymmetric evidence.** Warning about a place needs 3 observations; MUTING one needs 10. Warning
wrongly is an annoyance; muting wrongly is a ticket, and it is silent.

**Tools.** `tools/bp_radar_probe.py` is the first-contact diagnostic -- run it the day the hardware
arrives. `tools/bp_radar_fit.py` fits `RadarDetectorMinBars` from `/data/radar_alerts.jsonl`; the shipped 6 is
a guess. It EXCLUDES encounters where the car acted, because slowing changes how fast the bars climb
-- fitting a threshold to data that threshold produced, and the bias flatters. Third instance of the
manufactured-evidence trap in this feature.

**The USB adapter must be FTDI. Checked on the device 2026-08-07, not assumed.** AGNOS's kernel
registers `ftdi_sio` and `option` and nothing else -- `CONFIG_USB_SERIAL_CP210X`, `CH341`, `PL2303`
and even `USB_SERIAL_GENERIC` are all "is not set" in `/proc/config.gz`, and there is no
`/lib/modules`, so nothing can be loaded later. A CP2102 or CH340 adapter will not enumerate at all,
and the symptom would look exactly like a wiring fault.

**`/dev/serial/by-id/` is never empty on this device.** The internal Quectel EG25-G LTE modem
enumerates as ttyUSB0..3, so a naive "first serial port" pick returns the modem's AT command port.
`find_port` excludes it and prefers a recognised bridge; see `PORT_EXCLUDE` in `transport.py`.

**The mute bit is GLOBAL, and that may be a problem.** It reports that audio is muted, not which
alert was muted. Vortex's recommended Gen2 setup enables Auto Mute (Advanced), which mutes X, K and
Ku after three seconds -- so an auto-muted door opener could suppress our Ka gate on a real alert.
Unverified: whether the V1 clears the bit when a higher-priority threat arrives. If it does not, the
gate must read the priority band from respAlertData rather than trusting the global bit, and that
means transmitting reqStartAlertData. Test before trusting the feature.

**No other detector is an option, checked rather than assumed.** Vortex ranks the V1 Gen2 THIRD --
behind the Uniden R8w and the Escort Redline 360c -- so it is not the best detector, it is the only
one openpilot can read. Everything above it is Bluetooth or WiFi only, and the comma 3X has no
Bluetooth at all (no controller, no BlueZ, no firmware, rfkill lists only wlan). Escort publishes no
wired protocol anywhere in its line; searches for "Escort Serial Protocol" mostly return Valentine's
ESP, which is a different company's document. Uniden's protocol is undocumented on every model.

**The external USB-C port IS a host port**, so a USB serial adapter enumerates. Confirmed from the
device tree, not inferred: `/proc/device-tree/soc/ssusb@a800000/dwc3@a800000/dr_mode = host`, which
is bus 2 -- the USB 3.0 root hub that sits idle while the LTE modem occupies bus 1. A plug test with
a phone showed nothing, which is a false negative worth knowing about: most USB-C cables to hand are
charge-only, and two USB-C devices also have to negotiate roles. The device tree is the better
answer. The port is USB-C, so the FTDI cable needs a USB-C to USB-A OTG adapter.

**The bus is 5 V logic.** Valentine's guidance is that any 5 V-safe TTL UART can read the stream,
which is why the part is an FTDI TTL-232R-5V and not a 3.3 V one. Also note **Feature L** on the V1,
which governs Legacy Concealed Display output versus ESP -- the first thing to check if the wire is
powered and no ESP data appears.

**Comma 4: done, do not redo it.** All five `RadarDetector*` keys are in
`settings_ui_src/pages/cruise.yaml` under `speed_limit_settings`, and the audit reads 37/37 on this
branch. Gating is by what each control actually needs rather than copied from its neighbors:
`RadarDetectorEnabled` is UNGATED, because reading the detector and writing the log work on any car
and need neither longitudinal nor ICBM; only the three that move the car take
`longitudinal_and_icbm`.

And **the onroad pill is absent on a comma 4 by inheritance, which is correct** -- `_draw_radar_pill`
lives in `HudRendererBP` and `MiciHudRendererBP` extends upstream's `HudRenderer`. The feature still
degrades sensibly there: the approaching-place ALERT fires, because that is the standard event path
every device renders.

**All five are safe to expose remotely, because this feature has NO data-egress path.** Checked
2026-08-12 against the rule that a kill switch on data leaving the device stays local
(`DELIBERATELY_NOT_REMOTE` in the audit tool): there is no upload, no shared feed and no remote
endpoint anywhere in `radar_detector/`. `radar_alerts.jsonl` and the GeoJSON export are written to
`/data` and read at home over SSH, which `alert_log.py`'s docstring already commits to.

`RadarDetectorEnabled` does start a position log, and it IS remotely flippable -- deliberately, since
it gates LOCAL recording rather than egress, and a comma 4 owner has to be able to turn the feature
off from somewhere. **If this feature ever grows an upload, the switch for it goes in
`DELIBERATELY_NOT_REMOTE` and gets no SunnyLink entry**, which is a different decision from this one.

**Open, needs hardware:** the ACC jack data pin (6p4c RJ11, ACC is pin-reversed from MAIN -- meter
it), whether the V1 emits ESP data with nothing asserting ESP mode, whether the time-slice timing
holds from Python, and whether anything external ever sets the mute bit.

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

## DO NOT PORT THIS FORK'S UI TO THE COMMA 4

Decided 2026-08-12, when the mici settings-screen port was offered: *"remember what UI can actually be
rendered on the Comma 4. I don't think we even want to try to display stuff."*

**SunnyLink is the entire settings story on a comma 4.** There is no mici screen for any of this fork's
33 settings and none is wanted. Do not build one, and do not treat its absence as a gap to close.

The good news, checked rather than assumed: **nothing of ours renders on a comma 4, so nothing of ours can
break there.**

- `selfdrive/ui/bp/mici/layouts/settings/` never imports our cruise layout, so our settings items are
  simply absent rather than mis-laid-out.
- `MiciHudRendererBP` extends upstream's `HudRenderer`, **not** our `HudRendererBP`. The HOLD badge,
  the ACC status readout and the brake-lamp indicator are not drawn there at all.

That separation is what makes "compatible" true by absence. **If a future change moves one of our
readouts into a shared base class, it lands on the comma 4 screen** -- so when touching
`hud_renderer_bp.py`, check which class the mici renderer inherits before assuming the small screen
is unaffected.

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

**See `bluepilot/TSR-INVESTIGATION.md`.** Note that the gateway -- the most likely place a retrofit
routing fault would live -- is OFF LIMITS by his decision, and that is not to be reopened.

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
