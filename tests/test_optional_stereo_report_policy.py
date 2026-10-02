import inspect
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_script(name: str):
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fuse_mast3r_stereo_imu = _load_script("fuse_mast3r_stereo_imu")
fuse_mast3r_dual_ir_symmetric = _load_script("fuse_mast3r_dual_ir_symmetric")
prepare_dual_ir_eye_cache = _load_script("prepare_dual_ir_eye_cache")


def _stereo_report(
    *,
    name: str,
    scale: float | None = 1.0,
    result: str = "PASS",
    failures: list[str] | None = None,
    session: str = "/recording",
    trajectory: str = "/front/trajectory_imu_metric.csv",
    frame: str = "infrared_left_camera_i",
    baseline_m: float = 0.018083254,
    schema: str = "umi_mast3r_stereo_scale_v2",
    external_ground_truth_used: bool = False,
) -> dict:
    report = {
        "schema": schema,
        "result": result,
        "failures": failures or [],
        "slam_supervision": False,
        "external_ground_truth_used": external_ground_truth_used,
        "session": session,
        "trajectory": trajectory,
        "observation_frame": frame,
        "factory_stereo_calibration": {"baseline_m": baseline_m},
        "observations": [{"accepted": True, "source": name}],
        "report_path": f"/reports/{name}.json",
    }
    if scale is not None:
        report["scale_m_per_mast3r_unit"] = scale
    return report


def test_default_merge_policy_remains_strict_for_scale_disagreement():
    primary = _stereo_report(name="primary", scale=1.0)
    optional = _stereo_report(name="long_hops", scale=1.08)

    with pytest.raises(ValueError, match="scales disagree"):
        fuse_mast3r_stereo_imu.merge_stereo_reports(primary, [optional])


def test_opt_in_merge_policy_rejects_only_inconsistent_optional_window():
    primary = _stereo_report(name="primary", scale=1.0)
    inconsistent = _stereo_report(name="long_hops", scale=1.08)
    compatible = _stereo_report(name="dense10hz", scale=1.01)

    merged = fuse_mast3r_stereo_imu.merge_stereo_reports(
        primary,
        [inconsistent, compatible],
        optional_policy="reject_window",
    )

    assert merged["merged_report_count"] == 2
    assert [obs["source"] for obs in merged["observations"]] == [
        "primary",
        "dense10hz",
    ]
    assert merged["optional_report_rejections"] == [
        {
            "report_path": "/reports/long_hops.json",
            "reason": "scale_disagreement_over_5_percent",
            "result": "PASS",
            "failures": [],
        }
    ]


def test_opt_in_merge_policy_rejects_optional_fail_without_promoting_it():
    primary = _stereo_report(name="primary", scale=1.0)
    weak_window = _stereo_report(
        name="multisecond",
        scale=None,
        result="FAIL",
        failures=["right_stereo_scale_unobservable"],
    )

    merged = fuse_mast3r_stereo_imu.merge_stereo_reports(
        primary,
        [weak_window],
        optional_policy="reject_window",
    )

    assert merged["merged_report_count"] == 1
    assert merged["observations"] == primary["observations"]
    assert merged["optional_report_rejections"] == [
        {
            "report_path": "/reports/multisecond.json",
            "reason": "optional_report_failed",
            "result": "FAIL",
            "failures": ["right_stereo_scale_unobservable"],
        }
    ]


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda report: report.update(session="/other"), "different sessions"),
        (lambda report: report.update(trajectory="/other.csv"), "different source trajectories"),
        (lambda report: report.update(observation_frame="infrared_right_camera_i"), "different observation frames"),
        (lambda report: report["factory_stereo_calibration"].update(baseline_m=0.02), "different factory baselines"),
        (lambda report: report.update(schema="wrong_schema"), "unexpected report schema"),
        (lambda report: report.update(external_ground_truth_used=True), "GT independence"),
    ],
)
def test_opt_in_policy_keeps_identity_and_gt_errors_fatal(mutator, message):
    primary = _stereo_report(name="primary", scale=1.0)
    optional = _stereo_report(name="long_hops", scale=1.01)
    mutator(optional)

    with pytest.raises(ValueError, match=message):
        fuse_mast3r_stereo_imu.merge_stereo_reports(
            primary,
            [optional],
            optional_policy="reject_window",
        )


def test_primary_report_must_pass_even_when_optional_policy_is_enabled():
    primary = _stereo_report(
        name="primary",
        scale=None,
        result="FAIL",
        failures=["right_stereo_scale_unobservable"],
    )
    optional = _stereo_report(name="dense10hz", scale=1.0)

    with pytest.raises(ValueError, match="primary stereo report did not pass"):
        fuse_mast3r_stereo_imu.merge_stereo_reports(
            primary,
            [optional],
            optional_policy="reject_window",
        )


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda report: report.update(result="SKIPPED"), "unexpected optional stereo report result"),
        (lambda report: report["factory_stereo_calibration"].update(baseline_m=float("nan")), "baseline"),
        (lambda report: report["factory_stereo_calibration"].update(baseline_m=-0.01), "baseline"),
        (lambda report: report.update(scale_m_per_mast3r_unit=float("nan")), "scale"),
        (lambda report: report.update(scale_m_per_mast3r_unit=-1.0), "scale"),
    ],
)
def test_opt_in_policy_keeps_unknown_status_and_nonfinite_values_fatal(mutator, message):
    primary = _stereo_report(name="primary", scale=1.0)
    optional = _stereo_report(name="long_hops", scale=1.01)
    mutator(optional)

    with pytest.raises(ValueError, match=message):
        fuse_mast3r_stereo_imu.merge_stereo_reports(
            primary,
            [optional],
            optional_policy="reject_window",
        )


def test_opt_in_policy_validates_primary_scale_and_baseline_are_finite_positive():
    primary = _stereo_report(name="primary", scale=float("nan"))

    with pytest.raises(ValueError, match="scale"):
        fuse_mast3r_stereo_imu.merge_stereo_reports(
            primary,
            [],
            optional_policy="reject_window",
        )

    primary = _stereo_report(name="primary", scale=1.0, baseline_m=0.0)
    with pytest.raises(ValueError, match="baseline"):
        fuse_mast3r_stereo_imu.merge_stereo_reports(
            primary,
            [],
            optional_policy="reject_window",
        )


def test_load_eye_policy_is_explicit_and_strict_by_default():
    signature = inspect.signature(fuse_mast3r_dual_ir_symmetric.load_eye)

    assert signature.parameters["optional_stereo_policy"].default == "strict"


def test_load_eye_metadata_exposes_optional_report_rejections(tmp_path, monkeypatch):
    session = tmp_path / "recording"
    session.mkdir()
    (session / "d405_frames.csv").write_text("infrared_left_device_ms,infrared_left_mono\n", encoding="utf-8")
    eye_dir = tmp_path / "left"
    eye_dir.mkdir()

    rows = [
        "t_sec,x,y,z,qw,qx,qy,qz",
        "1.0,0,0,0,1,0,0,0",
        "2.0,1,0,0,1,0,0,0",
    ]
    (eye_dir / "trajectory_imu_metric.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")

    def write_report(name: str, report: dict):
        (eye_dir / name).write_text(json.dumps(report), encoding="utf-8")

    primary = _stereo_report(name="primary", scale=1.0, session=str(session), trajectory=str(eye_dir / "trajectory_imu_metric.csv"))
    inconsistent = _stereo_report(name="long_hops", scale=1.08, session=str(session), trajectory=str(eye_dir / "trajectory_imu_metric.csv"))
    dense = _stereo_report(name="dense10hz", scale=1.01, session=str(session), trajectory=str(eye_dir / "trajectory_imu_metric.csv"))
    multi = _stereo_report(name="multisecond", scale=None, result="FAIL", failures=["right_stereo_scale_unobservable"], session=str(session), trajectory=str(eye_dir / "trajectory_imu_metric.csv"))
    write_report("stereo_scale_bidirectional_report.json", primary)
    write_report("stereo_scale_long_hops_report.json", inconsistent)
    write_report("stereo_scale_dense10hz_report.json", dense)
    write_report("stereo_scale_multisecond_report.json", multi)
    write_report(
        "imu_scale_report.json",
        {
            "schema": "umi_mast3r_imu_scale_v1",
            "result": "PASS",
            "failures": [],
            "slam_supervision": False,
            "external_ground_truth_used": False,
            "scale": 1.0,
            "trajectory": str(eye_dir / "trajectory_imu_metric.csv"),
        },
    )

    fusion = fuse_mast3r_dual_ir_symmetric.fusion
    monkeypatch.setattr(fusion, "validate_imu_scale_report_binding", lambda *args: None)
    monkeypatch.setattr(
        fusion,
        "body_t_trajectory_camera_from_stereo_report",
        lambda *args: np.eye(4),
    )
    monkeypatch.setattr(
        fusion,
        "camera_epoch_to_monotonic",
        lambda *args: np.array([1.0, 2.0]),
    )
    monkeypatch.setattr(
        fusion,
        "refine_orientations",
        lambda rotations, *args, **kwargs: (rotations, {"result": "PASS"}),
    )

    _, metadata, _ = fuse_mast3r_dual_ir_symmetric.load_eye(
        eye_dir,
        "left",
        session,
        {"body_T_camera": np.eye(4), "td_s": 0.0},
        np.array([1.0, 2.0]),
        np.zeros((2, 3)),
        optional_stereo_policy="reject_window",
    )

    assert metadata["optional_stereo_policy"] == "reject_window"
    assert metadata["optional_report_rejections"] == [
        {
            "report_path": str((eye_dir / "stereo_scale_long_hops_report.json").resolve()),
            "reason": "scale_disagreement_over_5_percent",
            "result": "PASS",
            "failures": [],
        },
        {
            "report_path": str((eye_dir / "stereo_scale_multisecond_report.json").resolve()),
            "reason": "optional_report_failed",
            "result": "FAIL",
            "failures": ["right_stereo_scale_unobservable"],
        },
    ]


def test_prepare_right_cache_policy_allows_derive_rc2_only_for_optional_windows():
    assert prepare_dual_ir_eye_cache.STRICT_DERIVE_STEREO_RETURNCODES == (0,)
    assert prepare_dual_ir_eye_cache.OPTIONAL_DERIVE_STEREO_RETURNCODES == (0, 2)
    assert prepare_dual_ir_eye_cache.PRIMARY_STEREO_REPORTS == (
        ("stereo_scale_bidirectional_report.json", "stereo_scale_right_report.json"),
    )
    assert set(prepare_dual_ir_eye_cache.OPTIONAL_STEREO_REPORTS) == {
        ("stereo_scale_long_hops_report.json", "stereo_scale_long_hops_right_report.json"),
        ("stereo_scale_dense10hz_report.json", "stereo_scale_dense10hz_right_report.json"),
        ("stereo_scale_multisecond_report.json", "stereo_scale_multisecond_right_report.json"),
    }
