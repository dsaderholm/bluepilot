"""
BluePilot: every Side_Detect_L/R_Stat signal, decoded onto carStateBP.

Lives in its own file rather than inline in `carstate_ext.py` for one reason: **upstream rebase
cost**. This is a signal table and a loop that belong entirely to this fork's blind-spot work, and
every line of it sitting in a sunnypilot-owned file is a merge conflict paid on every future
sunnypilot update, forever. A new file conflicts with nothing. `carstate_ext.py` keeps one call.

WHY ALL OF THEM, NOT JUST THE ONE BOOL openpilot USES
`carState.leftBlindspot` is `SodDetct*_D_Stat != 0` and nothing else. That is blind-spot
OCCUPANCY, which cannot answer "is something closing" -- see BP-REAR-RADAR-PLAN.md. The rest of the
frame is logged to confirm that from real data rather than by assumption:

  Sod*_D_Stat       the system's own enable state
  SodAlrt*_D_Stat   the mirror lamp, whose Flash state follows the DRIVER's turn signal rather
                    than the other vehicle -- so it is about us, not them
  SodSns*_D_Stat    sensor health
  Cta*              cross-traffic alert, a different feature on the same module
  Btt*              blind-spot trailer tow

Logged to confirm what they are, not in hope of finding closing rate in them. That expectation was
settled by research and none of these carries range or rate.

**None of this populates until the canbox routes 0x3A6/0x3A7 onto a bus openpilot reads.** Until
then `dataAvailable` stays False on both sides, which the passing-assist panel reports as "no blind
spot data" rather than silently reading an absent sensor as a clear lane.
"""

# capnp field -> (left signal, right signal). Ford does not name these symmetrically, hence the
# explicit pairing rather than a prefix substitution.
BLIS_SIGNALS = (
  ("sodDetect", "SodDetctLeft_D_Stat", "SodDetctRight_D_Stat"),
  ("sodStat", "SodLeft_D_Stat", "SodRight_D_Stat"),
  ("sodAlert", "SodAlrtLeft_D_Stat", "SodAlrtRight_D_Stat"),
  ("sodSensor", "SodSnsLeft_D_Stat", "SodSnsRight_D_Stat"),
  ("sodWarnPeriodMs", "SodWarnLeft_Prd_Rq", "SodWarnRight_Prd_Rq"),
  ("ctaStat", "CtaLeft_D_Stat", "CtaRight_D_Stat"),
  ("ctaAlert", "CtaAlrtLeft_D_Stat", "CtaAlrtRight_D_Stat"),
  ("ctaAlert2", "CtaAlrtLeft2_D_Stat", "CtaAlrtRight2_D_Stat"),
  ("ctaSensor", "CtaSnsLeft_D_Stat", "CtaSnsRight_D_Stat"),
  ("bttStat", "BttLeft_D_Stat", "BttRight_D_Stat"),
  ("bttDriverReq", "BttLeft_D_RqDrv", "BttRight_D_RqDrv"),
  ("illumPercent", "Side_Detect_L_Illum", "Side_Detect_R_Illum"),
)

BLIS_BOOL_SIGNALS = (
  ("ctaBrakeDecelReq", "CtaLeftBrkDecel_B_Rq", "CtaRightBrkDecel_B_Rq"),
  ("ctaBrakeEnableReq", "CtaLeftBrkEnbl_B_Rq", "CtaRightBrkEnbl_B_Rq"),
  ("ctaBrakeMsgReq", "CtaBrkLeftMsgTxt_B_Rq", "CtaBrkRightMsgTxt_B_Rq"),
)


def update_blis(cp_bsm, blis_left, blis_right) -> None:
  """Fill both sides from the parser carrying Side_Detect_L/R_Stat.

  Leaves `dataAvailable` False for a side whose message is absent, which is the whole point: an
  unrouted BLIS must be distinguishable from an empty lane, and it is not if a missing message
  silently reads as zeros.
  """
  for target, msg, idx in ((blis_left, "Side_Detect_L_Stat", 0), (blis_right, "Side_Detect_R_Stat", 1)):
    try:
      vl = cp_bsm.vl[msg]
      target.dataAvailable = True
      for field, *sigs in BLIS_SIGNALS:
        setattr(target, field, int(vl[sigs[idx]]))
      for field, *sigs in BLIS_BOOL_SIGNALS:
        setattr(target, field, bool(vl[sigs[idx]]))
    except (KeyError, AttributeError):
      pass
