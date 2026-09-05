"""Do the big differing regions look like CODE or like TABLES?

Tables: many short monotone runs, values in narrow plausible ranges, low byte entropy.
Code: high entropy, few monotone runs.
"""
import struct, pathlib, sys, math
from collections import Counter
BASE = 0x17000
d = pathlib.Path(sys.argv[1]).read_bytes()

def ent(b):
  c = Counter(b); n = len(b)
  return -sum((v/n)*math.log2(v/n) for v in c.values())

def monotone_runs(vals, minlen=6):
  runs, s = [], 0
  for i in range(1, len(vals)+1):
    if i == len(vals) or vals[i] < vals[i-1]:
      if i - s >= minlen: runs.append((s, i-s))
      s = i
  return runs

for off, ln, name in [(0x0004DEFE, 85938, "big #1"), (0x000BB1AE, 48997, "big #2"),
                      (0x00064918, 8568, "mid #1"), (0x000B0FF0, 9582, "mid #2")]:
  reg = d[off:off+ln]
  print(f"\n=== {name}  file 0x{off:06X}  load 0x{BASE+off:08X}  len {ln}  entropy {ent(reg):.2f}/8 ===")
  # u16 LE monotone runs
  n = (len(reg)//2)*2
  u16 = struct.unpack(f"<{n//2}H", reg[:n])
  runs = monotone_runs(u16, 8)
  print(f"  u16-LE monotone runs >=8: {len(runs)}")
  for s, L in runs[:4]:
    print(f"     off 0x{off+s*2:06X} len {L}: {list(u16[s:s+min(L,12)])}")
  # float32 LE runs in a plausible physical range
  m = (len(reg)//4)*4
  f32 = struct.unpack(f"<{m//4}f", reg[:m])
  ok = [v if (v==0 or (1e-3 < abs(v) < 1e4)) else float('nan') for v in f32]
  fr = []
  s = None
  for i,v in enumerate(ok):
    if v==v and (s is None or v >= ok[i-1]):
      if s is None: s = i
    else:
      if s is not None and i-s >= 6: fr.append((s, i-s))
      s = None
  print(f"  f32-LE monotone runs >=6: {len(fr)}")
  for s, L in fr[:4]:
    print(f"     off 0x{off+s*4:06X} len {L}: {[round(x,3) for x in f32[s:s+min(L,10)]]}")
