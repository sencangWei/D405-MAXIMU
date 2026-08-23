from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _allan_report(*, stationary: bool = True) -> dict:
    return {
        "result": "FAIL",
        "duration_s": 21600.0,
        "rate_hz": 400.0,
        "gyroscope": {"noise_density": 1e-4, "random_walk": 2e-6},
        "accelerometer": {"noise_density": 1e-3, "random_walk": 3e-5},
        "checks": {
            "capture_summary_present": True,
            "duration": True,
            "rate_400hz": True,
            "counter_gaps_zero": False,
            "sequence_gaps_zero": True,
            "crc_errors_zero": False,
            "discarded_bytes_zero": False,
            "invalid_imu_flags_zero": True,
            "queue_overflow_zero": True,
            "capture_not_interrupted": True,
            "stationary_gyro_rms": stationary,
            "stationary_accel_rms": stationary,
        },
    }


def test_provisional_imu_yaml_accepts_only_transport_dirty_allan(tmp_path):
    from product_calibration.camera_imu_bench import write_provisional_imu_yaml

    output = tmp_path / "imu.yaml"
    evidence = write_provisional_imu_yaml(_allan_report(), output)
    document = yaml.safe_load(output.read_text(encoding="utf-8"))

    assert document["update_rate"] == 400.0
    assert document["gyroscope_noise_density"] == 1e-4
    assert document["accelerometer_random_walk"] == 3e-5
    assert evidence["allan_result"] == "FAIL"
    assert evidence["accepted_failed_checks"] == [
        "counter_gaps_zero", "crc_errors_zero", "discarded_bytes_zero"
    ]


def test_provisional_imu_yaml_rejects_nonstationary_allan(tmp_path):
    from product_calibration.camera_imu_bench import write_provisional_imu_yaml
    from product_calibration.workflow import WorkflowError

    with pytest.raises(WorkflowError, match="只能因允许的传输门"):
        write_provisional_imu_yaml(_allan_report(stationary=False), tmp_path / "imu.yaml")


def test_provisional_imu_yaml_rejects_short_capture_even_if_boolean_check_claims_pass(tmp_path):
    from product_calibration.camera_imu_bench import write_provisional_imu_yaml
    from product_calibration.workflow import WorkflowError

    report = _allan_report()
    report["duration_s"] = 3600.0
    with pytest.raises(WorkflowError, match="时长/频率/静止性必须通过"):
        write_provisional_imu_yaml(report, tmp_path / "imu.yaml")


def test_camera_imu_candidate_report_is_ttl_scoped_and_nonrelease():
    from product_calibration.camera_imu_bench import finalize_candidate_report

    comparison = {"result": "PASS", "checks": {"repeatability": True}}
    residuals = {
        "run1": {
            "reprojection_mean_px": 0.2,
            "gyroscope_mean_rad_s": 0.01,
            "accelerometer_mean_m_s2": 0.1,
        },
        "run2": {
            "reprojection_mean_px": 0.3,
            "gyroscope_mean_rad_s": 0.015,
            "accelerometer_mean_m_s2": 0.2,
        },
    }
    health = {
        "run1": {"result": "PASS", "metrics": {"guided_nine_grid": {"user_aborted": False}}},
        "run2": {"result": "PASS", "metrics": {"guided_nine_grid": {"user_aborted": False}}},
    }
    imu_health = {"run1": {"result": "PASS"}, "run2": {"result": "PASS"}}
    report = finalize_candidate_report(
        comparison,
        residuals,
        health,
        imu_health,
        product_id="P001",
        device={"serial": "123"},
        source_evidence={"stereo_report": "/tmp/stereo.yaml"},
        run_evidence={"run1_camchain": "/tmp/run1.yaml"},
    )

    assert report["result"] == "PASS"
    assert report["acceptance_scope"] == "product_bound_camera_imu_ttl_candidate"
    assert report["release_eligible"] is False
    assert report["product_result"] == "BLOCKED"
    assert report["temporal_calibration"]["td_status"] == "PROVISIONAL_USB_TTL_ONLY"
    assert report["spatial_calibration"]["reuse_if_mount_unchanged"] is True


def test_camera_imu_candidate_report_fails_on_camera_health():
    from product_calibration.camera_imu_bench import finalize_candidate_report

    residual = {
        "reprojection_mean_px": 0.2,
        "gyroscope_mean_rad_s": 0.01,
        "accelerometer_mean_m_s2": 0.1,
    }
    report = finalize_candidate_report(
        {"result": "PASS", "checks": {}},
        {"run1": residual, "run2": residual},
        {
            "run1": {"result": "FAIL", "metrics": {"guided_nine_grid": {"user_aborted": False}}},
            "run2": {"result": "PASS", "metrics": {"guided_nine_grid": {"user_aborted": False}}},
        },
        {"run1": {"result": "PASS"}, "run2": {"result": "PASS"}},
        product_id="P001",
        device={"serial": "123"},
        source_evidence={},
        run_evidence={},
    )
    assert report["result"] == "FAIL"
    assert report["checks"]["both_camera_capture_health_pass"] is False


def test_camera_imu_candidate_report_fails_on_imu_transport_health():
    from product_calibration.camera_imu_bench import finalize_candidate_report

    residual = {
        "reprojection_mean_px": 0.2,
        "gyroscope_mean_rad_s": 0.01,
        "accelerometer_mean_m_s2": 0.1,
    }
    camera = {
        "result": "PASS",
        "metrics": {"guided_nine_grid": {"user_aborted": False}},
    }
    report = finalize_candidate_report(
        {"result": "PASS", "checks": {}},
        {"run1": residual, "run2": residual},
        {"run1": camera, "run2": camera},
        {"run1": {"result": "FAIL"}, "run2": {"result": "PASS"}},
        product_id="P001",
        device={"serial": "123"},
        source_evidence={},
        run_evidence={},
    )
    assert report["result"] == "FAIL"
    assert report["checks"]["both_imu_transport_health_pass"] is False


def test_camera_imu_candidate_report_is_stm32_scoped_and_requires_combined_protocol():
    from product_calibration.camera_imu_bench import finalize_candidate_report

    residual = {
        "reprojection_mean_px": 0.2,
        "gyroscope_mean_rad_s": 0.01,
        "accelerometer_mean_m_s2": 0.1,
    }
    camera = {
        "result": "PASS",
        "metrics": {"guided_nine_grid": {"user_aborted": False}},
    }
    combined = {
        "result": "PASS",
        "metrics": {"protocol": "stm32_combined_v1"},
    }
    report = finalize_candidate_report(
        {"result": "PASS", "checks": {}},
        {"run1": residual, "run2": residual},
        {"run1": camera, "run2": camera},
        {"run1": combined, "run2": combined},
        product_id="P001",
        device={"serial": "123"},
        source_evidence={},
        run_evidence={},
        transport="stm32",
    )

    assert report["result"] == "PASS"
    assert report["checks"]["both_expected_imu_protocol"] is True
    assert report["acceptance_scope"] == "product_bound_camera_imu_stm32_candidate"
    assert report["temporal_calibration"]["td_status"] == "CANDIDATE_STM32_TIME_CHAIN"
    assert report["temporal_calibration"]["reuse_after_stm32"] is True


def test_camera_imu_bench_wrapper_exists_and_is_executable():
    wrapper = ROOT / "camera_bench_05_camera_imu.sh"
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o111


def test_product_camera_imu_paths_do_not_apply_optional_accelerometer_matrix():
    formal = (ROOT / "product_calibration_stage.py").read_text(encoding="utf-8")
    candidate = (ROOT / "product_calibration/camera_imu_bench.py").read_text(
        encoding="utf-8"
    )

    assert "apply_accelerometer_calibration" not in formal
    assert "apply_accelerometer_calibration" not in candidate
    assert "--intrinsic-report" not in candidate
    assert "NOT_APPLIED_PRODUCT_BASELINE" in formal
    assert "NOT_APPLIED_PRODUCT_BASELINE" in candidate
    assert "imu_capture_health(recording)" in formal
    assert 'report["imu_capture_health"]' in formal
