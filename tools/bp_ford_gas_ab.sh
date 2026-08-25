#!/bin/bash
# A/B the Ford safety GAS tests: device's own ford.h vs mine.
# Only the gas/longitudinal cases -- that is what min_gas touches, and the full suite takes >30 min.
# Everything happens in /tmp; the device's own tree is never written to.
set -u
export PYTHONPATH=/tmp/oc2:/data/openpilot
cd /tmp/oc2 || exit 1

K='gas or longitudinal or accel'

run() {
  local label="$1"
  echo "=== $label ==="
  timeout 900 /usr/local/venv/bin/python -m pytest opendbc/safety/tests/test_ford.py \
    -k "$K" -q -p no:randomly 2>&1 | tail -6
}

# NEVER take the baseline from the running device: by the time this was first run the
# device had already auto-pulled the change and its own header contained it.
#   git show ed6c0b71d7^:opendbc_repo/opendbc/safety/modes/ford.h > /tmp/ford_base.h
cp /tmp/ford_base.h /tmp/oc2/opendbc/safety/modes/ford.h
echo "flag present: $(grep -c ford_bp_passthrough_long /tmp/oc2/opendbc/safety/modes/ford.h)"
run "BASELINE (device ford.h, no passthrough flag)"

cp /tmp/ford_new.h /tmp/oc2/opendbc/safety/modes/ford.h
echo "flag present: $(grep -c ford_bp_passthrough_long /tmp/oc2/opendbc/safety/modes/ford.h)"
run "MINE (widened min_gas behind the flag)"
