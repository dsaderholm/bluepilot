# BluePilot fork — working notes

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

The suite is **299 passing, 0 failing**. Keep it that way. Do not learn to ignore a failure — three
of them were dismissed as "pre-existing stub artifacts" across two sessions, and that habit is
what let a startup crash reach the car.

Environment (already set up): `../.venv-bp312`, a 3.12.13 venv. Recreate with:

```bash
uv python install 3.12
uv venv --python 3.12 ../.venv-bp312
uv pip install --python ../.venv-bp312/Scripts/python.exe pytest pycapnp numpy zstandard requests tqdm crcmod-plus sympy pyserial raylib ruff
```

## What offline tests do NOT cover

Anything needing compiled extensions, a real CAN bus, or a display. Two specific gaps have bitten:

- **`get_can_parsers`.** `CANParser` rejects a duplicate message address with
  `RuntimeError("Duplicate Message Check: N")` at car init, which kills `card` and leaves the
  device on "waiting to start". This happened for real when an upstream merge added a second
  `Traffic_RecognitnData` registration. `test_can_parser_messages.py` now stubs `CANParser`/`DBC`
  and asserts on the argument lists across all flag combinations. **Anything that builds a parser
  or runs at car init needs a test of this shape** — the behavioural suite never reaches it.
- **Settings screens rendering.** Structure and names can be checked statically (see below), but
  nothing renders them offline.

Onroad HUD drawing *is* checkable: `selfdrive/ui/bp/onroad/tools/preview_acc_status.py` renders the
shipped drawing methods to PNG at device scale. Use it rather than guessing at sizes.

## Before saying a branch is safe to flash

1. `python tools/bp_offline_test.py` — expect 299 passed, 0 failed.
2. `ruff check --isolated --select F821,F811,F401,F841 <changed .py files>` — F821 (undefined name)
   is the one that matters; it catches what import tests cannot reach. Compare any finding against
   the merge base before treating it as yours.
3. For changes under `opendbc/car/` or anything in `card`: confirm nothing new is constructed at
   car init without a test, and that every CAN signal read exists in the DBC.
4. For new `Params` keys: confirm each is declared in `common/params_keys.h`. The stubbed `Params`
   raises on unknown keys the way the device does.

## Do not fix UNRELATED upstream bugs in this fork

This is a personal fork of BluePilot, which forks sunnypilot, which forks openpilot. A bug that
belongs to one of those layers, and has nothing to do with the work here, should be **reported
there, not patched here**.

Every upstream line this fork modifies is a merge conflict paid for on every future rebase, forever.
That is worth it for something this car needs and free-riding on someone else's maintenance for
anything else.

**ICBM is the exception, and it is a broad one.** Anything touching Intelligent Cruise Button
Management is in scope whatever layer owns the file, bug or feature, without asking. ICBM itself
lives under `sunnypilot/`, most of what it reads is sunnypilot's, and the whole point of this fork
is making it work properly on this car -- so "that is upstream's file" is not a reason to leave ICBM
behaviour broken. The same goes for anything ICBM depends on: `cruise_ext.py`'s button timers feed
the press stand-down, so they are ICBM's business too.

The rule is about bugs that are *not ours*. A boot-splash warning is not ours. ICBM always is.

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

## The ICBM button contract

Settled on the road, 2026-08-03. Do not change these meanings without asking — they are muscle
memory now, and the owner has already relearned them once.

A HOLD is the driver's own set speed. While one is held every other ICBM feature keeps working
against it: curves still slow the car, the hazard path still fires, and the speed returns to the
driver's number afterwards rather than to the speed limit.

| Button | Cruise engaged | Cruise off |
|---|---|---|
| `+` (`CcAslButtnSetIncPress`) | `accelCruise` — creates or raises a HOLD | `setCruise` — engages, **clears** the hold, SLA takes the speed |
| `−` (`CcAslButtnSetDecPress`) | `decelCruise` — creates or lowers a HOLD | `setCruise` — engages, **clears** the hold |
| CNCL/RES (`CcAslButtnCnclResPress`) | `cancel` | `resumeCruise` — engages and **keeps** the previous hold |

A hold is also cleared by returning the set speed to exactly SLA's target, or by the posted limit
moving more than `IcbmBaselineResetDelta`. It is NOT cleared by curves or lead vehicles.

Two things that decided the design:

- **SET does not hold the current vehicle speed**, even though Ford's PCM briefly sets it there.
  If it did, every engagement would create a hold and SLA would never manage a limit unless the
  driver explicitly handed it back each drive. `+` is already the deliberate "I want a different
  number" gesture, so SET is left meaning "engage and manage it".
- **Tap moves the set speed 1 mph, press-and-hold moves it 5 mph** — the car's behaviour, not
  openpilot's. Model set-speed movement as 5 mph jumps with stationary gaps, never a 1 mph ramp.

## Car

2020 Ford Fusion Titanium AWD with retrofitted Edge ADAS parts (Edge PSCM, rack, IPMA camera, CCM
radar; Fusion ABS, IPC, steering column). Platform `FORD_FUSION_MK5`, `flags 18` =
`ALT_STEER_ANGLE | TSR`, **not** CAN FD — that combination is what exposed the duplicate-message
bug, so keep it in mind when reasoning about flag-gated branches.
