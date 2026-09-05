"""Where do the two PSCM strategies differ? Differing regions are candidate CALIBRATION."""
import pathlib, sys
BASE = 0x17000
a = pathlib.Path(sys.argv[1]).read_bytes()
b = pathlib.Path(sys.argv[2]).read_bytes()
assert len(a) == len(b), (len(a), len(b))
diff = [i for i in range(len(a)) if a[i] != b[i]]
print(f"  size {len(a)}   differing bytes {len(diff)}  ({100.0*len(diff)/len(a):.3f}%)")
# group into runs, merging gaps < 64 bytes
runs, start, prev = [], None, None
for i in diff:
  if start is None: start = prev = i; continue
  if i - prev > 64:
    runs.append((start, prev)); start = i
  prev = i
if start is not None: runs.append((start, prev))
print(f"  {len(runs)} differing region(s) (gaps <64B merged)\n")
print(f"  {'file off':>10} {'load addr':>12} {'len':>8}")
for s, e in runs:
  print(f"  0x{s:08X} 0x{BASE+s:010X} {e-s+1:>8}")
