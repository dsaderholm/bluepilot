"""Decode Traffic_RecognitnData (0x3CD) from rlog.zst files -- LOCALLY, never on the car."""
import glob
import os
import re
import sys
from collections import Counter

import capnp
import zstandard as zstd

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
DBC = os.path.join(REPO, "opendbc_repo", "opendbc", "dbc", "ford_lincoln_base_pt.dbc")

capnp.remove_import_hook()
log_capnp = capnp.load(
    os.path.join(REPO, "cereal", "log.capnp"),
    imports=[os.path.join(REPO, "cereal"), os.path.join(REPO, "opendbc_repo", "opendbc", "car"), REPO],
)


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
    payloads = Counter()
    first_seen = {}
    v_max = 0.0
    moving_frames = 0
    files = sorted(glob.glob(pattern))
    print(f"decoding {len(files)} segments locally", flush=True)
    dctx = zstd.ZstdDecompressor()

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
                        if c.address == 973 and c.src == 2:
                            h = bytes(c.dat).hex()
                            payloads[h] += 1
                            if v_ego > 4.5:
                                moving_frames += 1
                            first_seen.setdefault(h, (os.path.basename(path), round(v_ego * 2.2369, 1)))
        except Exception as e:
            print(f"  {os.path.basename(path)}: {type(e).__name__}: {e}", flush=True)
        print(f"  {i+1}/{len(files)} {os.path.basename(path)[:34]}  "
              f"{sum(payloads.values())} frames, {len(payloads)} distinct", flush=True)

    print()
    print(f"TOTAL 0x3CD frames: {sum(payloads.values())}   distinct: {len(payloads)}")
    print(f"peak speed: {v_max * 2.2369:.1f} mph   frames while moving (>10 mph): {moving_frames}")
    print()
    for h, n in payloads.most_common():
        d = bytes.fromhex(h)
        raw, total = int.from_bytes(d, "big"), len(d) * 8
        g = {name: be(raw, total, s, l) for name, s, l in sigs}
        seg, spd = first_seen[h]
        print(f"{h}  x{n:<6} VLim1={g['TsrVLim1MsgTxt_D_Rq']:<4} "
              f"{vals['TsrStatMsgTxt_D_Rq'].get(g['TsrStatMsgTxt_D_Rq'],'?'):<24} "
              f"{vals['TsrMsgTxt_D_Rq'].get(g['TsrMsgTxt_D_Rq'],'?'):<26} "
              f"{vals['TsrVlUnitMsgTxt_D_Rq'].get(g['TsrVlUnitMsgTxt_D_Rq'],'?'):<4} "
              f"first@{spd}mph")

    print()
    hits = [h for h in payloads if bytes.fromhex(h)[3] != 255]
    if hits:
        print(f"*** {len(hits)} payload(s) with VLim1 != 255 -- THE CAMERA READ A SIGN ***")
        for h in hits:
            print(f"    {h}  VLim1 = {bytes.fromhex(h)[3]}  x{payloads[h]}  first@{first_seen[h][1]}mph")
    else:
        print("*** VLim1 was 255 (NoLimit) on EVERY frame ***")


if __name__ == "__main__":
    main(sys.argv[1])
