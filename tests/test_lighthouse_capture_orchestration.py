from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "capture_docker2_with_lighthouse.sh"
)
CALIBRATION_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "calibrate_lighthouse_aprilgrid_session.sh"
)
PREFLIGHT_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "lighthouse_umi_preflight.py"
)


def test_tracker_starts_after_slow_d405_setup_but_before_formal_capture() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    tracker_start = text.index('lighthouse_reference_check.py" record')
    tracker_ready_wait = text.index('if [[ "$TRACKER_READY" -ne 1 ]]')
    d405_start = text.index("run_d405_capture &")
    marker_wait = text.index('grep -Fq "$D405_READY_MARKER"')
    guided_formal_wait = text.index('if [[ "${APRILGRID_GUIDED_CAPTURE:-0}" == 1 ]]')

    assert d405_start < marker_wait < tracker_start < tracker_ready_wait < guided_formal_wait
    assert 'D405_READY_MARKER="[全流采集] 预热:"' in text
    assert "--warmup 0" in text
    assert "+ 30.0" in text


def test_joint_capture_enforces_tracker_geometry_and_time_overlap() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'tracker_integrity.get("status") != "PASS"' in text
    assert 'lighthouse_reference_check.py" overlap' in text
    assert 'overlap_report.get("status") != "PASS"' in text
    assert '--config "$LIGHTHOUSE_RUNTIME_CONFIG"' in text
    assert "--lighthouse-gen 2" in text
    assert (
        'install -m 0644 "$FROZEN_LIGHTHOUSE_CONFIG" "$LIGHTHOUSE_RUNTIME_CONFIG"'
        in text
    )
    assert "libsurvive_config_frozen_v13.json" in text
    assert "8b2f50ed5bf51ee4366d155dd99983a7a2791b1de4b7995d375d5036b94668d0" in text
    assert '"Lighthouse generation forced to 2"' in text


def test_preflight_uses_same_consensus_lighthouse_world_v13() -> None:
    text = PREFLIGHT_SCRIPT.read_text(encoding="utf-8")

    assert "lighthouse_recalibration_world_v13_20260926_consensus" in text
    assert "libsurvive_config_frozen_v13.json" in text
    assert "8b2f50ed5bf51ee4366d155dd99983a7a2791b1de4b7995d375d5036b94668d0" in text


def test_d405_quality_failure_preserves_moved_session_for_diagnostics() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'D405正式采集或文件搬运失败' not in text
    assert 'D405采集质量验收失败，但DB3已搬运完成' in text
    assert 'printf \'%s\\n\' "$SESSION" > "$OUT_DIR/d405_session.txt"' in text


def test_aprilgrid_guided_capture_has_a_visible_timed_prompt() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'APRILGRID_GUIDED_CAPTURE:-0' in text
    assert 'scripts/aprilgrid_motion_prompt.py"' in text
    assert '--duration "$DURATION_S"' in text
    assert 'D405_FORMAL_MARKER="[全流采集] 正式采集"' in text
    assert 'grep -Fq "$D405_FORMAL_MARKER"' in text


def test_external_calibration_fixes_independent_imu_tracker_offset() -> None:
    text = CALIBRATION_SCRIPT.read_text(encoding="utf-8")

    assert "estimate_lighthouse_imu_time_offset.py" in text
    assert '--tracker-query-offset-ms "$tracker_query_offset_ms"' in text
    assert '--body-camera-config "$stereo_config"' in text
    assert '"time_sync": time_sync' in text
    assert 'payload.get("calibration_target_frame") != "docker2_vins_body"' in text
    assert 'payload.get("time_offset_policy")' in text
