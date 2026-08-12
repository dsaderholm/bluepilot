**The radar detector integration is car-agnostic, and hardware-gated.** It is deliberately not in the
list above: nothing in it touches the PSCM, the retrofit or the platform, so it behaves identically
on your car whatever you are driving. What it needs is hardware, and all of it is required rather
than recommended:

- a **Valentine One Gen2** — the only current detector with a second, independent data path, because
  the comma 3X has no Bluetooth at all and everything else talks only to a phone
- an **FTDI** USB-serial adapter — AGNOS registers `ftdi_sio` and no other usb-serial driver, and
  cannot load one later, so a CP2102 or CH340 will not enumerate
- a **USB-C to USB-A OTG adapter**, which is what asserts host role rather than merely changing the
  plug
- a tap into the detector's accessory cord

With none of that plugged in it does nothing at all — no reader thread starts and the onroad readout
does not draw. That is the expected state, not a fault, and it is almost certainly your state. Each
of those requirements fails in a way that looks like a wiring fault, so read the section on it before
buying anything.
