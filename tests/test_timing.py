import numpy as np

from ego_vio.imu.imu_reader import fit_counter_timestamps
from ego_vio.timing import OnlineCounterFitter


def test_online_counter_fitter_stays_monotonic_across_refits_and_wraps():
    rng = np.random.default_rng(0)
    fitter = OnlineCounterFitter(counter_wrap=400, window_size=400, fit_every=50)
    outputs = []
    last_arrival = 1000.0

    for i in range(3200):
        physical_time = 1000.0 + i / 400.0
        burst_delay = 0.004 if i % 173 < 8 else 0.0
        arrival = physical_time + rng.normal(0.0, 0.0015) + burst_delay
        arrival = max(last_arrival + 1e-6, arrival)
        last_arrival = arrival
        outputs.append(fitter.feed(i % 400 + 1, arrival))

    dt = np.diff(outputs)
    assert np.all(dt > 0.0)
    # ImuReader hides the first 500 samples while the clock fit reanchors.
    np.testing.assert_allclose(dt[500:], 1.0 / 400.0, atol=2.5e-4)


def test_online_counter_fitter_preserves_a_skipped_counter_gap():
    fitter = OnlineCounterFitter(counter_wrap=400, window_size=40, fit_every=10)
    outputs = []
    counters = list(range(1, 31)) + list(range(32, 62))

    for i, counter in enumerate(counters):
        outputs.append(fitter.feed(counter, 2000.0 + i / 400.0))

    dt = np.diff(outputs)
    assert dt[29] > 1.8 / 400.0
    assert dt[29] < 2.2 / 400.0


def test_online_counter_fitter_uses_uint32_wrap_for_large_counters():
    fitter = OnlineCounterFitter(counter_wrap=400, window_size=20, fit_every=10)
    counters = [(1 << 32) - 2, (1 << 32) - 1, 0, 1]
    outputs = [fitter.feed(c, 3000.0 + i / 400.0) for i, c in enumerate(counters)]

    assert np.all(np.diff(outputs) > 0.0)


def test_counter_fit_handles_device_reset_after_free_running_count():
    counters = list(range(983_000, 983_030)) + list(range(1, 31))
    arrivals = 5000.0 + np.arange(len(counters)) / 400.0

    fitted, info = fit_counter_timestamps(arrivals, counters)

    np.testing.assert_allclose(np.diff(fitted), 1.0 / 400.0, atol=1e-9)
    assert abs(info["rate_hz"] - 400.0) < 1e-6


def test_online_counter_fitter_keeps_phase_on_device_reset_after_free_run():
    fitter = OnlineCounterFitter(counter_wrap=400, window_size=40, fit_every=10)
    counters = list(range(983_000, 983_030)) + list(range(1, 31))
    outputs = [
        fitter.feed(counter, 6000.0 + index / 400.0)
        for index, counter in enumerate(counters)
    ]

    np.testing.assert_allclose(np.diff(outputs)[20:], 1.0 / 400.0, atol=2.5e-4)


def test_online_counter_fitter_reset_reanchors_after_startup_discontinuity():
    fitter = OnlineCounterFitter(counter_wrap=400, window_size=40, fit_every=10)
    for i in range(30):
        fitter.feed(1000 + i, 4000.0 + i / 400.0)

    fitter.reset()
    timestamp = fitter.feed(5000, 4010.0)

    assert timestamp == 4010.0
    assert fitter.rate_hz is None


def test_online_counter_fitter_reanchors_on_non_wrap_counter_restart():
    fitter = OnlineCounterFitter(counter_wrap=400, window_size=40, fit_every=10)
    for i in range(30):
        fitter.feed(100_000 + i, 4500.0 + i / 400.0)

    timestamp = fitter.feed(10, 4510.0)

    assert timestamp == 4510.0
    assert fitter.rate_hz is None


def test_online_counter_fitter_recovers_phase_after_startup_burst():
    fitter = OnlineCounterFitter(counter_wrap=None, window_size=400, fit_every=50)
    outputs = []
    for index in range(900):
        physical_time = 5000.0 + index / 400.0
        arrival = 5000.0 if index < 100 else physical_time + 0.003
        outputs.append(fitter.feed(index, arrival))

    assert np.all(np.diff(outputs) > 0.0)
    assert abs(outputs[-1] - (5000.0 + 899 / 400.0 + 0.003)) < 0.01


def test_nominal_rate_fit_freezes_after_hidden_warmup():
    rng = np.random.default_rng(42)
    fitter = OnlineCounterFitter(
        counter_wrap=400,
        window_size=400,
        fit_every=50,
        nominal_rate_hz=400.0,
        freeze_after=500,
    )
    outputs = []
    for index in range(2000):
        physical_time = 7000.0 + index / 400.0
        arrival = physical_time + rng.normal(0.0, 0.004)
        outputs.append(fitter.feed(200_000 + index, arrival))

    dt = np.diff(outputs)
    np.testing.assert_allclose(dt[500:], 1.0 / 400.0, atol=1e-10)


def test_nominal_rate_pll_tracks_long_term_host_phase_without_pps():
    rng = np.random.default_rng(7)
    fitter = OnlineCounterFitter(
        counter_wrap=None,
        window_size=400,
        fit_every=50,
        nominal_rate_hz=400.0,
        freeze_after=None,
    )
    outputs = []
    arrivals = []
    # Deliberately model a 399.6 Hz device clock. A permanently frozen 400 Hz
    # mapping would accumulate about 25 ms error over this interval.
    for index in range(10_000):
        arrival = 8000.0 + index / 399.6 + rng.normal(0.0, 0.001)
        arrivals.append(arrival)
        outputs.append(fitter.feed(index, arrival))

    assert np.all(np.diff(outputs) > 0.0)
    assert abs(outputs[-1] - arrivals[-1]) < 0.01
