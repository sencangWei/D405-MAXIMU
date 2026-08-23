from ego_vio.camera.realsense_capture import (
    ConsecutiveFrameWarmup,
    configure_sensor_exposure,
)


class FakeOptions:
    enable_auto_exposure = "enable_auto_exposure"
    auto_exposure_limit_toggle = "auto_exposure_limit_toggle"
    auto_exposure_limit = "auto_exposure_limit"
    auto_gain_limit_toggle = "auto_gain_limit_toggle"
    auto_gain_limit = "auto_gain_limit"
    exposure = "exposure"
    gain = "gain"


class FakeRs:
    option = FakeOptions()


class FakeSensor:
    def __init__(self):
        self.values = {}

    def set_option(self, option, value):
        self.values[option] = value

    def get_option(self, option):
        return self.values[option]


def test_camera_warmup_restarts_after_counter_gap_and_opens_next_frame():
    gate = ConsecutiveFrameWarmup(required_frames=3)

    assert gate.observe(15) is False
    assert gate.observe(18) is False
    assert gate.observe(19) is False
    assert gate.observe(20) is False
    assert gate.observe(21) is True

    assert gate.stats() == {
        "required_consecutive_frames": 3,
        "completed": True,
        "observed_frames": 4,
        "consecutive_frames": 3,
        "dropped_frames": 2,
        "reset_events": 1,
    }


def test_camera_warmup_pair_rejection_resets_consecutive_run():
    gate = ConsecutiveFrameWarmup(required_frames=2)

    assert gate.observe(10) is False
    gate.reject_pair()
    assert gate.observe(12) is False
    assert gate.observe(13) is False
    assert gate.observe(14) is True
    assert gate.stats()["reset_events"] == 1


def test_zero_camera_warmup_preserves_existing_callers():
    gate = ConsecutiveFrameWarmup(required_frames=0)

    assert gate.observe(100) is True
    assert gate.stats()["completed"] is True


def test_live_camera_applies_same_limited_auto_exposure_as_capture_chain():
    sensor = FakeSensor()

    report = configure_sensor_exposure(
        FakeRs,
        sensor,
        auto_exposure=True,
        exposure_us=20000,
        gain=48,
        auto_exposure_limit_us=8000,
        auto_gain_limit=248,
    )

    assert report == {
        "mode": "auto_limited",
        "exposure_limit_us": 8000,
        "gain_limit": 248,
    }
    assert sensor.values["enable_auto_exposure"] == 1.0
