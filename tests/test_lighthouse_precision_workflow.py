from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "lighthouse_umi_precision_workflow.sh"
)


def test_calibration_is_independent_of_slam() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    calibration_case = text.split("    calibrate)", 1)[1].split("    evaluate)", 1)[0]

    assert "calibrate_lighthouse_aprilgrid_session.sh" in calibration_case
    assert "run_slam" not in calibration_case
    assert "calibrate_lighthouse_umi.py" not in calibration_case
    assert "APRILGRID_GUIDED_CAPTURE=1" in calibration_case


def test_evaluation_uses_only_estimate_timestamps_for_lighthouse_gt() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "apply_lighthouse_aprilgrid_calibration.py" in text
    assert '--query-times "$estimate"' in text
    assert "--body-camera-config \"$CONFIG\"" in text
    assert "calibrate_lighthouse_umi.py\" apply" not in text
    assert "lighthouse_ground_truth_provenance.json" in text


def test_frozen_checksum_is_checked_from_its_own_directory() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'cd "$(dirname -- "$checksum_file")"' in text
    assert 'sha256sum -c "$(basename -- "$checksum_file")"' in text
    assert "lighthouse_d405_aprilgrid_joint_handeye_v1" in text


def test_offline_vins_replay_is_throttled_for_complete_pose_coverage() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    run_slam = text.split("run_slam()", 1)[1].split("verify_calibration()", 1)[0]

    # 默认仍是 0.5（VINS_RATE 只作逃生阀）；1.0× 会因丢帧系统性劣化原始 VIO。
    assert '--rate "${VINS_RATE:-0.5}"' in run_slam


def test_slam_failure_is_diagnosed_but_only_rc3_is_tolerable() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    run_slam = text.split("run_slam()", 1)[1].split("verify_calibration()", 1)[0]

    # rc==3（验收 FAIL）才做物种判别；rc==4（INFRASTRUCTURE）必须照旧中止。
    assert "local slam_status=$?" in run_slam
    assert "if (( slam_status == 3 )); then" in run_slam
    assert 'return "$slam_status"' in run_slam
    # 两个失败物种的分界与处置建议必须都在。
    assert "回放压力型" in text and "VINS_RATE=0.25" in text
    assert "与回放速率无关" in text
