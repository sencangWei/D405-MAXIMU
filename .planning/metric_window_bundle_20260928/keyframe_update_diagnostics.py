"""Pure raw weighted-pointmap boundary checks; not an accuracy estimator."""
import numpy as np
from scipy.spatial.transform import Rotation


def stats(values):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return dict(status="UNKNOWN", count=0)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite diagnostic statistics")
    return dict(status="OK", count=len(values), min=float(values.min()),
                median=float(np.median(values)), p95=float(np.percentile(values, 95)), max=float(values.max()))


def scalar(sample, key):
    value = np.asarray(sample[key])
    if value.shape != ():
        raise ValueError(f"{key} must be scalar")
    return value.item()


def summarize_update(sample):
    if scalar(sample, "update_filtering_mode") != "weighted_pointmap":
        raise ValueError("unsupported native filtering mode")
    ids = np.asarray(sample["keyframe_pixel_ids"])
    n = len(ids)
    if (ids.ndim != 1 or not np.issubdtype(ids.dtype, np.integer) or (ids < 0).any()
            or len(np.unique(ids)) != n):
        raise ValueError("invalid shared pixel IDs")
    xyz_keys = ("raw_X_old", "raw_X_proposal", "raw_X_after", "raw_Xkf_match", "raw_Xkf_working")
    conf_keys = ("raw_C_old", "raw_C_new", "raw_C_after", "raw_Ckf_match", "raw_Ckf_working")
    arrays = {key: np.asarray(sample[key]) for key in (*xyz_keys, *conf_keys)}
    for key, value in arrays.items():
        shape = (n, 3) if key in xyz_keys else (n, 1)
        if value.shape != shape or not np.issubdtype(value.dtype, np.floating) or not np.isfinite(value).all():
            raise ValueError(f"invalid {key} shape/dtype/finite")
    if not n:
        return dict(status="UNKNOWN", count=0, reason="no_sampled_update_rays")
    before, after = scalar(sample, "update_N_before"), scalar(sample, "update_N_after")
    if (not isinstance(before, int) or isinstance(before, bool) or not isinstance(after, int)
            or isinstance(after, bool) or before < 1 or after != before+1):
        raise ValueError("weighted update counter mismatch")
    old, proposal, new = (arrays[k] for k in xyz_keys[:3])
    cold, cin, cafter = (arrays[k] for k in conf_keys[:3])
    if any((arrays[key] <= 0).any() for key in conf_keys):
        raise ValueError("nonpositive update confidence")
    expected_c = cold+cin
    expected = (cold*old+cin*proposal)/expected_c
    beta = cin.astype(float)/expected_c.astype(float)
    delta = new.astype(float)-old.astype(float)
    innovation = proposal.astype(float)-old.astype(float)
    formula_error = np.linalg.norm(new.astype(float)-expected.astype(float), axis=1)
    confidence_error = np.abs(cafter.astype(float)-expected_c.astype(float)).ravel()
    dtype = np.result_type(*[value.dtype for value in arrays.values()])
    eps = np.finfo(dtype).eps
    xyz_scale = np.maximum(1., np.maximum(np.linalg.norm(expected.astype(float), axis=1),
                                         np.linalg.norm(new.astype(float), axis=1)))
    conf_scale = np.maximum(1., np.abs(expected_c.astype(float)).ravel())
    result = dict(status="OK", count=n, update_N_before=before, update_N_after=after,
                  confidence_incoming_fraction=stats(beta.ravel()),
                  weighted_xyz_formula_error_learned_units=stats(formula_error),
                  accumulated_confidence_formula_error=stats(confidence_error),
                  weighted_formula_consistent=bool((formula_error <= 32*eps*xyz_scale).all()
                                                   and (confidence_error <= 32*eps*conf_scale).all()),
                  working_minus_match_xyz_learned_units=stats(np.linalg.norm(
                      arrays["raw_Xkf_working"].astype(float)-arrays["raw_Xkf_match"].astype(float), axis=1)),
                  proposal_minus_old_learned_units=stats(np.linalg.norm(innovation, axis=1)),
                  after_minus_old_learned_units=stats(np.linalg.norm(delta, axis=1)),
                  confidence_incoming_equals_working=bool(np.array_equal(cin, arrays["raw_Ckf_working"])),
                  negative_or_zero_z_counts={key: int((arrays[key][:, 2] <= 0).sum()) for key in xyz_keys},
                  note="native update identity and same-ray innovation, NOT trajectory error or covariance")
    transform = np.asarray(sample["update_T_boundary"], dtype=float)
    if (transform.shape != (8,) or not np.isfinite(transform).all() or transform[7] <= 0
            or abs(np.linalg.norm(transform[3:7])-1) > 1e-5):
        raise ValueError("invalid derived boundary Sim3")
    predicted = transform[7]*Rotation.from_quat(transform[3:7]).apply(arrays["raw_Xkf_working"])+transform[:3]
    pose_error = np.linalg.norm(predicted-proposal, axis=1)
    proposal_scale = np.maximum(1., np.linalg.norm(proposal.astype(float), axis=1))
    result["derived_pose_proposal_error_learned_units"] = stats(pose_error)
    result["derived_pose_binds_actual_proposal"] = bool((pose_error <= 32*eps*proposal_scale).all())
    result["boundary_pose_semantics"] = "derived from frame poses at update entry; not intercepted tracker-local transform"
    K, pixels = np.asarray(sample["K"], float), np.asarray(sample["pixel_keyframe"], float)
    if (K.shape != (3, 3) or pixels.shape != (n, 2) or not np.isfinite(K).all()
            or not np.isfinite(pixels).all() or K[0, 0] <= 0 or K[1, 1] <= 0):
        raise ValueError("invalid saved calibrated ray grid")
    ray_common = (old[:, 2] > 0) & (proposal[:, 2] > 0) & (new[:, 2] > 0)
    result["raw_ray_common_count"] = int(ray_common.sum())
    result["raw_keyframe_ray_pixel_disagreement"] = {}
    for label, xyz in (("old", old), ("proposal", proposal), ("after", new)):
        uv = xyz[ray_common, :2].astype(float)/xyz[ray_common, 2:3].astype(float)
        uv = uv*np.array([K[0, 0], K[1, 1]])+np.array([K[0, 2], K[1, 2]])
        result["raw_keyframe_ray_pixel_disagreement"][label] = stats(np.linalg.norm(uv-pixels[ray_common], axis=1))
    depth = np.asarray(sample["depth_keyframe_m"], dtype=float)
    valid = np.asarray(sample["valid"])
    if depth.shape != (n,) or valid.shape != (n,) or valid.dtype != np.bool_:
        raise ValueError("invalid same-ray stereo mask")
    common = valid & np.isfinite(depth) & (depth > 0)
    common &= (old[:, 2] > 0) & (proposal[:, 2] > 0) & (new[:, 2] > 0)
    result["common_stereo_count"] = int(common.sum())
    if common.sum() < 20:
        result["stereo_consistency"] = dict(status="UNKNOWN", reason="fewer_than20common_positive_depth_rays")
    else:
        alpha = float(np.median(depth[common]/old[common, 2]))
        metrics = dict(status="OK", learned_to_metric_old_scale=alpha)
        for key, xyz in (("old", old), ("proposal", proposal), ("after", new)):
            ratio = np.log(xyz[common, 2].astype(float)/depth[common])
            metrics[f"{key}_centered_log_depth_shape"] = stats(np.abs(ratio-np.median(ratio)))
            metrics[f"{key}_uniform_log_depth_ratio"] = float(np.median(ratio))
        metrics["same_old_units_proposal_innovation_norm_mm"] = stats(1000*alpha*np.linalg.norm(innovation[common], axis=1))
        metrics["same_old_units_actual_update_norm_mm"] = stats(1000*alpha*np.linalg.norm(delta[common], axis=1))
        result["stereo_consistency"] = metrics
    return result
