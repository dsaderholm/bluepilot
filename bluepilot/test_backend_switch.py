import pytest

from bluepilot import backend_switch as bs


class FakeParams:
  def __init__(self, **kw):
    self.d = {k: v for k, v in kw.items()}

  def get(self, k):
    return self.d.get(k)

  def get_bool(self, k):
    return bool(self.d.get(k))

  def put(self, k, v, block=False):
    self.d[k] = v

  def put_bool(self, k, v):
    self.d[k] = v

  def remove(self, k):
    self.d.pop(k, None)


@pytest.fixture(autouse=True)
def no_persist(monkeypatch):
  monkeypatch.setattr(bs, "_read_persist_comma_dongle_id", lambda: None)


def test_selfhost_registered_everywhere():
  assert bs.BACKENDS[3] == bs.BACKEND_SELFHOST == "selfhost"
  assert bs.backend_index("selfhost") == 3
  assert bs.backend_label("selfhost") == "Self-hosted"
  assert bs.pairing_host("selfhost").endswith(".invalid")
  assert bs.CACHE_PARAM["selfhost"] == "BPDongleIdSelfhost"
  assert set(bs.BACKENDS) == set(bs.BACKEND_LABELS) == set(bs.PAIRING_HOST)


def test_get_connect_backend_index_3():
  assert bs.get_connect_backend(FakeParams(BPConnectBackend=3)) == "selfhost"
  assert bs.get_connect_backend(FakeParams(BPConnectBackend=4)) == "comma"


def test_switch_comma_to_selfhost_clears_then_caches():
  p = FakeParams(BPConnectBackend=3, BPActiveBackend="comma", DongleId="aaaa")
  assert bs.reconcile_backend(p) == "selfhost"
  assert p.d["BPDongleIdComma"] == "aaaa"
  assert "DongleId" not in p.d  # forces registration with opconnect
  assert p.d["BPActiveBackend"] == "selfhost"

  p.d["DongleId"] = "bbbb"  # registration result
  bs.reconcile_backend(p)
  assert p.d["BPDongleIdSelfhost"] == "bbbb"


def test_switch_back_is_lossless():
  p = FakeParams(BPConnectBackend=3, BPActiveBackend="comma", DongleId="aaaa")
  bs.reconcile_backend(p)
  p.d["DongleId"] = "bbbb"
  bs.reconcile_backend(p)

  p.d["BPConnectBackend"] = 0
  bs.reconcile_backend(p)
  assert p.d["DongleId"] == "aaaa"
  assert p.d["BPDongleIdSelfhost"] == "bbbb"

  p.d["BPConnectBackend"] = 3
  bs.reconcile_backend(p)
  assert p.d["DongleId"] == "bbbb"
  assert p.d["BPDongleIdComma"] == "aaaa"


def test_konik_to_selfhost_keeps_konik_id():
  p = FakeParams(BPConnectBackend=3, BPActiveBackend="konik", DongleId="kkkk")
  bs.reconcile_backend(p)
  assert p.d["BPDongleIdKonik"] == "kkkk"


def test_recoverable_dongle_id_finds_selfhost_cache():
  p = FakeParams(BPConnectBackend=3, DongleId=bs.UNREGISTERED_DONGLE_ID, BPDongleIdSelfhost="bbbb")
  assert bs.find_recoverable_dongle_id(p) == "bbbb"
