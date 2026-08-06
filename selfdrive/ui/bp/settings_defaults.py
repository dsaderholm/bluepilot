"""BluePilot: put the shipped default in the settings description.

*"Why don't we just put in the description of the option what the recommended value is so I can
easily know what to change it to?"* (2026-08-05)

This exists because of the mechanism in params_migration.py: `manager.py` writes every declared key
to disk on the first boot that knows about it, and these keys are PERSISTENT | BACKUP. So once a
car has been driven, the default in params_keys.h no longer reaches it. Changing a default is now
only a recommendation, and the settings screen is the only place that recommendation can actually
be acted on.

The value is READ FROM params_keys.h at display time rather than written out by hand. Hand-copying
it would create a second source of truth for the default and guarantee the two drift -- which is
exactly the failure this whole area keeps producing: an "off by default" comment on a key that
ships on, an earliness documented at 100/140 that ships 170, a migration excluding two alerts as
"already on for him". A number that is fetched cannot go stale.
"""

from collections.abc import Callable

from openpilot.common.params import Params

_params = Params()


def recommended(description: str, param: str, label: Callable[[int], str] | None = None) -> str:
  """Append the shipped default for `param` to `description`.

  Args:
    description: the human text, already translated.
    param: the key whose params_keys.h default should be quoted.
    label: the same label_callback the control renders with, when it has one. Without it a value
      stored in tenths reads as "70" in the description and "7.0 s" on the control beside it, which
      is worse than saying nothing.

  Any failure -- unknown key, no declared default, a label callback that does not like the value --
  returns the description untouched. A settings screen that will not draw is far worse than one
  missing a hint, and this runs for every item on the page.
  """
  try:
    default = _params.get_default_value(param)
    if default is None:
      return description
    if isinstance(default, bool):
      shown = "On" if default else "Off"
    elif label is not None:
      shown = label(int(default))
    else:
      shown = str(default)
  except Exception:  # noqa: BLE001 - see docstring; never let a hint break the page
    return description

  return f"{description} Recommended: {shown}."
