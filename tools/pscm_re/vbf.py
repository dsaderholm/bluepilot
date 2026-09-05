"""Minimal VBF parser: ASCII header {...} then binary blocks of (addr u32, len u32, data, crc16)."""
import struct, sys, pathlib

def parse(path):
  raw = pathlib.Path(path).read_bytes()
  # header ends at the matching close brace of `header {`
  i = raw.index(b"header")
  depth = 0
  j = raw.index(b"{", i)
  k = j
  while True:
    c = raw[k:k+1]
    if c == b"{": depth += 1
    elif c == b"}":
      depth -= 1
      if depth == 0: break
    k += 1
  hdr = raw[:k+1].decode("latin-1")
  off = k+1
  blocks = []
  while off + 8 <= len(raw):
    addr, ln = struct.unpack_from(">II", raw, off)
    if ln == 0 or off + 8 + ln + 2 > len(raw): break
    data = raw[off+8: off+8+ln]
    crc = struct.unpack_from(">H", raw, off+8+ln)[0]
    blocks.append((addr, ln, data, crc))
    off += 8 + ln + 2
  return hdr, blocks, raw

if __name__ == "__main__":
  hdr, blocks, raw = parse(sys.argv[1])
  print(f"file bytes {len(raw)}   blocks {len(blocks)}")
  for a, l, d, c in blocks:
    print(f"  block @ 0x{a:08X}  len {l} (0x{l:X})  crc 0x{c:04X}")
  if blocks:
    out = pathlib.Path(sys.argv[2])
    out.write_bytes(blocks[0][2])
    print(f"wrote {out} ({len(blocks[0][2])} bytes), load address 0x{blocks[0][0]:08X}")
