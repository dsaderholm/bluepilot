## Lineage, and what still comes from where

```
openpilot (comma.ai)  →  sunnypilot  →  BluePilot  →  FusionPilot
```

**BluePilot is still upstream and updates are taken from it regularly.** This fork is a layer on top,
not a departure. The Ford lateral scheme, ICBM, Speed Limit Assist, MADS and Smart Cruise Control all
come from BluePilot and sunnypilot and are not reimplemented here.

Keeping updates easy is an explicit design constraint. Every line changed in an upstream file is a
merge conflict paid forever, so new work goes into new files where it can, hooks into upstream files
are kept to one-liners, and additions whose reason has expired are deleted rather than parked at a
neutral value. `CLAUDE.md` documents the rules that enforce this.

