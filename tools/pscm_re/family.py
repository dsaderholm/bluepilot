"""Ford replicates calibration families at a fixed stride. Find HIS stride, then his families.

On the F-150 the stride is ~0xF54 and each family has 4-5 copies. That replication is what let the
reference work identify tables without symbols -- so establishing it here is the equivalent step.
"""
import pathlib, sys, struct
from collections import Counter
d = pathlib.Path(sys.argv[1]).read_bytes()
N = len(d)
WIN = 24   # 12 u16 -- a table-sized window

# For every distinct non-trivial window, record where it occurs. Repeats => family copies.
seen = {}
for i in range(0, N-WIN):
  w = d[i:i+WIN]
  if w.count(w[0:1]) == WIN: continue          # all-same bytes
  if len(set(w)) < 6: continue                 # too degenerate
  seen.setdefault(w, []).append(i)

fams = {w:o for w,o in seen.items() if len(o) >= 2}
print(f"  distinct 24-byte windows repeated >=2x: {len(fams)}")

strides = Counter()
for offs in fams.values():
  for a, b in zip(offs, offs[1:]):
    strides[b-a] += 1
print("\n  most common repeat strides:")
for s, c in strides.most_common(12):
  print(f"    0x{s:04X} ({s:>6})   {c} occurrence(s)")

# Report the families with the most copies -- these are the multi-variant calibration families.
print("\n  families with the most copies (offset list, then values as u16 LE):")
ranked = sorted(fams.items(), key=lambda kv: -len(kv[1]))[:8]
for w, offs in ranked:
  vals = list(struct.unpack("<12H", w))
  ds = sorted({b-a for a,b in zip(offs, offs[1:])})
  print(f"    {len(offs)} copies  strides {[hex(x) for x in ds[:4]]}  first cal+0x{offs[0]:04X}")
  print(f"        {vals}")
