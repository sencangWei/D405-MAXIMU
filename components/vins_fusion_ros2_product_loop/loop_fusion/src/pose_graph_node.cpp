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

#include <algorithm>
#include <cmath>
#include <vector>
#include <rclcpp/rclcpp.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <nav_msgs/msg/path.hpp>
#include <sensor_msgs/msg/point_cloud.hpp>
#include <sensor_msgs/msg/image.hpp>
// #include <sensor_msgs/image_encodings.h>
#include "image_encodings.hpp"
#include <visualization_msgs/msg/marker.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/string.hpp>
#include <vins_fusion_ros2/msg/loop_key_frame.hpp>
#include <cv_bridge/cv_bridge.h>
#include <iostream>
// #include <ros/package.h>
#include <ament_index_cpp/get_package_share_directory.hpp>
#include <mutex>
#include <queue>
#include <sstream>
#include <thread>
#include <atomic>
#include <eigen3/Eigen/Dense>
#include <opencv2/opencv.hpp>
#include <opencv2/core/eigen.hpp>
#include "keyframe.h"
#include "utility/tic_toc.h"
#include "pose_graph.h"
#include "loop_correction_policy.h"
#include "feature_edge_weight.h"
#include "loop_excursion_anchor.h"
#include "loop_edge_strength.h"
#include "realtime_drift_limiter.h"
#include "utility/CameraPoseVisualization.h"
// #include "camodocal/camera_models/CameraFactory.h"
#include "parameters.h"
#define SKIP_FIRST_CNT 0
using namespace std;

queue<vins_fusion_ros2::msg::LoopKeyFrame::ConstSharedPtr> keyframe_buf;
queue<Eigen::Vector3d> odometry_buf;
std::mutex m_buf;
std::mutex m_process;
int frame_index  = 0;
int sequence = 1;
PoseGraph posegraph;
int skip_first_cnt = 0;
int SKIP_CNT;
int skip_cnt = 0;
bool load_flag = 0;
bool start_flag = 0;
double SKIP_DIS = 0;
std::atomic<bool> running{true};
std::atomic<uint64_t> stereo_keyframes_received{0};
std::atomic<uint64_t> keyframe_transport_drops{0};
std::atomic<uint64_t> keyframe_backlog_drops{0};
std::atomic<bool> loop_input_integrity_failed{false};
std::mutex m_origin_reference;
bool first_odometry_initialized = false;
bool origin_reference_finalized = false;
double first_odometry_time = 0.0;
Eigen::Vector3d first_odometry_translation = Eigen::Vector3d::Zero();
Eigen::Quaterniond first_odometry_rotation = Eigen::Quaterniond::Identity();

int VISUALIZATION_SHIFT_X;
int VISUALIZATION_SHIFT_Y;
int ROW;
int COL;
int DEBUG_IMAGE;
double MIN_LOOP_SPATIAL_SUPPORT = 0.0;
double STEREO_BASELINE_M = 0.0;
int MAX_LOOP_CANDIDATES = 4;
double BASE_LOOP_CORRECTION_LIMIT_M = 0.03;
double LOOP_CORRECTION_EXTENT_RATIO = 0.0;
double CONFIRMED_LOOP_CORRECTION_CEILING_M = 0.03;
double EXPANDED_LOOP_MIN_RETRIEVAL_SCORE = 0.0;
int ODOMETRY_EDGE_FULL_SUPPORT_POINTS = 0;
double ODOMETRY_EDGE_MINIMUM_WEIGHT = 1.0;
bool PRESERVE_LOOP_EXCURSION_ANCHOR = false;
bool FREEZE_LOOP_OUTBOUND_TO_ANCHOR = false;
int ACCEPTED_LOOP_COOLDOWN_KEYFRAMES = 10;
double LOOP_EDGE_WEIGHT = 1.0;
double MAX_STEREO_CAMERA_PARAMETER_DELTA = 0.5;
double REALTIME_DRIFT_TRANSLATION_RATE_MPS = 0.0;
double REALTIME_DRIFT_ROTATION_RATE_DEG_S = 0.0;
std::unique_ptr<RealtimeDriftLimiter> realtime_drift_limiter;
std::mutex m_realtime_point_cloud_transform;
LimitedDriftTransform realtime_point_cloud_transform;

std::string WORLD_FRAME_ID = "world";
std::string BODY_FRAME_ID = "body";
std::string CAMERA_FRAME_ID = "camera";

camodocal::CameraPtr m_camera;
camodocal::CameraPtr m_camera_right;
Eigen::Vector3d tic;
Eigen::Matrix3d qic;
rclcpp::Publisher<sensor_msgs::msg::Image>::SharedPtr pub_match_img;
rclcpp::Publisher<visualization_msgs::msg::MarkerArray>::SharedPtr pub_camera_pose_visual;
rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr pub_odometry_rect;
rclcpp::Publisher<std_msgs::msg::String>::SharedPtr pub_origin_reference_status;

std::string BRIEF_PATTERN_FILE;
std::string POSE_GRAPH_SAVE_PATH;
std::string VINS_RESULT_PATH;
CameraPoseVisualization cameraposevisual(1, 0, 0, 1);
Eigen::Vector3d last_t(-100, -100, -100);

rclcpp::Publisher<sensor_msgs::msg::PointCloud>::SharedPtr pub_point_cloud, pub_margin_cloud;

void publish_origin_reference_status(
    const std::string &state,
    const double first_keyframe_delay_s = 0.0,
    const double initial_translation_m = 0.0,
    const double initial_rotation_deg = 0.0)
{
    if (!pub_origin_reference_status)
        return;
    std::ostringstream body;
    body.setf(std::ios::fixed);
    body.precision(6);
    body << "{\"state\":\"" << state << "\""
         << ",\"first_keyframe_delay_s\":" << first_keyframe_delay_s
         << ",\"initial_translation_m\":" << initial_translation_m
         << ",\"initial_rotation_deg\":" << initial_rotation_deg << "}";
    std_msgs::msg::String message;
    message.data = body.str();
    pub_origin_reference_status->publish(message);
}

void finalize_origin_reference_status(
    const double keyframe_time,
    const Eigen::Vector3d &keyframe_translation,
    const Eigen::Matrix3d &keyframe_rotation)
{
    constexpr double kMaxInitialTranslationM = 0.05;
    constexpr double kMaxInitialRotationDeg = 15.0;
    std::lock_guard<std::mutex> lock(m_origin_reference);
    if (origin_reference_finalized)
        return;
    if (!first_odometry_initialized)
    {
        origin_reference_finalized = true;
        publish_origin_reference_status("UNAVAILABLE_NO_ODOMETRY");
        printf("[LOOP_ORIGIN_REFERENCE] state=UNAVAILABLE_NO_ODOMETRY\n");
        return;
    }

    const double delay_s = keyframe_time - first_odometry_time;
    const double translation_m =
        (keyframe_translation - first_odometry_translation).norm();
    const Eigen::Quaterniond keyframe_quaternion(keyframe_rotation);
    const double rotation_deg = first_odometry_rotation.angularDistance(
        keyframe_quaternion) * 180.0 / M_PI;
    const bool ready = translation_m <= kMaxInitialTranslationM &&
                       rotation_deg <= kMaxInitialRotationDeg;
    const std::string state = ready
        ? "READY"
        : "UNAVAILABLE_INITIALIZATION_MOTION";
    origin_reference_finalized = true;
    publish_origin_reference_status(
        state, delay_s, translation_m, rotation_deg);
    printf("[LOOP_ORIGIN_REFERENCE] state=%s first_keyframe_delay_s=%.6f "
           "initial_translation_m=%.6f initial_rotation_deg=%.6f\n",
           state.c_str(), delay_s, translation_m, rotation_deg);
}

void new_sequence()
{
    printf("new sequence\n");
    sequence++;
    printf("sequence cnt %d \n", sequence);
    if (sequence > 5)
    {
        ROS_WARN("only support 5 sequences since it's boring to copy code for more sequences.");
        // ROS_BREAK();
    }
    posegraph.posegraph_visualization->reset();
    posegraph.publish();
    m_buf.lock();
    while(!keyframe_buf.empty())
        keyframe_buf.pop();
    while(!odometry_buf.empty())
        odometry_buf.pop();
    m_buf.unlock();
    {
        std::lock_guard<std::mutex> lock(m_origin_reference);
        first_odometry_initialized = false;
        origin_reference_finalized = false;
    }
    publish_origin_reference_status("INITIALIZING");
}

void keyframe_callback(
    const vins_fusion_ros2::msg::LoopKeyFrame::ConstSharedPtr msg)
{
    std::lock_guard<std::mutex> lock(m_buf);
    static uint64_t expected_sequence = 0;
    static bool sequence_initialized = false;
    const uint64_t count = stereo_keyframes_received.fetch_add(1) + 1;
    if (!sequence_initialized)
    {
        expected_sequence = msg->sequence;
        sequence_initialized = true;
    }
    if (msg->sequence < expected_sequence)
    {
        ++keyframe_transport_drops;
        loop_input_integrity_failed = true;
        running = false;
        printf("[LOOP_INPUT_FATAL] reason=out_of_order sequence=%lu expected=%lu\n",
               msg->sequence, expected_sequence);
        rclcpp::shutdown();
        return;
    }
    if (msg->sequence > expected_sequence)
    {
        const uint64_t dropped = msg->sequence - expected_sequence;
        keyframe_transport_drops += dropped;
        loop_input_integrity_failed = true;
        running = false;
        printf("[LOOP_INPUT_FATAL] reason=transport sequence=%lu expected=%lu "
               "dropped=%lu total_transport_drops=%lu\n",
               msg->sequence, expected_sequence, dropped,
               keyframe_transport_drops.load());
        rclcpp::shutdown();
        return;
    }
    expected_sequence = msg->sequence + 1;
    constexpr size_t kMaxProcessingBacklog = 160;
    if (keyframe_buf.size() >= kMaxProcessingBacklog)
    {
        ++keyframe_backlog_drops;
        loop_input_integrity_failed = true;
        running = false;
        printf("[LOOP_INPUT_FATAL] reason=processing_backlog total_backlog_drops=%lu\n",
               keyframe_backlog_drops.load());
        rclcpp::shutdown();
        return;
    }
    keyframe_buf.push(msg);
    if (count % 50 == 0)
        printf("[LOOP_INPUT] atomic_keyframes=%lu transport_drops=%lu "
               "backlog_drops=%lu backlog=%zu\n",
               count, keyframe_transport_drops.load(),
               keyframe_backlog_drops.load(), keyframe_buf.size());
}

// only for visualization
void margin_point_callback(const sensor_msgs::msg::PointCloud::SharedPtr point_msg)
{
    if (loop_input_integrity_failed)
        return;
    LimitedDriftTransform applied_transform;
    {
        std::lock_guard<std::mutex> lock(m_realtime_point_cloud_transform);
        applied_transform = realtime_point_cloud_transform;
    }
    sensor_msgs::msg::PointCloud point_cloud;
    point_cloud.header = point_msg->header;
    for (unsigned int i = 0; i < point_msg->points.size(); i++)
    {
        cv::Point3f p_3d;
        p_3d.x = point_msg->points[i].x;
        p_3d.y = point_msg->points[i].y;
        p_3d.z = point_msg->points[i].z;
        Eigen::Vector3d tmp = applied_transform.rotation *
            Eigen::Vector3d(p_3d.x, p_3d.y, p_3d.z) +
            applied_transform.translation;
        geometry_msgs::msg::Point32 p;
        p.x = tmp(0);
        p.y = tmp(1);
        p.z = tmp(2);
        point_cloud.points.push_back(p);
    }
    pub_margin_cloud->publish(point_cloud);
}

void vio_callback(const nav_msgs::msg::Odometry::SharedPtr pose_msg)
{
    if (loop_input_integrity_failed)
        return;
    //ROS_INFO("vio_callback!");
    Vector3d vio_t(pose_msg->pose.pose.position.x, pose_msg->pose.pose.position.y, pose_msg->pose.pose.position.z);
    Quaterniond vio_q;
    vio_q.w() = pose_msg->pose.pose.orientation.w;
    vio_q.x() = pose_msg->pose.pose.orientation.x;
    vio_q.y() = pose_msg->pose.pose.orientation.y;
    vio_q.z() = pose_msg->pose.pose.orientation.z;
    {
        std::lock_guard<std::mutex> lock(m_origin_reference);
        if (!first_odometry_initialized)
        {
            first_odometry_initialized = true;
            first_odometry_time = pose_msg->header.stamp.sec +
                                  pose_msg->header.stamp.nanosec * 1e-9;
            first_odometry_translation = vio_t;
            first_odometry_rotation = vio_q.normalized();
        }
    }

    const PoseGraphTransformSnapshot transform = posegraph.getTransformSnapshot();
    LimitedDriftTransform target_transform;
    target_transform.rotation = transform.r_drift * transform.w_r_vio;
    target_transform.translation =
        transform.r_drift * transform.w_t_vio + transform.t_drift;
    const double timestamp_s = pose_msg->header.stamp.sec +
                               pose_msg->header.stamp.nanosec * 1e-9;
    const LimitedDriftTransform applied_transform =
        realtime_drift_limiter
            ? realtime_drift_limiter->update(timestamp_s, target_transform)
            : target_transform;
    LimitedDriftTransform world_alignment;
    world_alignment.rotation = transform.w_r_vio;
    world_alignment.translation = transform.w_t_vio;
    {
        std::lock_guard<std::mutex> lock(m_realtime_point_cloud_transform);
        realtime_point_cloud_transform = driftTransformForAlignedFrame(
            applied_transform, world_alignment);
    }
    vio_t = applied_transform.rotation * vio_t + applied_transform.translation;
    vio_q = applied_transform.rotation * vio_q;

    nav_msgs::msg::Odometry odometry;
    odometry.header = pose_msg->header;
    odometry.header.frame_id = WORLD_FRAME_ID;
    odometry.pose.pose.position.x = vio_t.x();
    odometry.pose.pose.position.y = vio_t.y();
    odometry.pose.pose.position.z = vio_t.z();
    odometry.pose.pose.orientation.x = vio_q.x();
    odometry.pose.pose.orientation.y = vio_q.y();
    odometry.pose.pose.orientation.z = vio_q.z();
    odometry.pose.pose.orientation.w = vio_q.w();
    pub_odometry_rect->publish(odometry);

    Vector3d vio_t_cam;
    Quaterniond vio_q_cam;
    vio_t_cam = vio_t + vio_q * tic;
    vio_q_cam = vio_q * qic;

    cameraposevisual.reset();
    cameraposevisual.add_pose(vio_t_cam, vio_q_cam);
    cameraposevisual.publish_by(pub_camera_pose_visual, pose_msg->header);


}

void extrinsic_callback(const nav_msgs::msg::Odometry::SharedPtr pose_msg)
{
    m_process.lock();
    tic = Vector3d(pose_msg->pose.pose.position.x,
                   pose_msg->pose.pose.position.y,
                   pose_msg->pose.pose.position.z);
    qic = Quaterniond(pose_msg->pose.pose.orientation.w,
                      pose_msg->pose.pose.orientation.x,
                      pose_msg->pose.pose.orientation.y,
                      pose_msg->pose.pose.orientation.z).toRotationMatrix();
    m_process.unlock();
}

void process()
{
    while (rclcpp::ok() && running)
    {
        vins_fusion_ros2::msg::LoopKeyFrame::ConstSharedPtr keyframe_msg;

        {
            std::lock_guard<std::mutex> lock(m_buf);
            if (!keyframe_buf.empty())
            {
                keyframe_msg = keyframe_buf.front();
                keyframe_buf.pop();
            }
        }

        if (keyframe_msg != nullptr)
        {
            const auto image_msg = std::make_shared<sensor_msgs::msg::Image>(
                keyframe_msg->stereo);
            const auto pose_msg = std::make_shared<nav_msgs::msg::Odometry>(
                keyframe_msg->pose);
            const auto point_msg = std::make_shared<sensor_msgs::msg::PointCloud>(
                keyframe_msg->cloud);
            //printf(" pose time %f \n", pose_msg->header.stamp.sec);
            //printf(" point time %f \n", point_msg->header.stamp.sec);
            //printf(" image time %f \n", image_msg->header.stamp.sec);
            // skip fisrt few
            if (skip_first_cnt < SKIP_FIRST_CNT)
            {
                skip_first_cnt++;
                continue;
            }

            if (skip_cnt < SKIP_CNT)
            {
                skip_cnt++;
                continue;
            }
            else
            {
                skip_cnt = 0;
            }

            cv_bridge::CvImageConstPtr ptr = cv_bridge::toCvCopy(
                image_msg, sensor_msgs::image_encodings::MONO8);
            if (ptr->image.cols != 2 * COL || ptr->image.rows != ROW)
            {
                printf("[AUTO_LOOP_KEYFRAME_REJECT] reason=invalid_stereo_pair "
                       "width=%d height=%d expected_width=%d expected_height=%d\n",
                       ptr->image.cols, ptr->image.rows, 2 * COL, ROW);
                continue;
            }
            cv::Mat image = ptr->image(cv::Rect(0, 0, COL, ROW)).clone();
            cv::Mat right_image = ptr->image(cv::Rect(COL, 0, COL, ROW)).clone();
            const double pose_time = pose_msg->header.stamp.sec +
                                     pose_msg->header.stamp.nanosec * 1e-9;
            const double image_time = image_msg->header.stamp.sec +
                                      image_msg->header.stamp.nanosec * 1e-9;
            if (std::abs(image_time - pose_time) > 1e-6)
            {
                printf("[AUTO_LOOP_KEYFRAME_REJECT] reason=keyframe_timestamp_skew "
                       "skew_ms=%.6f\n", 1000.0 * (image_time - pose_time));
                continue;
            }
            // build keyframe
            Vector3d T = Vector3d(pose_msg->pose.pose.position.x,
                                  pose_msg->pose.pose.position.y,
                                  pose_msg->pose.pose.position.z);
            Matrix3d R = Quaterniond(pose_msg->pose.pose.orientation.w,
                                     pose_msg->pose.pose.orientation.x,
                                     pose_msg->pose.pose.orientation.y,
                                     pose_msg->pose.pose.orientation.z).toRotationMatrix();
            if((T - last_t).norm() > SKIP_DIS)
            {
                vector<cv::Point3f> point_3d;
                vector<cv::Point2f> point_2d_uv;
                vector<cv::Point2f> point_2d_normal;
                vector<double> point_id;

                for (unsigned int i = 0; i < point_msg->points.size(); i++)
                {
                    cv::Point3f p_3d;
                    p_3d.x = point_msg->points[i].x;
                    p_3d.y = point_msg->points[i].y;
                    p_3d.z = point_msg->points[i].z;
                    point_3d.push_back(p_3d);

                    cv::Point2f p_2d_uv, p_2d_normal;
                    double p_id;
                    p_2d_normal.x = point_msg->channels[i].values[0];
                    p_2d_normal.y = point_msg->channels[i].values[1];
                    p_2d_uv.x = point_msg->channels[i].values[2];
                    p_2d_uv.y = point_msg->channels[i].values[3];
                    p_id = point_msg->channels[i].values[4];
                    point_2d_normal.push_back(p_2d_normal);
                    point_2d_uv.push_back(p_2d_uv);
                    point_id.push_back(p_id);

                    //printf("u %f, v %f \n", p_2d_uv.x, p_2d_uv.y);
                }

                finalize_origin_reference_status(pose_time, T, R);
                KeyFrame* keyframe = new KeyFrame(pose_msg->header.stamp.sec + pose_msg->header.stamp.nanosec * (1e-9), frame_index, T, R, image, right_image,
                                   point_3d, point_2d_uv, point_2d_normal, point_id, sequence);
                m_process.lock();
                start_flag = 1;
                posegraph.addKeyFrame(keyframe, 1);
                m_process.unlock();
                frame_index++;
                last_t = T;
            }
        }
        std::chrono::milliseconds dura(5);
        std::this_thread::sleep_for(dura);
    }
}

void command()
{
    while(1)
    {
        char c = getchar();
        if (c == 's')
        {
            m_process.lock();
            posegraph.savePoseGraph();
            m_process.unlock();
            printf("save pose graph finish\nyou can set 'load_previous_pose_graph' to 1 in the config file to reuse it next time\n");
            printf("program shutting down...\n");
            rclcpp::shutdown();
        }
        if (c == 'n')
            new_sequence();

        std::chrono::milliseconds dura(5);
        std::this_thread::sleep_for(dura);
    }
}

int main(int argc, char **argv)
{
    if(argc != 2)
    {
        printf("please intput: rosrun loop_fusion loop_fusion_node [config file] \n"
               "for example: rosrun loop_fusion loop_fusion_node "
               "/home/tony-ws1/catkin_ws/src/VINS-Fusion/config/euroc/euroc_stereo_imu_config.yaml \n");
        return 0;
    }

    string config_file = argv[1];
    printf("config_file: %s\n", argv[1]);

    cv::FileStorage fsSettings(config_file, cv::FileStorage::READ);
    if(!fsSettings.isOpened())
    {
        std::cerr << "ERROR: Wrong path to settings" << std::endl;
        return 1;
    }
    const cv::FileNode min_spatial_support_node =
        fsSettings["min_loop_spatial_support"];
    if (!min_spatial_support_node.empty())
        MIN_LOOP_SPATIAL_SUPPORT = static_cast<double>(min_spatial_support_node);
    if (MIN_LOOP_SPATIAL_SUPPORT < 0.0 || MIN_LOOP_SPATIAL_SUPPORT > 1.0)
    {
        std::cerr << "ERROR: min_loop_spatial_support must be in [0, 1]"
                  << std::endl;
        return 1;
    }
    const double requested_loop_spatial_support = MIN_LOOP_SPATIAL_SUPPORT;
    MIN_LOOP_SPATIAL_SUPPORT = std::max(
        MIN_LOOP_SPATIAL_SUPPORT, MINIMUM_STEREO_LOOP_SPATIAL_SUPPORT);
    printf("min_loop_spatial_support: %.6f (enabled, requested=%.6f, "
           "hard_minimum=%.6f)\n",
           MIN_LOOP_SPATIAL_SUPPORT, requested_loop_spatial_support,
           MINIMUM_STEREO_LOOP_SPATIAL_SUPPORT);
    const cv::FileNode max_loop_candidates_node =
        fsSettings["max_loop_candidates"];
    if (!max_loop_candidates_node.empty())
        MAX_LOOP_CANDIDATES = static_cast<int>(max_loop_candidates_node);
    if (MAX_LOOP_CANDIDATES < 1 || MAX_LOOP_CANDIDATES > 64)
    {
        std::cerr << "ERROR: max_loop_candidates must be in [1, 64]"
                  << std::endl;
        return 1;
    }
    printf("max_loop_candidates: %d\n", MAX_LOOP_CANDIDATES);
    const cv::FileNode base_correction_limit_node =
        fsSettings["base_loop_correction_limit_m"];
    if (!base_correction_limit_node.empty())
        BASE_LOOP_CORRECTION_LIMIT_M =
            static_cast<double>(base_correction_limit_node);
    const cv::FileNode correction_extent_ratio_node =
        fsSettings["loop_correction_extent_ratio"];
    if (!correction_extent_ratio_node.empty())
        LOOP_CORRECTION_EXTENT_RATIO =
            static_cast<double>(correction_extent_ratio_node);
    const cv::FileNode correction_ceiling_node =
        fsSettings["confirmed_loop_correction_ceiling_m"];
    if (!correction_ceiling_node.empty())
        CONFIRMED_LOOP_CORRECTION_CEILING_M =
            static_cast<double>(correction_ceiling_node);
    try
    {
        (void)LoopCorrectionPolicy(
            BASE_LOOP_CORRECTION_LIMIT_M,
            LOOP_CORRECTION_EXTENT_RATIO,
            CONFIRMED_LOOP_CORRECTION_CEILING_M);
    }
    catch (const std::invalid_argument &error)
    {
        std::cerr << "ERROR: " << error.what() << std::endl;
        return 1;
    }
    printf("loop_correction_policy: base_limit_m=%.4f extent_ratio=%.4f "
           "ceiling_m=%.4f\n",
           BASE_LOOP_CORRECTION_LIMIT_M, LOOP_CORRECTION_EXTENT_RATIO,
           CONFIRMED_LOOP_CORRECTION_CEILING_M);
    const cv::FileNode expanded_retrieval_score_node =
        fsSettings["expanded_loop_min_retrieval_score"];
    if (!expanded_retrieval_score_node.empty())
        EXPANDED_LOOP_MIN_RETRIEVAL_SCORE =
            static_cast<double>(expanded_retrieval_score_node);
    if (!std::isfinite(EXPANDED_LOOP_MIN_RETRIEVAL_SCORE) ||
        EXPANDED_LOOP_MIN_RETRIEVAL_SCORE < 0.0 ||
        EXPANDED_LOOP_MIN_RETRIEVAL_SCORE > 1.0)
    {
        std::cerr << "ERROR: expanded_loop_min_retrieval_score must be in [0, 1]"
                  << std::endl;
        return 1;
    }
    printf("expanded_loop_min_retrieval_score: %.5f\n",
           EXPANDED_LOOP_MIN_RETRIEVAL_SCORE);
    const cv::FileNode edge_full_support_node =
        fsSettings["odometry_edge_full_support_points"];
    if (!edge_full_support_node.empty())
        ODOMETRY_EDGE_FULL_SUPPORT_POINTS =
            static_cast<int>(edge_full_support_node);
    const cv::FileNode edge_minimum_weight_node =
        fsSettings["odometry_edge_minimum_weight"];
    if (!edge_minimum_weight_node.empty())
        ODOMETRY_EDGE_MINIMUM_WEIGHT =
            static_cast<double>(edge_minimum_weight_node);
    try
    {
        (void)FeatureEdgeWeight(
            ODOMETRY_EDGE_FULL_SUPPORT_POINTS,
            ODOMETRY_EDGE_MINIMUM_WEIGHT);
    }
    catch (const std::invalid_argument &error)
    {
        std::cerr << "ERROR: " << error.what() << std::endl;
        return 1;
    }
    printf("odometry_edge_feature_weight: full_support_points=%d "
           "minimum_weight=%.4f\n",
           ODOMETRY_EDGE_FULL_SUPPORT_POINTS,
           ODOMETRY_EDGE_MINIMUM_WEIGHT);
    const cv::FileNode preserve_excursion_anchor_node =
        fsSettings["preserve_loop_excursion_anchor"];
    if (!preserve_excursion_anchor_node.empty())
    {
        const int value = static_cast<int>(preserve_excursion_anchor_node);
        if (value != 0 && value != 1)
        {
            std::cerr << "ERROR: preserve_loop_excursion_anchor must be 0 or 1"
                      << std::endl;
            return 1;
        }
        PRESERVE_LOOP_EXCURSION_ANCHOR = value == 1;
    }
    printf("preserve_loop_excursion_anchor: %d\n",
           PRESERVE_LOOP_EXCURSION_ANCHOR ? 1 : 0);
    const cv::FileNode freeze_outbound_node =
        fsSettings["freeze_loop_outbound_to_anchor"];
    if (!freeze_outbound_node.empty())
    {
        const int value = static_cast<int>(freeze_outbound_node);
        if (value != 0 && value != 1)
        {
            std::cerr << "ERROR: freeze_loop_outbound_to_anchor must be 0 or 1"
                      << std::endl;
            return 1;
        }
        FREEZE_LOOP_OUTBOUND_TO_ANCHOR = value == 1;
    }
    if (FREEZE_LOOP_OUTBOUND_TO_ANCHOR &&
        !PRESERVE_LOOP_EXCURSION_ANCHOR)
    {
        std::cerr << "ERROR: freeze_loop_outbound_to_anchor requires "
                     "preserve_loop_excursion_anchor=1"
                  << std::endl;
        return 1;
    }
    printf("freeze_loop_outbound_to_anchor: %d\n",
           FREEZE_LOOP_OUTBOUND_TO_ANCHOR ? 1 : 0);
    const cv::FileNode accepted_loop_cooldown_node =
        fsSettings["accepted_loop_cooldown_keyframes"];
    if (!accepted_loop_cooldown_node.empty())
        ACCEPTED_LOOP_COOLDOWN_KEYFRAMES =
            static_cast<int>(accepted_loop_cooldown_node);
    if (ACCEPTED_LOOP_COOLDOWN_KEYFRAMES < 0)
    {
        std::cerr << "ERROR: accepted_loop_cooldown_keyframes must be >= 0"
                  << std::endl;
        return 1;
    }
    printf("accepted_loop_cooldown_keyframes: %d\n",
           ACCEPTED_LOOP_COOLDOWN_KEYFRAMES);
    const cv::FileNode loop_edge_weight_node =
        fsSettings["loop_edge_weight"];
    if (!loop_edge_weight_node.empty())
        LOOP_EDGE_WEIGHT = static_cast<double>(loop_edge_weight_node);
    try
    {
        LOOP_EDGE_WEIGHT = LoopEdgeStrength(LOOP_EDGE_WEIGHT).weight();
    }
    catch (const std::invalid_argument &error)
    {
        std::cerr << "ERROR: " << error.what() << std::endl;
        return 1;
    }
    printf("loop_edge_weight: %.3f\n", LOOP_EDGE_WEIGHT);

    const cv::FileNode realtime_translation_rate_node =
        fsSettings["realtime_drift_translation_rate_mps"];
    if (!realtime_translation_rate_node.empty())
        REALTIME_DRIFT_TRANSLATION_RATE_MPS =
            static_cast<double>(realtime_translation_rate_node);
    const cv::FileNode realtime_rotation_rate_node =
        fsSettings["realtime_drift_rotation_rate_deg_s"];
    if (!realtime_rotation_rate_node.empty())
        REALTIME_DRIFT_ROTATION_RATE_DEG_S =
            static_cast<double>(realtime_rotation_rate_node);
    try
    {
        realtime_drift_limiter = std::make_unique<RealtimeDriftLimiter>(
            REALTIME_DRIFT_TRANSLATION_RATE_MPS,
            REALTIME_DRIFT_ROTATION_RATE_DEG_S);
    }
    catch (const std::invalid_argument &error)
    {
        std::cerr << "ERROR: " << error.what() << std::endl;
        return 1;
    }
    printf("realtime_drift_limiter: translation_rate_mps=%.4f "
           "rotation_rate_deg_s=%.2f enabled=%d\n",
           REALTIME_DRIFT_TRANSLATION_RATE_MPS,
           REALTIME_DRIFT_ROTATION_RATE_DEG_S,
           realtime_drift_limiter->enabled() ? 1 : 0);

    VISUALIZATION_SHIFT_X = 0;
    VISUALIZATION_SHIFT_Y = 0;
    SKIP_CNT = 0;
    SKIP_DIS = 0;

    cameraposevisual.setScale(0.1);
    cameraposevisual.setLineWidth(0.01);

    int LOAD_PREVIOUS_POSE_GRAPH;

    ROW = fsSettings["image_height"];
    COL = fsSettings["image_width"];

    const cv::FileNode max_camera_parameter_delta_node =
        fsSettings["max_stereo_camera_parameter_delta"];
    if (!max_camera_parameter_delta_node.empty())
        MAX_STEREO_CAMERA_PARAMETER_DELTA =
            static_cast<double>(max_camera_parameter_delta_node);
    if (!std::isfinite(MAX_STEREO_CAMERA_PARAMETER_DELTA) ||
        MAX_STEREO_CAMERA_PARAMETER_DELTA <= 0.0 ||
        MAX_STEREO_CAMERA_PARAMETER_DELTA > 5.0)
    {
        std::cerr << "ERROR: max_stereo_camera_parameter_delta must be in (0, 5]"
                  << std::endl;
        return 1;
    }

    int pn = config_file.find_last_of('/');
    std::string configPath = config_file.substr(0, pn);
    std::string cam0Calib;
    fsSettings["cam0_calib"] >> cam0Calib;
    std::string cam0Path = configPath + "/" + cam0Calib;
    printf("cam calib path: %s\n", cam0Path.c_str());
    m_camera = camodocal::CameraFactory::instance()->generateCameraFromYamlFile(cam0Path.c_str());
    if (!m_camera)
    {
        std::cerr << "ERROR: failed to load camera calibration: " << cam0Path << std::endl;
        return 1;
    }
    std::string cam1Calib;
    fsSettings["cam1_calib"] >> cam1Calib;
    std::string cam1Path = configPath + "/" + cam1Calib;
    printf("right camera calib path: %s\n", cam1Path.c_str());
    m_camera_right = camodocal::CameraFactory::instance()->generateCameraFromYamlFile(
        cam1Path.c_str());
    if (!m_camera_right)
    {
        std::cerr << "ERROR: failed to load right camera calibration: "
                  << cam1Path << std::endl;
        return 1;
    }
    std::vector<double> cam0_parameters;
    std::vector<double> cam1_parameters;
    m_camera->writeParameters(cam0_parameters);
    m_camera_right->writeParameters(cam1_parameters);
    if (m_camera->modelType() != m_camera_right->modelType() ||
        cam0_parameters.size() != cam1_parameters.size())
    {
        std::cerr << "ERROR: stereo images are not from the same rectified camera model"
                  << std::endl;
        return 1;
    }
    double max_camera_parameter_delta = 0.0;
    for (size_t i = 0; i < cam0_parameters.size(); ++i)
        max_camera_parameter_delta = std::max(
            max_camera_parameter_delta,
            std::abs(cam0_parameters[i] - cam1_parameters[i]));
    if (max_camera_parameter_delta > MAX_STEREO_CAMERA_PARAMETER_DELTA)
    {
        std::cerr << "ERROR: left/right rectified camera parameters differ by "
                  << max_camera_parameter_delta << " > "
                  << MAX_STEREO_CAMERA_PARAMETER_DELTA << std::endl;
        return 1;
    }

    cv::Mat cv_body_T_cam0;
    fsSettings["body_T_cam0"] >> cv_body_T_cam0;
    if (cv_body_T_cam0.rows != 4 || cv_body_T_cam0.cols != 4)
    {
        std::cerr << "ERROR: body_T_cam0 must be a 4x4 matrix" << std::endl;
        return 1;
    }
    cv::cv2eigen(cv_body_T_cam0(cv::Rect(0, 0, 3, 3)), qic);
    cv::cv2eigen(cv_body_T_cam0(cv::Rect(3, 0, 1, 3)), tic);
    std::cout << "fixed body_T_cam0 translation: " << tic.transpose() << std::endl;

    cv::Mat cv_body_T_cam1;
    fsSettings["body_T_cam1"] >> cv_body_T_cam1;
    if (cv_body_T_cam1.rows != 4 || cv_body_T_cam1.cols != 4)
    {
        std::cerr << "ERROR: body_T_cam1 must be a 4x4 matrix" << std::endl;
        return 1;
    }
    Eigen::Vector3d tic1;
    Eigen::Matrix3d qic1;
    cv::cv2eigen(cv_body_T_cam1(cv::Rect(0, 0, 3, 3)), qic1);
    cv::cv2eigen(cv_body_T_cam1(cv::Rect(3, 0, 1, 3)), tic1);
    const Eigen::Vector3d baseline_cam0 = qic.transpose() * (tic1 - tic);
    const Eigen::Matrix3d relative_rotation = qic.transpose() * qic1;
    const double relative_rotation_deg =
        Eigen::AngleAxisd(relative_rotation).angle() * 180.0 / M_PI;
    constexpr double kMaxRectifiedNonHorizontalBaselineM = 0.0005;
    constexpr double kMaxRectifiedRelativeRotationDeg = 0.5;
    if (std::abs(baseline_cam0.y()) > kMaxRectifiedNonHorizontalBaselineM ||
        std::abs(baseline_cam0.z()) > kMaxRectifiedNonHorizontalBaselineM ||
        relative_rotation_deg > kMaxRectifiedRelativeRotationDeg)
    {
        std::cerr << "ERROR: stereo calibration is not rectified: baseline_cam0="
                  << baseline_cam0.transpose() << " rotation_deg="
                  << relative_rotation_deg << std::endl;
        return 1;
    }
    STEREO_BASELINE_M = std::abs(baseline_cam0.x());
    if (!std::isfinite(STEREO_BASELINE_M) || STEREO_BASELINE_M < 0.005 ||
        STEREO_BASELINE_M > 0.1)
    {
        std::cerr << "ERROR: invalid stereo baseline: " << STEREO_BASELINE_M
                  << " m" << std::endl;
        return 1;
    }
    std::cout << "fixed rectified stereo baseline: " << STEREO_BASELINE_M
              << " m, non_horizontal_m=" << baseline_cam0.tail<2>().norm()
              << ", relative_rotation_deg=" << relative_rotation_deg
              << std::endl;

    fsSettings["pose_graph_save_path"] >> POSE_GRAPH_SAVE_PATH;
    fsSettings["output_path"] >> VINS_RESULT_PATH;
    fsSettings["save_image"] >> DEBUG_IMAGE;

    fsSettings["world_frame_id"] >> WORLD_FRAME_ID;
    WORLD_FRAME_ID.empty()? WORLD_FRAME_ID = "world" : WORLD_FRAME_ID;
    fsSettings["body_frame_id"] >> BODY_FRAME_ID;
    BODY_FRAME_ID.empty()? BODY_FRAME_ID = "body" : BODY_FRAME_ID;
    fsSettings["camera_frame_id"] >> CAMERA_FRAME_ID;
    CAMERA_FRAME_ID.empty()? CAMERA_FRAME_ID = "camera" : CAMERA_FRAME_ID;

    LOAD_PREVIOUS_POSE_GRAPH = fsSettings["load_previous_pose_graph"];
    VINS_RESULT_PATH = VINS_RESULT_PATH + "/vio_loop.csv";
    std::ofstream fout(VINS_RESULT_PATH, std::ios::out);
    fout.close();

    int USE_IMU = fsSettings["imu"];
    fsSettings.release();

    rclcpp::init(argc, argv);
    auto n = rclcpp::Node::make_shared("loop_fusion");
    posegraph.registerPub(n);

    // referred from: https://answers.ros.org/question/288501/ros2-equivalent-of-rospackagegetpath/
    std::string pkg_path = ament_index_cpp::get_package_share_directory("vins_fusion_ros2");
    string vocabulary_file = pkg_path + "/support_files/brief_k10L6.bin";
    cout << "vocabulary_file" << vocabulary_file << endl;
    posegraph.loadVocabulary(vocabulary_file);

    BRIEF_PATTERN_FILE = pkg_path + "/support_files/brief_pattern.yml";
    cout << "BRIEF_PATTERN_FILE" << BRIEF_PATTERN_FILE << endl;
    posegraph.setIMUFlag(USE_IMU);

    if (LOAD_PREVIOUS_POSE_GRAPH)
    {
        printf("load pose graph\n");
        m_process.lock();
        posegraph.loadPoseGraph();
        m_process.unlock();
        printf("load pose graph finish\n");
        load_flag = 1;
    }
    else
    {
        printf("no previous pose graph\n");
        load_flag = 1;
    }

    auto sub_vio          = n->create_subscription<nav_msgs::msg::Odometry>("/odometry", rclcpp::QoS(rclcpp::KeepLast(2000)), vio_callback);
    rclcpp::QoS loop_keyframe_qos(rclcpp::KeepLast(32));
    loop_keyframe_qos.reliable();
    auto sub_keyframe     = n->create_subscription<vins_fusion_ros2::msg::LoopKeyFrame>("/loop_fusion/keyframe", loop_keyframe_qos, keyframe_callback);
    auto sub_margin_point = n->create_subscription<sensor_msgs::msg::PointCloud>("/margin_cloud", rclcpp::QoS(rclcpp::KeepLast(2000)), margin_point_callback);


    pub_match_img          = n->create_publisher<sensor_msgs::msg::Image>("match_image", 1000);
    pub_camera_pose_visual = n->create_publisher<visualization_msgs::msg::MarkerArray>("camera_pose_visual", 1000);
    pub_point_cloud        = n->create_publisher<sensor_msgs::msg::PointCloud>("point_cloud_loop_rect", 1000);
    pub_margin_cloud       = n->create_publisher<sensor_msgs::msg::PointCloud>("margin_cloud_loop_rect", 1000);
    pub_odometry_rect      = n->create_publisher<nav_msgs::msg::Odometry>("odometry_rect", 1000);
    rclcpp::QoS origin_status_qos(rclcpp::KeepLast(1));
    origin_status_qos.reliable().transient_local();
    pub_origin_reference_status = n->create_publisher<std_msgs::msg::String>(
        "origin_reference_status", origin_status_qos);
    publish_origin_reference_status("INITIALIZING");

    std::thread measurement_process;
    measurement_process = std::thread(process);

    rclcpp::spin(n);

    running = false;
    if (measurement_process.joinable())
        measurement_process.join();
    posegraph.stopOptimization();
    printf("[LOOP_INPUT_SUMMARY] received=%lu transport_drops=%lu "
           "backlog_drops=%lu remaining_backlog=%zu\n",
           stereo_keyframes_received.load(), keyframe_transport_drops.load(),
           keyframe_backlog_drops.load(), keyframe_buf.size());

    pub_match_img.reset();
    pub_camera_pose_visual.reset();
    pub_point_cloud.reset();
    pub_margin_cloud.reset();
    pub_odometry_rect.reset();
    pub_origin_reference_status.reset();
    posegraph.resetPublishers();

    return 0;
}
