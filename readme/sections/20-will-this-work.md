## Will this work on my car?

**If you did the same retrofit, most of it should.** The tuning here is fitted to an Edge PSCM and
rack in a CD4-platform Ford, and a Lincoln MKZ with the same swap is the same problem — same
platform, same retrofitted steering hardware, so the numbers that matter were fitted against the
component you also have rather than against a Fusion badge.

Two practical notes before you try it:

- **The fingerprint is three-quarters retrofit parts, and one part that is not.**
  `FORD_FUSION_MK5` is fingerprinted in `opendbc/sunnypilot/car/ford/fingerprints_ext.py` on four
  ECUs: the Edge PSCM (`K2GC-14D003-AH`), the CCM radar (`JX7T-14D049-AC`) and the IPMA camera
  (`KT4T-14F397-AE`) are all retrofit hardware you would also have installed, so those match. The
  fourth is the **Fusion's own ABS** (`KG9C-2D053-MD`), and yours will be a different part number.

  So expect fingerprinting not to complete on a different donor car. Either add your ABS firmware
  string to that same entry — a one-line addition, and the right fix if you want it recognized
  automatically — or select "Ford Fusion (ADAS retrofit) 2020" by hand.
- **Check the platform specs against your car.** `CarSpecs(mass=1731, wheelbase=2.85,
  steerRatio=17.07)` in `opendbc/car/ford/values.py`. Wheelbase and steer ratio should carry across
  the platform and the shared rack; mass is the one that moves, particularly on a hybrid, and it
  feeds the lateral tuning.

**On a stock Fusion, Edge or MKZ, expect it to be wrong rather than merely unnecessary.** Several
constants exist specifically to compensate for the retrofit PSCM having different steering authority
from either donor car in stock form.

What does not transfer at all:

- **Pinned holds**, which are literally GPS coordinates on one person's commute
- **Anything fitted to one driver's comfort.** The curve-speed factors were set from measured
  cornering that this driver repeatedly chose and was happy with, around 0.28-0.31 g. That is a
  preference, not a limit, and yours may differ.

<!-- fragments: portability -->

