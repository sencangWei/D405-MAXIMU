import sys
import signal
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from monitor_d405_stm32_soak import (
    counter_delta,
    evaluate_soak,
    running_checkpoint,
)


def healthy_camera(received: int = 324000) -> dict:
    return {
        name: {
            "received": received,
            "first_frame_number": 30,
            "last_frame_number": 30 + received - 1,
            "skipped_frames": 0,
            "gap_events": 0,
            "gap_ratio": 0.0,
            "rate_hz": 30.0,
            "repeated_frames": 0,
            "frame_number_resets": 0,
            "timestamp_regressions": 0,
            "first_arrival_delay_s": 0.03,
            "last_arrival_age_s": 0.03,
            "max_host_interarrival_s": 1.0 / 30.0,
        }
        for name in ("depth", "infrared_left", "infrared_right")
    }


def healthy_imu(frames: int = 4_320_000) -> dict:
    return {
        "protocol": "stm32_combined_v1",
        "frames_ok": frames,
        "frames_bad": 0,
        "resyncs": 0,
        "dropped_frames": 0,
        "counter_resets": 0,
        "counter_stalls": 0,
        "sequence_gaps": 0,
        "invalid_imu_flags": 0,
        "queue_overflow_flags": 0,
        "serial_errors": 0,
        "serial_reconnects": 0,
        "arrival": {
            "arrival_samples": frames,
            "first_arrival_delay_s": 0.0025,
            "last_arrival_age_s": 0.0025,
            "max_host_interarrival_s": 0.005,
        },
    }


def test_evaluate_soak_accepts_clean_three_hour_monitor_only_run():
    report = evaluate_soak(
        camera_streams=healthy_camera(),
        imu_stats=healthy_imu(),
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "PASS"
    assert report["camera"]["result"] == "PASS"
    assert report["imu"]["result"] == "PASS"
    assert report["imu"]["rate_hz"] == 400.0
    assert report["storage_mode"] == "monitor_only"
    assert report["raw_frames_written"] == 0
    assert report["failures"] == []


def test_evaluate_soak_rejects_one_camera_gap():
    camera = healthy_camera()
    camera["infrared_right"]["skipped_frames"] = 1
    camera["infrared_right"]["gap_events"] = 1
    camera["infrared_right"]["gap_ratio"] = 1 / 324000

    report = evaluate_soak(
        camera_streams=camera,
        imu_stats=healthy_imu(),
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert report["camera"]["result"] == "FAIL"
    assert any("infrared_right.skipped_frames=1" in item for item in report["failures"])


def test_evaluate_soak_rejects_stm32_queue_overflow():
    imu = healthy_imu()
    imu["queue_overflow_flags"] = 1

    report = evaluate_soak(
        camera_streams=healthy_camera(),
        imu_stats=imu,
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert report["imu"]["result"] == "FAIL"
    assert any("imu.queue_overflow_flags=1" in item for item in report["failures"])


def test_evaluate_soak_rejects_early_stop():
    report = evaluate_soak(
        camera_streams=healthy_camera(received=162000),
        imu_stats=healthy_imu(frames=2_160_000),
        duration_s=5_400.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert any("duration" in item for item in report["failures"])


def test_evaluate_soak_rejects_stream_that_stops_after_one_minute():
    camera = healthy_camera()
    camera["infrared_right"].update(
        {
            "received": 1800,
            "first_frame_number": 30,
            "last_frame_number": 1829,
            "rate_hz": 30.0,
            "last_arrival_age_s": 10_740.0,
        }
    )

    report = evaluate_soak(
        camera_streams=camera,
        imu_stats=healthy_imu(),
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert any("infrared_right.received=1800" in item for item in report["failures"])
    assert any("infrared_right.last_arrival_age_s" in item for item in report["failures"])


def test_evaluate_soak_rejects_tail_loss_below_old_quarter_second_gate():
    camera = healthy_camera(received=323_993)
    camera["infrared_right"]["last_arrival_age_s"] = 0.24

    report = evaluate_soak(
        camera_streams=camera,
        imu_stats=healthy_imu(),
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert any("infrared_right.last_arrival_age_s=0.240000" in item for item in report["failures"])


def test_evaluate_soak_rejects_camera_pause_with_continuous_frame_numbers():
    camera = healthy_camera()
    camera["infrared_left"]["max_host_interarrival_s"] = 0.2

    report = evaluate_soak(
        camera_streams=camera,
        imu_stats=healthy_imu(),
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert any("infrared_left.max_host_interarrival_s=0.200000" in item for item in report["failures"])


def test_evaluate_soak_rejects_imu_terminal_silence_hidden_by_average_rate():
    imu = healthy_imu(frames=4_312_000)
    imu["arrival"]["last_arrival_age_s"] = 20.0

    report = evaluate_soak(
        camera_streams=healthy_camera(),
        imu_stats=imu,
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert 399.0 <= report["imu"]["rate_hz"] <= 401.0
    assert report["result"] == "FAIL"
    assert any("imu.arrival.last_arrival_age_s=20.000000" in item for item in report["failures"])


def test_evaluate_soak_rejects_imu_pause_with_continuous_counter():
    imu = healthy_imu()
    imu["arrival"]["max_host_interarrival_s"] = 0.5

    report = evaluate_soak(
        camera_streams=healthy_camera(),
        imu_stats=imu,
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert any("imu.arrival.max_host_interarrival_s=0.500000" in item for item in report["failures"])


def test_evaluate_soak_rejects_one_second_short_of_three_hours():
    report = evaluate_soak(
        camera_streams=healthy_camera(),
        imu_stats=healthy_imu(),
        duration_s=10_799.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert any("duration" in item for item in report["failures"])


def test_evaluate_soak_rejects_wrong_camera_rate():
    camera = healthy_camera()
    camera["depth"]["rate_hz"] = 60.0

    report = evaluate_soak(
        camera_streams=camera,
        imu_stats=healthy_imu(),
        duration_s=10_800.0,
        requested_duration_s=10_800.0,
        capture_error=None,
    )

    assert report["result"] == "FAIL"
    assert any("camera.depth.rate_hz=60.0" in item for item in report["failures"])


def test_running_checkpoint_cannot_be_mistaken_for_final_pass():
    health = evaluate_soak(
        camera_streams=healthy_camera(received=1800),
        imu_stats=healthy_imu(frames=24_000),
        duration_s=60.0,
        requested_duration_s=60.0,
        capture_error=None,
    )

    checkpoint = running_checkpoint(health)

    assert checkpoint["state"] == "RUNNING"
    assert checkpoint["result"] == "IN_PROGRESS"
    assert checkpoint["current_health"] == "PASS"


def test_sigterm_handler_allows_early_stop_finalization():
    code = f"""
import signal
import sys
import time
sys.path.insert(0, {str(SCRIPTS)!r})
from monitor_d405_stm32_soak import StopRequested, raise_stop_requested
signal.signal(signal.SIGTERM, raise_stop_requested)
try:
    print('READY', flush=True)
    time.sleep(30)
except StopRequested as exc:
    print('FINALIZE:' + str(exc), flush=True)
    raise SystemExit(2)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout.readline().strip() == "READY"
    process.send_signal(signal.SIGTERM)
    stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == 2
    assert "FINALIZE:SIGTERM" in stdout
    assert stderr == ""


def test_formal_wrapper_rejects_duration_override():
    wrapper = ROOT / "monitor_d405_stm32_3h_rsusb.sh"
    result = subprocess.run(
        ["bash", str(wrapper), "--duration", "10"],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 2
    assert "固定为10800秒" in result.stderr


def test_cumulative_counter_delta_preserves_reconnect_evidence():
    start = healthy_imu(frames=500)
    end = healthy_imu(frames=4500)
    end["serial_errors"] = 1
    end["serial_reconnects"] = 1

    delta = counter_delta(
        start,
        end,
        (
            "frames_ok",
            "serial_errors",
            "serial_reconnects",
        ),
    )

    assert delta["protocol"] == "stm32_combined_v1"
    assert delta["frames_ok"] == 4000
    assert delta["serial_errors"] == 1
    assert delta["serial_reconnects"] == 1
