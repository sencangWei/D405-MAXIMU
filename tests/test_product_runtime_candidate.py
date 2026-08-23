from pathlib import Path

import numpy as np
import yaml

from product_calibration.runtime_candidate import (
    build_stage6_runtime,
    vins_body_t_camera,
)


ROOT = Path(__file__).parents[1]


def _candidate_report() -> dict:
    return {"result": "PASS", "release_eligible": True, "candidate": {
        "td_s": -0.00931226565298875,
        "T_cam0_imu": [
            [0.9998767831427768, -0.01565347068844075, -0.0011778741269478292, 0.013989832302965912],
            [0.015668745543103657, 0.9997746293087866, 0.014324140587947903, 0.010958984649418363],
            [0.0009533861538111327, -0.014340831422338971, 0.9998967104701153, -0.027965760629541266],
            [0.0, 0.0, 0.0, 1.0],
        ],
        "T_cam1_imu": [
            [0.9998770678443301, -0.015673077656470746, -0.0004514817809858759, -0.004031353260102705],
            [0.015677893571266588, 0.9997772264115741, 0.014131567568632705, 0.010953524360784688],
            [0.00022989604685864397, -0.014136908627879967, 0.9999000424853743, -0.027813646901480513],
            [0.0, 0.0, 0.0, 1.0],
        ],
    }}


def _runtime_template(root: Path) -> Path:
    config = root / "config/product_live_stm32"
    config.mkdir(parents=True)
    (config / "vins_config.yaml").write_text(
        "%YAML:1.0\n"
        'output_path: "/tmp/old/"\n'
        'cam0_calib: "left.yaml"\ncam1_calib: "right.yaml"\n'
        "td: -0.0117\n"
        "body_T_cam0: !!opencv-matrix\n   rows: 4\n   cols: 4\n   dt: d\n"
        "   data: [ 1, 0, 0, 0,\n           0, 1, 0, 0,\n"
        "           0, 0, 1, 0,\n           0, 0, 0, 1 ]\n"
        "body_T_cam1: !!opencv-matrix\n   rows: 4\n   cols: 4\n   dt: d\n"
        "   data: [ 1, 0, 0, 0,\n           0, 1, 0, 0,\n"
        "           0, 0, 1, 0,\n           0, 0, 0, 1 ]\n"
        'pose_graph_save_path: "/tmp/old/pose_graph/"\n',
        encoding="utf-8",
    )
    (root / "config/devices_product_live_stm32.yaml").write_text(
        yaml.safe_dump({"units": [{
            "imu": {"port": "/dev/old", "protocol": "old", "calibration": ""},
            "camera": {"serial": "old"},
        }]}, sort_keys=False),
        encoding="utf-8",
    )
    return root


def test_runtime_mapping_reconstructs_selected_product_live_extrinsics():
    report = _candidate_report()
    expected0 = np.asarray([
        [0.999951001263, 0.004000299257, -0.009054980843, -0.011649026284],
        [-0.008888369803, -0.039869269088, -0.999165370829, -0.027337390226],
        [-0.004357975959, 0.999196897007, -0.039831759405, -0.012371766409],
        [0.0, 0.0, 0.0, 1.0],
    ])
    np.testing.assert_allclose(
        vins_body_t_camera(report["candidate"]["T_cam0_imu"]),
        expected0,
        atol=2e-12,
    )


def test_stage6_runtime_is_isolated_and_uses_factory_intrinsics(tmp_path):
    stereo = {
        "result": "PASS",
        "release_eligible": True,
        "runtime_policy": "USE_INTEL_FACTORY_RECTIFIED_INTRINSICS",
        "metrics": {
            "cam0_intrinsics": {"fx": 647.519775, "fy": 647.519775,
                                "cx": 638.534302, "cy": 369.768250},
            "cam1_intrinsics": {"fx": 647.519775, "fy": 647.519775,
                                "cx": 638.534302, "cy": 369.768250},
        },
    }
    runtime = build_stage6_runtime(
        destination=tmp_path / "candidate",
        runtime_root=_runtime_template(tmp_path / "runtime"),
        identity={"devices": {
            "d405": {"serial": "260322273737"},
            "imu_port": "/dev/serial/by-id/example",
        }},
        stereo=stereo,
        camera_imu=_candidate_report(),
    )

    vins = Path(runtime["vins_config"]).read_text(encoding="utf-8")
    devices = yaml.safe_load(Path(runtime["device_config"]).read_text(encoding="utf-8"))
    left = (tmp_path / "candidate/left.yaml").read_text(encoding="utf-8")
    assert "td: -0.009312266" in vins
    assert "fx: 647.519775000000" in left
    assert devices["units"][0]["camera"]["serial"] == "260322273737"
    assert devices["units"][0]["imu"]["calibration"] == ""
    assert not (tmp_path / "candidate/imu_accel_runtime.yaml").exists()
    sources = yaml.safe_load(
        (tmp_path / "candidate/source_stage_values.yaml").read_text(encoding="utf-8")
    )
    assert sources["imu_runtime_policy"]["accelerometer_matrix"] == "NOT_APPLIED"
    assert "imu_multipose" not in sources
    assert (tmp_path / "candidate/source_stage_values.yaml").is_file()
    assert Path(runtime["manifest"]).is_file()


def test_stage6_runtime_rejects_diagnostic_only_stage_reports(tmp_path):
    stereo = {
        "result": "PASS",
        "release_eligible": False,
        "runtime_policy": "USE_INTEL_FACTORY_RECTIFIED_INTRINSICS",
        "metrics": {},
    }
    camera_imu = _candidate_report()
    camera_imu["release_eligible"] = False

    import pytest
    from product_calibration.workflow import WorkflowError

    with pytest.raises(WorkflowError, match="正式采集健康门"):
        build_stage6_runtime(
            destination=tmp_path / "candidate-camera-imu",
            runtime_root=tmp_path / "runtime-unused",
            identity={},
            stereo={**stereo, "release_eligible": True},
            camera_imu=camera_imu,
        )
    with pytest.raises(WorkflowError, match="正式留出验收"):
        build_stage6_runtime(
            destination=tmp_path / "candidate-stereo",
            runtime_root=tmp_path / "runtime-unused",
            identity={},
            stereo=stereo,
            camera_imu=_candidate_report(),
        )
