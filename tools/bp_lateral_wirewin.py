import glob, os, sys
import capnp, zstandard
REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
capnp.remove_import_hook()
L = capnp.load(os.path.join(REPO,"cereal","log.capnp"), imports=[os.path.join(REPO,"cereal")])
ADDR, SCALE, OFFSET = 979, 0.0005, -0.5
def be_bits(data, start, length):
    val=0; bit=start
    for _ in range(length):
        byte=bit//8
        if byte>=len(data): return None
        val=(val<<1)|((data[byte]>>(bit%8))&1)
        bit = bit+15 if bit%8==0 else bit-1
    return val
d, route, t_lo, t_hi = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
def si(p):
    try: return int(os.path.basename(p).split("--")[2].split(".")[0])
    except: return -1
files = sorted([f for f in glob.glob(os.path.join(d,"*.rlog.zst"))
                if os.path.basename(f).split("--")[0]==route], key=si)
# only segments plausibly covering the window (segments are ~60 s)
files = [f for f in files if (t_lo/60 - 3) <= si(f) <= (t_hi/60 + 2)]
rows=[]; t0=None; des=act=0.0; ang=0.0
for p in files:
    try:
        raw = zstandard.ZstdDecompressor().stream_reader(open(p,"rb")).read()
        evs = L.Event.read_multiple_bytes(raw, traversal_limit_in_words=2**32)
    except Exception: continue
    while True:
        try: m=next(evs)
        except StopIteration: break
        except Exception: break
        mono=m.logMonoTime/1e9
        if t0 is None or mono<t0: t0=mono
        ts=mono-t0
        w=m.which()
        try:
            if w=="controlsState":
                des=float(m.controlsState.desiredCurvature); act=float(m.controlsState.curvature)
            elif w=="carState":
                ang=float(m.carState.steeringAngleDeg)
            elif w=="sendcan" and t_lo<=ts<=t_hi:
                for c in m.sendcan:
                    if c.address!=ADDR: continue
                    r=be_bits(bytes(c.dat),31,11)
                    if r is None: continue
                    rows.append((ts, r*SCALE+OFFSET, des*1000, act*1000, ang))
        except Exception: continue
if not rows:
    print("no 979 frames in window (t0 anchoring may differ) -- got", len(rows)); sys.exit()
print("  %8s %12s %10s %10s %9s"%("t+","path_angle","des 1/km","act 1/km","steer deg"))
step=max(1,len(rows)//28)
for r in rows[::step]:
    print("  %8.2f %11.4f %10.2f %10.2f %9.1f"%r)
