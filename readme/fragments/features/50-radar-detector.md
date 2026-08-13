### Radar detector

**None of this has met hardware yet**, and it reads a Valentine One Gen2 over its wired accessory
bus to aim just under the posted limit while a strong Ka alert is out there. The protocol decoder is
written against Valentine's published specification and checked against their own worked example
packets, which is the best evidence obtainable without a detector on the bench and is not the same
thing as having one.

- **It needs one specific detector, and that is not a preference.** The V1 Gen2 is the only current
  detector with a second, independent data path: a wired ESP bus on the ACC jack of the power
  adapter. Everything else — the Uniden R-series, the Escort line — reaches a phone over Bluetooth
  and nothing else, and the comma 3X has no Bluetooth at all: no controller, no BlueZ, no firmware,
  and `rfkill` lists only wlan. So other detectors are not worse options here, they are unreadable.
- **The USB adapter must be FTDI.** AGNOS registers `ftdi_sio` and nothing else — CP210x, CH341,
  PL2303 and even the generic USB-serial driver are all unset in `/proc/config.gz`, and there is no
  `/lib/modules` to load one later. A CP2102 or CH340 adapter will not enumerate, and the symptom
  looks exactly like a wiring fault. The port is USB-C, so it also needs a USB-C to USB-A OTG
  adapter — which is what asserts host role, not merely a change of plug.
- **With nothing plugged in it does nothing at all**, which is the state most readers will be in. No
  serial adapter means no reader thread is even started, and the onroad readout does not draw. There
  is no failure mode to avoid here; it is simply inert.
- **It is a speed limit offset, not a cruise-button feature.** While an alert holds it replaces
  `SpeedLimitOffsetValue` with a negative one, so it composes with whatever offset you normally run
  — someone at +5 over gets a 6 mph change out of a 1 mph margin — and it keeps working unchanged if
  openpilot ever drives longitudinal directly. Ka only, and only when the strength holds for a second
  and a half; K band is every automatic door and half the blind-spot monitors on the road.
- **It learns places.** Somewhere that alerts on nearly every pass is a supermarket door, and the
  detector is told to stay quiet there. Somewhere that alerts occasionally is where police actually
  work, and you get a spoken warning before you reach it. The discriminator is the hit ratio, which
  is why the quiet passes are counted too — without them every location looks like a fixed source.
  Muting needs ten observations where warning needs three: warning wrongly is an annoyance, muting
  wrongly is a ticket, and it is silent.
- **Laser marks itself.** By the time a lidar alert fires you have already been measured, so there is
  nothing to react to and nothing to ask the driver to decide. It records the spot on its own, and
  never treats laser as a false alarm however rarely it repeats — a lidar gun is aimed, so most
  passes through a laser position produce no detection at all.
- **The strength threshold ships as a guess and is meant to be replaced.** It ships LOW rather than
  high, because a default only ever reaches a device that has never stored the key: too low fires
  more than you like and you notice, too high fires never and looks broken.
  `tools/bp_radar_fit.py` reads the encounter log and reports, per speed band, whether a threshold
  would have given enough warning to actually reach the limit at the rate this car sheds speed. It
  measures that rate rather than assuming it, and excludes encounters where the car acted — those
  would fit the threshold to data the threshold itself produced.
- **What bounds all of it: the set speed falls at about 3.3 mph/s**, which is the car's own repeat
  rate for a held cruise button rather than anything tunable. Twenty mph over the limit therefore
  takes six seconds to even ask for, before the car begins responding. Nothing here assumes faster;
  the fitter measures the whole chain empirically.

`tools/bp_radar_probe.py` is the first-contact diagnostic — run it before trusting any of this.
