import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.sparse import csr_matrix, vstack
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts" / "stereo_window_shape_system.py"
SPEC = importlib.util.spec_from_file_location("stereo_window_shape_system", PATH)
system = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = system
SPEC.loader.exec_module(system)

GRAPH_PATH = ROOT / "scripts" / "stereo_window_shape_graph.py"
GRAPH_SPEC = importlib.util.spec_from_file_location("stereo_window_shape_graph", GRAPH_PATH)
shape_graph = importlib.util.module_from_spec(GRAPH_SPEC)
sys.modules[GRAPH_SPEC.name] = shape_graph
GRAPH_SPEC.loader.exec_module(shape_graph)


def native_layout(node_count, edge_count, *, relative=True, static_pairs=0, scale=True):
    columns = 6 * node_count + 6 + (1 if scale else 0)
    relative_rows = node_count - 1 if relative else 0
    stereo_start = 3 * node_count + 3 + 3 * max(node_count - 2, 0) + 3 * relative_rows + 6 * (node_count - 1)
    rows = stereo_start + 3 * edge_count + 6 + 3 * static_pairs
    matrix = csr_matrix(np.arange(rows * columns, dtype=float).reshape(rows, columns))
    target = np.arange(rows, dtype=float) / 10.0
    return matrix, target, stereo_start, columns


def edge(first, second, group=None, part=None, source="shape"):
    row = {"first_index": first, "second_index": second, "metric_source": source}
    if group is not None:
        row.update(
            {
                "correlated_factor_group_id": group,
                "correlated_factor_group_part": part,
                "correlated_factor_group_size": 2,
            }
        )
    return row


def identities(accepted):
    return [system.edge_identity(edge) for edge in accepted]


def group_row(group_id, endpoint_indices, columns, *, rank=2, accepted=None, source="shape", use_scale=False):
    data = np.zeros((rank, columns), dtype=float)
    for row in range(rank):
        data[row, row] = 10.0 + row
        if use_scale:
            data[row, columns - 1] = -0.25 * (row + 1)
    endpoint_identities = identities([accepted[index] for index in endpoint_indices]) if accepted is not None else []
    return {
        "group_id": group_id,
        "endpoint_edge_indices": endpoint_indices,
        "endpoint_identities": endpoint_identities,
        "metric_source": source,
        "jacobian": csr_matrix(data),
        "residual": np.arange(rank, dtype=float) + 0.5,
        "rank": rank,
    }


def test_replaces_two_complete_groups_and_preserves_order_and_other_rows():
    matrix, target, stereo_start, columns = native_layout(6, 5, relative=True, static_pairs=2)
    accepted = [
        edge(0, 4, "g0", 0),
        edge(4, 8, "g0", 1),
        edge(10, 15, None, None, "native_stereo"),
        edge(20, 24, "g1", 0),
        edge(24, 28, "g1", 1),
    ]
    rows = [
        group_row("g0", [0, 1], columns, rank=2, accepted=accepted, use_scale=True),
        group_row("g1", [3, 4], columns, rank=3, accepted=accepted, use_scale=True),
    ]

    out_matrix, out_target, report = system.splice_shape_rows(
        matrix, target, accepted, rows, node_count=6, relative_motion=True, static_pair_count=2,
        accepted_order_identities=identities(accepted),
    )

    removed = list(range(stereo_start, stereo_start + 6)) + list(range(stereo_start + 9, stereo_start + 15))
    kept = [row for row in range(matrix.shape[0]) if row not in removed]
    expected_matrix = vstack([matrix[kept], rows[0]["jacobian"], rows[1]["jacobian"]]).tocsr()
    expected_target = np.concatenate([target[kept], -rows[0]["residual"], -rows[1]["residual"]])
    np.testing.assert_array_equal(out_matrix.toarray(), expected_matrix.toarray())
    np.testing.assert_array_equal(out_target, expected_target)
    assert report["removed_endpoint_edge_indices"] == [0, 1, 3, 4]
    assert report["removed_row_indices"] == removed
    assert report["appended_rank_rows"] == 5
    assert report["accepted_edge_count"] == 5
    np.testing.assert_array_equal(matrix.toarray(), np.arange(matrix.shape[0] * columns, dtype=float).reshape(matrix.shape[0], columns))
    np.testing.assert_array_equal(target, np.arange(matrix.shape[0], dtype=float) / 10.0)


def test_noop_empty_replacements_returns_same_objects():
    matrix, target, _, _ = native_layout(4, 2)
    out_matrix, out_target, report = system.splice_shape_rows(matrix, target, [edge(0, 1), edge(1, 2)], [], node_count=4)
    assert out_matrix is matrix
    assert out_target is target
    assert report["appended_rank_rows"] == 0
    assert report["removed_endpoint_edge_indices"] == []
    with pytest.raises(ValueError, match="shape_rows"):
        system.splice_shape_rows(matrix, target, [edge(0, 1), edge(1, 2)], None, node_count=4)


def test_rank_zero_group_removes_endpoints_without_appending_rows():
    matrix, target, stereo_start, columns = native_layout(3, 2, relative=False, scale=False)
    accepted = [edge(0, 4, "g", 0), edge(4, 8, "g", 1)]
    row = group_row("g", [0, 1], columns, rank=0, accepted=accepted)
    out_matrix, out_target, report = system.splice_shape_rows(
        matrix, target, accepted, [row], node_count=3, relative_motion=False, scale_active=False,
        accepted_order_identities=identities(accepted),
    )
    kept = [row_index for row_index in range(matrix.shape[0]) if row_index not in range(stereo_start, stereo_start + 6)]
    np.testing.assert_array_equal(out_matrix.toarray(), matrix[kept].toarray())
    np.testing.assert_array_equal(out_target, target[kept])
    assert report["appended_rank_rows"] == 0
    assert report["shape_group_count"] == 1


@pytest.mark.parametrize(
    "accepted,shape_rows,match",
    [
        ([edge(0, 4, "g", 0)], lambda a: [group_row("g", [0, 1], 24)], "incomplete"),
        ([edge(0, 4, "g", 0), edge(4, 8, "g", 0)], lambda a: [group_row("g", [0, 1], 24, accepted=a)], "duplicate"),
        ([edge(4, 8, "g", 1), edge(0, 4, "g", 0)], lambda a: [group_row("g", [0, 1], 24, accepted=a)], "accepted order"),
        ([edge(0, 4, "g", 0), edge(4, 8, "g", 1)], lambda a: [group_row("g", [0, 1], 24, accepted=a), group_row("g", [0, 1], 24, accepted=a)], "duplicate"),
        ([edge(0, 4, "g", 0), edge(4, 8, "g", 1)], lambda a: [group_row("g", [0, 0], 24, accepted=a)], "duplicate"),
        ([edge(0, 4, "g", 0), edge(4, 8, "g", 1)], lambda a: [group_row("g", [0, 2], 24)], "out of range"),
    ],
)
def test_rejects_partial_duplicate_or_noncanonical_groups(accepted, shape_rows, match):
    matrix, target, _, _ = native_layout(3, len(accepted), scale=False)
    with pytest.raises(ValueError, match=match):
        system.splice_shape_rows(
            matrix, target, accepted, shape_rows(accepted), node_count=3, scale_active=False,
            accepted_order_identities=identities(accepted),
        )


def test_rejects_bad_native_dimensions_and_shape_widths():
    matrix, target, _, columns = native_layout(5, 2, relative=True, scale=True)
    accepted = [edge(0, 4, "g", 0), edge(4, 8, "g", 1)]
    with pytest.raises(ValueError, match="native row layout"):
        system.splice_shape_rows(
            matrix[:-1], target[:-1], accepted, [group_row("g", [0, 1], columns, accepted=accepted)],
            node_count=5, accepted_order_identities=identities(accepted),
        )
    with pytest.raises(ValueError, match="width"):
        system.splice_shape_rows(
            matrix, target, accepted, [group_row("g", [0, 1], columns + 1, accepted=accepted)],
            node_count=5, accepted_order_identities=identities(accepted),
        )
    bad = group_row("g", [0, 1], columns, accepted=accepted)
    bad["residual"] = np.array([np.nan, 1.0])
    with pytest.raises(ValueError, match="finite"):
        system.splice_shape_rows(matrix, target, accepted, [bad], node_count=5, accepted_order_identities=identities(accepted))


def test_rejects_int_truncation_bool_flags_and_bad_edge_identity():
    matrix, target, _, columns = native_layout(3, 2, scale=False)
    accepted = [edge(0, 4, "g", 0), edge(4, 8, "g", 1)]
    row = group_row("g", [0, 1], columns, accepted=accepted)
    with pytest.raises(ValueError, match="node_count"):
        system.splice_shape_rows(matrix, target, accepted, [row], node_count=3.0, scale_active=False,
                                 accepted_order_identities=identities(accepted))
    with pytest.raises(ValueError, match="node_count"):
        system.splice_shape_rows(matrix, target, accepted, [row], node_count=True, scale_active=False,
                                 accepted_order_identities=identities(accepted))
    with pytest.raises(ValueError, match="relative_motion"):
        system.splice_shape_rows(matrix, target, accepted, [row], node_count=3, relative_motion="yes", scale_active=False,
                                 accepted_order_identities=identities(accepted))
    with pytest.raises(ValueError, match="scale_active"):
        system.splice_shape_rows(matrix, target, accepted, [row], node_count=3, scale_active="no",
                                 accepted_order_identities=identities(accepted))
    bad_float = [edge(0.5, 4, "g", 0), accepted[1]]
    with pytest.raises(ValueError, match="first_index"):
        identities(bad_float)
    bad_bool_part = [edge(0, 4, "g", True), accepted[1]]
    with pytest.raises(ValueError, match="correlated_factor_group_part"):
        identities(bad_bool_part)
    bad_group = [edge(0, 4, 17, 0), accepted[1]]
    with pytest.raises(ValueError, match="group id"):
        identities(bad_group)
    backwards = [edge(4, 4, "g", 0), accepted[1]]
    with pytest.raises(ValueError, match="first_index < second_index"):
        identities(backwards)


def test_shape_jacobian_cannot_touch_velocity_gravity_or_bias_columns():
    matrix, target, _, columns = native_layout(4, 2, scale=True)
    accepted = [edge(0, 4, "g", 0), edge(4, 8, "g", 1)]
    row = group_row("g", [0, 1], columns, accepted=accepted)
    bad = dict(row)
    data = row["jacobian"].toarray()
    data[0, 3 * 4] = 1.0  # first velocity column; forbidden
    bad["jacobian"] = csr_matrix(data)
    with pytest.raises(ValueError, match="position and active scale"):
        system.splice_shape_rows(matrix, target, accepted, [bad], node_count=4,
                                 accepted_order_identities=identities(accepted))
    stored_zero = dict(row)
    original = row["jacobian"].tocoo()
    # Dense-to-CSR conversion drops zeros, so insert a stored zero explicitly.
    stored_zero["jacobian"] = csr_matrix(
        (np.append(original.data, 0.0),
         (np.append(original.row, 0), np.append(original.col, 3 * 4))),
        shape=original.shape,
    )
    zero_columns_before = stored_zero["jacobian"].indices.copy()
    zero_data_before = stored_zero["jacobian"].data.copy()
    assert np.any((zero_columns_before == 3 * 4) & (zero_data_before == 0.0))
    out_matrix, out_target, _ = system.splice_shape_rows(
        matrix, target, accepted, [stored_zero], node_count=4,
        accepted_order_identities=identities(accepted),
    )
    assert out_matrix.shape[0] == matrix.shape[0] - 6 + stored_zero["jacobian"].shape[0]
    assert out_target.shape[0] == out_matrix.shape[0]
    np.testing.assert_array_equal(stored_zero["jacobian"].indices, zero_columns_before)
    np.testing.assert_array_equal(stored_zero["jacobian"].data, zero_data_before)


def test_interoperates_with_build_shape_group_row_exactly():
    node_count = 9
    matrix, target, stereo_start, columns = native_layout(node_count, 2, scale=True)
    accepted = [edge(0, 4, "groupA", 0, "stereo_window_shape_v1"), edge(4, 8, "groupA", 1, "stereo_window_shape_v1")]
    sensitivity = np.random.default_rng(20260928).normal(size=(3, 24))
    factor = {
        "diagnostic_only": True,
        "available": True,
        "solver_accepted": True,
        "not_admissible_for_graph": True,
        "frames": 9,
        "gauge": "all_relative_centers_mapped_through_first_camera_rotation",
        "compact_sqrt_sensitivity": sensitivity.tolist(),
        "reference_center_vector_m": np.linspace(0.0, 0.023, 24).tolist(),
        "affine_offset": [0.3, -0.2, 0.1],
        "metadata": {
            "calibrated_covariance": False,
            "statistical_independence_claimed": False,
            "available_for_graph": False,
        },
    }
    positions = np.column_stack((np.linspace(0.0, 0.08, 9), np.sin(np.arange(9)) * 0.01, np.zeros(9)))
    rotations = Rotation.from_rotvec(np.tile([0.2, -0.1, 0.05], (9, 1)))
    row = shape_graph.build_shape_group_row(
        factor,
        list(range(9)),
        positions,
        rotations,
        {index: 3 * index for index in range(9)},
        total_columns=columns,
        scale_column=columns - 1,
    )
    shape_row = {
        "group_id": "groupA",
        "endpoint_edge_indices": [0, 1],
        "endpoint_identities": identities(accepted),
        "metric_source": "stereo_window_shape_v1",
        "jacobian": row["jacobian"],
        "residual": row["residual"],
    }

    out_matrix, out_target, report = system.splice_shape_rows(
        matrix, target, accepted, [shape_row], node_count=node_count,
        accepted_order_identities=identities(accepted),
    )

    kept = [row_index for row_index in range(matrix.shape[0]) if row_index not in range(stereo_start, stereo_start + 6)]
    expected = vstack([matrix[kept], row["jacobian"]]).tocsr()
    np.testing.assert_array_equal(out_matrix.toarray(), expected.toarray())
    np.testing.assert_array_equal(out_target, np.concatenate([target[kept], -row["residual"]]))
    assert report["appended_rank_rows"] == row["jacobian"].shape[0]


def test_rejects_changed_accepted_order_identity_and_metric_source_mismatch():
    matrix, target, _, columns = native_layout(3, 3, scale=False)
    accepted = [edge(0, 4, "g", 0), edge(4, 8, "g", 1), edge(9, 10, None, None, "native")]
    row = group_row("g", [0, 1], columns, accepted=accepted)
    frozen = identities(accepted)
    changed_nonreplaced = [dict(item) for item in frozen]
    changed_nonreplaced[2]["first_index"] = 99
    with pytest.raises(ValueError, match="accepted observation order"):
        system.splice_shape_rows(
            matrix, target, accepted, [row], node_count=3, scale_active=False,
            accepted_order_identities=changed_nonreplaced,
        )
    accepted_bad_source = [edge(0, 4, "g", 0, "shape"), edge(4, 8, "g", 1, "other"), accepted[2]]
    with pytest.raises(ValueError, match="metric_source"):
        system.splice_shape_rows(
            matrix, target, accepted_bad_source, [group_row("g", [0, 1], columns, accepted=accepted_bad_source)],
            node_count=3, scale_active=False, accepted_order_identities=identities(accepted_bad_source),
        )
