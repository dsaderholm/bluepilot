- **`tools/bp_radar_probe.py`** — first contact with the detector. Run it the day the hardware
  arrives, before trusting anything: it says whether bytes are arriving, whether they frame as ESP,
  whether the decode looks sane, and whether the mute bit ever moves. When it sees nothing it names
  the pinout before the software, because the accessory jack is pin-reversed from the main one
- **`tools/bp_radar_fit.py`** — replaces the guessed strength threshold with a fitted one. Reports,
  per speed band, whether a given threshold would have given enough warning to actually reach the
  limit at the rate this car sheds speed — measured from the log rather than assumed, and excluding
  the encounters where the car acted, since those would fit the threshold to its own output
