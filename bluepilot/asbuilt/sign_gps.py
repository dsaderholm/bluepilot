import os, sys, glob
import capnp, zstandard as zstd

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal"),
                                os.path.join(REPO, "opendbc_repo", "opendbc", "car"), REPO])

def be(raw, start, length):
    bi, bib = start // 8, start % 8
    msb = bi * 8 + (7 - bib)
    return (raw >> (64 - msb - length)) & ((1 << length) - 1)

def decode_462(dat):
    raw = int.from_bytes(dat, "big")
    lat_deg = be(raw, 7, 8) - 89
    lat_min = be(raw, 15, 6)
    lat_dec = be(raw, 23, 14) * 0.0001
    lon_deg = be(raw, 39, 9) - 179
    lon_min = be(raw, 46, 6)
    lon_dec = be(raw, 55, 14) * 0.0001
    south = be(raw, 25, 2)
    east = be(raw, 9, 2)
    # Minutes are a MAGNITUDE, so they move away from zero -- for a western longitude that means
    # subtracting. Adding them to a negative degree walks the position EAST, which put a Salt Lake
    # City fix in the Ashley National Forest, 1.7 degrees off.
    lat = lat_deg + (lat_min + lat_dec) / 60.0 * (1 if lat_deg >= 0 else -1)
    lon = lon_deg + (lon_min + lon_dec) / 60.0 * (1 if lon_deg >= 0 else -1)
    return lat, lon, south, east

dctx = zstd.ZstdDecompressor()
for path in sorted(glob.glob(sys.argv[1])):
    with open(path, "rb") as f:
        data = dctx.stream_reader(f).read()
    last = None
    v_ego = 0.0
    hits = []
    for evt in log_capnp.Event.read_multiple_bytes(data):
        w = evt.which()
        if w == "carState":
            v_ego = evt.carState.vEgo
        elif w == "can":
            for c in evt.can:
                if c.address == 0x462 and c.src == 0:
                    last = decode_462(bytes(c.dat))
                elif c.address == 0x3CD and c.src == 2 and bytes(c.dat)[3] != 255:
                    hits.append((bytes(c.dat)[3], round(v_ego * 2.2369, 1), last))
    if hits:
        print(f"=== {os.path.basename(path)} ===")
        seen = set()
        for limit, spd, g in hits:
            if g is None:
                print(f"  limit {limit}  {spd:>5} mph   (no 0x462 yet)"); continue
            lat, lon, s, e = g
            key = (round(lat, 5), round(lon, 5))
            print(f"  limit {limit}  {spd:>5} mph   {lat:.6f}, {lon:.6f}   (S={s} E={e})")
            if key not in seen:
                seen.add(key)
                print(f"      https://www.google.com/maps?q={lat:.6f},{lon:.6f}")
