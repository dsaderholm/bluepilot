"""Score a drive for traffic sign recognition. One command, everything.

    python bluepilot/asbuilt/tsr_score.py "route_dir/*.rlog.zst"

Reports, for a locally-pulled route:

  * every frame where the camera reported a limit, with a Google Maps link
  * the APPROACH PROFILE for each detection -- distance and heading over the run-up,
    which is what turns "it read a sign" into "it recognised it at N metres"
  * whether the APIM sent 0x463 / 0x464 (the U0253 messages)
  * the camera's TSR status enumerants across the drive

THE NUMBER THAT MATTERS IS DETECTION RANGE, and as of 2026-08-21 it is ZERO -- the camera
recognised a sign only when level with it, after 183 m of dead-straight approach. See section 4j
of bluepilot/TSR-INVESTIGATION.md. Any configuration change should be scored on whether that
range moves, not on whether a sign is read at all: at 30 mph a 0 m range means the sign is usable
for well under a second, which is why 44 of 50 segments contain no detection at all.

RUNS LOCALLY. Never on the device -- an on-device rlog scan cost him openpilot mid-drive on
2026-08-21. scp the segments off and run this here.
"""

import glob
import math
import os
import re
import sys
from collections import Counter

import capnp
import zstandard as zstd

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DBC = os.path.join(REPO, "opendbc_repo", "opendbc", "dbc", "ford_lincoln_base_pt.dbc")

capnp.remove_import_hook()
log_capnp = capnp.load(
  os.path.join(REPO, "cereal", "log.capnp"),
  imports=[os.path.join(REPO, "cereal"),
           os.path.join(REPO, "opendbc_repo", "opendbc", "car"), REPO])

TSR_ADDR = 0x3CD
GPS_ADDR = 0x462
NAV2_ADDR, NAV3_ADDR = 0x463, 0x464
NO_LIMIT = 255


def dbc_defs():
  text = open(DBC).read()
  sigs, inblk = [], False
  for line in text.splitlines():
    if line.startswith("BO_ 973 "):
      inblk = True
      continue
    if inblk:
      m = re.match(r" SG_ (\S+) : (\d+)\|(\d+)@", line)
      if m:
        sigs.append((m.group(1), int(m.group(2)), int(m.group(3))))
      elif not line.strip():
        break
  vals = {}
  for m in re.finditer(r'^VAL_ 973 (\S+) (.*);', text, re.M):
    vals[m.group(1)] = {int(k): v for k, v in re.findall(r'(-?\d+) "([^"]*)"', m.group(2))}
  return sigs, vals


def _be(raw, total, start, length):
  bi, bib = start // 8, start % 8
  msb = bi * 8 + (7 - bib)
  return (raw >> (total - msb - length)) & ((1 << length) - 1)


def decode_gps(dat):
  """0x462 lat/lon. Minutes are a MAGNITUDE -- on a western longitude they SUBTRACT from a
  negative degree. Adding them walks the fix east and put a Salt Lake City position in the
  Ashley National Forest on the first attempt."""
  raw = int.from_bytes(dat, "big")
  lat = _be(raw, 64, 7, 8) - 89
  lat += (_be(raw, 64, 15, 6) + _be(raw, 64, 23, 14) * 0.0001) / 60.0 * (1 if lat >= 0 else -1)
  lon = _be(raw, 64, 39, 9) - 179
  lon += (_be(raw, 64, 46, 6) + _be(raw, 64, 55, 14) * 0.0001) / 60.0 * (1 if lon >= 0 else -1)
  return lat, lon


def metres(a, b):
  dlat = (a[0] - b[0]) * 111320.0
  dlon = (a[1] - b[1]) * 111320.0 * math.cos(math.radians(a[0]))
  return math.hypot(dlat, dlon)


def bearing(a, b):
  dlat = (b[0] - a[0]) * 111320.0
  dlon = (b[1] - a[1]) * 111320.0 * math.cos(math.radians(a[0]))
  return (math.degrees(math.atan2(dlon, dlat)) + 360) % 360


def main(pattern):
  sigs, vals = dbc_defs()
  files = sorted(glob.glob(pattern), key=lambda p: (len(p), p))
  if not files:
    print(f"no segments matched {pattern!r}")
    return 1
  dctx = zstd.ZstdDecompressor()

  samples = []          # (t, pos, mph, limit) for every 0x3CD frame
  payloads = Counter()
  nav = Counter()
  v_max = 0.0
  t_base = None

  print(f"scanning {len(files)} segments locally", flush=True)
  for i, path in enumerate(files):
    pos, mph = None, 0.0
    try:
      with open(path, "rb") as f:
        data = dctx.stream_reader(f).read()
      for evt in log_capnp.Event.read_multiple_bytes(data):
        w = evt.which()
        if t_base is None:
          t_base = evt.logMonoTime
        t = (evt.logMonoTime - t_base) / 1e9
        if w == "carState":
          mph = evt.carState.vEgo * 2.2369
          v_max = max(v_max, mph)
        elif w == "can":
          for c in evt.can:
            if c.address == GPS_ADDR and c.src == 0:
              pos = decode_gps(bytes(c.dat))
            elif c.address in (NAV2_ADDR, NAV3_ADDR):
              nav[c.address] += 1
            elif c.address == TSR_ADDR and c.src == 2:
              d = bytes(c.dat)
              payloads[d.hex()] += 1
              samples.append((t, pos, mph, d[3]))
    except Exception as e:  # noqa: BLE001 -- a bad segment must not lose the rest of the drive
      print(f"  {os.path.basename(path)}: {type(e).__name__}: {e}", flush=True)

  # A detection is a REAL posted limit. 255 is the no-data sentinel, and 0 is an uninitialised
  # frame -- at boot the camera emits all-zero and near-zero payloads whose TsrStatMsgTxt reads
  # Null or NoDataExists. Counting those reported "2 with a limit" on a drive that had none.
  hits = [s for s in samples if s[3] not in (0, NO_LIMIT)]

  print()
  print("=" * 74)
  print(f"{len(samples)} frames of 0x3CD   peak {v_max:.1f} mph   {len(hits)} with a limit")
  print("=" * 74)
  for h, n in payloads.most_common(6):
    d = bytes.fromhex(h)
    raw = int.from_bytes(d, "big")
    g = {nm: _be(raw, 64, s, l) for nm, s, l in sigs}
    print(f"  {h}  x{n:<6} VLim1={g['TsrVLim1MsgTxt_D_Rq']:<4} "
          f"{vals['TsrStatMsgTxt_D_Rq'].get(g['TsrStatMsgTxt_D_Rq'], '?'):<24}"
          f"{vals['TsrMsgTxt_D_Rq'].get(g['TsrMsgTxt_D_Rq'], '?')}")

  print()
  print(f"  APIM 0x463 -> IPMA: {nav[NAV2_ADDR]} frames    0x464 -> IPMA: {nav[NAV3_ADDR]} frames")
  if not nav[NAV2_ADDR] and not nav[NAV3_ADDR]:
    print("  (still the U0253 defect -- real, and NOT what stops sign reads)")

  if not hits:
    print()
    print("*** NO SIGN READ. TsrVLim1MsgTxt was 255 on every frame. ***")
    return 0

  # Group contiguous detections into events.
  events, cur = [], [hits[0]]
  for h in hits[1:]:
    if h[0] - cur[-1][0] < 10.0:
      cur.append(h)
    else:
      events.append(cur)
      cur = [h]
  events.append(cur)

  print()
  print("=" * 74)
  print(f"{len(events)} DETECTION EVENT(S)")
  print("=" * 74)
  for ev in events:
    first = ev[0]
    limit = first[3]
    print()
    print(f"  limit {limit}, {len(ev)} frames, first at {first[2]:.1f} mph")
    if first[1]:
      print(f"  https://www.google.com/maps?q={first[1][0]:.6f},{first[1][1]:.6f}")
      anchor = first[1]
      run = [s for s in samples if s[1] and s[0] < first[0] and metres(s[1], anchor) < 250]
      if run:
        print()
        print(f"    {'t (s)':>8}{'dist (m)':>10}{'mph':>7}{'heading':>9}")
        prev = None
        for s in run[-14:]:
          hd = f"{bearing(prev, s[1]):.0f}" if prev else "-"
          print(f"    {s[0]:8.1f}{metres(s[1], anchor):10.1f}{s[2]:7.1f}{hd:>9}")
          prev = s[1]
        straight = len({round(bearing(run[k][1], run[k + 1][1]) / 15) for k in range(len(run) - 1)
                        if run[k][1] != run[k + 1][1]}) <= 2
        print()
        print(f"    RECOGNISED AT ~0 m after {metres(run[0][1], anchor):.0f} m of approach"
              f"{' (dead straight)' if straight else ''}")
        print("    -> detection range is effectively ZERO. The number to beat.")
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "*.rlog.zst"))
