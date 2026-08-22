#!/usr/bin/env python3
"""Ford as-built checksum verify/compute, and a two-car block diff.

The checksum is the last byte of the block. It is a plain 8-bit sum of the module
address bytes, the section and block indices, and every data byte:

    checksum = (0x07 + 0x06 + section + block + sum(data_bytes)) & 0xFF

for module 706.  Section and block are the literal hex of the printed label, so
"706-02-10" contributes 0x02 and 0x10 -- NOT decimal 2 and 10.

Verified against all 58 blocks of both IPMA dumps in this directory.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent


def parse(path):
    """-> {label: (data_bytes, checksum_byte)}"""
    out = {}
    for line in Path(path).read_text().splitlines():
        m = re.match(r"^(\d{3})-(\d{2})-(\d{2})\s+(.+)$", line.strip())
        if not m:
            continue
        addr, sec, blk, rest = m.groups()
        raw = bytes.fromhex(rest.replace(" ", ""))
        out[f"{addr}-{sec}-{blk}"] = (raw[:-1], raw[-1])
    return out


def checksum(label, data):
    addr, sec, blk = label.split("-")
    total = int(addr[0:1], 16) + int(addr[1:2], 16) + int(addr[2:3], 16)
    total += int(sec, 16) + int(blk, 16)
    return (total + sum(data)) & 0xFF


def verify(path):
    blocks = parse(path)
    bad = [(l, c, checksum(l, d)) for l, (d, c) in blocks.items() if checksum(l, d) != c]
    return len(blocks), bad


def diff(a_path, b_path):
    a, b = parse(a_path), parse(b_path)
    rows = []
    for label in sorted(set(a) | set(b)):
        av, bv = a.get(label), b.get(label)
        if av is None or bv is None:
            rows.append((label, av, bv, "PRESENT ON ONE CAR ONLY"))
            continue
        if av == bv:
            continue
        ah = (av[0] + bytes([av[1]])).hex().upper()
        bh = (bv[0] + bytes([bv[1]])).hex().upper()
        # nibble positions that differ, excluding the trailing checksum byte
        n = [str(i + 1) for i in range(len(ah) - 2) if ah[i] != bh[i]]
        rows.append((label, ah, bh, ", ".join(n) if n else "checksum only"))
    return rows


if __name__ == "__main__":
    his, friend = HERE / "his-ipma-706.txt", HERE / "friend-ipma-706.txt"
    ok = True
    for p in (his, friend):
        n, bad = verify(p)
        print(f"{p.name}: {n} blocks, {len(bad)} checksum failures")
        for label, got, want in bad:
            ok = False
            print(f"    {label}  stored {got:#04x}  computed {want:#04x}")
    print()
    print(f"{'block':<12}{'HIS (TSR broken)':<18}{'FRIEND (TSR works)':<20}differing nibbles")
    for label, ah, bh, note in diff(his, friend):
        fmt = lambda h: " ".join(h[i:i+4] for i in range(0, len(h), 4)) if h else "--absent--"
        print(f"{label:<12}{fmt(ah):<18}{fmt(bh):<20}{note}")
    sys.exit(0 if ok else 1)
