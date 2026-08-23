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
extern camodocal::CameraPtr m_camera_right;
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
constexpr double MINIMUM_STEREO_LOOP_SPATIAL_SUPPORT = 0.02;
extern double STEREO_BASELINE_M;
extern int MAX_LOOP_CANDIDATES;
extern double BASE_LOOP_CORRECTION_LIMIT_M;
extern double LOOP_CORRECTION_EXTENT_RATIO;
extern double CONFIRMED_LOOP_CORRECTION_CEILING_M;
extern double EXPANDED_LOOP_MIN_RETRIEVAL_SCORE;
extern int ODOMETRY_EDGE_FULL_SUPPORT_POINTS;
extern double ODOMETRY_EDGE_MINIMUM_WEIGHT;
extern bool PRESERVE_LOOP_EXCURSION_ANCHOR;
extern bool FREEZE_LOOP_OUTBOUND_TO_ANCHOR;
extern int ACCEPTED_LOOP_COOLDOWN_KEYFRAMES;
extern double LOOP_EDGE_WEIGHT;
extern std::string WORLD_FRAME_ID;
extern std::string BODY_FRAME_ID;
extern std::string CAMERA_FRAME_ID;
