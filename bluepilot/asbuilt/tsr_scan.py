"""Scan a route's rlogs for Traffic_RecognitnData (0x3CD) and decode every TSR signal."""
import glob
import re
import sys
from collections import Counter

import zstandard as zstd
from cereal import log as capnp_log

DBC = "/data/openpilot/opendbc_repo/opendbc/dbc/ford_lincoln_base_pt.dbc"


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


def main(route_glob):
    sigs, vals = dbc_defs()
    payloads = Counter()
    segs = sorted(glob.glob(route_glob + "/rlog.zst"))
    print(f"scanning {len(segs)} segments", flush=True)
    dctx = zstd.ZstdDecompressor()
    for i, path in enumerate(segs):
        try:
            with open(path, "rb") as f:
                data = dctx.stream_reader(f).read()
            for evt in capnp_log.Event.read_multiple_bytes(data):
                if evt.which() != "can":
                    continue
                for c in evt.can:
                    if c.address == 973 and c.src == 2:
                        payloads[bytes(c.dat).hex()] += 1
        except Exception as e:
            print(f"  seg {i}: {type(e).__name__}: {e}", flush=True)
        print(f"  seg {i+1}/{len(segs)}: {sum(payloads.values())} frames, "
              f"{len(payloads)} distinct", flush=True)

    print()
    print(f"TOTAL 0x3CD frames: {sum(payloads.values())}   distinct payloads: {len(payloads)}")
    print()
    for hexs, n in payloads.most_common():
        data = bytes.fromhex(hexs)
        raw, total = int.from_bytes(data, "big"), len(data) * 8
        d = {name: be(raw, total, s, l) for name, s, l in sigs}
        vlim = d.get("TsrVLim1MsgTxt_D_Rq")
        stat = vals.get("TsrStatMsgTxt_D_Rq", {}).get(d.get("TsrStatMsgTxt_D_Rq"), "?")
        msg = vals.get("TsrMsgTxt_D_Rq", {}).get(d.get("TsrMsgTxt_D_Rq"), "?")
        unit = vals.get("TsrVlUnitMsgTxt_D_Rq", {}).get(d.get("TsrVlUnitMsgTxt_D_Rq"), "?")
        print(f"{hexs}  x{n:<7} VLim1={vlim:<4} stat={stat:<22} msg={msg:<20} unit={unit}")

    print()
    nz = [h for h in payloads if bytes.fromhex(h)[3] != 255]
    if nz:
        print(f"*** {len(nz)} payload(s) with VLim1 != 255 -- THE CAMERA READ A SIGN ***")
        for h in nz:
            print("   ", h, "VLim1 =", bytes.fromhex(h)[3])
    else:
        print("*** VLim1 was 255 (NoLimit) on EVERY frame of this route ***")


if __name__ == "__main__":
    main(sys.argv[1])
