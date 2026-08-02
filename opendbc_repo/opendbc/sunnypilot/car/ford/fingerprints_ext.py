"""
BluePilot Ford firmware version extensions.

Contains FW_VERSIONS for BluePilot-only Ford platforms (Ford Edge MK2, Ford Mondeo MK5).
Merged into the main FW_VERSIONS dict at module load time in
opendbc/car/ford/fingerprints.py via merge_fw_versions().
"""

from opendbc.car.ford.values import CAR
from opendbc.car.structs import CarParams

Ecu = CarParams.Ecu

# BluePilot-only Ford platform firmware versions
FW_VERSIONS_EXT = {
  CAR.FORD_EDGE_MK2: {
    (Ecu.eps, 0x730, None): [
      b'M2GC-14D003-AA\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.abs, 0x760, None): [
      b'M2GC-2D053-CB\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
      b'M2GC-2D053-EA\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.fwdRadar, 0x764, None): [
      b'JX7T-14D049-AD\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.fwdCamera, 0x706, None): [
      b'KT4T-14F397-AF\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
  },
  # BluePilot: read off the car 2026-08-02. The ADAS hardware here was pulled from a Ford Edge.
  #
  # Do not read the prefixes as model codes -- the first character is a MODEL YEAR code and only
  # characters 2-4 identify the part program. K2GC and M2GC are the same 2GC steering part, two
  # model years apart. Comparing programs against every Ford entry in tree:
  #
  #   eps    14D003  program 2GC  <- shared by Edge Mk2, Fusion and Mondeo
  #   camera 14F397  program T4T  <- shared by Edge Mk2, Fusion and Mondeo
  #   radar  14D049  program X7T  <- shared by Edge Mk2, Fusion, Mondeo, Focus, Ranger
  #   abs    2D053   program G9C  <- Fusion/Mondeo only; Edge Mk2 carries 2GC
  #
  # So three of the four are CD4 parts common to Edge and Fusion, which is consistent with
  # Edge-sourced hardware rather than evidence against it, and the ABS -- the one component that
  # was not transplanted -- is the only genuinely model-distinguishing entry. An earlier version
  # of this comment claimed these were "not Edge parts", which the program codes do not support.
  #
  # What matters for matching is unaffected: all four strings differ from FORD_EDGE_MK2's, so
  # exact matching separates the two and this should fingerprint without manual selection.
  CAR.FORD_FUSION_MK5: {
    (Ecu.eps, 0x730, None): [
      b'K2GC-14D003-AH\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.abs, 0x760, None): [
      b'KG9C-2D053-MD\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.fwdRadar, 0x764, None): [
      b'JX7T-14D049-AC\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.fwdCamera, 0x706, None): [
      b'KT4T-14F397-AE\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
  },
  CAR.FORD_MONDEO_MK5: {
    # BluePilot: the original PR (#135) included several non-ASCII byte strings alongside each
    # legit part number below -- e.g. a bare b'U', 0xff-padded blobs -- that look like NAK/error
    # responses captured verbatim rather than real FW versions. Short/degenerate entries like
    # those can spuriously match other Ford vehicles' unrelated ECU responses, making the overall
    # fingerprint ambiguous and forcing manual selection. Removed; keeping only the part numbers.
    (Ecu.fwdCamera, 0x706, None): [
      b'KT4T-14F397-AE\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.abs, 0x760, None): [
      b'KG9C-2D053-DF\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.eps, 0x730, None): [
      b'K2GC-14D003-AJ\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.fwdRadar, 0x764, None): [
      b'JX7T-14D049-AD\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.engine, 0x7e0, None): [
      b'HS7A-14C204-CJD\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    (Ecu.debug, 0x7d0, None): [
      b'1U5T-14G374-DA\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00',
    ],
    # BluePilot: removed an (Ecu.adas, 0x730, None) entry here -- b'\xf1\x10DS-K2GC-3F964-AG\x00...',
    # 26 bytes with a non-ASCII \xf1\x10 prefix, same NAK/error-response signature as the garbage
    # already stripped from this file (see the comment above). Mondeo is low-volume and non-US;
    # manual vehicle selection remains available regardless.
  }
}
