import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest
import yaml


MODULE_PATH = Path(__file__).with_name("device2_d405_control.py")
SPEC = importlib.util.spec_from_file_location("device2_d405_control", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_candidate(tmp_path: Path) -> Path:
    session_root = (
        tmp_path / "calibration_sessions" / MODULE.CALIB_PRODUCT_ID
    )
    attempt = session_root / "world_z/attempts/attempt_002"
    runtime = attempt / "candidate_runtime"
    runtime.mkdir(parents=True)
    files = {
        "left.yaml": "camera: left\n",
        "right.yaml": "camera: right\n",
        "source_stage_values.yaml": "source: signed\n",
        "vins_config.yaml": "td: -0.009109323\n",
        "devices.yaml": yaml.safe_dump({
            "units": [{
                "role": "realtime_vio",
                "camera": {"serial": "260322279785"},
                "imu": {
                    "port": "/dev/serial/by-id/device2",
                    "protocol": "stm32_combined_v1",
                },
            }],
        }),
    }
    for name, content in files.items():
        (runtime / name).write_text(content, encoding="utf-8")
    manifest = {
        "format_version": 1,
        "result": "CANDIDATE",
        "activation": "STAGE6_ONLY_DO_NOT_OVERWRITE_PRODUCT_LIVE",
        "td_s": -0.009109323,
        "files": {name: sha(runtime / name) for name in files},
    }
    (runtime / "manifest.yaml").write_text(
        yaml.safe_dump(manifest), encoding="utf-8"
    )
    report = {
        "result": "PASS",
        "activation": "CANDIDATE_ONLY_NOT_INSTALLED",
        "mode": "live_capture",
        "release_eligible": False,
        "selection": {"selected_model": "IDENTITY_NO_CORRECTION_REQUIRED"},
        "fit": {"R_world_z_from_vins_world": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]},
        "runtime_candidate": {
            "directory": str(runtime),
            "manifest": str(runtime / "manifest.yaml"),
            "vins_config": str(runtime / "vins_config.yaml"),
            "device_config": str(runtime / "devices.yaml"),
            "manifest_sha256": sha(runtime / "manifest.yaml"),
        },
    }
    report_path = attempt / "report.yaml"
    report_path.write_text(
        yaml.safe_dump(report), encoding="utf-8"
    )
    canonical_report = session_root / "world_z/report.yaml"
    canonical_report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(report_path, canonical_report)
    session = {
        "product_id": MODULE.CALIB_PRODUCT_ID,
        "results": {
            "world_z": {
                "result": "PASS",
                "artifact": "world_z/report.yaml",
                "artifact_sha256": sha(canonical_report),
            }
        },
    }
    (session_root / "session.yaml").write_text(
        yaml.safe_dump(session), encoding="utf-8"
    )
    return runtime


def test_candidate_runtime_is_hash_bound_and_identity_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "DATA", tmp_path)
    runtime = make_candidate(tmp_path)

    candidate = MODULE.validate_candidate_runtime(
        runtime,
        serial="260322279785",
        port=Path("/dev/serial/by-id/device2"),
    )

    assert candidate["td_s"] == pytest.approx(-0.009109323)
    assert candidate["vins_config"] == runtime / "vins_config.yaml"
    assert candidate["device_config"] == runtime / "devices.yaml"


def test_candidate_runtime_rejects_tampered_vins_config(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "DATA", tmp_path)
    runtime = make_candidate(tmp_path)
    (runtime / "vins_config.yaml").write_text("td: 0.0\n", encoding="utf-8")

    with pytest.raises(MODULE.Blocked, match="哈希"):
        MODULE.validate_candidate_runtime(
            runtime,
            serial="260322279785",
            port=Path("/dev/serial/by-id/device2"),
        )


def test_candidate_runtime_rejects_foreign_self_signed_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "DATA", tmp_path)
    runtime = make_candidate(tmp_path)
    forged = tmp_path / "forged/attempt_999/candidate_runtime"
    forged.parent.mkdir(parents=True)
    shutil.copytree(runtime, forged)
    shutil.copy2(runtime.parent / "report.yaml", forged.parent / "report.yaml")

    with pytest.raises(MODULE.Blocked, match="不属于当前产品档案"):
        MODULE.validate_candidate_runtime(
            forged,
            serial="260322279785",
            port=Path("/dev/serial/by-id/device2"),
        )


def test_candidate_runtime_rejects_tampered_canonical_report(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "DATA", tmp_path)
    runtime = make_candidate(tmp_path)
    canonical = runtime.parents[2] / "report.yaml"
    canonical.write_text("result: FAIL\n", encoding="utf-8")

    with pytest.raises(MODULE.Blocked, match="产物哈希"):
        MODULE.validate_candidate_runtime(
            runtime,
            serial="260322279785",
            port=Path("/dev/serial/by-id/device2"),
        )


def test_device_config_rejects_extra_units(tmp_path):
    path = tmp_path / "devices.yaml"
    unit = {
        "role": "realtime_vio",
        "camera": {"serial": "260322279785"},
        "imu": {
            "port": "/dev/serial/by-id/device2",
            "protocol": "stm32_combined_v1",
        },
    }
    path.write_text(yaml.safe_dump({"units": [unit, unit]}), encoding="utf-8")

    with pytest.raises(MODULE.Blocked, match="只能包含1个"):
        MODULE.validate_device_config(
            path, "260322279785", Path("/dev/serial/by-id/device2")
        )


def test_candidate_capture_binding_rejects_changed_db3(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    db3 = session / "capture.db3"
    db3.write_bytes(b"original")
    (session / "d405_frames.csv").write_text("frames", encoding="utf-8")
    imu = session / "external_imu/imu.bin"
    imu.parent.mkdir()
    imu.write_bytes(b"imu-original")
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    files = {}
    for name in ("manifest.yaml", "report.yaml", "vins_config.yaml", "devices.yaml"):
        path = candidate_root / name
        path.write_text(name, encoding="utf-8")
        files[name] = path
    candidate = {
        "source": candidate_root,
        "manifest_path": files["manifest.yaml"],
        "report_path": files["report.yaml"],
        "vins_config": files["vins_config.yaml"],
        "device_config": files["devices.yaml"],
        "td_s": -0.009,
    }
    MODULE.candidate_capture_binding(session, candidate, "260322279785")
    imu.write_bytes(b"imu-changed")

    with pytest.raises(MODULE.Blocked, match="imu.bin"):
        MODULE.verify_candidate_capture_binding(
            session, candidate, "260322279785"
        )


def test_direct_child_rejects_ancestor_symlink(tmp_path):
    real = tmp_path / "real"
    recordings = real / "recordings"
    session = recordings / "session"
    session.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(MODULE.Blocked, match="符号链接"):
        MODULE.direct_child(
            alias / "recordings/session",
            alias / "recordings",
            must_exist=True,
            label="test",
        )


def test_candidate_capture_binding_rejects_added_second_db3(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "capture.db3").write_bytes(b"original")
    (session / "d405_frames.csv").write_text("frames", encoding="utf-8")
    imu = session / "external_imu/imu.bin"
    imu.parent.mkdir()
    imu.write_bytes(b"imu")
    candidate_root = tmp_path / "candidate"
    candidate_root.mkdir()
    candidate = {"source": candidate_root, "td_s": -0.009}
    for key, name in (
        ("manifest_path", "manifest.yaml"),
        ("report_path", "report.yaml"),
        ("vins_config", "vins_config.yaml"),
        ("device_config", "devices.yaml"),
    ):
        path = candidate_root / name
        path.write_text(name, encoding="utf-8")
        candidate[key] = path
    MODULE.candidate_capture_binding(session, candidate, "260322279785")
    (session / "larger.db3").write_bytes(b"larger-unbound-database")

    with pytest.raises(MODULE.Blocked, match="只能包含1个DB3"):
        MODULE.verify_candidate_capture_binding(
            session, candidate, "260322279785"
        )


def test_candidate_ab_commands_are_explicit():
    capture = MODULE.parser().parse_args([
        "candidate-capture", "/data/candidate", "60",
    ])
    post = MODULE.parser().parse_args([
        "candidate-postprocess", "/data/candidate", "/data/session",
        "/data/output", "--variant", "baseline",
    ])
    realtime = MODULE.parser().parse_args([
        "candidate-realtime", "/data/candidate",
    ])
    realtime_record = MODULE.parser().parse_args([
        "candidate-realtime-record", "/data/candidate", "60",
    ])

    assert capture.command == "candidate-capture"
    assert capture.duration == 60.0
    assert post.variant == "baseline"
    assert realtime.command == "candidate-realtime"
    assert realtime_record.command == "candidate-realtime-record"
    assert realtime_record.duration == 60.0


def test_formal_runtime_bundle_is_hash_bound_and_launcher_is_pinned():
    project = Path(__file__).resolve().parents[1]
    runtime = project / "formal_runtime_calibration"
    manifest = yaml.safe_load(
        (runtime / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert manifest["result"] == "PASS"
    assert manifest["release_id"] == "UMI_DEVICE2_D405_PRODUCT_V1_20260829"
    assert manifest["device_set_id"] == MODULE.DEVICE_SET_ID
    assert manifest["d405_serial"] == "260322279785"
    for name, expected in manifest["files"].items():
        assert sha(runtime / name) == expected
    launcher = (project / "umi-device2-d405.sh").read_text(encoding="utf-8")
    assert "device2-c48df736-d405-product-v1-20260829" in launcher
    assert "install-bundled-runtime-calibration" in launcher


def test_candidate_live_runner_keeps_product_entry_immutable_and_single_owner():
    builder_path = Path(__file__).with_name("build_candidate_live_runner.py")
    spec = importlib.util.spec_from_file_location("candidate_builder", builder_path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source_path = Path(
        "/home/robot/releases/ego_vio_humble/product_v1_20260824/"
        "run_vins_realtime.sh"
    )
    original = source_path.read_text(encoding="utf-8")
    transformed = builder.transform(original)

    assert source_path.read_text(encoding="utf-8") == original
    assert 'nice -n "${EGO_VIO_VIEWER_NICE:-10}"' in transformed
    assert '"$ROOT/scripts/capture_d405_720p_rgb_stereo_ir.py"' in transformed
    assert "--publish-vins --no-preview" in transformed
    assert "EGO_VIO_RECORD_DURATION_S" in transformed
    assert transformed.count("wait_with_viewer_supervision") == 3


def test_candidate_live_io_patch_preserves_rgb_preview_and_settles_display_only():
    patch_path = Path(__file__).with_name("patch_candidate_live_io.py")
    spec = importlib.util.spec_from_file_location("candidate_io_patch", patch_path)
    live_patch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live_patch)
    product = Path(
        "/home/robot/releases/ego_vio_humble/product_v1_20260824"
    )
    capture = live_patch.patch_capture(
        (product / "scripts/capture_d405_720p_rgb_stereo_ir.py").read_text(
            encoding="utf-8"
        )
    )
    assert 'cv2.COLOR_YUV2BGR_YUY2' in capture
    assert "def color_frame_to_bgr(frame)" in capture
    assert "color_yuyv.dtype == np.uint16" in capture
    assert "color=color_frame_to_bgr(" in capture
    assert 'preview_topic="/rgb_preview/image_raw"' in capture
    assert 'gripper_topic="/gripper/state"' in capture
    assert "EGO_VIO_IMU_LEAD_GUARD_MS" in capture
    patch_source = patch_path.read_text(encoding="utf-8")
    assert 'gripper_topic: str = "/gripper/state"' in patch_source
    assert "EGO_VIO_VIEWER_INITIAL_SETTLE_S" in patch_source
    assert "Raw and corrected CSV evidence remains untouched" in patch_source
    assert "log_gripper_state" in patch_source
    assert "**编码器角度**：{angle}" in patch_source
    assert "**夹爪开合距离**：{gap}" in patch_source
    assert 'f"## 编码器角度' not in patch_source
    assert 'f"；夹爪显示 {args.gripper_topic}"' in patch_source
    assert 'f\' gripper={counts["gripper"]}\'' in patch_source


def test_candidate_live_interrupt_finalizes_binding(tmp_path, monkeypatch):
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    binding = {"schema": "umi_candidate_realtime_v1", "result": "STARTING"}

    result = MODULE.run_candidate_live(
        env={}, run_dir=tmp_path, binding_document=binding
    )

    written = yaml.safe_load(
        (tmp_path / "candidate_realtime_binding.yaml").read_text(encoding="utf-8")
    )
    assert result == 130
    assert written["result"] == "STOPPED_BY_USER"
    assert written["exit_code"] == 130


def test_formal_live_interrupt_is_clean(monkeypatch, capsys):
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    result = MODULE.run_product_live({})

    assert result == 130
    assert "实时链已由操作员停止" in capsys.readouterr().err


def test_wrapper_pins_direct_nondefault_replay_executable():
    wrapper = Path(__file__).with_name(
        "run_slam_postprocess_configurable.sh"
    ).read_text(encoding="utf-8")
    assert (
        'REPLAY_EXECUTABLE="$BUILD_ROOT/loop_ws/build/'
        'vins_fusion_ros2/db3_replay_cpp"'
    ) in wrapper
    assert "2a869141fc9dd46ce23c92fe06300d5c222f46ee9019245365ba94d0b7964973" in wrapper
    assert 'verify_hash PRODUCT_LIVE_REPLAY_SHA256 "$REPLAY_EXECUTABLE"' not in wrapper
