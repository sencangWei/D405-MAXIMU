import importlib.util
import sys
import types
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import lsqr as scipy_lsqr
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "stereo_window_shape_refiner", ROOT / "scripts" / "stereo_window_shape_refiner.py"
)
refiner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = refiner
SPEC.loader.exec_module(refiner)


def factor(rank=3, available=True):
    sensitivity = np.zeros((rank, 24), dtype=float)
    for row in range(rank):
        sensitivity[row, row] = 1.0
    return {
        "diagnostic_only": True,
        "available": available,
        "solver_accepted": True,
        "not_admissible_for_graph": True,
        "frames": 9,
        "gauge": "all_relative_centers_mapped_through_first_camera_rotation",
        "compact_sqrt_sensitivity": sensitivity.tolist(),
        "reference_center_vector_m": np.linspace(0.0, 0.08, 24).tolist(),
        "affine_offset": np.full(rank, 0.1).tolist(),
        "metadata": {
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
            "available_for_graph": False,
        },
    }


def force_factor(rank=3, affine_value=100.0):
    item = factor(rank=rank)
    item["affine_offset"] = np.full(rank, affine_value).tolist()
    item["compact_sqrt_sensitivity"] = (np.eye(rank, 24) * 100.0).tolist()
    return item


def edge(first, second, group=None, part=None, source=refiner.SHAPE_ENDPOINT_SOURCE):
    row = {
        "accepted": True,
        "first_index": first,
        "second_index": second,
        "metric_source": source,
        "metric_displacement_camera_i_m": [0.0, 0.0, 0.0],
    }
    if group is not None:
        row.update(
            {
                "correlated_factor_group_id": group,
                "correlated_factor_group_part": part,
                "correlated_factor_group_size": 2,
            }
        )
    return row


def make_native(tmp_path, *, mutate_accepted=False, fail_after_lsqr=False, wrong_layout=False, iterations=4):
    native = types.SimpleNamespace()
    native_file = tmp_path / "fake_native.py"
    native_file.write_text("# fake native\n", encoding="utf-8")
    native.__file__ = str(native_file)
    native.lsqr = scipy_lsqr
    native.lsqr_calls = []

    def regular_node_indices(position_count, node_stride):
        return np.arange(0, position_count, node_stride, dtype=int)

    def refine_positions_visual_inertial(
        positions,
        camera_rotations,
        observations,
        visual_times_mono=None,
        imu_times=None,
        gyro_body=None,
        accel_body=None,
        body_t_camera=None,
        td_s=0.0,
        node_stride=4,
        max_correction_m=0.02,
        correction_node_indices=None,
        relative_motion_positions_body=None,
        relative_motion_valid=None,
        relative_motion_sigma_m=0.008,
        reference_stereo_scale=None,
        visual_position_sigma_m=0.020,
        correction_cap_mode="global",
        correction_interpolation_mode="linear",
        solve_metric_scale=False,
    ):
        accepted = [
            observation
            for observation in observations
            if observation.get("accepted")
            and "metric_displacement_camera_i_m" in observation
        ]
        if correction_node_indices is None:
            nodes = sorted(
                set(regular_node_indices(len(positions), node_stride).tolist())
                | {int(item[key]) for item in accepted for key in ("first_index", "second_index")}
            )
        else:
            nodes = np.asarray(correction_node_indices, dtype=int).tolist()
        node_count = len(nodes)
        scale_active = bool(
            solve_metric_scale
            and np.max(np.linalg.norm(np.asarray(positions) - np.asarray(positions)[0], axis=1)) > 0.001
        )
        relative = relative_motion_positions_body is not None
        static_pairs = 1
        rows, cols, start = refiner.shape_system.expected_shape(
            node_count,
            len(accepted),
            relative_motion=relative,
            static_pair_count=static_pairs,
            scale_active=scale_active,
        )
        if wrong_layout == "negative":
            rows = start + 3 * len(accepted) + 5
        elif wrong_layout == "nondivisible":
            rows += 1
        matrix = np.zeros((rows, cols), dtype=float)
        target = np.zeros(rows, dtype=float)
        for column in range(min(rows, cols)):
            matrix[column, column] = 1.0
            target[column] = 0.01 * (column + 1)
        for _ in range(iterations):
            result = native.lsqr(csr_matrix(matrix), target, atol=1e-10, btol=1e-10, iter_lim=5000)
            native.lsqr_calls.append((matrix.shape, target.shape, result[0].copy()))
            if mutate_accepted:
                accepted[0]["first_index"] = int(accepted[0]["first_index"]) + 1
        if fail_after_lsqr:
            raise RuntimeError("native failed after lsqr")
        refined = np.asarray(positions, dtype=float).copy()
        solution = result[0]
        for ordinal, node in enumerate(nodes):
            refined[node] += solution[3 * ordinal : 3 * ordinal + 3]
        return refined, {
            "nodes": node_count,
            "linear_system_rows": rows,
            "linear_system_unknowns": cols,
            "position_correction_requested_max_m": float(np.linalg.norm(solution[: 3 * node_count])),
            "position_correction_max_m": float(np.linalg.norm(refined - positions)),
            "position_correction_limit_m": max_correction_m,
            "correction_scale": 1.0,
            "correction_clipped_frames": 0,
        }

    native.regular_node_indices = regular_node_indices
    native.refine_positions_visual_inertial = refine_positions_visual_inertial
    return native


def base_inputs():
    positions = np.column_stack((np.linspace(0.0, 0.14, 15), np.zeros(15), np.zeros(15)))
    return {
        "positions": positions,
        "camera_rotations": Rotation.identity(len(positions)),
        "observations": [
            edge(0, 4, "g0", 0),
            edge(4, 8, "g0", 1),
            edge(2, 6, None, None, "native_stereo"),
            edge(6, 10, None, None, "native_stereo"),
        ],
        "visual_times_mono": np.arange(len(positions), dtype=float) / 30.0,
        "imu_times": np.arange(100, dtype=float) / 400.0,
        "gyro_body": np.zeros((100, 3)),
        "accel_body": np.zeros((100, 3)),
        "body_t_camera": np.eye(4),
        "td_s": 0.0,
        "relative_motion_positions_body": positions.copy(),
    }


def group(group_id="g0", indices=None, rank=3, available=True):
    return {
        "group_id": group_id,
        "indices9": list(range(9)) if indices is None else indices,
        "factor": factor(rank=rank, available=available),
    }


def group_with_factor(group_id="g0", indices=None, item=None):
    return {
        "group_id": group_id,
        "indices9": list(range(9)) if indices is None else indices,
        "factor": factor() if item is None else item,
    }


def sha(native):
    return refiner.file_sha256(native.__file__)


def load_actual_native():
    path = ROOT / "scripts" / "fuse_mast3r_stereo_imu.py"
    spec = importlib.util.spec_from_file_location("actual_fuse_mast3r_stereo_imu_for_shape_refiner", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def actual_inputs():
    count = 60
    times = np.arange(count, dtype=float) / 30.0
    positions = np.column_stack(
        (
            0.010 * np.arange(count),
            0.001 * np.sin(np.arange(count) / 5.0),
            0.0005 * np.cos(np.arange(count) / 7.0),
        )
    )
    rotations = Rotation.from_rotvec(
        np.column_stack(
            (
                0.002 * np.sin(np.arange(count) / 11.0),
                -0.003 * np.cos(np.arange(count) / 13.0),
                0.010 * np.arange(count),
            )
        )
    )
    observations = []
    for first, second, group_id, part, source in [
        (0, 4, "g0", 0, refiner.SHAPE_ENDPOINT_SOURCE),
        (4, 8, "g0", 1, refiner.SHAPE_ENDPOINT_SOURCE),
        (8, 12, None, None, "native_stereo"),
        (12, 16, None, None, "native_stereo"),
    ]:
        camera_delta = rotations[first].inv().apply(positions[second] - positions[first])
        row = {
            "accepted": True,
            "first_index": first,
            "second_index": second,
            "metric_source": source,
            "metric_displacement_camera_i_m": camera_delta.tolist(),
            "pnp_inlier_ratio": 0.9,
            "rotation_error_deg": 0.1,
            "scale": 1.0,
            "bidirectional_relative_disagreement": 0.0,
        }
        if group_id is not None:
            row.update(
                {
                    "correlated_factor_group_id": group_id,
                    "correlated_factor_group_part": part,
                    "correlated_factor_group_size": 2,
                }
            )
        observations.append(row)
    imu_times = np.arange(-0.10, times[-1] + 0.20, 0.0025)
    gyro = np.zeros((len(imu_times), 3), dtype=float)
    accel = np.tile([0.0, 0.0, 9.80665], (len(imu_times), 1))
    body_t_camera = np.eye(4)
    body_t_camera[:3, :3] = Rotation.from_rotvec([0.05, -0.03, 0.02]).as_matrix()
    body_t_camera[:3, 3] = [0.012, -0.018, 0.027]
    return {
        "positions": positions,
        "camera_rotations": rotations,
        "observations": observations,
        "visual_times_mono": times,
        "imu_times": imu_times,
        "gyro_body": gyro,
        "accel_body": accel,
        "body_t_camera": body_t_camera,
        "td_s": 0.0,
        "node_stride": 10,
        "correction_node_indices": np.array([0, 4, 8, 12, 16, count - 1], dtype=int),
        "relative_motion_positions_body": positions.copy(),
        "solve_metric_scale": False,
    }


def run_actual(native, shape_groups, mode, **overrides):
    inputs = actual_inputs()
    inputs.update(overrides)
    return refiner.refine_positions_visual_inertial_with_shape(
        native,
        shape_groups,
        mode,
        expected_native_sha256=sha(native),
        **inputs,
    )


def test_zero_group_calls_original_unchanged_no_hash_hook_or_extra_report(tmp_path):
    native = make_native(tmp_path)
    inputs = base_inputs()
    original_lsqr = native.lsqr
    refined, report = refiner.refine_positions_visual_inertial_with_shape(
        native, [], refiner.GROUPED_SHAPE_MODE, **inputs
    )
    direct_refined, direct_report = native.refine_positions_visual_inertial(**inputs)
    np.testing.assert_allclose(refined, direct_refined)
    assert report == direct_report
    assert native.lsqr is original_lsqr
    assert "stereo_window_shape_refiner" not in report


def test_grouped_and_endpoint_control_use_identical_node_union_and_preserve_inputs(tmp_path):
    native = make_native(tmp_path)
    inputs = base_inputs()
    observations_before = [dict(item) for item in inputs["observations"]]
    positions_before = inputs["positions"].copy()
    expected = sha(native)
    _, endpoint = refiner.refine_positions_visual_inertial_with_shape(
        native, [group()], refiner.ENDPOINT_CONTROL_MODE, expected_native_sha256=expected, **inputs
    )
    _, grouped = refiner.refine_positions_visual_inertial_with_shape(
        native, [group()], refiner.GROUPED_SHAPE_MODE, expected_native_sha256=expected, **inputs
    )
    ep_diag = endpoint["stereo_window_shape_refiner"]
    gr_diag = grouped["stereo_window_shape_refiner"]
    assert ep_diag["node_indices"] == gr_diag["node_indices"]
    assert set(range(9)).issubset(ep_diag["node_indices"])
    assert ep_diag["node_indices"][0] == 0 and ep_diag["node_indices"][-1] == 14
    assert ep_diag["iterations"][0]["appended_rank_rows"] == 0
    assert gr_diag["iterations"][0]["removed_endpoint_edge_indices"] == [0, 1]
    assert gr_diag["iterations"][0]["appended_rank_rows"] == 3
    assert inputs["observations"] == observations_before
    np.testing.assert_array_equal(inputs["positions"], positions_before)


@pytest.mark.parametrize("solve_metric_scale,expected_columns", [(False, 78), (True, 79)])
def test_four_iterations_dimension_guards_and_scale_column(tmp_path, solve_metric_scale, expected_columns):
    native = make_native(tmp_path)
    inputs = base_inputs()
    _, report = refiner.refine_positions_visual_inertial_with_shape(
        native,
        [group(rank=2)],
        refiner.GROUPED_SHAPE_MODE,
        expected_native_sha256=sha(native),
        solve_metric_scale=solve_metric_scale,
        **inputs,
    )
    diag = report["stereo_window_shape_refiner"]
    assert len(diag["iterations"]) == 4
    assert all(item["matrix_columns"] == expected_columns for item in diag["iterations"])
    assert diag["scale_active"] is solve_metric_scale
    assert diag["last_uncapped_lsqr_state"] is not None
    assert len(diag["last_uncapped_shape_evaluation"]) == 1
    assert len(diag["actual_returned_camera_shape_evaluation"]) == 1


def test_exact_ancillary_rows_and_target_when_removing_complete_endpoint_group(tmp_path):
    native = make_native(tmp_path)
    inputs = base_inputs()
    _, endpoint = refiner.refine_positions_visual_inertial_with_shape(
        native, [group(rank=4)], refiner.ENDPOINT_CONTROL_MODE, expected_native_sha256=sha(native), **inputs
    )
    _, grouped = refiner.refine_positions_visual_inertial_with_shape(
        native, [group(rank=4)], refiner.GROUPED_SHAPE_MODE, expected_native_sha256=sha(native), **inputs
    )
    ep_iter = endpoint["stereo_window_shape_refiner"]["iterations"][0]
    gr_iter = grouped["stereo_window_shape_refiner"]["iterations"][0]
    assert gr_iter["matrix_rows_after"] == ep_iter["matrix_rows_after"] - 6 + 4
    assert gr_iter["removed_row_indices"] == list(range(gr_iter["stereo_row_start"], gr_iter["stereo_row_start"] + 6))


@pytest.mark.parametrize(
    "bad_groups,match",
    [
        ([group(indices=[0, 1, 2])], "exactly nine"),
        ([group(), group()], "duplicate"),
        ([group(available=False)], "unavailable"),
        ([group(indices=[0, 1, 2, 3, 4, 5, 6, 7, 9])], "missing complete"),
    ],
)
def test_group_validation_fails_closed(tmp_path, bad_groups, match):
    native = make_native(tmp_path)
    with pytest.raises(ValueError, match=match):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            bad_groups,
            refiner.GROUPED_SHAPE_MODE,
            expected_native_sha256=sha(native),
            **base_inputs(),
        )


@pytest.mark.parametrize("wrong_layout,match", [("negative", "negative"), ("nondivisible", "divisible")])
def test_static_count_layout_guards(tmp_path, wrong_layout, match):
    native = make_native(tmp_path, wrong_layout=wrong_layout)
    with pytest.raises(ValueError, match=match):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.GROUPED_SHAPE_MODE,
            expected_native_sha256=sha(native),
            **base_inputs(),
        )


def test_source_hash_guard_before_and_during_hook(tmp_path):
    native = make_native(tmp_path)
    with pytest.raises(ValueError, match="before"):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.GROUPED_SHAPE_MODE,
            expected_native_sha256="0" * 64,
            **base_inputs(),
        )
    expected = sha(native)
    original_lsqr = native.lsqr

    def mutating_lsqr(matrix, target, *args, **kwargs):
        Path(native.__file__).write_text("# changed\n", encoding="utf-8")
        return scipy_lsqr(matrix, target, *args, **kwargs)

    native.lsqr = mutating_lsqr
    with pytest.raises(ValueError, match="during|after"):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.ENDPOINT_CONTROL_MODE,
            expected_native_sha256=expected,
            **base_inputs(),
        )
    assert native.lsqr is mutating_lsqr
    native.lsqr = original_lsqr


def test_accepted_order_identity_mutation_is_detected_and_lsqr_restored(tmp_path):
    native = make_native(tmp_path, mutate_accepted=True)
    original_lsqr = native.lsqr
    with pytest.raises(ValueError, match="accepted observation order"):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.GROUPED_SHAPE_MODE,
            expected_native_sha256=sha(native),
            **base_inputs(),
        )
    assert native.lsqr is original_lsqr


def test_accepted_order_identity_mutation_is_detected_in_endpoint_control(tmp_path):
    native = make_native(tmp_path, mutate_accepted=True)
    original_lsqr = native.lsqr
    with pytest.raises(ValueError, match="accepted observation order"):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.ENDPOINT_CONTROL_MODE,
            expected_native_sha256=sha(native),
            **base_inputs(),
        )
    assert native.lsqr is original_lsqr


@pytest.mark.parametrize("iterations", [3, 5])
def test_active_native_call_must_intercept_exactly_four_lsqr_calls(tmp_path, iterations):
    native = make_native(tmp_path, iterations=iterations)
    with pytest.raises(ValueError, match="exactly four lsqr"):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.ENDPOINT_CONTROL_MODE,
            expected_native_sha256=sha(native),
            **base_inputs(),
        )


def test_exception_restores_lsqr(tmp_path):
    native = make_native(tmp_path, fail_after_lsqr=True)
    original_lsqr = native.lsqr
    with pytest.raises(RuntimeError, match="native failed"):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.GROUPED_SHAPE_MODE,
            expected_native_sha256=sha(native),
            **base_inputs(),
        )
    assert native.lsqr is original_lsqr


def test_actual_native_zero_group_matches_direct_bitidentity():
    native = load_actual_native()
    inputs = actual_inputs()
    direct_refined, direct_report = native.refine_positions_visual_inertial(**deepcopy(inputs))
    wrapped_refined, wrapped_report = refiner.refine_positions_visual_inertial_with_shape(
        native, [], refiner.GROUPED_SHAPE_MODE, **deepcopy(inputs)
    )
    np.testing.assert_array_equal(wrapped_refined, direct_refined)
    assert wrapped_report == direct_report
    assert "stereo_window_shape_refiner" not in wrapped_report


def test_actual_native_active_endpoint_and_grouped_match_nodes_hash_hook_and_rows():
    native = load_actual_native()
    original_lsqr = native.lsqr
    calls = []

    def spy_lsqr(matrix, target, *args, **kwargs):
        calls.append((matrix.shape, target.shape, dict(kwargs)))
        return original_lsqr(matrix, target, *args, **kwargs)

    native.lsqr = spy_lsqr
    endpoint_refined, endpoint_report = run_actual(native, [group()], refiner.ENDPOINT_CONTROL_MODE)
    assert native.lsqr is spy_lsqr
    endpoint_calls = list(calls)
    calls.clear()
    grouped_refined, grouped_report = run_actual(native, [group(rank=4)], refiner.GROUPED_SHAPE_MODE)
    assert native.lsqr is spy_lsqr
    native.lsqr = original_lsqr

    endpoint_diag = endpoint_report["stereo_window_shape_refiner"]
    grouped_diag = grouped_report["stereo_window_shape_refiner"]
    assert endpoint_diag["node_indices"] == grouped_diag["node_indices"]
    assert set(range(9)).issubset(grouped_diag["node_indices"])
    assert len(endpoint_diag["iterations"]) == 4
    assert len(grouped_diag["iterations"]) == 4
    assert len(endpoint_calls) == 4
    assert len(calls) == 4
    assert endpoint_diag["native_sha256"] == endpoint_diag["native_sha256_after"] == sha(native)
    assert grouped_diag["native_sha256"] == grouped_diag["native_sha256_after"] == sha(native)
    first_endpoint = endpoint_diag["iterations"][0]
    first_grouped = grouped_diag["iterations"][0]
    assert first_endpoint["matrix_rows_after"] == first_endpoint["matrix_rows_before"]
    assert first_grouped["removed_endpoint_edge_indices"] == [0, 1]
    assert first_grouped["matrix_rows_after"] == first_grouped["matrix_rows_before"] - 6 + 4
    assert first_grouped["appended_rank_rows"] == 4
    assert endpoint_report["nodes"] == grouped_report["nodes"] == len(grouped_diag["node_indices"])
    assert endpoint_refined.shape == grouped_refined.shape == actual_inputs()["positions"].shape


@pytest.mark.parametrize("solve_metric_scale,unknown_delta", [(False, 0), (True, 1)])
def test_actual_native_scale_on_off_with_nonidentity_rotation_and_camera_lever(solve_metric_scale, unknown_delta):
    native = load_actual_native()
    refined, report = run_actual(
        native,
        [group(rank=2)],
        refiner.GROUPED_SHAPE_MODE,
        solve_metric_scale=solve_metric_scale,
    )
    diag = report["stereo_window_shape_refiner"]
    assert diag["scale_active"] is solve_metric_scale
    assert report["joint_metric_scale"]["rigid_camera_lever_scaled"] is False
    assert report["linear_system_unknowns"] == 6 * report["nodes"] + 6 + unknown_delta
    assert np.all(np.isfinite(refined))
    assert diag["shape_groups"][0]["rank"] == 2


def test_actual_native_cap_report_distinguishes_uncapped_solution_from_returned_camera_shape():
    native = load_actual_native()
    refined, report = run_actual(
        native,
        [group_with_factor(item=force_factor(rank=3, affine_value=200.0))],
        refiner.GROUPED_SHAPE_MODE,
        max_correction_m=1e-6,
    )
    diag = report["stereo_window_shape_refiner"]
    assert report["correction_scale"] < 1.0
    assert report["position_correction_requested_max_m"] > report["position_correction_max_m"]
    uncapped = diag["last_uncapped_shape_evaluation"][0]["max_local_node_delta_from_initial_m"]
    actual = diag["actual_returned_camera_shape_evaluation"][0]["max_local_node_delta_from_initial_m"]
    assert uncapped > actual
    assert report["position_correction_max_m"] <= report["position_correction_limit_m"] + 1e-12
    assert np.max(np.linalg.norm(refined - actual_inputs()["positions"], axis=1)) <= 1.1e-6


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda inputs: inputs["observations"][0].__setitem__("correlated_factor_group_part", True), "correlated_factor_group_part"),
        (lambda inputs: inputs["observations"][0].__setitem__("correlated_factor_group_size", 2.0), "correlated_factor_group_size"),
        (lambda inputs: inputs.__setitem__("correction_node_indices", np.array([0, 4.2, 8, 59], dtype=object)), "correction node index"),
        (lambda inputs: inputs.__setitem__("correction_node_indices", np.array([0, True, 8, 59], dtype=object)), "correction node index"),
        (lambda inputs: inputs.__setitem__("correction_node_indices", np.array([0, 4, 4, 59], dtype=object)), "duplicate"),
    ],
)
def test_actual_native_malformed_group_metadata_and_node_indices_fail_closed(mutate, match):
    native = load_actual_native()
    inputs = actual_inputs()
    mutate(inputs)
    with pytest.raises(ValueError, match=match):
        refiner.refine_positions_visual_inertial_with_shape(
            native,
            [group()],
            refiner.GROUPED_SHAPE_MODE,
            expected_native_sha256=sha(native),
            **inputs,
        )
