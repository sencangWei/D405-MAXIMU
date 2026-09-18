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

#pragma once

#include "vins/camera_models/CameraFactory.h"
#include "vins/camera_models/CataCamera.h"
#include "vins/camera_models/PinholeCamera.h"
#include <eigen3/Eigen/Dense>
#include "rclcpp/rclcpp.hpp"
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/point_cloud.hpp>
// #include <sensor_msgs/image_encodings.h>
#include "image_encodings.hpp"
#include <cv_bridge/cv_bridge.h>

extern camodocal::CameraPtr m_camera;
extern Eigen::Vector3d tic;
extern Eigen::Matrix3d qic;
extern rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr pub_match_img;
extern int VISUALIZATION_SHIFT_X;
extern int VISUALIZATION_SHIFT_Y;
extern std::string BRIEF_PATTERN_FILE;
extern std::string POSE_GRAPH_SAVE_PATH;
extern int ROW;
extern int COL;
extern std::string VINS_RESULT_PATH;
extern int DEBUG_IMAGE;
extern double MIN_LOOP_SPATIAL_SUPPORT;
extern int MAX_LOOP_CANDIDATES;
// Large loop corrections are accepted only after stronger multi-frame,
// dual-IR geometric confirmation.  These are loop-graph gates only; VIO
// frames with weak visual quality remain in the estimator with lower weight.
extern int LOOP_CONFIRMATIONS;
extern double LARGE_LOOP_CORRECTION_THRESHOLD_M;
extern int LARGE_LOOP_CONFIRMATIONS;
extern int LARGE_LOOP_MIN_PNP_INLIERS;
extern double LARGE_LOOP_MIN_PNP_INLIER_RATIO;
extern int LARGE_LOOP_MIN_RIGHT_INLIERS;
extern double LARGE_LOOP_MIN_RIGHT_INLIER_RATIO;
extern double LARGE_LOOP_MAX_PNP_RMSE_PX;
extern double LARGE_LOOP_MAX_PNP_P95_PX;
extern std::string LEARNED_LOOP_MATCHES_PATH;
extern double LEARNED_LOOP_TIMESTAMP_TOLERANCE_S;
extern double LEARNED_LOOP_MAX_TRACK_ASSOCIATION_PX;
extern int LEARNED_LOOP_MIN_MATCHES;
extern std::string WORLD_FRAME_ID;
extern std::string BODY_FRAME_ID;
extern std::string CAMERA_FRAME_ID;
