"""Anchor hunt: find the documented speed axis [0,10,30,50,70,90,130,250] kph in any plausible encoding.

ford-pscm-re puts the F-150 authority bell curve next to a speed axis with those breakpoints. If the
same axis exists here, the torque table is adjacent -- which is how you find a table WITHOUT a second
vehicle to diff against.
"""
import struct, pathlib, sys
BASE = 0x17000
d = pathlib.Path(sys.argv[1]).read_bytes()
AXIS = [0, 10, 30, 50, 70, 90, 130, 250]

def hits(label, pack):
  pat = b"".join(pack(v) for v in AXIS)
  out = []
  start = 0
  while True:
    i = d.find(pat, start)
    if i < 0: break
    out.append(i); start = i + 1
  print(f"  {label:<34}{len(out)} hit(s)" + ("".join(f"  0x{BASE+o:08X}" for o in out[:6]) if out else ""))
  return out

print("EXACT speed-axis searches:")
found = {}
found['f32le'] = hits("float32 LE",  lambda v: struct.pack("<f", float(v)))
found['f32be'] = hits("float32 BE",  lambda v: struct.pack(">f", float(v)))
found['u16le'] = hits("uint16 LE (kph)", lambda v: struct.pack("<H", v))
found['u16be'] = hits("uint16 BE (kph)", lambda v: struct.pack(">H", v))
found['u8']    = hits("uint8 (kph)", lambda v: struct.pack("B", v))
for sc in (10, 100, 128, 256):
  found[f'u16le_x{sc}'] = hits(f"uint16 LE (kph*{sc})", lambda v, s=sc: struct.pack("<H", min(v*s, 65535)))
  found[f'u16be_x{sc}'] = hits(f"uint16 BE (kph*{sc})", lambda v, s=sc: struct.pack(">H", min(v*s, 65535)))

print("\nF-150 LCA torque envelope [0.0,0.7,1.5,2.5,3.5,4.5,5.5,6.5] as float32:")
T = [0.0,0.7,1.5,2.5,3.5,4.5,5.5,6.5]
for lbl, fmt in (("LE","<f"),("BE",">f")):
  pat = b"".join(struct.pack(fmt, v) for v in T)
  print(f"  float32 {lbl}: {'FOUND at 0x%08X' % (BASE+d.find(pat)) if d.find(pat)>=0 else 'not present'}")
