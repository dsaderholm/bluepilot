"""FusionPilot: the vendored mapd v2 binary is the right file, for the right machine.

Two failures this catches, and neither announces itself:

- **Wrong architecture.** The release page serves one asset named `mapd` and it is aarch64. Build it
  yourself, or grab the wrong artifact, and you get an x86 ELF that the device cannot exec -- which
  manager reports as a process that will not start, several layers away from the cause.
- **A mangled binary.** A 21 MB file with no extension sits one bad .gitattributes rule away from
  being line-ending "normalized" into garbage. This repo had exactly that class of bug on
  2026-08-16, where `bluepilot/**` was marking 250 source files binary; the inverse is worse,
  because a corrupted executable fails at exec time on the car rather than in a diff.

The hash is the same guard test_mapd_version.py applies to v1, and it doubles as the version pin:
if the file changes, the constant has to change with it, in the same commit.
"""
import hashlib
import os

from openpilot.sunnypilot.mapd import MAPD_V2_PATH, MAPD_V2_VERSION

# sha256 of https://github.com/pfeiferj/openpilot-mapd/releases/download/v2.3.1/mapd
MAPD_V2_SHA256 = "db99c010103da86db17dc7c196822e26c70be091066093629923c3b80d5648bc"
MAPD_V2_SIZE = 21806000


def test_the_binary_is_vendored():
  assert os.path.exists(MAPD_V2_PATH), (
    f"{MAPD_V2_PATH} is missing. It is committed to the repo the way v1's is, so the device gets it "
    f"with the update and a test drive needs no network.")


def test_it_is_an_aarch64_executable():
  """Read the ELF header rather than shelling out to `file`, which is not on every dev machine."""
  with open(MAPD_V2_PATH, "rb") as f:
    header = f.read(20)
  assert header[:4] == b"\x7fELF", "not an ELF binary at all"
  assert header[4] == 2, "not 64-bit"
  # e_machine is a little-endian half at offset 18. 0xB7 = AArch64, 0x3E = x86-64.
  machine = int.from_bytes(header[18:20], "little")
  assert machine == 0xB7, (
    f"e_machine is 0x{machine:02X}, not 0xB7 (AArch64). The comma 3X cannot exec this; manager "
    f"would report a process that refuses to start, with nothing pointing at the architecture.")


def test_hash_and_size_pin_the_version():
  size = os.path.getsize(MAPD_V2_PATH)
  assert size == MAPD_V2_SIZE, f"{size} bytes, expected {MAPD_V2_SIZE} for {MAPD_V2_VERSION}"
  digest = hashlib.sha256(open(MAPD_V2_PATH, "rb").read()).hexdigest()
  assert digest == MAPD_V2_SHA256, (
    f"binary does not match the pinned {MAPD_V2_VERSION} release.\n  got:      {digest}\n"
    f"  expected: {MAPD_V2_SHA256}\nIf this is a deliberate version bump, update MAPD_V2_VERSION "
    f"and both constants here in the same commit.")
