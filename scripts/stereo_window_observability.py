"""UMI-only local endpoint sensitivity; no IO, gates, or covariance claim.

The optimized Jacobian is conditional on the current robust residual model and
provisional pixel/gyro noise assumptions. This is NOT absolute metric accuracy.
Instrumentation is single-threaded and restores the original solver function.
"""
import numpy as np


def endpoint_columns(frames):
    if not isinstance(frames, (int, np.integer)) or frames < 2:
        raise ValueError('at least two frames required')
    start = 3*(frames-1) + 3*(frames-2)
    return list(range(start, start+3))


def marginal_endpoint(jacobian, columns):
    """Project away nuisance span using SVD, avoiding squared normal equations.

    Normalizing nuisance columns preserves their span while removing arbitrary
    parameter-unit scaling. Returned sensitivities refer to the supplied rows
    (unit normalized robust residuals), not raw pixel noise or a probability.
    """
    jac = np.asarray(jacobian, dtype=float)
    cols = np.asarray(columns)
    if (jac.ndim != 2 or jac.shape[0] < 3 or not np.all(np.isfinite(jac))
            or cols.shape != (3,) or not np.issubdtype(cols.dtype, np.integer)
            or len(set(cols.tolist())) != 3 or np.any(cols < 0)
            or np.any(cols >= jac.shape[1])):
        raise ValueError('invalid endpoint Jacobian')
    endpoint = jac[:, cols]
    other = np.ones(jac.shape[1], dtype=bool)
    other[cols] = False
    nuisance = jac[:, other]
    norms = np.linalg.norm(nuisance, axis=0)
    nuisance = nuisance[:, norms > 0]/norms[norms > 0]
    nuisance_rank = 0
    conditional = endpoint.copy()
    if nuisance.shape[1]:
        u, singular, _ = np.linalg.svd(nuisance, full_matrices=False)
        tolerance = np.finfo(float).eps*max(nuisance.shape)*singular[0]
        nuisance_rank = int(np.count_nonzero(singular > tolerance))
        basis = u[:, :nuisance_rank]
        conditional -= basis@(basis.T@endpoint)
    _, singular, vh = np.linalg.svd(conditional, full_matrices=False)
    # Use pre-projection endpoint scale too: near-complete cancellation must
    # not become spuriously full rank just because its own tiny values differ.
    scale = max(float(np.linalg.norm(endpoint, ord=2)), float(singular[0]))
    tolerance = np.finfo(float).eps*max(jac.shape)*scale
    rank = int(np.count_nonzero(singular > tolerance))
    return dict(
        diagnostic_only=True, is_calibrated_covariance=False,
        nuisance_rank=nuisance_rank, endpoint_rank=rank,
        rank_deficient=rank < 3,
        singular_values_per_m=singular.tolist(),
        rank_tolerance_per_m=float(tolerance),
        weak_axis_camera_i=vh[-1].tolist(),
        null_axes_camera_i=vh[rank:].tolist(),
        weak_response_mm_per_unit_normalized_residual=(
            float(1000/singular[-1]) if rank == 3 else None),
        interpretation='local linear sensitivity, not calibrated uncertainty or accuracy')


def _capture_diagnostic(bundle, result, data):
    frames, points, _, valid, _, left, _, baseline = data[:8]
    expected = 6*(frames-1)+3*points+3
    jac = result.jac.toarray() if hasattr(result.jac, 'toarray') else np.asarray(result.jac)
    if jac.shape[1] != expected:
        raise ValueError('Jacobian/variable packing mismatch')
    rotations, centers, landmarks, _ = bundle._unpack(result.x, frames, points)
    mask = valid[-1]
    endpoint_xyz = rotations[-1].inv().apply(landmarks[mask]-centers[-1])
    depth = endpoint_xyz[:, 2]
    pixel_rows = int(np.count_nonzero(valid)*4)
    cols = endpoint_columns(frames)
    return dict(
        diagnostic_only=True, is_calibrated_covariance=False,
        gauge='first_camera_center_zero_rotation_identity',
        jacobian='scipy_optimized_transformed_soft_l1_pixels_soft_gyro_bias_prior',
        uncalibrated_noise_assumption=True,
        normalized_pixel_sigma_px=float(data[11]),
        gyro_noise_density=float(data[9]), gyro_bias_sigma=float(data[10]),
        conditional_on_current_solution=True,
        all_factors=marginal_endpoint(jac, cols),
        pixel_rows_only=marginal_endpoint(jac[:pixel_rows], cols),
        node_tracks=np.count_nonzero(valid, axis=1).tolist(),
        endpoint_tracks=int(np.count_nonzero(mask)),
        source_endpoint_common_tracks=int(np.count_nonzero(valid[0]&valid[-1])),
        endpoint_depth_m_quantiles=np.percentile(depth, [5, 50, 95]).tolist(),
        # Equivalent rectified disparity leverage; not an independently
        # observed/validated disparity when the factory intrinsics differ.
        endpoint_fx_baseline_over_depth_px_quantiles=np.percentile(
            left[0]*baseline/depth, [5, 50, 95]).tolist(),
        solver_optimality=float(result.optimality))


def solve_with_observability(bundle, *args, **kwargs):
    """Run the exact existing solver; append diagnostics without changing it."""
    original = bundle.least_squares
    captured = []
    def intercept(*solver_args, **solver_kwargs):
        result = original(*solver_args, **solver_kwargs)
        captured.append((result, solver_kwargs['args']))
        return result
    bundle.least_squares = intercept
    try:
        answer = bundle.solve_stereo_window(*args, **kwargs)
    finally:
        bundle.least_squares = original
    if captured and 'diagnostics' in answer:
        try:
            record = _capture_diagnostic(bundle, *captured[-1])
        except (ValueError, np.linalg.LinAlgError) as error:
            record = dict(diagnostic_only=True, available=False,
                          reason=str(error), is_calibrated_covariance=False)
        answer['diagnostics'] = {**answer['diagnostics'], 'endpoint_observability': record}
    return answer
