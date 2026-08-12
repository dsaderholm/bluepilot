## Settings

Settings behave **exactly as they do on stock BluePilot, sunnypilot and openpilot**: `manager.py`
writes each param's default on the first boot that knows the key, and the stored value never changes
again.

So a changed default is a **recommendation, not a change**. Every tunable control prints its shipped
default in its own description — read live, so it cannot go stale — and applying it is a deliberate
act. Nothing here decides what your settings should be.

