## Installing and updating

This is installed the same way as any openpilot fork, by URL at device setup. Updating:

```bash
python tools/bp_merge_upstream.py     # pull in a newer BluePilot release
```

Then on the device:

```bash
cd /data/openpilot && git pull && sudo reboot
```

