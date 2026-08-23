from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_stereo_bench_report_is_product_bound_but_not_release_eligible(tmp_path):
    from product_calibration.camera_bench import finalize_stereo_report

    identity = tmp_path / "identity.yaml"
    identity.write_text("result: PASS\n", encoding="utf-8")
    base = {
        "result": "PASS",
        "checks": {"stereo_solver": True},
        "evidence": {"camchain": "/tmp/camchain.yaml"},
    }
    heldout = {"result": "PASS", "checks": {"epipolar": True}}
    health = {"result": "PASS", "checks": {"formal_drops_zero": True}}
    report = finalize_stereo_report(
        base,
        heldout,
        health,
        health,
        product_id="P001",
        identity_report=identity,
        device={"serial": "123", "firmware": "5.17.0.10"},
    )

    assert report["result"] == "PASS"
    assert report["acceptance_scope"] == "product_bound_stereo_candidate"
    assert report["release_eligible"] is False
    assert report["product_result"] == "BLOCKED"
    assert report["evidence"]["identity_report"] == str(identity.resolve())
    assert len(report["evidence"]["identity_report_sha256"]) == 64


def test_stereo_bench_report_fails_if_heldout_gate_fails(tmp_path):
    from product_calibration.camera_bench import finalize_stereo_report

    identity = tmp_path / "identity.yaml"
    identity.write_text("result: PASS\n", encoding="utf-8")
    report = finalize_stereo_report(
        {"result": "PASS", "checks": {}, "evidence": {}},
        {"result": "FAIL", "checks": {"epipolar": False}},
        {"result": "PASS", "checks": {}},
        {"result": "PASS", "checks": {}},
        product_id="P001",
        identity_report=identity,
        device={"serial": "123", "firmware": "5.17.0.10"},
    )

    assert report["result"] == "FAIL"
    assert report["release_eligible"] is False


def test_stereo_bench_report_fails_if_formal_camera_window_dropped_frames(tmp_path):
    from product_calibration.camera_bench import finalize_stereo_report

    identity = tmp_path / "identity.yaml"
    identity.write_text("result: PASS\n", encoding="utf-8")
    report = finalize_stereo_report(
        {"result": "PASS", "checks": {}, "evidence": {}},
        {"result": "PASS", "checks": {}},
        {"result": "FAIL", "checks": {"formal_device_frame_drops_zero": False}},
        {"result": "PASS", "checks": {"formal_device_frame_drops_zero": True}},
        product_id="P001",
        identity_report=identity,
        device={"serial": "123", "firmware": "5.17.0.10"},
    )

    assert report["result"] == "FAIL"
    assert report["release_eligible"] is False


def test_camera_bench_wrapper_exists_and_is_executable():
    wrapper = ROOT / "camera_bench_04_d405_stereo.sh"
    assert wrapper.is_file()
    assert wrapper.stat().st_mode & 0o111
