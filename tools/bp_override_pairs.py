"""FusionPilot: what separates a stop-override that SURVIVES from one that kills Ford ACC?

CLAUDE.md has the outcome table -- 9 episodes, 4/4 with a lead survived, 3/3 cancels were leadless
-- and explicitly rules out duration and contradiction magnitude. But two LEADLESS episodes also
survived, one of them for 11.9 s, and they sit on the same route as a 9.0 s leadless one that
died. That matched pair is the experiment: same car, same drive, same condition, opposite outcome.

This dumps every opStop episode with a wide feature set so the pair can be diffed directly,
rather than adding a tenth theory.

    python tools/bp_override_pairs.py 000003b5 000003b7 000003b8 000003ba
"""
import os
import sys

REALDATA = os.environ.get("REALDATA", "/data/media/0/realdata")
sys.path.insert(0, "/data/openpilot")
from openpilot.tools.lib.logreader import LogReader  # noqa: E402

ACCDATA, ACCDATA_3, ENGBRAKE = 390, 394, 357
MSG_TXT = {0: "No_Text", 1: "ACC_Unavail", 2: "ACC_Cancelled", 3: "BrkCap", 4: "ACC_Overridden",
           5: "ACC_Sel", 6: "IACC_Unavail", 7: "ShiftDn", 8: "TJA_Unavail", 9: "OnlyFollowLowSpd",
           10: "PressBrkHold", 11: "IACC_Sel", 12: "ACC_TJA", 13: "IACC_TJA", 14: "NCC", 15: "n/a"}
STOP_MDE = {0: "NoStop", 1: "StopRq", 2: "Creep", 3: "Hold"}


def be(data, start, nbits):
  v = int.from_bytes(data, "big")
  idx = (start // 8) * 8 + (7 - (start % 8))
  return (v >> (len(data) * 8 - idx - nbits)) & ((1 << nbits) - 1)


def acc_fields(d):
  return {
    "brk": be(d, 4, 13) * 0.0039 - 20,
    "gas": be(d, 49, 10) * 0.01 - 5,
    "vtrg": be(d, 32, 9) * 0.5,
    "cancel": be(d, 39, 1),
    "stopstat": be(d, 34, 1),
    "cmbb": be(d, 50, 1),
    "prchg": be(d, 54, 1),
    "decel": be(d, 55, 1),
    "resum": be(d, 33, 1),
  }


def seg_index(n):
  try:
    return int(n.rsplit("--", 1)[1])
  except Exception:
    return -1


def run(route):
  segs = sorted([d for d in os.listdir(REALDATA) if d.startswith(route)], key=seg_index)
  if not segs:
    print(f"{route}: no segments"); return

  t0 = None
  cam = ours = None            # latest decoded ACCDATA from camera / our TX
  msgtxt = 0
  stopmde = 0
  auth = "?"
  lead_d = None
  v_ego = 0.0
  standstill = False
  brake_p = gas_p = False

  eps = []                     # override episodes
  cur = None
  cancel_runs = []             # (t_start, t_end) of camera cancel while it stayed high
  crun = None

  for s in segs:
    p = os.path.join(REALDATA, s, "rlog")
    if not os.path.exists(p):
      p += ".zst"
    if not os.path.exists(p):
      continue
    try:
      lr = LogReader(p)
    except Exception:
      continue
    for m in lr:
      t = m.logMonoTime / 1e9
      t0 = t if t0 is None else min(t0, t)
      w = m.which()

      if w == "can":
        for c in m.can:
          if c.address == ACCDATA and len(c.dat) == 8:
            f = acc_fields(c.dat)
            if c.src in (2,):
              cam = f
              if f["cancel"] and crun is None:
                crun = t
              elif not f["cancel"] and crun is not None:
                cancel_runs.append((crun, t)); crun = None
            elif c.src in (0, 128):
              ours = f
          elif c.address == ACCDATA_3 and c.src == 2 and len(c.dat) == 8:
            # AccMsgTxt_D2_Rq is 31|4 in the DBC. This read 26|4 on the first run and every
            # episode came back "ACC_Cancelled" -- a column that looked authoritative and
            # was decoding four unrelated bits. bp_cancel_reason.py had it right all along.
            msgtxt = be(c.dat, 31, 4)
          elif c.address == ENGBRAKE and c.src == 0 and len(c.dat) == 8:
            stopmde = be(c.dat, 39, 2)

      elif w == "carState":
        v_ego = m.carState.vEgo
        standstill = m.carState.standstill
        brake_p = m.carState.brakePressed
        gas_p = m.carState.gasPressed
      elif w == "radarState":
        lo = m.radarState.leadOne
        lead_d = lo.dRel if lo.status else None
      elif w == "controllerStateBP":
        auth = str(m.controllerStateBP.accAuthority).split(".")[-1]

      # --- track override episodes ---
      is_ov = auth == "opStop"
      if is_ov and cur is None:
        cur = {"t": t, "n": 0, "vstart": v_ego, "lead": 0, "nolead": 0, "stopstat1": 0,
               "cmbb0": 0, "stopmde": {}, "msgtxt": {}, "brkmin": 9, "cambrkmin": 9,
               "gapmax": 0.0, "standstill": 0, "brake_p": 0, "gas_p": 0, "vmin": 99,
               "prchg": 0, "decel": 0, "resum": 0, "camcancel": 0}
      if is_ov and cur is not None:
        # COUNT carState ONLY. Incrementing on every message made a 9.00 s episode report 17,358
        # "frames" -- 1,929 Hz -- and every per-episode tally was really "messages while in this
        # state". Ratios survived that; the absolute numbers did not, and they printed as frames.
        if w != "carState":
          continue
        cur["n"] += 1
        cur["vmin"] = min(cur["vmin"], v_ego)
        if lead_d is not None and lead_d < 60: cur["lead"] += 1
        else: cur["nolead"] += 1
        cur["standstill"] += bool(standstill)
        cur["brake_p"] += bool(brake_p)
        cur["gas_p"] += bool(gas_p)
        cur["stopmde"][STOP_MDE.get(stopmde, stopmde)] = cur["stopmde"].get(STOP_MDE.get(stopmde, stopmde), 0) + 1
        cur["msgtxt"][MSG_TXT.get(msgtxt, msgtxt)] = cur["msgtxt"].get(MSG_TXT.get(msgtxt, msgtxt), 0) + 1
        if ours:
          cur["stopstat1"] += ours["stopstat"]
          cur["cmbb0"] += (not ours["cmbb"])
          cur["prchg"] += ours["prchg"]; cur["decel"] += ours["decel"]; cur["resum"] += ours["resum"]
          cur["brkmin"] = min(cur["brkmin"], ours["brk"])
        if cam:
          cur["cambrkmin"] = min(cur["cambrkmin"], cam["brk"])
          cur["camcancel"] += cam["cancel"]
          if ours:
            cur["gapmax"] = max(cur["gapmax"], abs(cam["brk"] - ours["brk"]))
      if not is_ov and cur is not None:
        cur["end"] = t; cur["endauth"] = auth
        eps.append(cur); cur = None

  if crun is not None:
    cancel_runs.append((crun, 1e18))
  if cur is not None:
    cur["end"] = 1e18; cur["endauth"] = auth; eps.append(cur)

  print(f"\n=== {route} ===  {len(segs)} segs, {len(eps)} override episodes, "
        f"{len(cancel_runs)} camera cancel runs")
  for e in eps:
    dur = e["end"] - e["t"]
    # did a cancel run open within 5 s after the episode started, and did it ever clear?
    fatal, delay = "", None
    for (cs, ce) in cancel_runs:
      if e["t"] - 1.0 <= cs <= e["end"] + 5.0:
        delay = cs - e["t"]
        fatal = "NEVER-CLEARED" if ce > 1e17 else f"cleared after {ce - cs:.1f}s"
        break
    lead_pct = 100.0 * e["lead"] / max(1, e["lead"] + e["nolead"])
    print(f"\n  t+{e['t'] - t0:8.1f}  dur {dur:5.2f}s  frames {e['n']:4d}  "
          f"ended->{e['endauth']}")
    print(f"      outcome        {'CANCEL @+%.1fs  %s' % (delay, fatal) if delay is not None else 'clean'}")
    print(f"      speed          {e['vstart'] * 2.237:5.1f} -> {e['vmin'] * 2.237:5.1f} mph"
          f"   standstill {e['standstill']}")
    print(f"      lead           {lead_pct:5.1f}% of frames")
    print(f"      our brake min  {e['brkmin']:6.2f}   camera brake min {e['cambrkmin']:6.2f}"
          f"   peak gap {e['gapmax']:.2f}")
    print(f"      our bits       StopStat={e['stopstat1']}/{e['n']}  Cmbb_OFF={e['cmbb0']}"
          f"  prchg={e['prchg']} decel={e['decel']} resum={e['resum']}")
    print(f"      PCM stop mode  {dict(sorted(e['stopmde'].items(), key=lambda kv: -kv[1]))}")
    print(f"      camera says    {dict(sorted(e['msgtxt'].items(), key=lambda kv: -kv[1]))}")
    print(f"      driver         brake {e['brake_p']}  gas {e['gas_p']}")


for r in sys.argv[1:]:
  try:
    run(r)
  except Exception as ex:
    print(f"{r}: FAILED {type(ex).__name__}: {ex}")
