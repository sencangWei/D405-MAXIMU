"""Prepare shared stereo pixel tracks; no learned trajectory or external truth."""
import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from align_mast3r_scale_with_stereo import stereo_disparity, left_right_consistent


def _flow(source, target, points, guess=None):
    options = dict(winSize=(31,31), maxLevel=4,
                   criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,40,.01))
    next_points, status, _ = cv2.calcOpticalFlowPyrLK(
        source, target, points.astype(np.float32),
        None if guess is None else guess.astype(np.float32),
        flags=0 if guess is None else cv2.OPTFLOW_USE_INITIAL_FLOW, **options)
    if next_points is None:
        return points.copy(), np.zeros(len(points),dtype=bool)
    # Stereo disparity already supplies a direction-specific initialization.
    # Use its inverse for the backward refinement too: an unseeded pyramid can
    # jump one texture period even when the forward solution is exact.
    backward_seed = None if guess is None else points.astype(np.float32).copy()
    previous, backward, _ = cv2.calcOpticalFlowPyrLK(
        target, source, next_points, backward_seed,
        flags=0 if backward_seed is None else cv2.OPTFLOW_USE_INITIAL_FLOW, **options)
    if previous is None:
        return next_points, np.zeros(len(points),dtype=bool)
    valid = status.ravel().astype(bool) & backward.ravel().astype(bool)
    valid &= np.all(np.isfinite(next_points),axis=1)
    valid &= np.linalg.norm(previous-points,axis=1) <= 1.
    height,width = target.shape
    valid &= (next_points[:,0] >= 1) & (next_points[:,0] < width-1)
    valid &= (next_points[:,1] >= 1) & (next_points[:,1] < height-1)
    return next_points,valid


def track_stereo_window(left_images, right_images, calibration, max_points=180,
                        initialize_poses=True):
    """Track landmarks and initialize metric camera poses from image-only PnP.

    Input pairs must already be synchronized, rectified grayscale images.
    Matching and formal depth limits are fixed; no per-recording tuning.
    """
    if len(left_images) != len(right_images) or len(left_images) < 3:
        raise ValueError('need at least three synchronized stereo image pairs')
    shape = left_images[0].shape
    if len(shape) != 2 or any(im.shape != shape or im.dtype != np.uint8
                              for im in list(left_images)+list(right_images)):
        raise ValueError('stereo images must have identical grayscale uint8 shape')
    left,right = calibration['left_intrinsics'],calibration['right_intrinsics']
    baseline = float(calibration['baseline_m'])
    if not np.isfinite(baseline) or baseline <= 0 or max_points < 20:
        raise ValueError('need positive factory baseline and at least20 points')
    if any(any(float(c) != 0 for c in k.get('coeffs',[])) for k in (left,right)):
        raise ValueError('images must be rectified; nonzero distortion unsupported')
    if any(left[k] != right[k] for k in ('fx','fy','cx','cy')):
        raise ValueError('source disparity initialization requires equal rectified intrinsics')
    matrix = np.array([[left['fx'],0.,left['cx']],[0.,left['fy'],left['cy']],[0.,0.,1.]])
    disparities = [stereo_disparity(a,b,128) for a,b in zip(left_images,right_images)]
    disparity = disparities[0][0]
    depth = left['fx']*baseline / np.maximum(disparity,1e-6)
    mask = ((disparity>.5) & (depth>=.07) & (depth<=.6)).astype(np.uint8)*255
    features = cv2.goodFeaturesToTrack(left_images[0],maxCorners=max_points,
                    qualityLevel=.01,minDistance=7,mask=mask,blockSize=7)
    if features is None or len(features)<20:
        return dict(accepted=False,reason='insufficient_source_stereo_features')
    points = features.reshape(-1,2)
    observations,validity = [],[]
    temporal_valid = np.ones(len(points),dtype=bool)
    for index,(left_image,right_image) in enumerate(zip(left_images,right_images)):
        if index:
            points,current = _flow(left_images[index-1],left_image,points)
            temporal_valid &= current
        consistent,disparity_at_points = left_right_consistent(points,*disparities[index],tolerance_px=1.)
        guess = points.copy()
        guess[:,0] -= np.where(np.isfinite(disparity_at_points),disparity_at_points,0.)
        # Invalid SGBM values are never admitted as observations, but provide a
        # finite in-bounds flow seed rather than NaNs to OpenCV.
        guess[:,0] = np.clip(guess[:,0],1,shape[1]-2)
        matched,stereo_valid = _flow(left_image,right_image,points,guess)
        actual_disparity = points[:,0]-matched[:,0]
        actual_depth = left['fx']*baseline / np.maximum(actual_disparity,1e-6)
        valid = temporal_valid & consistent & stereo_valid
        valid &= np.abs(points[:,1]-matched[:,1])<=1.
        valid &= (actual_disparity>.5) & (actual_depth>=.07) & (actual_depth<=.6)
        observations.append(np.column_stack((points,matched)))
        validity.append(valid)
    observations,valid = np.asarray(observations,dtype=float),np.asarray(validity)
    keep = valid[0] & (valid.sum(axis=0)>=2)
    observations,valid = observations[:,keep],valid[:,keep]
    if len(observations[0])<20 or any(np.sum(v)<20 for v in valid):
        return dict(accepted=False,reason='insufficient_persistent_stereo_tracks')
    source = observations[0]
    depth = left['fx']*baseline/(source[:,0]-source[:,2])
    initial_points = np.column_stack(((source[:,0]-left['cx'])*depth/left['fx'],
                         (source[:,1]-left['cy'])*depth/left['fy'],depth))
    result = dict(accepted=True,observations=observations,valid=valid,
        initial_points=initial_points,
        observation_frame='infrared_left_camera0',source_depth_limits_m=[.07,.6],
        tracked_landmarks=int(len(initial_points)),observations_count=int(valid.sum()),
        policy='SGBM-gated seeded stereo LK pixels; no learned positions or external reference')
    if not initialize_poses:
        return result
    centers = [np.zeros(3)]
    rotations = [Rotation.identity().as_quat()]
    for index in range(1,len(left_images)):
        cv2.setRNGSeed(0)
        ok,rvec,tvec,inliers = cv2.solvePnPRansac(initial_points[valid[index]].astype(np.float32),
            observations[index,valid[index],:2].astype(np.float32),matrix,None,
            iterationsCount=200,reprojectionError=2.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
        if not ok or inliers is None or len(inliers)<20:
            return dict(accepted=False,reason='window_visual_initialization_failed',frame=index)
        camera_from_first = Rotation.from_rotvec(rvec.ravel())
        centers.append(-camera_from_first.inv().apply(tvec.ravel()))
        rotations.append(camera_from_first.inv().as_quat())
    return dict(result,initial_centers=np.asarray(centers),
                initial_rotations=Rotation.from_quat(rotations))
