"""Response parsing for bp_osm_tag_census.py.

These numbers get quoted to an upstream maintainer to argue for one tag over another, which is a
place a wrong number does real damage: it was a single-corridor coverage figure quoted the same way
that had to be retracted on mapd issue 129. taginfo returns several rows per key and only one of
them is the planet-wide total, so picking the wrong row silently returns a plausible number that is
not the one being claimed.
"""
import importlib.util
import json
import pathlib
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
  "bp_osm_tag_census",
  pathlib.Path(__file__).resolve().parents[3] / "tools" / "bp_osm_tag_census.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


class _Resp:
  def __init__(self, payload):
    self._payload = json.dumps(payload).encode()

  def read(self):
    return self._payload

  def __enter__(self):
    return self

  def __exit__(self, *a):
    return False


def _urlopen(payload):
  return mock.patch.object(mod.urllib.request, "urlopen", return_value=_Resp(payload))


# taginfo's real shape: one row per object type plus an 'all' row that is their sum.
REAL_SHAPE = {"data": [
  {"type": "nodes", "count": 12},
  {"type": "ways", "count": 151_000},
  {"type": "relations", "count": 83},
  {"type": "all", "count": 151_095},
]}


class TestStats:
  def test_reads_the_all_row_not_the_first_one(self):
    """The failure that matters: 'ways' is first, plausible, and roughly right, so taking row
    zero produces a number nobody would question and which is not the planet total."""
    with _urlopen(REAL_SHAPE):
      assert mod.stats("overtaking") == 151_095

  def test_row_order_does_not_matter(self):
    shuffled = {"data": list(reversed(REAL_SHAPE["data"]))}
    with _urlopen(shuffled):
      assert mod.stats("overtaking") == 151_095

  def test_a_key_nobody_uses_is_zero_not_none(self):
    """Zero is an answer and None means taginfo would not say. The census prints them
    differently, and conflating them turns 'this tag is unused' into 'the network failed'."""
    with _urlopen({"data": [{"type": "all", "count": 0}]}):
      assert mod.stats("overtaking:both_ways") == 0

  def test_no_all_row_is_zero(self):
    with _urlopen({"data": [{"type": "ways", "count": 999}]}):
      assert mod.stats("whatever") == 0

  def test_a_dead_network_is_none_after_retries(self):
    with mock.patch.object(mod.urllib.request, "urlopen", side_effect=OSError("down")), \
         mock.patch.object(mod.time, "sleep"):
      assert mod.stats("change", retries=2) is None

  def test_garbage_json_is_none_rather_than_an_exception(self):
    """The census walks a fixed key list; one malformed response must not take the run down."""
    bad = mock.MagicMock()
    bad.__enter__ = mock.Mock(return_value=mock.Mock(read=mock.Mock(return_value=b"<html>")))
    bad.__exit__ = mock.Mock(return_value=False)
    with mock.patch.object(mod.urllib.request, "urlopen", return_value=bad), \
         mock.patch.object(mod.time, "sleep"):
      assert mod.stats("change", retries=2) is None

  def test_it_recovers_when_a_retry_succeeds(self):
    with mock.patch.object(mod.urllib.request, "urlopen",
                           side_effect=[OSError("flaky"), _Resp(REAL_SHAPE)]), \
         mock.patch.object(mod.time, "sleep"):
      assert mod.stats("overtaking") == 151_095
