/*******************************************************
 * Copyright (C) 2019, Aerial Robotics Group, Hong Kong University of Science and Technology
 * 
 * This file is part of VINS.
 * 
 * Licensed under the GNU General Public License v3.0;
 * you may not use this file except in compliance with the License.
 *
 * Author: Qin Tong (qintonguav@gmail.com)
 *******************************************************/

#include "keyframe.h"
#include "tracked_brief_matcher.h"

#include <algorithm>
#include <cmath>
#include <limits>

template <typename Derived>
static void reduceVector(vector<Derived> &v, vector<uchar> status)
{
    int j = 0;
    for (int i = 0; i < int(v.size()); i++)
        if (status[i])
            v[j++] = v[i];
    v.resize(j);
}

static double imageHullFraction(const vector<cv::Point2f> &points)
{
	if (points.size() < 3 || COL <= 0 || ROW <= 0)
		return 0.0;
	vector<cv::Point2f> hull;
	cv::convexHull(points, hull);
	return std::abs(cv::contourArea(hull)) /
	       (static_cast<double>(COL) * static_cast<double>(ROW));
}

// create keyframe online
KeyFrame::KeyFrame(double _time_stamp, int _index, Vector3d &_vio_T_w_i, Matrix3d &_vio_R_w_i,
		           cv::Mat &_image, cv::Mat &_right_image,
		           vector<cv::Point3f> &_point_3d, vector<cv::Point2f> &_point_2d_uv, vector<cv::Point2f> &_point_2d_norm,
		           vector<double> &_point_id, int _sequence)
{
	time_stamp = _time_stamp;
	index = _index;
	vio_T_w_i = _vio_T_w_i;
	vio_R_w_i = _vio_R_w_i;
	T_w_i = vio_T_w_i;
	R_w_i = vio_R_w_i;
	origin_vio_T = vio_T_w_i;		
	origin_vio_R = vio_R_w_i;
	image = _image.clone();
	right_image = _right_image.clone();
	cv::resize(image, thumbnail, cv::Size(80, 60));
	point_3d = _point_3d;
	point_2d_uv = _point_2d_uv;
	point_2d_norm = _point_2d_norm;
	point_id = _point_id;
	has_loop = false;
	loop_index = -1;
	has_fast_point = false;
	loop_info << 0, 0, 0, 0, 0, 0, 0, 0;
	sequence = _sequence;
	computeWindowBRIEFPoint();
	computeBRIEFPoint();
	computeORBPoint();
	computeRightORBPoint();
	if(!DEBUG_IMAGE)
	{
		image.release();
		right_image.release();
	}
}

// load previous keyframe
KeyFrame::KeyFrame(double _time_stamp, int _index, Vector3d &_vio_T_w_i, Matrix3d &_vio_R_w_i, Vector3d &_T_w_i, Matrix3d &_R_w_i,
					cv::Mat &_image, int _loop_index, Eigen::Matrix<double, 8, 1 > &_loop_info,
					vector<cv::KeyPoint> &_keypoints, vector<cv::KeyPoint> &_keypoints_norm, vector<BRIEF::bitset> &_brief_descriptors)
{
	time_stamp = _time_stamp;
	index = _index;
	//vio_T_w_i = _vio_T_w_i;
	//vio_R_w_i = _vio_R_w_i;
	vio_T_w_i = _T_w_i;
	vio_R_w_i = _R_w_i;
	T_w_i = _T_w_i;
	R_w_i = _R_w_i;
	if (DEBUG_IMAGE)
	{
		image = _image.clone();
		cv::resize(image, thumbnail, cv::Size(80, 60));
	}
	if (_loop_index != -1)
		has_loop = true;
	else
		has_loop = false;
	loop_index = _loop_index;
	loop_info = _loop_info;
	has_fast_point = false;
	sequence = 0;
	keypoints = _keypoints;
	keypoints_norm = _keypoints_norm;
	brief_descriptors = _brief_descriptors;
	if (!image.empty())
		computeORBPoint();
}


void KeyFrame::computeWindowBRIEFPoint()
{
	BriefExtractor extractor(BRIEF_PATTERN_FILE.c_str());
	for(int i = 0; i < (int)point_2d_uv.size(); i++)
	{
	    cv::KeyPoint key;
	    key.pt = point_2d_uv[i];
	    window_keypoints.push_back(key);
	}
	extractor(image, window_keypoints, window_brief_descriptors);
}

void KeyFrame::computeBRIEFPoint()
{
	BriefExtractor extractor(BRIEF_PATTERN_FILE.c_str());
	const int fast_th = 20; // corner detector response threshold
	if(1)
		cv::FAST(image, keypoints, fast_th, true);
	else
	{
		vector<cv::Point2f> tmp_pts;
		cv::goodFeaturesToTrack(image, tmp_pts, 500, 0.01, 10);
		for(int i = 0; i < (int)tmp_pts.size(); i++)
		{
		    cv::KeyPoint key;
		    key.pt = tmp_pts[i];
		    keypoints.push_back(key);
		}
	}
	extractor(image, keypoints, brief_descriptors);
	for (int i = 0; i < (int)keypoints.size(); i++)
	{
		Eigen::Vector3d tmp_p;
		m_camera->liftProjective(Eigen::Vector2d(keypoints[i].pt.x, keypoints[i].pt.y), tmp_p);
		cv::KeyPoint tmp_norm;
		tmp_norm.pt = cv::Point2f(tmp_p.x()/tmp_p.z(), tmp_p.y()/tmp_p.z());
		keypoints_norm.push_back(tmp_norm);
	}
}

void KeyFrame::computeORBPoint()
{
	auto orb = cv::ORB::create(2000, 1.2f, 8, 31, 0, 2,
	                           cv::ORB::HARRIS_SCORE, 31, 10);
	orb->detectAndCompute(image, cv::noArray(), orb_keypoints, orb_descriptors);
}

void KeyFrame::computeRightORBPoint()
{
	if (right_image.empty())
		return;
	auto orb = cv::ORB::create(2000, 1.2f, 8, 31, 0, 2,
	                           cv::ORB::HARRIS_SCORE, 31, 10);
	orb->detectAndCompute(right_image, cv::noArray(), right_orb_keypoints,
	                      right_orb_descriptors);
}

bool KeyFrame::verifyRightImageLoop(const KeyFrame *old_kf) const
{
	if (right_orb_descriptors.empty() || old_kf->right_orb_descriptors.empty())
	{
		printf("[AUTO_LOOP_REJECT] current=%d matched=%d reason=no_right_orb_descriptors\n",
		       index, old_kf->index);
		return false;
	}

	cv::BFMatcher matcher(cv::NORM_HAMMING, false);
	std::vector<std::vector<cv::DMatch>> forward_knn;
	std::vector<std::vector<cv::DMatch>> reverse_knn;
	matcher.knnMatch(right_orb_descriptors, old_kf->right_orb_descriptors,
	                 forward_knn, 2);
	matcher.knnMatch(old_kf->right_orb_descriptors, right_orb_descriptors,
	                 reverse_knn, 1);

	std::vector<cv::Point2f> current_points;
	std::vector<cv::Point2f> old_points;
	for (const auto &candidate : forward_knn)
	{
		if (candidate.size() < 2)
			continue;
		const cv::DMatch &best = candidate[0];
		const cv::DMatch &second = candidate[1];
		if (best.distance >= 70.0f || best.distance >= 0.80f * second.distance)
			continue;
		if (best.trainIdx < 0 || best.trainIdx >= static_cast<int>(reverse_knn.size()) ||
		    reverse_knn[best.trainIdx].empty() ||
		    reverse_knn[best.trainIdx][0].trainIdx != best.queryIdx)
			continue;
		current_points.push_back(right_orb_keypoints[best.queryIdx].pt);
		old_points.push_back(old_kf->right_orb_keypoints[best.trainIdx].pt);
	}

	if (current_points.size() < 8)
	{
		printf("[AUTO_LOOP_REJECT] current=%d matched=%d reason=right_matches matches=%zu\n",
		       index, old_kf->index, current_points.size());
		return false;
	}

	std::vector<uchar> inlier_mask;
	cv::findFundamentalMat(current_points, old_points, cv::FM_RANSAC, 4.0, 0.99,
	                       inlier_mask);
	const size_t inliers = static_cast<size_t>(
		std::count(inlier_mask.begin(), inlier_mask.end(), static_cast<uchar>(1)));
	const double ratio = static_cast<double>(inliers) / current_points.size();
	if (inliers < MIN_RIGHT_LOOP_NUM || ratio < MIN_RIGHT_LOOP_INLIER_RATIO)
	{
		printf("[AUTO_LOOP_REJECT] current=%d matched=%d reason=right_geometry "
		       "matches=%zu inliers=%zu ratio=%.3f\n",
		       index, old_kf->index, current_points.size(), inliers, ratio);
		return false;
	}

	printf("[AUTO_LOOP_RIGHT_PASS] current=%d matched=%d matches=%zu inliers=%zu ratio=%.3f\n",
	       index, old_kf->index, current_points.size(), inliers, ratio);
	return true;
}

void BriefExtractor::operator() (const cv::Mat &im, vector<cv::KeyPoint> &keys, vector<BRIEF::bitset> &descriptors) const
{
  m_brief.compute(im, keys, descriptors);
}


bool KeyFrame::searchInAera(const BRIEF::bitset window_descriptor,
                            const std::vector<BRIEF::bitset> &descriptors_old,
                            const std::vector<cv::KeyPoint> &keypoints_old,
                            const std::vector<cv::KeyPoint> &keypoints_old_norm,
                            cv::Point2f &best_match,
                            cv::Point2f &best_match_norm)
{
    cv::Point2f best_pt;
    int bestDist = 128;
    int bestIndex = -1;
    for(int i = 0; i < (int)descriptors_old.size(); i++)
    {

        int dis = HammingDis(window_descriptor, descriptors_old[i]);
        if(dis < bestDist)
        {
            bestDist = dis;
            bestIndex = i;
        }
    }
    //printf("best dist %d", bestDist);
    if (bestIndex != -1 && bestDist < 80)
    {
      best_match = keypoints_old[bestIndex].pt;
      best_match_norm = keypoints_old_norm[bestIndex].pt;
      return true;
    }
    else
      return false;
}

void KeyFrame::searchByBRIEFDes(std::vector<cv::Point2f> &matched_2d_old,
								std::vector<cv::Point2f> &matched_2d_old_norm,
                                std::vector<uchar> &status,
                                const std::vector<BRIEF::bitset> &descriptors_old,
                                const std::vector<cv::KeyPoint> &keypoints_old,
                                const std::vector<cv::KeyPoint> &keypoints_old_norm)
{
    for(int i = 0; i < (int)window_brief_descriptors.size(); i++)
    {
        cv::Point2f pt(0.f, 0.f);
        cv::Point2f pt_norm(0.f, 0.f);
        if (searchInAera(window_brief_descriptors[i], descriptors_old, keypoints_old, keypoints_old_norm, pt, pt_norm))
          status.push_back(1);
        else
          status.push_back(0);
        matched_2d_old.push_back(pt);
        matched_2d_old_norm.push_back(pt_norm);
    }

}


void KeyFrame::FundmantalMatrixRANSAC(const std::vector<cv::Point2f> &matched_2d_cur,
                                      const std::vector<cv::Point2f> &matched_2d_old,
                                      vector<uchar> &status)
{
	int n = (int)matched_2d_cur.size();
	for (int i = 0; i < n; i++)
		status.push_back(0);
    if (n >= 8)
    {
        cv::findFundamentalMat(matched_2d_cur, matched_2d_old,
                               cv::FM_RANSAC, 4.0, 0.99, status);
    }
}

void KeyFrame::PnPRANSAC(const vector<cv::Point2f> &matched_2d_old_norm,
                         const std::vector<cv::Point3f> &matched_3d,
                         std::vector<uchar> &status,
                         Eigen::Vector3d &PnP_T_old, Eigen::Matrix3d &PnP_R_old,
                         double &reprojection_rmse_px,
                         double &reprojection_p95_px)
{
	reprojection_rmse_px = std::numeric_limits<double>::infinity();
	reprojection_p95_px = std::numeric_limits<double>::infinity();
	//for (int i = 0; i < matched_3d.size(); i++)
	//	printf("3d x: %f, y: %f, z: %f\n",matched_3d[i].x, matched_3d[i].y, matched_3d[i].z );
	//printf("match size %d \n", matched_3d.size());
    cv::Mat r, rvec, t, D, tmp_r;
    cv::Mat K = (cv::Mat_<double>(3, 3) << 1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0);
    Matrix3d R_inital;
    Vector3d P_inital;
    Matrix3d R_w_c = origin_vio_R * qic;
    Vector3d T_w_c = origin_vio_T + origin_vio_R * tic;

    R_inital = R_w_c.inverse();
    P_inital = -(R_inital * T_w_c);

    cv::eigen2cv(R_inital, tmp_r);
    cv::Rodrigues(tmp_r, rvec);
    cv::eigen2cv(P_inital, t);

    cv::Mat inliers;
    TicToc t_pnp_ransac;

    std::vector<double> camera_parameters;
    m_camera->writeParameters(camera_parameters);
    double focal_length_px = 460.0;
    if (m_camera->modelType() == camodocal::Camera::PINHOLE &&
        camera_parameters.size() >= 8)
    {
        focal_length_px = 0.5 * (camera_parameters[4] + camera_parameters[5]);
    }
    const double reprojection_threshold_normalized = 4.0 / focal_length_px;

    if (CV_MAJOR_VERSION < 3)
        solvePnPRansac(matched_3d, matched_2d_old_norm, K, D, rvec, t, true, 200,
                       reprojection_threshold_normalized, 100, inliers);
    else
    {
        if (CV_MINOR_VERSION < 2)
            solvePnPRansac(matched_3d, matched_2d_old_norm, K, D, rvec, t, true, 200,
                           reprojection_threshold_normalized, 0.999, inliers);
        else
            solvePnPRansac(matched_3d, matched_2d_old_norm, K, D, rvec, t, true, 200,
                           reprojection_threshold_normalized, 0.999, inliers);

    }

    if (inliers.rows >= 4)
    {
        std::vector<cv::Point3f> inlier_points_3d;
        std::vector<cv::Point2f> inlier_points_2d;
        inlier_points_3d.reserve(inliers.rows);
        inlier_points_2d.reserve(inliers.rows);
        for (int i = 0; i < inliers.rows; ++i)
        {
            const int inlier_index = inliers.at<int>(i);
            inlier_points_3d.push_back(matched_3d[inlier_index]);
            inlier_points_2d.push_back(matched_2d_old_norm[inlier_index]);
        }
        solvePnP(inlier_points_3d, inlier_points_2d, K, D, rvec, t, true,
                 cv::SOLVEPNP_ITERATIVE);

		std::vector<cv::Point2f> projected_points;
		cv::projectPoints(inlier_points_3d, rvec, t, K, D, projected_points);
		std::vector<double> errors_px;
		errors_px.reserve(projected_points.size());
		double squared_error_sum = 0.0;
		for (size_t i = 0; i < projected_points.size(); ++i)
		{
			const double error_px =
				cv::norm(projected_points[i] - inlier_points_2d[i]) * focal_length_px;
			errors_px.push_back(error_px);
			squared_error_sum += error_px * error_px;
		}
		if (!errors_px.empty())
		{
			reprojection_rmse_px =
				std::sqrt(squared_error_sum / static_cast<double>(errors_px.size()));
			std::sort(errors_px.begin(), errors_px.end());
			const size_t p95_index = static_cast<size_t>(
				std::ceil(0.95 * static_cast<double>(errors_px.size()))) - 1;
			reprojection_p95_px = errors_px[std::min(p95_index, errors_px.size() - 1)];
		}
    }

    for (int i = 0; i < (int)matched_2d_old_norm.size(); i++)
        status.push_back(0);

    for( int i = 0; i < inliers.rows; i++)
    {
        int n = inliers.at<int>(i);
        status[n] = 1;
    }

    cv::Rodrigues(rvec, r);
    Matrix3d R_pnp, R_w_c_old;
    cv::cv2eigen(r, R_pnp);
    R_w_c_old = R_pnp.transpose();
    Vector3d T_pnp, T_w_c_old;
    cv::cv2eigen(t, T_pnp);
    T_w_c_old = R_w_c_old * (-T_pnp);

    PnP_R_old = R_w_c_old * qic.transpose();
    PnP_T_old = T_w_c_old - PnP_R_old * tic;

}


bool KeyFrame::findConnection(KeyFrame* old_kf)
{
	TicToc tmp_t;
	//printf("find Connection\n");
	vector<cv::Point2f> matched_2d_cur, matched_2d_old;
	vector<cv::Point2f> matched_2d_cur_norm, matched_2d_old_norm;
	vector<cv::Point3f> matched_3d;
	vector<double> matched_id;
	vector<uchar> status;

	const size_t tracked_point_count = point_2d_uv.size();
	TicToc t_match;
	#if 0
		if (DEBUG_IMAGE)    
	    {
	        cv::Mat gray_img, loop_match_img;
	        cv::Mat old_img = old_kf->image;
	        cv::hconcat(image, old_img, gray_img);
	        cvtColor(gray_img, loop_match_img, cv::COLOR_GRAY2RGB);
	        for(int i = 0; i< (int)point_2d_uv.size(); i++)
	        {
	            cv::Point2f cur_pt = point_2d_uv[i];
	            cv::circle(loop_match_img, cur_pt, 5, cv::Scalar(0, 255, 0));
	        }
	        for(int i = 0; i< (int)old_kf->keypoints.size(); i++)
	        {
	            cv::Point2f old_pt = old_kf->keypoints[i].pt;
	            old_pt.x += COL;
	            cv::circle(loop_match_img, old_pt, 5, cv::Scalar(0, 255, 0));
	        }
	        ostringstream path;
	        path << "/home/tony-ws1/raw_data/loop_image/"
	                << index << "-"
	                << old_kf->index << "-" << "0raw_point.jpg";
	        cv::imwrite( path.str().c_str(), loop_match_img);
	    }
	#endif
	const size_t current_descriptor_count = std::min(
		window_brief_descriptors.size(),
		std::min(point_2d_uv.size(),
		         std::min(point_2d_norm.size(),
		                  std::min(point_3d.size(), point_id.size()))));
	const size_t old_descriptor_count = std::min(
		old_kf->brief_descriptors.size(),
		std::min(old_kf->keypoints.size(), old_kf->keypoints_norm.size()));
	if (current_descriptor_count == 0 || old_descriptor_count < 2)
	{
		printf("[AUTO_LOOP_REJECT] current=%d matched=%d reason=no_brief_descriptors\n",
		       index, old_kf->index);
		return false;
	}

	const std::vector<tracked_brief::Match> brief_matches =
		tracked_brief::match(
			window_brief_descriptors, current_descriptor_count,
			old_kf->brief_descriptors, old_descriptor_count);
	for (const tracked_brief::Match &match : brief_matches)
	{
		matched_2d_cur.push_back(point_2d_uv[match.current_index]);
		matched_2d_cur_norm.push_back(point_2d_norm[match.current_index]);
		matched_2d_old.push_back(old_kf->keypoints[match.old_index].pt);
		matched_2d_old_norm.push_back(old_kf->keypoints_norm[match.old_index].pt);
		matched_3d.push_back(point_3d[match.current_index]);
		matched_id.push_back(point_id[match.current_index]);
	}
	const size_t descriptor_match_count = matched_2d_cur.size();
	if (descriptor_match_count >= 8)
	{
		status.clear();
		FundmantalMatrixRANSAC(matched_2d_cur, matched_2d_old, status);
		reduceVector(matched_2d_cur, status);
		reduceVector(matched_2d_old, status);
		reduceVector(matched_2d_cur_norm, status);
		reduceVector(matched_2d_old_norm, status);
		reduceVector(matched_3d, status);
		reduceVector(matched_id, status);
	}

	#if 0 
		if (DEBUG_IMAGE)
	    {
			int gap = 10;
        	cv::Mat gap_image(ROW, gap, CV_8UC1, cv::Scalar(255, 255, 255));
            cv::Mat gray_img, loop_match_img;
            cv::Mat old_img = old_kf->image;
            cv::hconcat(image, gap_image, gap_image);
            cv::hconcat(gap_image, old_img, gray_img);
            cvtColor(gray_img, loop_match_img, cv::COLOR_GRAY2RGB);
	        for(int i = 0; i< (int)matched_2d_cur.size(); i++)
	        {
	            cv::Point2f cur_pt = matched_2d_cur[i];
	            cv::circle(loop_match_img, cur_pt, 5, cv::Scalar(0, 255, 0));
	        }
	        for(int i = 0; i< (int)matched_2d_old.size(); i++)
	        {
	            cv::Point2f old_pt = matched_2d_old[i];
	            old_pt.x += (COL + gap);
	            cv::circle(loop_match_img, old_pt, 5, cv::Scalar(0, 255, 0));
	        }
	        for (int i = 0; i< (int)matched_2d_cur.size(); i++)
	        {
	            cv::Point2f old_pt = matched_2d_old[i];
	            old_pt.x +=  (COL + gap);
	            cv::line(loop_match_img, matched_2d_cur[i], old_pt, cv::Scalar(0, 255, 0), 1, 8, 0);
	        }

	        ostringstream path, path1, path2;
	        path <<  "/home/tony-ws1/raw_data/loop_image/"
	                << index << "-"
	                << old_kf->index << "-" << "1descriptor_match.jpg";
	        cv::imwrite( path.str().c_str(), loop_match_img);
	        /*
	        path1 <<  "/home/tony-ws1/raw_data/loop_image/"
	                << index << "-"
	                << old_kf->index << "-" << "1descriptor_match_1.jpg";
	        cv::imwrite( path1.str().c_str(), image);
	        path2 <<  "/home/tony-ws1/raw_data/loop_image/"
	                << index << "-"
	                << old_kf->index << "-" << "1descriptor_match_2.jpg";
	        cv::imwrite( path2.str().c_str(), old_img);	        
	        */
	        
	    }
	#endif
	status.clear();
	/*
	FundmantalMatrixRANSAC(matched_2d_cur, matched_2d_old, status);
	reduceVector(matched_2d_cur, status);
	reduceVector(matched_2d_old, status);
	reduceVector(matched_2d_cur_norm, status);
	reduceVector(matched_2d_old_norm, status);
	reduceVector(matched_3d, status);
	reduceVector(matched_id, status);
	*/
	#if 0
		if (DEBUG_IMAGE)
	    {
			int gap = 10;
        	cv::Mat gap_image(ROW, gap, CV_8UC1, cv::Scalar(255, 255, 255));
            cv::Mat gray_img, loop_match_img;
            cv::Mat old_img = old_kf->image;
            cv::hconcat(image, gap_image, gap_image);
            cv::hconcat(gap_image, old_img, gray_img);
            cvtColor(gray_img, loop_match_img, cv::COLOR_GRAY2RGB);
	        for(int i = 0; i< (int)matched_2d_cur.size(); i++)
	        {
	            cv::Point2f cur_pt = matched_2d_cur[i];
	            cv::circle(loop_match_img, cur_pt, 5, cv::Scalar(0, 255, 0));
	        }
	        for(int i = 0; i< (int)matched_2d_old.size(); i++)
	        {
	            cv::Point2f old_pt = matched_2d_old[i];
	            old_pt.x += (COL + gap);
	            cv::circle(loop_match_img, old_pt, 5, cv::Scalar(0, 255, 0));
	        }
	        for (int i = 0; i< (int)matched_2d_cur.size(); i++)
	        {
	            cv::Point2f old_pt = matched_2d_old[i];
	            old_pt.x +=  (COL + gap) ;
	            cv::line(loop_match_img, matched_2d_cur[i], old_pt, cv::Scalar(0, 255, 0), 1, 8, 0);
	        }

	        ostringstream path;
	        path <<  "/home/tony-ws1/raw_data/loop_image/"
	                << index << "-"
	                << old_kf->index << "-" << "2fundamental_match.jpg";
	        cv::imwrite( path.str().c_str(), loop_match_img);
	    }
	#endif
	Eigen::Vector3d PnP_T_old;
	Eigen::Matrix3d PnP_R_old;
	Eigen::Vector3d relative_t;
	Quaterniond relative_q;
	double relative_yaw;
	double pnp_reprojection_rmse_px = std::numeric_limits<double>::infinity();
	double pnp_reprojection_p95_px = std::numeric_limits<double>::infinity();
	if ((int)matched_2d_cur.size() > MIN_LOOP_NUM)
	{
		status.clear();
	    PnPRANSAC(matched_2d_old_norm, matched_3d, status, PnP_T_old, PnP_R_old,
	               pnp_reprojection_rmse_px, pnp_reprojection_p95_px);
	    reduceVector(matched_2d_cur, status);
	    reduceVector(matched_2d_old, status);
	    reduceVector(matched_2d_cur_norm, status);
	    reduceVector(matched_2d_old_norm, status);
	    reduceVector(matched_3d, status);
	    reduceVector(matched_id, status);
	    #if 1
	    	if (DEBUG_IMAGE)
	        {
	        	int gap = 10;
	        	cv::Mat gap_image(ROW, gap, CV_8UC1, cv::Scalar(255, 255, 255));
	            cv::Mat gray_img, loop_match_img;
	            cv::Mat old_img = old_kf->image;
	            cv::hconcat(image, gap_image, gap_image);
	            cv::hconcat(gap_image, old_img, gray_img);
	            cvtColor(gray_img, loop_match_img, cv::COLOR_GRAY2RGB);
	            for(int i = 0; i< (int)matched_2d_cur.size(); i++)
	            {
	                cv::Point2f cur_pt = matched_2d_cur[i];
	                cv::circle(loop_match_img, cur_pt, 5, cv::Scalar(0, 255, 0));
	            }
	            for(int i = 0; i< (int)matched_2d_old.size(); i++)
	            {
	                cv::Point2f old_pt = matched_2d_old[i];
	                old_pt.x += (COL + gap);
	                cv::circle(loop_match_img, old_pt, 5, cv::Scalar(0, 255, 0));
	            }
	            for (int i = 0; i< (int)matched_2d_cur.size(); i++)
	            {
	                cv::Point2f old_pt = matched_2d_old[i];
	                old_pt.x += (COL + gap) ;
	                cv::line(loop_match_img, matched_2d_cur[i], old_pt, cv::Scalar(0, 255, 0), 2, 8, 0);
	            }
	            cv::Mat notation(50, COL + gap + COL, CV_8UC3, cv::Scalar(255, 255, 255));
	            putText(notation, "current frame: " + to_string(index) + "  sequence: " + to_string(sequence), cv::Point2f(20, 30), cv::FONT_HERSHEY_SIMPLEX, 1, cv::Scalar(255), 3);

	            putText(notation, "previous frame: " + to_string(old_kf->index) + "  sequence: " + to_string(old_kf->sequence), cv::Point2f(20 + COL + gap, 30), cv::FONT_HERSHEY_SIMPLEX, 1, cv::Scalar(255), 3);
	            cv::vconcat(notation, loop_match_img, loop_match_img);

	            /*
	            ostringstream path;
	            path <<  "/home/tony-ws1/raw_data/loop_image/"
	                    << index << "-"
	                    << old_kf->index << "-" << "3pnp_match.jpg";
	            cv::imwrite( path.str().c_str(), loop_match_img);
	            */
	            if ((int)matched_2d_cur.size() > MIN_LOOP_NUM)
	            {
	            	/*
	            	cv::imshow("loop connection",loop_match_img);  
	            	cv::waitKey(10);  
	            	*/
	            	cv::Mat thumbimage;
	            	cv::resize(loop_match_img, thumbimage, cv::Size(loop_match_img.cols / 2, loop_match_img.rows / 2));
	    	    	// sensor_msgs::msg::ImagePtr
					sensor_msgs::msg::Image::SharedPtr msg = cv_bridge::CvImage(std_msgs::msg::Header(), "bgr8", thumbimage).toImageMsg();
	                
					int sec_ts = (int)time_stamp;
					uint nsec_ts = (uint)((time_stamp - sec_ts) * 1e9);
					msg->header.stamp.sec = sec_ts;
					msg->header.stamp.nanosec = nsec_ts;

	    	    	pub_match_img->publish(*msg);
	            }
	        }
	    #endif
	}

	const double pnp_inlier_ratio = descriptor_match_count > 0
		? static_cast<double>(matched_2d_cur.size()) / descriptor_match_count
		: 0.0;
	const double current_hull_fraction = imageHullFraction(matched_2d_cur);
	const double old_hull_fraction = imageHullFraction(matched_2d_old);
	const double spatial_support =
		std::min(current_hull_fraction, old_hull_fraction);
	if (!matched_2d_cur.empty())
	{
		printf("[AUTO_LOOP_PNP_QUALITY] current=%d matched=%d inliers=%zu "
		       "rmse_px=%.3f p95_px=%.3f current_hull=%.4f old_hull=%.4f\n",
		       index, old_kf->index, matched_2d_cur.size(),
		       pnp_reprojection_rmse_px, pnp_reprojection_p95_px,
		       current_hull_fraction, old_hull_fraction);
	}
	if ((int)matched_2d_cur.size() > MIN_LOOP_NUM &&
	    pnp_inlier_ratio >= MIN_LOOP_INLIER_RATIO &&
	    spatial_support < MIN_LOOP_SPATIAL_SUPPORT)
	{
		printf("[AUTO_LOOP_SPATIAL_REJECT] current=%d matched=%d "
		       "support=%.4f threshold=%.4f\n",
		       index, old_kf->index, spatial_support,
		       MIN_LOOP_SPATIAL_SUPPORT);
		return false;
	}
	if ((int)matched_2d_cur.size() > MIN_LOOP_NUM &&
	    pnp_inlier_ratio >= MIN_LOOP_INLIER_RATIO)
	{
	    if (!verifyRightImageLoop(old_kf))
	        return false;
	    relative_t = PnP_R_old.transpose() * (origin_vio_T - PnP_T_old);
	    relative_q = PnP_R_old.transpose() * origin_vio_R;
	    relative_yaw = Utility::normalizeAngle(Utility::R2ypr(origin_vio_R).x() - Utility::R2ypr(PnP_R_old).x());
	    //printf("PNP relative\n");
	    //cout << "pnp relative_t " << relative_t.transpose() << endl;
	    //cout << "pnp relative_yaw " << relative_yaw << endl;
	    if (abs(relative_yaw) < 30.0 && relative_t.norm() < 20.0)
	    {

	    	has_loop = true;
	    	loop_index = old_kf->index;
	    	loop_info << relative_t.x(), relative_t.y(), relative_t.z(),
	    	             relative_q.w(), relative_q.x(), relative_q.y(), relative_q.z(),
	    	             relative_yaw;
	    	printf("[AUTO_LOOP_GEOMETRY_PASS] current=%d matched=%d inliers=%zu ratio=%.3f "
	    	       "rel_t_m=%.4f yaw_deg=%.2f\n",
	    	       index, old_kf->index, matched_2d_cur.size(), pnp_inlier_ratio,
	    	       relative_t.norm(), relative_yaw);
	    	//cout << "pnp relative_t " << relative_t.transpose() << endl;
	    	//cout << "pnp relative_q " << relative_q.w() << " " << relative_q.vec().transpose() << endl;
	        return true;
	    }
	}
	if ((int)matched_2d_cur.size() >= 4 && PnP_T_old.allFinite() && PnP_R_old.allFinite())
	{
	    relative_t = PnP_R_old.transpose() * (origin_vio_T - PnP_T_old);
	    relative_yaw = Utility::normalizeAngle(Utility::R2ypr(origin_vio_R).x() - Utility::R2ypr(PnP_R_old).x());
	    printf("[AUTO_LOOP_REJECT] current=%d matched=%d tracked=%zu descriptor=%zu "
	           "inliers=%zu ratio=%.3f rel_t_m=%.4f yaw_deg=%.2f\n",
	           index, old_kf->index, tracked_point_count, descriptor_match_count,
	           matched_2d_cur.size(), pnp_inlier_ratio, relative_t.norm(), relative_yaw);
	}
	else
	{
	    printf("[AUTO_LOOP_REJECT] current=%d matched=%d tracked=%zu descriptor=%zu inliers=%zu\n",
	           index, old_kf->index, tracked_point_count, descriptor_match_count,
	           matched_2d_cur.size());
	}
	//printf("loop final use num %d %lf--------------- \n", (int)matched_2d_cur.size(), t_match.toc());
	return false;
}


int KeyFrame::HammingDis(const BRIEF::bitset &a, const BRIEF::bitset &b)
{
    BRIEF::bitset xor_of_bitset = a ^ b;
    int dis = xor_of_bitset.count();
    return dis;
}

void KeyFrame::getVioPose(Eigen::Vector3d &_T_w_i, Eigen::Matrix3d &_R_w_i)
{
    _T_w_i = vio_T_w_i;
    _R_w_i = vio_R_w_i;
}

void KeyFrame::getPose(Eigen::Vector3d &_T_w_i, Eigen::Matrix3d &_R_w_i)
{
    _T_w_i = T_w_i;
    _R_w_i = R_w_i;
}

void KeyFrame::updatePose(const Eigen::Vector3d &_T_w_i, const Eigen::Matrix3d &_R_w_i)
{
    T_w_i = _T_w_i;
    R_w_i = _R_w_i;
}

void KeyFrame::updateVioPose(const Eigen::Vector3d &_T_w_i, const Eigen::Matrix3d &_R_w_i)
{
	vio_T_w_i = _T_w_i;
	vio_R_w_i = _R_w_i;
	T_w_i = vio_T_w_i;
	R_w_i = vio_R_w_i;
}

Eigen::Vector3d KeyFrame::getLoopRelativeT()
{
    return Eigen::Vector3d(loop_info(0), loop_info(1), loop_info(2));
}

Eigen::Quaterniond KeyFrame::getLoopRelativeQ()
{
    return Eigen::Quaterniond(loop_info(3), loop_info(4), loop_info(5), loop_info(6));
}

double KeyFrame::getLoopRelativeYaw()
{
    return loop_info(7);
}

void KeyFrame::updateLoop(Eigen::Matrix<double, 8, 1 > &_loop_info)
{
	if (abs(_loop_info(7)) < 30.0 && Vector3d(_loop_info(0), _loop_info(1), _loop_info(2)).norm() < 20.0)
	{
		//printf("update loop info\n");
		loop_info = _loop_info;
	}
}

void KeyFrame::clearLoop()
{
	has_loop = false;
	loop_index = -1;
	loop_info.setZero();
}

BriefExtractor::BriefExtractor(const std::string &pattern_file)
{
  // The DVision::BRIEF extractor computes a random pattern by default when
  // the object is created.
  // We load the pattern that we used to build the vocabulary, to make
  // the descriptors compatible with the predefined vocabulary

  // loads the pattern
  cv::FileStorage fs(pattern_file.c_str(), cv::FileStorage::READ);
  if(!fs.isOpened()) throw string("Could not open file ") + pattern_file;

  vector<int> x1, y1, x2, y2;
  fs["x1"] >> x1;
  fs["x2"] >> x2;
  fs["y1"] >> y1;
  fs["y2"] >> y2;

  m_brief.importPairs(x1, y1, x2, y2);
}
