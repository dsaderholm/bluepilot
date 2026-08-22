"""How long was he near the sign before the camera recognised it?"""
import glob, math, os, sys
import capnp, zstandard as zstd

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal"),
                                os.path.join(REPO, "opendbc_repo", "opendbc", "car"), REPO])

SIGN = (40.725463, -111.829903)   # first frame that carried the limit

def be(raw, start, length):
    bi, bib = start // 8, start % 8
    msb = bi * 8 + (7 - bib)
    return (raw >> (64 - msb - length)) & ((1 << length) - 1)

def decode_462(dat):
    raw = int.from_bytes(dat, "big")
    lat = (be(raw, 7, 8) - 89)
    lat += (be(raw, 15, 6) + be(raw, 23, 14) * 0.0001) / 60.0 * (1 if lat >= 0 else -1)
    lon = (be(raw, 39, 9) - 179)
    lon += (be(raw, 46, 6) + be(raw, 55, 14) * 0.0001) / 60.0 * (1 if lon >= 0 else -1)
    return lat, lon

def dist_m(a, b):
    dlat = (a[0] - b[0]) * 111320.0
    dlon = (a[1] - b[1]) * 111320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlon)

dctx = zstd.ZstdDecompressor()
rows = []
for path in sorted(glob.glob(sys.argv[1])):
    with open(path, "rb") as f:
        data = dctx.stream_reader(f).read()
    pos, v, t0 = None, 0.0, None
    for evt in log_capnp.Event.read_multiple_bytes(data):
        w = evt.which()
        if t0 is None:
            t0 = evt.logMonoTime
        if w == "carState":
            v = evt.carState.vEgo * 2.2369
        elif w == "can":
            for c in evt.can:
                if c.address == 0x462 and c.src == 0:
                    pos = decode_462(bytes(c.dat))
                elif c.address == 0x3CD and c.src == 2 and pos:
                    rows.append(((evt.logMonoTime - t0) / 1e9, dist_m(pos, SIGN), v, bytes(c.dat)[3]))

near = [r for r in rows if r[1] < 120]
print(f"{'t (s)':>8}{'dist to sign (m)':>20}{'mph':>8}{'VLim1':>8}")
for t, d, v, lim in near:
    mark = "   <<<<< READ" if lim != 255 else ""
    print(f"{t:8.1f}{d:20.1f}{v:8.1f}{lim:8}{mark}")

reads = [r for r in near if r[3] != 255]
if reads and near:
    first = reads[0]
    approach = [r for r in near if r[0] < first[0]]
    print()
    print(f"0x3CD frames within 120 m of the sign: {len(near)}")
    print(f"  ...before the first read: {len(approach)}  (about {len(approach)} seconds at 1 Hz)")
    if approach:
        print(f"  closest approach BEFORE recognising it: {min(r[1] for r in approach):.0f} m")
    print(f"  distance at first read: {first[1]:.0f} m, at {first[2]:.1f} mph")
