# Settings to check before a test drive

**Defaults I change land on your device on their own.** That is what `_migrate_bp_redefaulted` is
for, and each one is listed here so a silent change is still a readable one. What does NOT get
touched is anything you set yourself -- the migration that used to reach into the passing-assist
display toggles is gone for good.

So this file is the record, not a chore list. **A value marked "default" needs nothing from you** --
it is listed so you can confirm rather than hunt. Anything marked **SET THIS** is one I cannot apply
for you, because you have already written a value to that key by hand.

Everything below is on the device, in the settings menu. No SSH.

---

## Settings > Steering > Customize Blinker

Moved here from the BluePilot page. The blinker is the actuator a lane change is made of, so its
controls now sit with everything else that decides when to signal.

| Control | Set to | Why |
|---|---|---|
| Blink Spacing If Unmeasurable | **760 ms** (applied) | Your own flasher, measured with Measure My Blinker. This one IS applied for you -- it is in `_BP_REDEFAULTED`, generation 2, so it lands whatever you had stored. |

The buttons need the car **stopped, cruise off, and your own stalk idle**. They self-clear.

- **Measure My Blinker** -- watches your stalk for 12 s and reports the rate. Nothing is sent.
- **Blink Left / Blink Right** -- what a commanded signal looks like. Should be an ordinary blink.
- **One Frame Left / Right** -- exactly one flash. The reference: the lamp mirrors each message once
  and latches nothing, which is the whole reason any of this works.

## Settings > Steering > Customize Passing Assist

| Control | Set to | Why |
|---|---|---|
| Passing Assist (Log Only) | **On** (default) | Nothing else here matters if this is off. |
| Chime When It Decides | **On** (default) | The tone now waits 0.5 s for the decision to hold and will not repeat inside 8 s. |
| Show The Onroad Panel | **On** (default) | The panel IS the output. Off means the drive measures nothing you can read. |
| Show Next Lane Speeds | **On** (default) | Draws what the radar thinks is beside you -- the readout that shows whether a refusal was right. |
| Show Next Lane Speeds → oncoming | **On** (default) | Same, for traffic coming the other way. |
| Signal Before Moving | **1 s** (default) | Your habit, and Utah's 2-second rule is about a *continuous* signal, which the crossing satisfies. |
| Only Above | **30 mph** (default) | Below this the geometry stops meaning what it says -- turn pockets and driveways look like lanes. |
| Confirm For | **1 s** (default) | How long a car has to look slow before it counts. |

Leave the rest at their defaults unless a drive gives you a reason to move one.

## Settings > BluePilot

Nothing of mine lives here now. If your BluePilot section looks wrong after the wipe, it is the
fork's own display settings that were cleared -- set them back to taste; nothing in there affects
whether passing assist works.

---

## What changed since the last drive

- **The shoulder.** Two width thresholds failed at this in a row, and width was the wrong question:
  the gate measured your lane line out to the road edge, which is the next lane *plus* its shoulder
  from an interior lane and the shoulder *alone* from the rightmost one -- one number for two
  different things. It now measures the candidate lane by itself and how much road is left past its
  far line. Where the model has no lane out there it puts the far "lane line" on the road edge --
  the red line on the barrier wall -- and that now reads as `R shoulder 0.3ft` on the panel instead
  of as an invitation.
- **The blinker buttons that worked "sometimes".** A permanent latch with a millisecond-wide
  trigger. When a pulse ends the request param is cleared and read straight back, to prove the
  store took the write. Press a button in that gap and the read-back returns *your* press instead
  of the zero -- so the guard concludes the store is broken and refuses every request from then on.
  Nothing else ever writes that key, so it never read zero again and the buttons stayed dead until
  the ignition cycled. Pressing again could not help; pressing again was the cause. It now retries
  the clear instead of latching, so at worst you lose the one press that landed in the seam.
- **The loop.** "Would be changing right, would be done", over and over. A completed dry run left
  every reason to go still true, because nothing actuates and the car never moved. A finished run
  now stands down 30 s, and the panel says `WOULD BE DONE / holding 24s before looking again`
  in purple -- distinct from the red `BACKED OUT`, which is a different thing.
