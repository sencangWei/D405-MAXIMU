"""Read-only same-ray depth diagnostics; no estimator or external reference."""
import numpy as np


def stats(values):
    values = np.asarray(values, dtype=float)
    if not values.size:
        return dict(status="UNKNOWN", count=0)
    if not np.isfinite(values).all():
        raise ValueError("nonfinite diagnostic values")
    return dict(status="OK", count=int(values.size), median=float(np.median(values)),
                p95=float(np.percentile(values, 95)), max=float(values.max()))


def points(sample):
    pre, post = np.asarray(sample["Xk"], float), np.asarray(sample["Xk_after"], float)
    ids = np.asarray(sample["keyframe_pixel_ids"])
    if (pre.ndim != 2 or pre.shape[1] != 3 or post.shape != pre.shape
            or ids.shape != (len(pre),) or not np.issubdtype(ids.dtype, np.integer)
            or (ids < 0).any() or len(np.unique(ids)) != len(ids)):
        raise ValueError("invalid same-ray points or pixel IDs")
    return pre, post, ids


def compare_update(sample):
    pre, post, _ = points(sample)
    depth, valid = np.asarray(sample["depth_keyframe_m"], float), np.asarray(sample["valid"])
    if depth.shape != (len(pre),) or valid.shape != depth.shape or valid.dtype != np.bool_:
        raise ValueError("invalid stereo/valid shape or non-bool mask")
    common = (valid & np.isfinite(depth) & (depth > 0)
              & np.isfinite(pre).all(axis=1) & np.isfinite(post).all(axis=1)
              & (pre[:, 2] > 0) & (post[:, 2] > 0))
    count = int(common.sum())
    if count < 20:
        return dict(status="UNKNOWN", count=count, reason="fewer_than20same_stereo_rays")
    pre, post, depth = pre[common], post[common], depth[common]
    alpha = float(np.median(depth/pre[:, 2]))
    before = np.log(pre[:, 2]/depth)
    after = np.log(post[:, 2]/depth)
    before -= np.median(before)
    after -= np.median(after)
    return dict(status="OK", count=count, learned_to_metric_pre_scale=alpha,
                fixed_pre_scale_update_norm_mm=stats(1000*alpha*np.linalg.norm(post-pre, axis=1)),
                uniform_log_depth_change=float(np.median(np.log(post[:, 2]/pre[:, 2]))),
                centered_log_depth_shape_before=stats(np.abs(before)),
                centered_log_depth_shape_after=stats(np.abs(after)),
                note="same-ray point-map change and stereo consistency, NOT trajectory error; uniform scale separated")


def compare_continuity(previous, current, previous_keyframe, current_keyframe):
    if previous_keyframe != current_keyframe:
        return dict(status="UNKNOWN", count=0, reason="different_keyframes")
    _, post, old_ids = points(previous)
    pre, _, new_ids = points(current)
    shared, old, new = np.intersect1d(old_ids, new_ids, return_indices=True)
    finite = np.isfinite(post[old]).all(axis=1) & np.isfinite(pre[new]).all(axis=1)
    if not finite.any():
        return dict(status="UNKNOWN", count=0, reason="no_common_finite_sampled_rays")
    delta = post[old[finite]]-pre[new[finite]]
    return dict(status="OK", count=int(finite.sum()), intersection_count=len(shared),
                absolute_z_delta_learned_units=stats(np.abs(delta[:, 2])),
                xyz_delta_learned_units=stats(np.linalg.norm(delta, axis=1)),
                note="only sampled shared pixel IDs in unchanged keyframe; floating ray x/y reprojection may differ")
