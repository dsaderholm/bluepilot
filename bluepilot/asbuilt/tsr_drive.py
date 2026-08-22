"""Score a drive for the three questions the Android Auto test asks, in one pass.

  1. Did the APIM ever send 0x463 / 0x464?           -> was Android Auto suppressing them
  2. Did the camera reach Available_FusionMode?      -> did the GPS unblock fusion
  3. Did TsrVLim1MsgTxt ever leave 255?              -> did it actually read a sign

Reports per-minute so the no-destination half and the navigating half can be compared
within one route. Runs LOCALLY -- never on the car (see CLAUDE.md).
"""
import glob
import os
import re
import sys
from collections import Counter, defaultdict

import capnp
import zstandard as zstd

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
DBC = os.path.join(REPO, "opendbc_repo", "opendbc", "dbc", "ford_lincoln_base_pt.dbc")

capnp.remove_import_hook()
log_capnp = capnp.load(
  os.path.join(REPO, "cereal", "log.capnp"),
  imports=[os.path.join(REPO, "cereal"),
           os.path.join(REPO, "opendbc_repo", "opendbc", "car"), REPO])

NAV1, NAV2, NAV3, TSR = 0x462, 0x463, 0x464, 0x3CD


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


def be(raw, total, start, length):
  bi, bib = start // 8, start % 8
  msb = bi * 8 + (7 - bib)
  return (raw >> (total - msb - length)) & ((1 << length) - 1)


def main(pattern):
  sigs, vals = dbc_defs()
  files = sorted(glob.glob(pattern))
  dctx = zstd.ZstdDecompressor()

  nav_counts = Counter()
  tsr_payloads = Counter()
  per_min = defaultdict(lambda: {"nav2": 0, "nav3": 0, "fusion": 0, "tsr": 0, "sign": 0, "vmax": 0.0})
  sign_hits = []
  v_max = 0.0
  print(f"scanning {len(files)} segments", flush=True)

  for i, path in enumerate(files):
    v_ego = 0.0
    try:
      with open(path, "rb") as f:
        data = dctx.stream_reader(f).read()
      for evt in log_capnp.Event.read_multiple_bytes(data):
        w = evt.which()
        if w == "carState":
          v_ego = evt.carState.vEgo
          v_max = max(v_max, v_ego)
        elif w == "can":
          for c in evt.can:
            if c.address in (NAV1, NAV2, NAV3):
              nav_counts[c.address] += 1
              if c.address == NAV2:
                per_min[i]["nav2"] += 1
              elif c.address == NAV3:
                per_min[i]["nav3"] += 1
            elif c.address == TSR and c.src == 2:
              h = bytes(c.dat).hex()
              tsr_payloads[h] += 1
              per_min[i]["tsr"] += 1
              d = bytes.fromhex(h)
              raw, total = int.from_bytes(d, "big"), len(d) * 8
              g = {n: be(raw, total, s, l) for n, s, l in sigs}
              if g["TsrStatMsgTxt_D_Rq"] == 2:      # Available_FusionMode
                per_min[i]["fusion"] += 1
              if g["TsrVLim1MsgTxt_D_Rq"] != 255:
                per_min[i]["sign"] += 1
                sign_hits.append((i, g["TsrVLim1MsgTxt_D_Rq"], round(v_ego * 2.2369, 1)))
      per_min[i]["vmax"] = round(v_max * 2.2369, 1)
    except Exception as e:
      print(f"  {os.path.basename(path)}: {type(e).__name__}", flush=True)

  print()
  print("=" * 72)
  print("Q1  DID THE APIM SEND THE TWO MESSAGES THE CAMERA WAITS ON?")
  print("=" * 72)
  for a, name in ((NAV1, "0x462 Nav_1 (position)"), (NAV2, "0x463 Nav_2 -> IPMA"), (NAV3, "0x464 Nav_3 -> IPMA")):
    mark = "" if a == NAV1 else ("   <<<<< APPEARED" if nav_counts[a] else "   --- still never ---")
    print(f"  {name:<28}{nav_counts[a]:>8} frames{mark}")

  print()
  print("=" * 72)
  print("Q2/Q3  WHAT THE CAMERA REPORTED")
  print("=" * 72)
  total_tsr = sum(tsr_payloads.values())
  print(f"  {total_tsr} frames of 0x3CD, {len(tsr_payloads)} distinct payloads, peak {v_max * 2.2369:.1f} mph")
  print()
  for h, n in tsr_payloads.most_common(8):
    d = bytes.fromhex(h)
    raw, total = int.from_bytes(d, "big"), len(d) * 8
    g = {n2: be(raw, total, s, l) for n2, s, l in sigs}
    print(f"  {h}  x{n:<6} VLim1={g['TsrVLim1MsgTxt_D_Rq']:<4} "
          f"{vals['TsrStatMsgTxt_D_Rq'].get(g['TsrStatMsgTxt_D_Rq'],'?'):<24} "
          f"{vals['TsrMsgTxt_D_Rq'].get(g['TsrMsgTxt_D_Rq'],'?'):<26} "
          f"{vals['TsrVlUnitMsgTxt_D_Rq'].get(g['TsrVlUnitMsgTxt_D_Rq'],'?')}")

  print()
  print("=" * 72)
  print("PER SEGMENT -- compare the no-destination half against the navigating half")
  print("=" * 72)
  print(f"  {'seg':<5}{'0x463':<8}{'0x464':<8}{'fusion':<9}{'SIGN':<7}{'peak mph':<10}")
  for i in sorted(per_min):
    r = per_min[i]
    flag = "  <<<<<" if r["sign"] else ""
    print(f"  {i:<5}{r['nav2']:<8}{r['nav3']:<8}{r['fusion']:<9}{r['sign']:<7}{r['vmax']:<10}{flag}")

  print()
  if sign_hits:
    print(f"*** THE CAMERA READ A SIGN -- {len(sign_hits)} frames ***")
    for seg, limit, spd in sign_hits[:20]:
      print(f"    seg {seg}: limit {limit} at {spd} mph")
  else:
    print("*** TsrVLim1MsgTxt was 255 on every frame ***")


if __name__ == "__main__":
  main(sys.argv[1])
