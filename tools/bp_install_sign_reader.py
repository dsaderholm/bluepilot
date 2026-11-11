#!/usr/bin/env python3
"""Put the sign reader's two missing pieces on the comma: the model weights and an MNN runtime.

Neither is in the repo (the weights are not ours to publish; MNN is a 57 MB binary). Both go under
/data/bluepilot/, which a software update does not touch, and nothing is installed into the
device's system Python.

    python tools/bp_install_sign_reader.py --weights PATH/speed_limit_reader.mnn          # prints the plan
    python tools/bp_install_sign_reader.py --weights PATH/speed_limit_reader.mnn --yes    # does it

It only ever copies files into /data/bluepilot/{models,lib}. It does not reboot, restart anything or
change a setting: turn the reader on afterwards with the BPSignReaderEnabled param, and it starts the
next time the car goes onroad.

The MNN runtime is fetched here for the device's platform (aarch64, Python 3.12) with uv, so nothing
has to be downloaded on the car.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HOST = "comma@10.69.10.206"
MODELS_DIR = "/data/bluepilot/models"
LIB_DIR = "/data/bluepilot/lib"
RUNTIME_PARTS = ("MNN", "mnn.libs", "_mnncengine.cpython-312-aarch64-linux-gnu.so", "_tools.cpython-312-aarch64-linux-gnu.so")


def plan(weights: Path, runtime: Path, host: str) -> list[list[str]]:
  """The commands, in order. Pure, so it can be tested and printed without running anything."""
  steps = [["ssh", host, f"mkdir -p {shlex.quote(MODELS_DIR)} {shlex.quote(LIB_DIR)}"],
           ["scp", str(weights), f"{host}:{MODELS_DIR}/speed_limit_reader.mnn"]]
  for part in RUNTIME_PARTS:
    steps.append(["scp", "-r", str(runtime / part), f"{host}:{LIB_DIR}/"])
  steps.append(["ssh", host, f"cd {shlex.quote(LIB_DIR)} && /usr/local/venv/bin/python -c 'import MNN; print(\"MNN ok\", MNN.__file__)'"])
  return steps


def fetch_runtime(into: Path) -> Path:
  subprocess.run(["uv", "pip", "install", "-q", "--python-platform", "aarch64-manylinux_2_28", "--python-version", "3.12",
                  "--no-deps", "--target", str(into), "MNN"], check=True)
  return into


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--weights", required=True, type=Path, help="the speed_limit_reader.mnn file")
  ap.add_argument("--runtime", type=Path, help="an already-fetched aarch64 MNN folder (default: fetch one)")
  ap.add_argument("--host", default=HOST)
  ap.add_argument("--yes", action="store_true", help="actually run the commands; without it they are only printed")
  a = ap.parse_args()

  if not a.weights.is_file():
    print(f"no such weights file: {a.weights}", file=sys.stderr)
    return 2
  with tempfile.TemporaryDirectory() as tmp:
    runtime = a.runtime or fetch_runtime(Path(tmp) / "mnn")
    steps = plan(a.weights, runtime, a.host)
    for s in steps:
      print(" ".join(shlex.quote(x) for x in s))
    if not a.yes:
      print("\n(plan only; add --yes to run it)")
      return 0
    for s in steps:
      subprocess.run(s, check=True)
  print("\ninstalled. Turn the reader on with the BPSignReaderEnabled param; it starts the next time the car goes onroad.")
  return 0


if __name__ == "__main__":
  sys.exit(main())
