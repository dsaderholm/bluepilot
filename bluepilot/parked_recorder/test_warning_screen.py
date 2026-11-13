from bluepilot.parked_recorder.warning_screen_bp import MAX_ON_S, WarningScreenState


class Clock:
  t = 100.0

  def __call__(self):
    return self.t


def make(value=True):
  clock, box = Clock(), {"v": value}
  return WarningScreenState(lambda: box["v"], clock), clock, box


def test_on_only_when_param_and_parked():
  s, c, b = make(True)
  assert s.update(parked=True)
  assert not s.update(parked=False)


def test_off_when_param_off():
  s, c, b = make(False)
  assert not s.update(True)


def test_elapsed_counts_from_start_and_resets():
  s, c, b = make(True)
  s.update(True)
  c.t += 3.0
  s.update(True)
  assert abs(s.elapsed() - 3.0) < 1e-9
  b["v"] = False
  c.t += 1.0
  assert not s.update(True)
  assert s.elapsed() == 0.0


def test_ignition_on_stops_it_immediately():
  s, c, b = make(True)
  assert s.update(True)
  c.t += 0.01
  assert not s.update(False)


def test_param_polled_not_read_every_frame():
  calls = []
  c = Clock()
  s = WarningScreenState(lambda: calls.append(1) or True, c)
  for _ in range(10):
    s.update(True)
    c.t += 0.01
  assert len(calls) == 1


def test_gives_up_if_param_stuck_then_rearms_after_clear():
  s, c, b = make(True)
  assert s.update(True)
  c.t += MAX_ON_S + 1
  assert not s.update(True)
  c.t += 5
  assert not s.update(True)
  b["v"] = False
  c.t += 1
  s.update(True)
  b["v"] = True
  c.t += 1
  assert s.update(True)


def test_read_error_is_off():
  def boom():
    raise OSError
  c = Clock()
  assert not WarningScreenState(boom, c).update(True)
