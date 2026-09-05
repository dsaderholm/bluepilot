"""Hunt the AUTHORITY CURVE SIGNATURE: a 12-entry u16 that rises then falls (a bell).

ford-pscm-re: 12-entry u16, three sites, peak value 44 in one build and 32 in the BlueCruise one.
So the peak is a SMALL number -- which is a strong extra filter and rules out full-scale tables.
"""
import struct, pathlib, sys
BASE = 0x17000
d = pathlib.Path(sys.argv[1]).read_bytes()
n = len(d)//2
u16 = struct.unpack(f"<{n}H", d[:n*2])

def is_bell(v):
  if v[0] > min(v): return False
  pk = v.index(max(v))
  if pk in (0, len(v)-1): return False           # must peak in the middle
  if len(set(v)) < 4: return False               # not flat/degenerate
  rise = all(v[i] <= v[i+1] for i in range(pk))
  fall = all(v[i] >= v[i+1] for i in range(pk, len(v)-1))
  return rise and fall

print("12-entry u16 bell curves with a SMALL peak (<=255), as ford-pscm-re describes:")
found = 0
for i in range(n-12):
  w = u16[i:i+12]
  if max(w) <= 255 and is_bell(w):
    print(f"  load 0x{BASE+i*2:08X}  peak {max(w):>4} @ idx {w.index(max(w)):>2}   {list(w)}")
    found += 1
    if found >= 25: print("  ... (truncated)"); break
if not found: print("  none")

print("\nSame, but any peak magnitude, restricted to the low-entropy data region 0x0B0FF0..0x0B34FE:")
lo, hi = 0x0B0FF0//2, 0x0B34FE//2
found = 0
for i in range(lo, min(hi, n-12)):
  w = u16[i:i+12]
  if is_bell(w) and max(w) > 8:
    print(f"  load 0x{BASE+i*2:08X}  peak {max(w):>6} @ idx {w.index(max(w)):>2}   {list(w)}")
    found += 1
    if found >= 15: print("  ... (truncated)"); break
if not found: print("  none")
