from audit_optics import audit


def test_channels_are_not_station_indices(tmp_path):
    path = tmp_path / "sample.rec"
    path.write_text("0 3 LH_POSE 0 0 0 1 0 0 0 2640831677\n"
                    "0 WM0 B 3 9 100 0 0.2\n"
                    "1 WM0 RA 9 0 0.01 0\n")
    result = audit(path)
    assert result["channel_station_ids"] == {"3": ["2640831677"]}
    assert result["optical_channels"] == {"3": 1}
    assert result["residual_by_station_axis"]["0/0"]["n"] == 1


def test_quaternion_sign_is_not_rotation_jump(tmp_path):
    path = tmp_path / "sample.rec"
    path.write_text("1 WM0 POSE 0 0 0 1 0 0 0\n1.008 WM0 POSE 0 0 0 -1 0 0 0\n")
    assert audit(path)["recorded_pose_jumps"] == []


def test_raw_and_final_streams_are_separate(tmp_path):
    path = tmp_path / "sample.rec"
    path.write_text("1 WM0 POSE 0 0 0 1 0 0 0\n"
                    "1 WM0-raw-obs EXTERNAL_POSE 1 0 0 1 0 0 0\n"
                    "1.008 WM0 POSE 0 0 0 1 0 0 0\n"
                    "1.008 WM0-raw-obs EXTERNAL_POSE 1.1 0 0 1 0 0 0\n")
    jumps = audit(path)["recorded_pose_jumps"]
    assert len(jumps) == 1
    assert jumps[0]["stream"] == "WM0-raw-obs"
    assert abs(jumps[0]["position_step_mm"] - 100) < 1e-6


def test_long_gap_not_classed_short_jump(tmp_path):
    path = tmp_path / "sample.rec"
    path.write_text("1 WM0 POSE 0 0 0 1 0 0 0\n2 WM0 POSE 1 0 0 1 0 0 0\n")
    assert audit(path)["recorded_pose_jumps"] == []


def test_same_record_time_does_not_hide_discontinuity(tmp_path):
    path = tmp_path / "sample.rec"
    path.write_text("1 WM0 POSE 0 0 0 1 0 0 0\n1 WM0 POSE 0.03 0 0 1 0 0 0\n")
    jumps = audit(path)["recorded_pose_jumps"]
    assert len(jumps) == 1
    assert jumps[0]["record_dt_ms"] == 0
