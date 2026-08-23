import csv
import json
import struct

import numpy as np
import pytest
import yaml

import product_calibration.imu_bench as imu_bench
from product_calibration.imu_bench import build_parser, main
from product_calibration.imu_ellipsoid import fit_and_validate, load_pose_means
from product_calibration.imu_multipose_capture import (
    accepted_source_pose_ids,
    capture_pose_csv,
    transport_clean,
)
from product_calibration.imu_stream import CaptureStats, NORMALIZED_FORMAT
from product_calibration_stage import parser as product_parser


def write_capture(path, count, *, dt=0.0025, seed=17):
    path.mkdir()
    rng = np.random.default_rng(seed)
    with (path / "imu.bin").open("wb") as stream:
        for index in range(count):
            gyro = rng.normal(0.02, 0.01, 3)
            accel = np.array([0.0, 0.0, 1.0]) + rng.normal(0.0, 0.001, 3)
            stream.write(
                struct.pack(
                    NORMALIZED_FORMAT,
                    index * dt,
                    index,
                    *gyro,
                    *accel,
                    26.0,
                )
            )
    (path / "summary.json").write_text(
        json.dumps(
            {
                "counter_gaps": 0,
                "sequence_gaps": 0,
                "crc_or_checksum_errors": 0,
                "discarded_bytes": 0,
                "invalid_imu_flags": 0,
                "queue_overflow_flags": 0,
                "interrupted": False,
            }
        ),
        encoding="utf-8",
    )


def only_report(output_root):
    reports = list(output_root.glob("*/report.yaml"))
    assert len(reports) == 1
    return yaml.safe_load(reports[0].read_text(encoding="utf-8"))


def write_multipose_csv(path):
    indices = np.arange(30, dtype=float)
    z = 1.0 - 2.0 * (indices + 0.5) / len(indices)
    angle = indices * np.pi * (3.0 - np.sqrt(5.0))
    radius = np.sqrt(1.0 - z * z)
    points = np.column_stack((radius * np.cos(angle), radius * np.sin(angle), z)) * 9.80665
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["pose_id", "split", "ax", "ay", "az"])
        writer.writeheader()
        for index, point in enumerate(points):
            writer.writerow(
                {
                    "pose_id": f"P{index + 1:02d}",
                    "split": "fit" if index < 20 else "validation",
                    "ax": point[0],
                    "ay": point[1],
                    "az": point[2],
                }
            )


def write_recovery_source(source):
    source.mkdir()
    source_csv = source / "imu_multipose.csv"
    rng = np.random.default_rng(23)
    fit_signs = np.asarray(
        [(1, 1, 1), (1, 1, -1), (1, -1, 1), (-1, 1, 1), (-1, -1, -1)]
    )
    fit = []
    for index in range(20):
        vector = np.abs(rng.normal(size=3)) * fit_signs[index % len(fit_signs)]
        fit.append(vector / np.linalg.norm(vector) * 9.80665)
    validation = []
    for _ in range(10):
        vector = rng.normal(size=3)
        validation.append(vector / np.linalg.norm(vector) * 9.80665)
    with source_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["pose_id", "split", "ax", "ay", "az"])
        writer.writeheader()
        for index, vector in enumerate(fit + validation):
            writer.writerow(
                {
                    "pose_id": f"P{index + 1:02d}",
                    "split": "fit" if index < 20 else "validation",
                    "ax": vector[0], "ay": vector[1], "az": vector[2],
                }
            )
    source_report = fit_and_validate(np.asarray(fit), np.asarray(validation))
    assert source_report["result"] == "FAIL"
    assert source_report["checks"]["fit_octant_coverage"] is False
    clean = vars(CaptureStats(
        protocol="kt_ex9_37", frames=12001, duration_s=30.0, rate_hz=400.0
    ))
    short = vars(CaptureStats(
        protocol="kt_ex9_37", frames=3750, duration_s=9.3725, rate_hz=400.0
    ))
    source_report["pose_capture_health"] = [
        {
            "pose_id": f"P{index + 1:02d}", "trial": 1,
            "gyro_std_max_deg_s": 0.06, "accel_std_max_g": 0.001,
            "capture_health": short if index == 3 else clean,
        }
        for index in range(30)
    ]
    source_report["evidence"] = {"pose_csv_sha256": imu_bench.sha256_file(source_csv)}
    (source / "report.yaml").write_text(
        yaml.safe_dump(source_report, sort_keys=False), encoding="utf-8"
    )
    return source


def test_static_bench_reanalysis_is_never_product_release_eligible(tmp_path):
    capture = tmp_path / "static_capture"
    output = tmp_path / "results"
    write_capture(capture, 500)

    assert main([
        "static", "--input-capture", str(capture), "--output-root", str(output),
        "--warmup", "0.2", "--formal", "0.8",
    ]) == 0

    report = only_report(output)
    assert report["result"] == "PASS"
    assert report["acceptance_scope"] == "bench_provisional"
    assert report["release_eligible"] is False
    assert report["product_result"] == "BLOCKED"


def test_live_static_bench_excludes_serial_opening_from_formal_health(tmp_path, monkeypatch):
    calls = []

    def fake_capture(**kwargs):
        calls.append(kwargs)
        write_capture(kwargs["output_dir"], 500)
        return CaptureStats(
            protocol="kt_ex9_37", frames=500, duration_s=1.2475, rate_hz=400.0,
            startup_discard_s=kwargs.get("startup_discard_s", 0.0),
        )

    monkeypatch.setattr(imu_bench, "find_imu_port", lambda _requested: "fake")
    monkeypatch.setattr(imu_bench, "capture_serial", fake_capture)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert main([
        "static", "--output-root", str(tmp_path / "results"),
        "--duration", "1.25", "--warmup", "0.2", "--formal", "0.8",
    ]) == 0
    assert calls[0]["startup_discard_s"] == 1.0


def test_ten_hour_allan_defaults_and_report_remain_below_product_gate(tmp_path):
    args = build_parser().parse_args(["allan"])
    assert args.duration == 10 * 3600
    assert args.minimum_duration == 6 * 3600
    product_args = product_parser().parse_args(["imu-noise", "P001"])
    assert product_args.duration == 10 * 3600
    assert product_args.minimum_duration == 6 * 3600

    capture = tmp_path / "allan_capture"
    output = tmp_path / "results"
    write_capture(capture, 4096)

    assert main([
        "allan", "--input-capture", str(capture), "--output-root", str(output),
        "--minimum-duration", "10",
    ]) == 0

    report = only_report(output)
    assert report["result"] == "PASS"
    assert report["release_eligible"] is False
    assert report["product_result"] == "BLOCKED"
    assert report["product_gate"]["minimum_duration_s"] == 6 * 3600
    assert report["product_gate"]["duration_pass"] is False


def test_product_init_accepts_stm32_firmware_provenance():
    args = product_parser().parse_args([
        "init", "P001",
        "--firmware-bin", "/tmp/firmware.bin",
        "--flash-evidence", "/tmp/flash.yaml",
    ])

    assert str(args.firmware_bin) == "/tmp/firmware.bin"
    assert str(args.flash_evidence) == "/tmp/flash.yaml"


def test_multipose_bench_solves_20_fit_10_heldout_without_product_session(tmp_path):
    pose_csv = tmp_path / "poses.csv"
    output = tmp_path / "results"
    write_multipose_csv(pose_csv)

    assert main([
        "multipose", "--input-csv", str(pose_csv), "--output-root", str(output),
    ]) == 0

    report = only_report(output)
    assert report["result"] == "PASS"
    assert report["fit_pose_count"] == 20
    assert report["validation_pose_count"] == 10
    assert report["release_eligible"] is False
    assert report["reuse_policy"]["repeat_long_calibration"] == "only_if_invalidated"


def test_multipose_bench_defaults_to_30_second_pose_windows():
    args = build_parser().parse_args(["multipose"])

    assert args.pose_duration == 30.0
    assert args.input_csv is None
    assert args.resume_attempt is None


@pytest.mark.parametrize("duration", ["9", "nan", "inf", "-inf"])
def test_multipose_cli_rejects_invalid_pose_windows(duration):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["multipose", "--pose-duration", duration])


def test_multipose_transport_gate_rejects_short_formal_window():
    stats = CaptureStats(
        protocol="kt_ex9_37",
        frames=3750,
        duration_s=9.3725,
        rate_hz=400.0,
    )

    assert transport_clean(vars(stats), required_duration_s=30.0) is False


def test_multipose_recovery_excludes_short_source_pose_and_keeps_latest_retry():
    clean = vars(CaptureStats(
        protocol="kt_ex9_37",
        frames=12001,
        duration_s=30.0,
        rate_hz=400.0,
    ))
    short = vars(CaptureStats(
        protocol="kt_ex9_37",
        frames=3750,
        duration_s=9.3725,
        rate_hz=400.0,
    ))
    reports = [
        {
            "pose_id": "P01", "trial": 1,
            "gyro_std_max_deg_s": 0.2, "accel_std_max_g": 0.001,
            "capture_health": clean,
        },
        {
            "pose_id": "P01", "trial": 2,
            "gyro_std_max_deg_s": 0.06, "accel_std_max_g": 0.001,
            "capture_health": clean,
        },
        {
            "pose_id": "P04", "trial": 1,
            "gyro_std_max_deg_s": 0.06, "accel_std_max_g": 0.001,
            "capture_health": short,
        },
    ]

    accepted, excluded = accepted_source_pose_ids(reports, required_duration_s=30.0)

    assert accepted == {"P01"}
    assert excluded == {"P04": "capture_health"}


def test_multipose_capture_retries_only_the_transport_dirty_pose(tmp_path):
    calls = []

    def fake_capture(**kwargs):
        output = kwargs["output_dir"]
        output.mkdir(parents=True)
        calls.append(output)
        with (output / "imu.bin").open("wb") as stream:
            for index in range(100):
                stream.write(
                    struct.pack(
                        NORMALIZED_FORMAT,
                        index * 0.0025,
                        index,
                        0.01,
                        -0.02,
                        0.03,
                        0.0,
                        0.0,
                        1.0,
                        26.0,
                    )
                )
        return CaptureStats(
            protocol="kt_ex9_37",
            frames=100,
            counter_gaps=1 if len(calls) == 1 else 0,
            duration_s=0.25,
            rate_hz=400.0,
        )

    csv_path, reports = capture_pose_csv(
        port="fake",
        baud=921600,
        protocol="auto",
        pose_duration_s=0.25,
        attempt=tmp_path,
        capture_fn=fake_capture,
        input_fn=lambda _prompt: "",
        print_fn=lambda _message: None,
    )

    assert len(calls) == 31
    assert all(call.name.startswith("P") for call in calls)
    assert reports[0]["pose_id"] == "P01"
    assert reports[0]["transport_clean"] is False
    assert reports[1]["pose_id"] == "P01"
    assert reports[1]["transport_clean"] is True
    with csv_path.open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 30


def test_multipose_capture_stops_and_aggregates_repeated_pose_failures(tmp_path):
    calls = []

    def dirty_capture(**kwargs):
        output = kwargs["output_dir"]
        output.mkdir(parents=True)
        calls.append(output)
        with (output / "imu.bin").open("wb") as stream:
            for index in range(100):
                stream.write(struct.pack(
                    NORMALIZED_FORMAT, index * 0.0025, index,
                    0.01, -0.02, 0.03, 0.0, 0.0, 1.0, 26.0,
                ))
        return CaptureStats(
            protocol="kt_ex9_37", frames=100, counter_gaps=1,
            duration_s=30.0, rate_hz=400.0,
        )

    with pytest.raises(ValueError, match="P01连续2次未通过"):
        capture_pose_csv(
            port="fake",
            baud=921600,
            protocol="auto",
            pose_duration_s=30.0,
            attempt=tmp_path,
            capture_fn=dirty_capture,
            input_fn=lambda _prompt: "",
            print_fn=lambda _message: None,
            max_trials_per_pose=2,
        )

    stop = json.loads((tmp_path / "capture_stop.json").read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert stop["result"] == "FAIL"
    assert len(stop["pose_capture_health"]) == 2


def test_multipose_capture_interrupt_stops_without_loading_empty_capture(tmp_path):
    def interrupted_capture(**kwargs):
        output = kwargs["output_dir"]
        output.mkdir(parents=True)
        (output / "imu.bin").write_bytes(b"")
        return CaptureStats(
            protocol="kt_ex9_37", interrupted=True, duration_s=0.0, rate_hz=0.0
        )

    with pytest.raises(ValueError, match="用户在P01采集中断"):
        capture_pose_csv(
            port="fake",
            baud=921600,
            protocol="auto",
            pose_duration_s=30.0,
            attempt=tmp_path,
            capture_fn=interrupted_capture,
            input_fn=lambda _prompt: "",
            print_fn=lambda _message: None,
        )

    stop = json.loads((tmp_path / "capture_stop.json").read_text(encoding="utf-8"))
    assert stop["result"] == "BLOCKED"
    assert stop["pose_capture_health"][0]["capture_health"]["interrupted"] is True


def test_multipose_gate_failure_maps_to_cli_fail_exit(monkeypatch):
    def fail(_args):
        raise imu_bench.MultiposeCaptureFailure("姿态门禁失败")

    monkeypatch.setattr(imu_bench, "run_multipose", fail)

    assert main(["multipose"]) == 1


def test_multipose_recovery_reuses_validation_and_collects_only_missing_fit_octants(
    tmp_path, monkeypatch
):
    source = write_recovery_source(tmp_path / "source")

    candidates = iter((np.array([1.0, -1.0, -1.0]), np.array([-1.0, 1.0, -1.0])))

    def fake_capture(**kwargs):
        vector = next(candidates)
        vector = vector / np.linalg.norm(vector)
        output = kwargs["output_dir"]
        output.mkdir(parents=True)
        with (output / "imu.bin").open("wb") as stream:
            for index in range(100):
                stream.write(struct.pack(
                    NORMALIZED_FORMAT, index * 0.0025, index,
                    0.01, -0.02, 0.03, *vector, 26.0,
                ))
        return CaptureStats(
            protocol="kt_ex9_37", frames=12001, duration_s=30.0, rate_hz=400.0
        )

    monkeypatch.setattr(imu_bench, "capture_serial", fake_capture)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    attempt = tmp_path / "recovery"
    attempt.mkdir()
    combined, supplemental, recovery = imu_bench.recover_multipose(
        source_attempt=source,
        attempt=attempt,
        port="fake",
        baud=921600,
        protocol="auto",
        pose_duration_s=30.0,
    )

    recovered_fit, recovered_validation = load_pose_means(combined)
    assert len(recovered_fit) == 21
    assert len(recovered_validation) == 10
    assert fit_and_validate(recovered_fit, recovered_validation)["result"] == "PASS"
    assert len(supplemental) == 2
    assert recovery["excluded_source_pose_ids"] == {"P04": "capture_health"}
    assert recovery["source_validation_poses_reused"] == 10


def test_multipose_recovery_stops_after_bounded_duplicate_candidates(tmp_path, monkeypatch):
    source = write_recovery_source(tmp_path / "source")
    calls = []

    def fake_capture(**kwargs):
        calls.append(kwargs["output_dir"])
        vector = np.array([1.0, 1.0, 1.0]) / np.sqrt(3.0)
        output = kwargs["output_dir"]
        output.mkdir(parents=True)
        with (output / "imu.bin").open("wb") as stream:
            for index in range(100):
                stream.write(struct.pack(
                    NORMALIZED_FORMAT, index * 0.0025, index,
                    0.01, -0.02, 0.03, *vector, 26.0,
                ))
        return CaptureStats(
            protocol="kt_ex9_37", frames=12001, duration_s=30.0, rate_hz=400.0
        )

    monkeypatch.setattr(imu_bench, "capture_serial", fake_capture)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")
    attempt = tmp_path / "recovery"
    attempt.mkdir()

    with pytest.raises(imu_bench.BenchError, match="连续2个补采候选"):
        imu_bench.recover_multipose(
            source_attempt=source,
            attempt=attempt,
            port="fake",
            baud=921600,
            protocol="auto",
            pose_duration_s=30.0,
            max_candidates=2,
        )
    assert len(calls) == 2
    failure = yaml.safe_load((attempt / "report.yaml").read_text(encoding="utf-8"))
    assert failure["result"] == "FAIL"
    assert failure["release_eligible"] is False
    assert len(failure["supplemental_pose_capture_health"]) == 2


def test_multipose_recovery_requires_exactly_coverage_failed_source(tmp_path):
    source = write_recovery_source(tmp_path / "source")
    report_path = source / "report.yaml"
    report = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    report["result"] = "PASS"
    report_path.write_text(yaml.safe_dump(report, sort_keys=False), encoding="utf-8")
    attempt = tmp_path / "recovery"
    attempt.mkdir()

    with pytest.raises(imu_bench.BenchError, match="必须且只能因姿态覆盖失败"):
        imu_bench.recover_multipose(
            source_attempt=source,
            attempt=attempt,
            port="fake",
            baud=921600,
            protocol="auto",
            pose_duration_s=30.0,
        )


def test_multipose_recovery_prompt_interrupt_writes_blocked_report(tmp_path, monkeypatch):
    source = write_recovery_source(tmp_path / "source")
    attempt = tmp_path / "recovery"
    attempt.mkdir()
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(imu_bench.BenchError, match="用户在摆放提示阶段中断"):
        imu_bench.recover_multipose(
            source_attempt=source,
            attempt=attempt,
            port="fake",
            baud=921600,
            protocol="auto",
            pose_duration_s=30.0,
        )

    report = yaml.safe_load((attempt / "report.yaml").read_text(encoding="utf-8"))
    assert report["result"] == "BLOCKED"
    assert report["release_eligible"] is False


def test_orientation_preview_reports_axes_and_missing_target_without_formal_capture(
    tmp_path, monkeypatch
):
    source = write_recovery_source(tmp_path / "source")
    output_root = tmp_path / "results"

    def fake_capture(**kwargs):
        output = kwargs["output_dir"]
        output.mkdir(parents=True)
        vector = np.array([1.0, -1.0, -1.0]) / np.sqrt(3.0)
        with (output / "imu.bin").open("wb") as stream:
            for index in range(100):
                stream.write(struct.pack(
                    NORMALIZED_FORMAT, index * 0.0025, index,
                    0.01, -0.02, 0.03, *vector, 26.0,
                ))
        return CaptureStats(
            protocol="kt_ex9_37", frames=801, duration_s=2.0, rate_hz=400.0
        )

    monkeypatch.setattr(imu_bench, "find_imu_port", lambda _requested: "fake")
    monkeypatch.setattr(imu_bench, "capture_serial", fake_capture)

    assert main([
        "orientation-preview",
        "--source-attempt", str(source),
        "--output-root", str(output_root),
    ]) == 0
    report = only_report(output_root)
    assert report["result"] == "PREVIEW"
    assert report["direction_octant"] == "+--"
    assert report["direction_margin_g"] > 0.15
    assert report["target_ready"] is True
    assert report["release_eligible"] is False
