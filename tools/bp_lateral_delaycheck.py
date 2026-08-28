"""Is liveDelay.lateralDelay LEARNED, or is it a seed being republished?

lagd publishes `self.initial_lag` whenever status != estimated, and initial_lag is
`CP.steerActuatorDelay + 0.2` (0.42 here) or a STORED value reloaded from params. A stored seed
republished every frame looks exactly like a perfectly converged estimate: dead flat, no variance.

The measured 0.381 has almost no spread across 309k samples, which is the signature of either a
seed or a fully settled estimate. `status` and `validBlocks` tell them apart, and the whole lateral
conclusion rests on which it is.
"""
import collections
import glob
import os
import sys

import capnp
import zstandard

REPO = r"C:\Users\D.J. Saderholm\Documents\GitHub\Sandbox\bluepilot-icbm"
capnp.remove_import_hook()
log_capnp = capnp.load(os.path.join(REPO, "cereal", "log.capnp"),
                       imports=[os.path.join(REPO, "cereal")])

STATUS = {0: "unestimated", 1: "estimated", 2: "invalid"}


def main():
    d = sys.argv[1]
    routes = sys.argv[2:]
    files = sorted(glob.glob(os.path.join(d, "*.rlog.zst")))
    if routes:
        files = [f for f in files if os.path.basename(f).split("--")[0] in routes]
    files = files[:40]

    st = collections.Counter()
    blocks = collections.Counter()
    delay = collections.Counter()
    est = []
    std = []
    for p in files:
        try:
            with open(p, "rb") as f:
                raw = zstandard.ZstdDecompressor().stream_reader(f).read()
            evs = log_capnp.Event.read_multiple_bytes(raw, traversal_limit_in_words=2 ** 32)
        except Exception:
            continue
        while True:
            try:
                m = next(evs)
            except (StopIteration, Exception):
                break
            if m.which() != "liveDelay":
                continue
            ld = m.liveDelay
            try:
                st[str(ld.status)] += 1
                blocks[int(ld.validBlocks)] += 1
                delay[round(float(ld.lateralDelay), 4)] += 1
                est.append(float(ld.lateralDelayEstimate))
                std.append(float(ld.lateralDelayEstimateStd))
            except Exception:
                continue

    print("=== IS THE 0.38 LEARNED OR A SEED? ===")
    print()
    print("  status counts        : %s" % dict(st))
    print("  validBlocks (top 5)  : %s" % dict(blocks.most_common(5)))
    print("  lateralDelay values  : %s" % dict(delay.most_common(5)))
    if est:
        est_s = sorted(est)
        std_s = sorted(std)
        n = len(est_s)
        print("  lateralDelayEstimate : min %.4f  p50 %.4f  max %.4f" % (
            est_s[0], est_s[n // 2], est_s[-1]))
        print("  estimateStd          : min %.4f  p50 %.4f  max %.4f" % (
            std_s[0], std_s[n // 2], std_s[-1]))
    print()
    print("  initial_lag would be CP.steerActuatorDelay + 0.2 = 0.22 + 0.2 = 0.42")
    print("  -- a published value of exactly 0.42 means SEED, not learned.")
    print("  -- status 'estimated' with validBlocks > 0 means genuinely learned.")


if __name__ == "__main__":
    main()
