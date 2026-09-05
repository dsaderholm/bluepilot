"""Compare his 32 KB PSCM cal against the F-150 reference, using their documented signatures."""
import struct, pathlib, sys
his = pathlib.Path(sys.argv[1]).read_bytes()
bdl = pathlib.Path(sys.argv[2]).read_bytes()
edl = pathlib.Path(sys.argv[3]).read_bytes()
print(f"  his cal {len(his)} B   F150 BDL {len(bdl)} B   F150 EDL {len(edl)} B")

BELL_BDL = [14,25,32,40,43,44,42,33,23,14,9,0]
BELL_EDL = [10,19,23,29,31,32,31,23,16,10,6,0]
AXIS_BDL = [0,51,66,78,100,135,182,240,307,387,479,582,735,897,1067,1245,1434,1628,1831]

def u16s(b): 
  n=len(b)//2; return struct.unpack(f"<{n}H", b[:n*2])

def find_seq(b, seq):
  pat = b"".join(struct.pack("<H", v) for v in seq)
  out, s = [], 0
  while True:
    i = b.find(pat, s)
    if i<0: break
    out.append(i); s = i+1
  return out

print("\n=== sanity: do the reference signatures appear in the F-150 cal where documented? ===")
for nm, seq, ref in [("bell BDL", BELL_BDL, bdl), ("bell EDL", BELL_EDL, edl), ("axis BDL", AXIS_BDL, bdl)]:
  hits = find_seq(ref, seq)
  print(f"  {nm:<10} -> {[hex(h) for h in hits] if hits else 'NOT FOUND'}")

print("\n=== do they appear in HIS cal? ===")
for nm, seq in [("bell BDL", BELL_BDL), ("bell EDL", BELL_EDL), ("axis BDL", AXIS_BDL)]:
  hits = find_seq(his, seq)
  print(f"  {nm:<10} -> {[hex(h) for h in hits] if hits else 'not present'}")

def is_bell(v):
  pk = v.index(max(v))
  if pk in (0,len(v)-1) or max(v)==0: return False
  if len(set(v))<5: return False
  return all(v[i]<=v[i+1] for i in range(pk)) and all(v[i]>=v[i+1] for i in range(pk,len(v)-1))

print("\n=== HIS cal: 12-entry u16 BELL curves ending at 0 (the F-150 signature shape) ===")
h = u16s(his); found=0
for i in range(len(h)-12):
  w = list(h[i:i+12])
  if w[-1]==0 and max(w)<=4096 and is_bell(w):
    print(f"  cal+0x{i*2:04X}  peak {max(w):>5} @ idx {w.index(max(w))}   {w}")
    found+=1
    if found>=20: print("  ..."); break
if not found: print("  none")

print("\n=== HIS cal: 19-entry monotone u16 axes (the breakpoint-family signature) ===")
found=0
for i in range(len(h)-19):
  w = list(h[i:i+19])
  if w[0]==0 and all(w[j]<w[j+1] for j in range(18)) and w[-1]<20000:
    print(f"  cal+0x{i*2:04X}  {w}")
    found+=1
    if found>=10: print("  ..."); break
if not found: print("  none")
