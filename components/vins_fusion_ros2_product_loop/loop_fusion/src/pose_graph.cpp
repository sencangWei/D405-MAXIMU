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

#include "pose_graph.h"
#include "loop_correction_policy.h"
#include "feature_edge_weight.h"
#include "loop_excursion_anchor.h"
#include "loop_candidate_priority.h"

#include <algorithm>
#include <cmath>

namespace
{
constexpr int kLoopTemporalExclusionKeyframes = 90;
constexpr double kLoopNeighborScoreThreshold = 0.05;
constexpr double kLoopCandidateScoreThreshold = 0.015;

double medianScalar(std::vector<double> values)
{
    const size_t middle = values.size() / 2;
    std::nth_element(values.begin(), values.begin() + middle, values.end());
    if (values.size() % 2 == 1)
        return values[middle];
    const double upper = values[middle];
    std::nth_element(values.begin(), values.begin() + middle - 1, values.end());
    return 0.5 * (values[middle - 1] + upper);
}

Eigen::Vector3d medianTranslation(const std::vector<Eigen::Vector3d> &values)
{
    std::vector<double> x, y, z;
    x.reserve(values.size());
    y.reserve(values.size());
    z.reserve(values.size());
    for (const Eigen::Vector3d &value : values)
    {
        x.push_back(value.x());
        y.push_back(value.y());
        z.push_back(value.z());
    }
    return Eigen::Vector3d(medianScalar(x), medianScalar(y), medianScalar(z));
}

double medianAngle(const std::vector<double> &values)
{
    const double reference = values.front();
    std::vector<double> unwrapped;
    unwrapped.reserve(values.size());
    for (double value : values)
        unwrapped.push_back(reference + Utility::normalizeAngle(value - reference));
    return Utility::normalizeAngle(medianScalar(unwrapped));
}
}

PoseGraph::PoseGraph()
{
    posegraph_visualization = new CameraPoseVisualization(1.0, 0.0, 1.0, 1.0);
    posegraph_visualization->setScale(0.1);
    posegraph_visualization->setLineWidth(0.01);
    earliest_loop_index = -1;
    t_drift = Eigen::Vector3d(0, 0, 0);
    yaw_drift = 0;
    r_drift = Eigen::Matrix3d::Identity();
    w_t_vio = Eigen::Vector3d(0, 0, 0);
    w_r_vio = Eigen::Matrix3d::Identity();
    global_index = 0;
    sequence_cnt = 0;
    sequence_loop.push_back(0);
    base_sequence = 1;
    use_imu = 0;
}

PoseGraph::~PoseGraph()
{
    stopOptimization();
}

void PoseGraph::stopOptimization()
{
    running_ = false;
    if (t_optimization.joinable())
        t_optimization.join();
}

void PoseGraph::resetPublishers()
{
    pub_pg_path.reset();
    pub_base_path.reset();
    pub_pose_graph.reset();
    for (auto &publisher : pub_path)
        publisher.reset();
}

PoseGraphTransformSnapshot PoseGraph::getTransformSnapshot()
{
    std::lock_guard<std::mutex> lock(m_drift);
    return {t_drift, r_drift, w_t_vio, w_r_vio};
}

void PoseGraph::registerPub(rclcpp::Node::SharedPtr n)
{
    pub_pg_path = n->create_publisher<nav_msgs::msg::Path>("pose_graph_path", 1000);
    pub_base_path = n->create_publisher<nav_msgs::msg::Path>("base_path", 1000);
    pub_pose_graph = n->create_publisher<visualization_msgs::msg::MarkerArray>("pose_graph", 1000);
    for (int i = 1; i < 10; i++)
        pub_path[i] = n->create_publisher<nav_msgs::msg::Path>("path_" + to_string(i), 1000);
}

void PoseGraph::setIMUFlag(bool _use_imu)
{
    use_imu = _use_imu;
    if(use_imu)
    {
        printf("VIO input, perfrom 4 DoF (x, y, z, yaw) pose graph optimization\n");
        t_optimization = std::thread(&PoseGraph::optimize4DoF, this);
    }
    else
    {
        printf("VO input, perfrom 6 DoF pose graph optimization\n");
        t_optimization = std::thread(&PoseGraph::optimize6DoF, this);
    }

}

void PoseGraph::loadVocabulary(std::string voc_path)
{
    voc = new BriefVocabulary(voc_path);
    db.setVocabulary(*voc, false, 0);
}

void PoseGraph::addKeyFrame(KeyFrame* cur_kf, bool flag_detect_loop)
{
    //shift to base frame
    Vector3d vio_P_cur;
    Matrix3d vio_R_cur;
    if (sequence_cnt != cur_kf->sequence)
    {
        sequence_cnt++;
        sequence_loop.push_back(0);
        m_drift.lock();
        w_t_vio = Eigen::Vector3d(0, 0, 0);
        w_r_vio = Eigen::Matrix3d::Identity();
        t_drift = Eigen::Vector3d(0, 0, 0);
        r_drift = Eigen::Matrix3d::Identity();
        m_drift.unlock();
    }

    cur_kf->getVioPose(vio_P_cur, vio_R_cur);
    const PoseGraphTransformSnapshot initial_transform = getTransformSnapshot();
    vio_P_cur = initial_transform.w_r_vio * vio_P_cur + initial_transform.w_t_vio;
    vio_R_cur = initial_transform.w_r_vio * vio_R_cur;
    cur_kf->updateVioPose(vio_P_cur, vio_R_cur);
	if (!trajectory_bounds_initialized_)
	{
		trajectory_min_ = vio_P_cur;
		trajectory_max_ = vio_P_cur;
		trajectory_bounds_initialized_ = true;
	}
	else
	{
		trajectory_min_ = trajectory_min_.cwiseMin(vio_P_cur);
		trajectory_max_ = trajectory_max_.cwiseMax(vio_P_cur);
	}
	printf("[POSE_GRAPH_KEYFRAME_SUPPORT] current=%d tracked_points=%zu\n",
	       global_index, cur_kf->point_3d.size());
	cur_kf->index = global_index;
    global_index++;
	int loop_index = -1;
	double loop_retrieval_score = 0.0;
	bool loop_connection_found = false;
    if (flag_detect_loop)
    {
        TicToc tmp_t;
		const std::vector<std::pair<int, double>> retrieved_candidates =
			detectLoopCandidates(cur_kf, cur_kf->index);
		const std::vector<std::pair<int, double>> loop_candidates =
			prioritizeFreshPendingCandidate(
				retrieved_candidates, pending_loop_index_);
		// Each confirmation must be freshly retrieved by DBoW.  Replaying a
		// pending index that disappeared from the current query allowed strong
		// PnP geometry to confirm a visually unsupported historical frame.
		for (const auto &[candidate_index, candidate_score] : loop_candidates)
		{
			if (cur_kf->index - candidate_index < kLoopTemporalExclusionKeyframes)
				continue;
			KeyFrame* candidate = getKeyFrame(candidate_index);
			if (candidate != nullptr && cur_kf->findConnection(candidate))
			{
				loop_index = candidate_index;
				loop_retrieval_score = candidate_score;
				loop_connection_found = true;
				break;
			}
			cur_kf->clearLoop();
		}
    }
    else
    {
        addKeyFrameIntoVoc(cur_kf);
    }
	if (loop_connection_found)
	{
        //printf(" %d detect loop with %d \n", cur_kf->index, loop_index);
        KeyFrame* old_kf = getKeyFrame(loop_index);

		if (old_kf != nullptr)
        {
			constexpr int kRequiredLoopConfirmations = 3;
			constexpr int kMaxHistoricalRegionDeltaKeyframes = 24;
			constexpr double kMaxConfirmationTranslationDisagreementM = 0.020;
			constexpr double kMaxConfirmationYawDisagreementDeg = 1.5;
			auto reset_pending_loop = [this]() {
				pending_loop_index_ = -1;
				pending_loop_count_ = 0;
				pending_loop_current_index_ = -1000;
				pending_loop_correction_t_.setZero();
				pending_loop_correction_yaw_ = 0.0;
				pending_loop_correction_translations_.clear();
				pending_loop_correction_yaws_.clear();
			};
			const Vector3d candidate_relative_t = cur_kf->getLoopRelativeT();
			const double candidate_relative_yaw = cur_kf->getLoopRelativeYaw();
			const Quaterniond candidate_relative_q = cur_kf->getLoopRelativeQ();
			Vector3d candidate_old_position, candidate_vio_position;
			Matrix3d candidate_old_rotation, candidate_vio_rotation;
			old_kf->getVioPose(candidate_old_position, candidate_old_rotation);
			cur_kf->getVioPose(candidate_vio_position, candidate_vio_rotation);
			const Vector3d candidate_loop_position =
				candidate_old_rotation * candidate_relative_t + candidate_old_position;
			const Matrix3d candidate_loop_rotation =
				candidate_old_rotation * candidate_relative_q.toRotationMatrix();
			const double candidate_correction_yaw = Utility::normalizeAngle(
				Utility::R2ypr(candidate_loop_rotation).x() -
				Utility::R2ypr(candidate_vio_rotation).x());
			const Matrix3d candidate_correction_rotation =
				Utility::ypr2R(Vector3d(candidate_correction_yaw, 0, 0));
			const Vector3d candidate_correction_t =
				candidate_loop_position - candidate_correction_rotation * candidate_vio_position;
			const bool same_historical_region =
				pending_loop_index_ >= 0 &&
				std::abs(loop_index - pending_loop_index_) <=
					kMaxHistoricalRegionDeltaKeyframes;
			const bool temporally_adjacent =
				cur_kf->index - pending_loop_current_index_ <= 3;
			const Vector3d pending_correction_center =
				pending_loop_correction_translations_.empty()
					? pending_loop_correction_t_
					: medianTranslation(pending_loop_correction_translations_);
			const double pending_yaw_center =
				pending_loop_correction_yaws_.empty()
					? pending_loop_correction_yaw_
					: medianAngle(pending_loop_correction_yaws_);
			const double translation_disagreement =
				(candidate_correction_t - pending_correction_center).norm();
			const double yaw_disagreement = abs(Utility::normalizeAngle(
				candidate_correction_yaw - pending_yaw_center));
			const bool relative_pose_consistent =
				translation_disagreement <
					kMaxConfirmationTranslationDisagreementM &&
				yaw_disagreement < kMaxConfirmationYawDisagreementDeg;

			if (same_historical_region && temporally_adjacent && relative_pose_consistent)
				pending_loop_count_++;
			else
			{
				if (same_historical_region && temporally_adjacent && pending_loop_count_ > 0)
				{
					printf("[AUTO_LOOP_INCONSISTENT] current=%d matched=%d "
					       "translation_disagreement_m=%.4f yaw_disagreement_deg=%.2f\n",
					       cur_kf->index, loop_index,
					       translation_disagreement, yaw_disagreement);
				}
				pending_loop_count_ = 1;
				pending_loop_correction_translations_.clear();
				pending_loop_correction_yaws_.clear();
			}
			pending_loop_index_ = loop_index;
			pending_loop_current_index_ = cur_kf->index;
			pending_loop_correction_t_ = candidate_correction_t;
			pending_loop_correction_yaw_ = candidate_correction_yaw;
			pending_loop_correction_translations_.push_back(candidate_correction_t);
			pending_loop_correction_yaws_.push_back(candidate_correction_yaw);

			const double trajectory_extent_m =
				(trajectory_max_ - trajectory_min_).norm();
			const LoopCorrectionPolicy correction_policy(
				BASE_LOOP_CORRECTION_LIMIT_M,
				LOOP_CORRECTION_EXTENT_RATIO,
				CONFIRMED_LOOP_CORRECTION_CEILING_M);
			const double allowed_correction_m =
				correction_policy.allowedCorrectionM(trajectory_extent_m);
			if (pending_loop_count_ >= kRequiredLoopConfirmations)
			{
				printf("[AUTO_LOOP_CORRECTION_BUDGET] current=%d matched=%d "
				       "correction_t_m=%.4f trajectory_extent_m=%.4f "
				       "base_limit_m=%.4f extent_ratio=%.4f ceiling_m=%.4f "
				       "allowed_m=%.4f\n",
				       cur_kf->index, loop_index, candidate_correction_t.norm(),
				       trajectory_extent_m, BASE_LOOP_CORRECTION_LIMIT_M,
				       LOOP_CORRECTION_EXTENT_RATIO,
				       CONFIRMED_LOOP_CORRECTION_CEILING_M,
				       allowed_correction_m);
			}

			if (pending_loop_count_ < kRequiredLoopConfirmations)
			{
				printf("[AUTO_LOOP_PENDING] current=%d matched=%d confirmations=%d/%d "
				       "rel_t_m=%.4f yaw_deg=%.2f correction_t_m=%.4f "
				       "correction_xyz_m=(%.4f,%.4f,%.4f) correction_yaw_deg=%.2f\n",
				       cur_kf->index, loop_index, pending_loop_count_,
				       kRequiredLoopConfirmations,
				       candidate_relative_t.norm(), candidate_relative_yaw,
				       candidate_correction_t.norm(), candidate_correction_t.x(),
				       candidate_correction_t.y(), candidate_correction_t.z(),
				       candidate_correction_yaw);
				cur_kf->clearLoop();
			}
			else if (!correction_policy.accepts(
			             candidate_correction_t.norm(), trajectory_extent_m))
			{
				printf("[AUTO_LOOP_CORRECTION_REJECT] current=%d matched=%d "
				       "fused_correction_t_m=%.4f limit_m=%.4f "
				       "trajectory_extent_m=%.4f\n",
				       cur_kf->index, loop_index, candidate_correction_t.norm(),
				       allowed_correction_m, trajectory_extent_m);
				cur_kf->clearLoop();
				reset_pending_loop();
			}
			else if (!correction_policy.hasRequiredRetrievalEvidence(
			             candidate_correction_t.norm(), loop_retrieval_score,
			             EXPANDED_LOOP_MIN_RETRIEVAL_SCORE))
			{
				printf("[AUTO_LOOP_RETRIEVAL_SCORE_REJECT] current=%d matched=%d "
				       "fused_correction_t_m=%.4f retrieval_score=%.5f "
				       "expanded_minimum=%.5f base_limit_m=%.4f\n",
				       cur_kf->index, loop_index, candidate_correction_t.norm(),
				       loop_retrieval_score, EXPANDED_LOOP_MIN_RETRIEVAL_SCORE,
				       BASE_LOOP_CORRECTION_LIMIT_M);
				cur_kf->clearLoop();
				reset_pending_loop();
			}
			else if (cur_kf->index - last_accepted_loop_current_index_ <=
			         ACCEPTED_LOOP_COOLDOWN_KEYFRAMES)
			{
				printf("[AUTO_LOOP_COOLDOWN] current=%d matched=%d\n",
				       cur_kf->index, loop_index);
				cur_kf->clearLoop();
				reset_pending_loop();
			}
			else
			{
				// Earlier frames establish that this is the same revisited region.
				// Apply the current frame's strict stereo-PnP measurement instead of
				// averaging corrections collected while the rig was still moving
				// toward the historical pose.
				const Vector3d fused_correction_t = candidate_correction_t;
				const double fused_correction_yaw = candidate_correction_yaw;
				const Matrix3d fused_correction_rotation =
					Utility::ypr2R(Vector3d(fused_correction_yaw, 0, 0));
				const Vector3d fused_loop_position =
					fused_correction_rotation * candidate_vio_position + fused_correction_t;
				const Matrix3d fused_loop_rotation =
					fused_correction_rotation * candidate_vio_rotation;
				const Vector3d fused_relative_t =
					candidate_old_rotation.transpose() *
					(fused_loop_position - candidate_old_position);
				const Quaterniond fused_relative_q(
					candidate_old_rotation.transpose() * fused_loop_rotation);
				const double fused_relative_yaw = Utility::normalizeAngle(
					Utility::R2ypr(fused_loop_rotation).x() -
					Utility::R2ypr(candidate_old_rotation).x());
				Eigen::Matrix<double, 8, 1> fused_loop_info;
				fused_loop_info << fused_relative_t.x(), fused_relative_t.y(),
					fused_relative_t.z(), fused_relative_q.w(), fused_relative_q.x(),
					fused_relative_q.y(), fused_relative_q.z(), fused_relative_yaw;
				cur_kf->updateLoop(fused_loop_info);
				last_accepted_loop_current_index_ = cur_kf->index;
				printf("[AUTO_LOOP_ACCEPT] current=%d matched=%d confirmations=%d "
				       "inliers_gate=>%d retrieval_score=%.5f fused_correction_t_m=%.4f "
				       "fused_correction_xyz_m=(%.4f,%.4f,%.4f) "
				       "fused_correction_yaw_deg=%.2f\n",
				       cur_kf->index, loop_index, pending_loop_count_, MIN_LOOP_NUM,
				       loop_retrieval_score,
				       fused_correction_t.norm(), fused_correction_t.x(),
				       fused_correction_t.y(), fused_correction_t.z(),
				       fused_correction_yaw);
				reset_pending_loop();
            if (earliest_loop_index > loop_index || earliest_loop_index == -1)
                earliest_loop_index = loop_index;

            Vector3d w_P_old, w_P_cur, vio_P_cur;
            Matrix3d w_R_old, w_R_cur, vio_R_cur;
            old_kf->getVioPose(w_P_old, w_R_old);
            cur_kf->getVioPose(vio_P_cur, vio_R_cur);

            Vector3d relative_t;
            Quaterniond relative_q;
            relative_t = cur_kf->getLoopRelativeT();
            relative_q = (cur_kf->getLoopRelativeQ()).toRotationMatrix();
            w_P_cur = w_R_old * relative_t + w_P_old;
            w_R_cur = w_R_old * relative_q;
            double shift_yaw;
            Matrix3d shift_r;
            Vector3d shift_t;
            if(use_imu)
            {
                shift_yaw = Utility::R2ypr(w_R_cur).x() - Utility::R2ypr(vio_R_cur).x();
                shift_r = Utility::ypr2R(Vector3d(shift_yaw, 0, 0));
            }
            else
                shift_r = w_R_cur * vio_R_cur.transpose();
            shift_t = w_P_cur - w_R_cur * vio_R_cur.transpose() * vio_P_cur;
            // shift vio pose of whole sequence to the world frame
            if (old_kf->sequence != cur_kf->sequence && sequence_loop[cur_kf->sequence] == 0)
            {
                {
                    std::lock_guard<std::mutex> lock(m_drift);
                    w_r_vio = shift_r;
                    w_t_vio = shift_t;
                }
                vio_P_cur = shift_r * vio_P_cur + shift_t;
                vio_R_cur = shift_r * vio_R_cur;
                cur_kf->updateVioPose(vio_P_cur, vio_R_cur);
                list<KeyFrame*>::iterator it = keyframelist.begin();
                for (; it != keyframelist.end(); it++)
                {
                    if((*it)->sequence == cur_kf->sequence)
                    {
                        Vector3d vio_P_cur;
                        Matrix3d vio_R_cur;
                        (*it)->getVioPose(vio_P_cur, vio_R_cur);
                        vio_P_cur = shift_r * vio_P_cur + shift_t;
                        vio_R_cur = shift_r * vio_R_cur;
                        (*it)->updateVioPose(vio_P_cur, vio_R_cur);
                    }
                }
                sequence_loop[cur_kf->sequence] = 1;
            }
            m_optimize_buf.lock();
            optimize_buf.push(cur_kf->index);
            m_optimize_buf.unlock();
			}
        }
	}
	m_keyframelist.lock();
    Vector3d P;
    Matrix3d R;
    cur_kf->getVioPose(P, R);
    const PoseGraphTransformSnapshot current_transform = getTransformSnapshot();
    P = current_transform.r_drift * P + current_transform.t_drift;
    R = current_transform.r_drift * R;
    cur_kf->updatePose(P, R);
    Quaterniond Q{R};
    geometry_msgs::msg::PoseStamped pose_stamped;

    int sec_ts = (int)cur_kf->time_stamp;
    uint nsec_ts = (uint)((cur_kf->time_stamp - sec_ts) * 1e9);
    pose_stamped.header.stamp.sec = sec_ts;
    pose_stamped.header.stamp.nanosec = nsec_ts;

    pose_stamped.header.frame_id = WORLD_FRAME_ID;
    pose_stamped.pose.position.x = P.x() + VISUALIZATION_SHIFT_X;
    pose_stamped.pose.position.y = P.y() + VISUALIZATION_SHIFT_Y;
    pose_stamped.pose.position.z = P.z();
    pose_stamped.pose.orientation.x = Q.x();
    pose_stamped.pose.orientation.y = Q.y();
    pose_stamped.pose.orientation.z = Q.z();
    pose_stamped.pose.orientation.w = Q.w();
    path[sequence_cnt].poses.push_back(pose_stamped);
    path[sequence_cnt].header = pose_stamped.header;

    if (SAVE_LOOP_PATH)
    {
        ofstream loop_path_file(VINS_RESULT_PATH, ios::app);
        loop_path_file.setf(ios::fixed, ios::floatfield);
        loop_path_file.precision(0);
        loop_path_file << cur_kf->time_stamp * 1e9 << ",";
        loop_path_file.precision(5);
        loop_path_file  << P.x() << ","
              << P.y() << ","
              << P.z() << ","
              << Q.w() << ","
              << Q.x() << ","
              << Q.y() << ","
              << Q.z() << ","
              << endl;
        loop_path_file.close();
    }
    //draw local connection
    if (SHOW_S_EDGE)
    {
        list<KeyFrame*>::reverse_iterator rit = keyframelist.rbegin();
        for (int i = 0; i < 4; i++)
        {
            if (rit == keyframelist.rend())
                break;
            Vector3d conncected_P;
            Matrix3d connected_R;
            if((*rit)->sequence == cur_kf->sequence)
            {
                (*rit)->getPose(conncected_P, connected_R);
                posegraph_visualization->add_edge(P, conncected_P);
            }
            rit++;
        }
    }
    if (SHOW_L_EDGE)
    {
        if (cur_kf->has_loop)
        {
            //printf("has loop \n");
            KeyFrame* connected_KF = getKeyFrame(cur_kf->loop_index);
            Vector3d connected_P,P0;
            Matrix3d connected_R,R0;
            connected_KF->getPose(connected_P, connected_R);
            //cur_kf->getVioPose(P0, R0);
            cur_kf->getPose(P0, R0);
            if(cur_kf->sequence > 0)
            {
                //printf("add loop into visual \n");
                posegraph_visualization->add_loopedge(P0, connected_P + Vector3d(VISUALIZATION_SHIFT_X, VISUALIZATION_SHIFT_Y, 0));
            }

        }
    }
    //posegraph_visualization->add_pose(P + Vector3d(VISUALIZATION_SHIFT_X, VISUALIZATION_SHIFT_Y, 0), Q);

	keyframelist.push_back(cur_kf);
    publish();
	m_keyframelist.unlock();
}


void PoseGraph::loadKeyFrame(KeyFrame* cur_kf, bool flag_detect_loop)
{
    cur_kf->index = global_index;
    global_index++;
    int loop_index = -1;
    bool loop_connection_found = false;
    if (flag_detect_loop)
	{
		const std::vector<std::pair<int, double>> loop_candidates =
			detectLoopCandidates(cur_kf, cur_kf->index);
		for (const auto &[candidate_index, candidate_score] : loop_candidates)
		{
			(void)candidate_score;
			if (cur_kf->index - candidate_index < kLoopTemporalExclusionKeyframes)
				continue;
			KeyFrame* candidate = getKeyFrame(candidate_index);
			if (candidate != nullptr && cur_kf->findConnection(candidate))
			{
				loop_index = candidate_index;
				loop_connection_found = true;
				break;
			}
			cur_kf->clearLoop();
		}
	}
    else
    {
        addKeyFrameIntoVoc(cur_kf);
    }
    if (loop_connection_found)
    {
        printf(" %d detect loop with %d \n", cur_kf->index, loop_index);
        KeyFrame* old_kf = getKeyFrame(loop_index);
        if (old_kf != nullptr)
        {
            if (earliest_loop_index > loop_index || earliest_loop_index == -1)
                earliest_loop_index = loop_index;
            m_optimize_buf.lock();
            optimize_buf.push(cur_kf->index);
            m_optimize_buf.unlock();
        }
    }
    m_keyframelist.lock();
    Vector3d P;
    Matrix3d R;
    cur_kf->getPose(P, R);
    Quaterniond Q{R};
    geometry_msgs::msg::PoseStamped pose_stamped;

    int sec_ts = (int)cur_kf->time_stamp;
    uint nsec_ts = (uint)((cur_kf->time_stamp - sec_ts) * 1e9);
    pose_stamped.header.stamp.sec = sec_ts;
    pose_stamped.header.stamp.nanosec = nsec_ts;

    pose_stamped.header.frame_id = WORLD_FRAME_ID;
    pose_stamped.pose.position.x = P.x() + VISUALIZATION_SHIFT_X;
    pose_stamped.pose.position.y = P.y() + VISUALIZATION_SHIFT_Y;
    pose_stamped.pose.position.z = P.z();
    pose_stamped.pose.orientation.x = Q.x();
    pose_stamped.pose.orientation.y = Q.y();
    pose_stamped.pose.orientation.z = Q.z();
    pose_stamped.pose.orientation.w = Q.w();
    base_path.poses.push_back(pose_stamped);
    base_path.header = pose_stamped.header;

    //draw local connection
    if (SHOW_S_EDGE)
    {
        list<KeyFrame*>::reverse_iterator rit = keyframelist.rbegin();
        for (int i = 0; i < 1; i++)
        {
            if (rit == keyframelist.rend())
                break;
            Vector3d conncected_P;
            Matrix3d connected_R;
            if((*rit)->sequence == cur_kf->sequence)
            {
                (*rit)->getPose(conncected_P, connected_R);
                posegraph_visualization->add_edge(P, conncected_P);
            }
            rit++;
        }
    }
    /*
    if (cur_kf->has_loop)
    {
        KeyFrame* connected_KF = getKeyFrame(cur_kf->loop_index);
        Vector3d connected_P;
        Matrix3d connected_R;
        connected_KF->getPose(connected_P,  connected_R);
        posegraph_visualization->add_loopedge(P, connected_P, SHIFT);
    }
    */

    keyframelist.push_back(cur_kf);
    //publish();
    m_keyframelist.unlock();
}

KeyFrame* PoseGraph::getKeyFrame(int index)
{
//    unique_lock<mutex> lock(m_keyframelist);
    list<KeyFrame*>::iterator it = keyframelist.begin();
    for (; it != keyframelist.end(); it++)
    {
        if((*it)->index == index)
            break;
    }
    if (it != keyframelist.end())
        return *it;
    else
        return NULL;
}

std::vector<std::pair<int, double>> PoseGraph::detectLoopCandidates(
	KeyFrame* keyframe, int frame_index)
{
    // put image into image_pool; for visualization
    cv::Mat compressed_image;
    if (DEBUG_IMAGE)
    {
        int feature_num = keyframe->keypoints.size();
        cv::resize(keyframe->image, compressed_image, cv::Size(376, 240));
        putText(compressed_image, "feature_num:" + to_string(feature_num), cv::Point2f(10, 10), cv::FONT_HERSHEY_SIMPLEX, 0.4, cv::Scalar(255));
        image_pool[frame_index] = compressed_image;
    }
    TicToc tmp_t;
    //first query; then add this frame into database!
    QueryResults ret;
    TicToc t_query;
    db.query(
        keyframe->brief_descriptors,
        ret,
        MAX_LOOP_CANDIDATES,
        frame_index - kLoopTemporalExclusionKeyframes);
    //printf("query time: %f", t_query.toc());
    //cout << "Searching for Image " << frame_index << ". " << ret << endl;

    TicToc t_add;
    db.add(keyframe->brief_descriptors);
    //printf("add feature time: %f", t_add.toc());
    // ret[0] is the nearest neighbour's score. threshold change with neighour score
    std::vector<std::pair<int, double>> candidates;
    cv::Mat loop_result;
    if (DEBUG_IMAGE)
    {
        loop_result = compressed_image.clone();
        if (ret.size() > 0)
            putText(loop_result, "neighbour score:" + to_string(ret[0].Score), cv::Point2f(10, 50), cv::FONT_HERSHEY_SIMPLEX, 0.5, cv::Scalar(255));
    }
    // visual loop result
    if (DEBUG_IMAGE)
    {
        for (unsigned int i = 0; i < ret.size(); i++)
        {
            int tmp_index = ret[i].Id;
            auto it = image_pool.find(tmp_index);
            cv::Mat tmp_image = (it->second).clone();
            putText(tmp_image, "index:  " + to_string(tmp_index) + "loop score:" + to_string(ret[i].Score), cv::Point2f(10, 50), cv::FONT_HERSHEY_SIMPLEX, 0.5, cv::Scalar(255));
            cv::hconcat(loop_result, tmp_image, loop_result);
        }
    }
    // a good match with its nerghbour
    if (frame_index > kLoopTemporalExclusionKeyframes &&
        ret.size() >= 2 && ret[0].Score > kLoopNeighborScoreThreshold)
        for (const auto &result : ret)
            if (frame_index - static_cast<int>(result.Id) >=
                    kLoopTemporalExclusionKeyframes &&
                result.Score > kLoopCandidateScoreThreshold)
				candidates.emplace_back(
					static_cast<int>(result.Id), static_cast<double>(result.Score));
	if (frame_index > kLoopTemporalExclusionKeyframes && !ret.empty())
	{
		printf("[AUTO_LOOP_RETRIEVAL] current=%d returned=%zu eligible=%zu "
		       "neighbor_threshold=%.5f candidate_threshold=%.5f",
		       frame_index, ret.size(), candidates.size(),
		       kLoopNeighborScoreThreshold, kLoopCandidateScoreThreshold);
		for (size_t rank = 0; rank < ret.size(); rank++)
			printf(" rank%zu=%u:%.5f", rank + 1, ret[rank].Id, ret[rank].Score);
		printf("\n");
	}
/*
    if (DEBUG_IMAGE)
    {
        cv::imshow("loop_result", loop_result);
        cv::waitKey(20);
    }
*/
    return candidates;

}

void PoseGraph::addKeyFrameIntoVoc(KeyFrame* keyframe)
{
    // put image into image_pool; for visualization
    cv::Mat compressed_image;
    if (DEBUG_IMAGE)
    {
        int feature_num = keyframe->keypoints.size();
        cv::resize(keyframe->image, compressed_image, cv::Size(376, 240));
        putText(compressed_image, "feature_num:" + to_string(feature_num), cv::Point2f(10, 10), cv::FONT_HERSHEY_SIMPLEX, 0.4, cv::Scalar(255));
        image_pool[keyframe->index] = compressed_image;
    }

    db.add(keyframe->brief_descriptors);
}

void PoseGraph::optimize4DoF()
{
    while(running_)
    {
        int cur_index = -1;
        int first_looped_index = -1;
        m_optimize_buf.lock();
        while(!optimize_buf.empty())
        {
            cur_index = optimize_buf.front();
            first_looped_index = earliest_loop_index;
            optimize_buf.pop();
        }
        m_optimize_buf.unlock();
        if (cur_index != -1)
        {
            printf("optimize pose graph \n");
            TicToc tmp_t;
            m_keyframelist.lock();
            KeyFrame* cur_kf = getKeyFrame(cur_index);

            int max_length = cur_index + 1;

            // w^t_i   w^q_i
            double t_array[max_length][3];
            Quaterniond q_array[max_length];
            double euler_array[max_length][3];
            double sequence_array[max_length];
			std::vector<int> feature_support(max_length, 0);
			const FeatureEdgeWeight edge_weight_policy(
				ODOMETRY_EDGE_FULL_SUPPORT_POINTS,
				ODOMETRY_EDGE_MINIMUM_WEIGHT);
			int odometry_edges_total = 0;
			int odometry_edges_downweighted = 0;
			double minimum_odometry_edge_weight = 1.0;

            ceres::Problem problem;
            ceres::Solver::Options options;
            options.linear_solver_type = ceres::SPARSE_NORMAL_CHOLESKY;
            //options.minimizer_progress_to_stdout = true;
            //options.max_solver_time_in_seconds = SOLVER_TIME * 3;
            options.max_num_iterations = 5;
            ceres::Solver::Summary summary;
            ceres::LossFunction *loss_function;
            loss_function = new ceres::HuberLoss(0.1);
            //loss_function = new ceres::CauchyLoss(1.0);
            ceres::LocalParameterization* angle_local_parameterization =
                AngleLocalParameterization::Create();

            list<KeyFrame*>::iterator it;
			int excursion_anchor_index = -1;
			if (PRESERVE_LOOP_EXCURSION_ANCHOR)
			{
				std::vector<LoopAnchorPoint> anchor_candidates;
				for (it = keyframelist.begin(); it != keyframelist.end(); ++it)
				{
					if ((*it)->index < first_looped_index)
						continue;
					Vector3d candidate_position;
					Matrix3d candidate_rotation;
					(*it)->getVioPose(candidate_position, candidate_rotation);
					anchor_candidates.push_back({
						(*it)->index, candidate_position.x(),
						candidate_position.y(), candidate_position.z()});
					if ((*it)->index == cur_index)
						break;
				}
				if (anchor_candidates.size() >= 2)
					excursion_anchor_index =
						selectLoopExcursionAnchor(anchor_candidates);
				printf("[POSE_GRAPH_EXCURSION_ANCHOR] current=%d anchor=%d "
				       "first_looped=%d candidates=%zu\n",
				       cur_index, excursion_anchor_index, first_looped_index,
				       anchor_candidates.size());
			}

            int i = 0;
            for (it = keyframelist.begin(); it != keyframelist.end(); it++)
            {
                if ((*it)->index < first_looped_index)
                    continue;
                (*it)->local_index = i;
                Quaterniond tmp_q;
                Matrix3d tmp_r;
                Vector3d tmp_t;
                (*it)->getVioPose(tmp_t, tmp_r);
                tmp_q = tmp_r;
                t_array[i][0] = tmp_t(0);
                t_array[i][1] = tmp_t(1);
                t_array[i][2] = tmp_t(2);
                q_array[i] = tmp_q;

                Vector3d euler_angle = Utility::R2ypr(tmp_q.toRotationMatrix());
                euler_array[i][0] = euler_angle.x();
                euler_array[i][1] = euler_angle.y();
                euler_array[i][2] = euler_angle.z();

                sequence_array[i] = (*it)->sequence;
				feature_support[i] = static_cast<int>((*it)->point_3d.size());

                problem.AddParameterBlock(euler_array[i], 1, angle_local_parameterization);
                problem.AddParameterBlock(t_array[i], 3);

				const bool freeze_loop_pose = shouldFreezeLoopPose(
					(*it)->index, first_looped_index, excursion_anchor_index,
					FREEZE_LOOP_OUTBOUND_TO_ANCHOR);
				if (freeze_loop_pose || (*it)->sequence == 0)
                {
                    problem.SetParameterBlockConstant(euler_array[i]);
                    problem.SetParameterBlockConstant(t_array[i]);
                }

                //add edge
                for (int j = 1; j < 5; j++)
                {
                  if (i - j >= 0 && sequence_array[i] == sequence_array[i-j])
                  {
                    Vector3d euler_conncected = Utility::R2ypr(q_array[i-j].toRotationMatrix());
                    Vector3d relative_t(t_array[i][0] - t_array[i-j][0], t_array[i][1] - t_array[i-j][1], t_array[i][2] - t_array[i-j][2]);
                    relative_t = q_array[i-j].inverse() * relative_t;
                    double relative_yaw = euler_array[i][0] - euler_array[i-j][0];
					std::vector<int> edge_support(
						feature_support.begin() + i - j,
						feature_support.begin() + i + 1);
					const double edge_weight =
						edge_weight_policy.weightForSpan(edge_support);
					odometry_edges_total++;
					if (edge_weight < 1.0)
						odometry_edges_downweighted++;
					minimum_odometry_edge_weight = std::min(
						minimum_odometry_edge_weight, edge_weight);
                    ceres::CostFunction* cost_function = FourDOFError::Create( relative_t.x(), relative_t.y(), relative_t.z(),
                                                   relative_yaw, euler_conncected.y(), euler_conncected.z(),
												   edge_weight);
                    problem.AddResidualBlock(cost_function, NULL, euler_array[i-j],
                                            t_array[i-j],
                                            euler_array[i],
                                            t_array[i]);
                  }
                }

                //add loop edge

                if((*it)->has_loop)
                {
                    assert((*it)->loop_index >= first_looped_index);
                    int connected_index = getKeyFrame((*it)->loop_index)->local_index;
                    Vector3d euler_conncected = Utility::R2ypr(q_array[connected_index].toRotationMatrix());
                    Vector3d relative_t;
                    relative_t = (*it)->getLoopRelativeT();
                    double relative_yaw = (*it)->getLoopRelativeYaw();
					ceres::CostFunction* cost_function = FourDOFWeightError::Create( relative_t.x(), relative_t.y(), relative_t.z(),
																			   relative_yaw, euler_conncected.y(), euler_conncected.z(),
																			   LOOP_EDGE_WEIGHT);
                    problem.AddResidualBlock(cost_function, loss_function, euler_array[connected_index],
                                                                  t_array[connected_index],
                                                                  euler_array[i],
                                                                  t_array[i]);

                }

                if ((*it)->index == cur_index)
                    break;
                i++;
            }
            m_keyframelist.unlock();
			printf("[POSE_GRAPH_EDGE_WEIGHTS] current=%d total=%d downweighted=%d "
			       "minimum=%.4f full_support_points=%d floor=%.4f loop=%.3f\n",
			       cur_index, odometry_edges_total, odometry_edges_downweighted,
			       minimum_odometry_edge_weight,
			       ODOMETRY_EDGE_FULL_SUPPORT_POINTS,
			       ODOMETRY_EDGE_MINIMUM_WEIGHT, LOOP_EDGE_WEIGHT);

            ceres::Solve(options, &problem, &summary);
            const bool solution_usable = summary.IsSolutionUsable() &&
                std::isfinite(summary.initial_cost) &&
                std::isfinite(summary.final_cost);
            printf("[POSE_GRAPH_OPTIMIZATION] current=%d usable=%d "
                   "initial_cost=%.6f final_cost=%.6f iterations=%zu time_s=%.4f\n",
                   cur_index, solution_usable ? 1 : 0, summary.initial_cost,
                   summary.final_cost, summary.iterations.size(),
                   summary.total_time_in_seconds);
            if (!solution_usable)
            {
                printf("[POSE_GRAPH_OPTIMIZATION_REJECT] current=%d reason=%s\n",
                       cur_index, summary.BriefReport().c_str());
                continue;
            }

            //printf("pose optimization time: %f \n", tmp_t.toc());
            /*
            for (int j = 0 ; j < i; j++)
            {
                printf("optimize i: %d p: %f, %f, %f\n", j, t_array[j][0], t_array[j][1], t_array[j][2] );
            }
            */
            m_keyframelist.lock();
            i = 0;
            for (it = keyframelist.begin(); it != keyframelist.end(); it++)
            {
                if ((*it)->index < first_looped_index)
                    continue;
                Quaterniond tmp_q;
                tmp_q = Utility::ypr2R(Vector3d(euler_array[i][0], euler_array[i][1], euler_array[i][2]));
                Vector3d tmp_t = Vector3d(t_array[i][0], t_array[i][1], t_array[i][2]);
                Matrix3d tmp_r = tmp_q.toRotationMatrix();
                (*it)-> updatePose(tmp_t, tmp_r);

                if ((*it)->index == cur_index)
                    break;
                i++;
            }

            Vector3d cur_t, vio_t;
            Matrix3d cur_r, vio_r;
            cur_kf->getPose(cur_t, cur_r);
            cur_kf->getVioPose(vio_t, vio_r);
            const double optimized_yaw_drift =
                Utility::R2ypr(cur_r).x() - Utility::R2ypr(vio_r).x();
            const Matrix3d optimized_r_drift =
                Utility::ypr2R(Vector3d(optimized_yaw_drift, 0, 0));
            const Vector3d optimized_t_drift =
                cur_t - optimized_r_drift * vio_t;
            {
                std::lock_guard<std::mutex> lock(m_drift);
                yaw_drift = optimized_yaw_drift;
                r_drift = optimized_r_drift;
                t_drift = optimized_t_drift;
            }
            //cout << "t_drift " << t_drift.transpose() << endl;
            //cout << "r_drift " << Utility::R2ypr(r_drift).transpose() << endl;
            //cout << "yaw drift " << yaw_drift << endl;

            it++;
            for (; it != keyframelist.end(); it++)
            {
                Vector3d P;
                Matrix3d R;
                (*it)->getVioPose(P, R);
                P = optimized_r_drift * P + optimized_t_drift;
                R = optimized_r_drift * R;
                (*it)->updatePose(P, R);
            }
            m_keyframelist.unlock();
            updatePath();
        }

        std::chrono::milliseconds dura(2000);
        std::this_thread::sleep_for(dura);
    }
    return;
}


void PoseGraph::optimize6DoF()
{
    while(running_)
    {
        int cur_index = -1;
        int first_looped_index = -1;
        m_optimize_buf.lock();
        while(!optimize_buf.empty())
        {
            cur_index = optimize_buf.front();
            first_looped_index = earliest_loop_index;
            optimize_buf.pop();
        }
        m_optimize_buf.unlock();
        if (cur_index != -1)
        {
            printf("optimize pose graph \n");
            TicToc tmp_t;
            m_keyframelist.lock();
            KeyFrame* cur_kf = getKeyFrame(cur_index);

            int max_length = cur_index + 1;

            // w^t_i   w^q_i
            double t_array[max_length][3];
            double q_array[max_length][4];
            double sequence_array[max_length];

            ceres::Problem problem;
            ceres::Solver::Options options;
            options.linear_solver_type = ceres::SPARSE_NORMAL_CHOLESKY;
            //ptions.minimizer_progress_to_stdout = true;
            //options.max_solver_time_in_seconds = SOLVER_TIME * 3;
            options.max_num_iterations = 5;
            ceres::Solver::Summary summary;
            ceres::LossFunction *loss_function;
            loss_function = new ceres::HuberLoss(0.1);
            //loss_function = new ceres::CauchyLoss(1.0);
            ceres::LocalParameterization* quaternion_local_parameterization =
                new ceres::QuaternionParameterization();

            list<KeyFrame*>::iterator it;

            int i = 0;
            for (it = keyframelist.begin(); it != keyframelist.end(); it++)
            {
                if ((*it)->index < first_looped_index)
                    continue;
                (*it)->local_index = i;
                Quaterniond tmp_q;
                Matrix3d tmp_r;
                Vector3d tmp_t;
                (*it)->getVioPose(tmp_t, tmp_r);
                tmp_q = tmp_r;
                t_array[i][0] = tmp_t(0);
                t_array[i][1] = tmp_t(1);
                t_array[i][2] = tmp_t(2);
                q_array[i][0] = tmp_q.w();
                q_array[i][1] = tmp_q.x();
                q_array[i][2] = tmp_q.y();
                q_array[i][3] = tmp_q.z();

                sequence_array[i] = (*it)->sequence;

                problem.AddParameterBlock(q_array[i], 4, quaternion_local_parameterization);
                problem.AddParameterBlock(t_array[i], 3);

                if ((*it)->index == first_looped_index || (*it)->sequence == 0)
                {
                    problem.SetParameterBlockConstant(q_array[i]);
                    problem.SetParameterBlockConstant(t_array[i]);
                }

                //add edge
                for (int j = 1; j < 5; j++)
                {
                    if (i - j >= 0 && sequence_array[i] == sequence_array[i-j])
                    {
                        Vector3d relative_t(t_array[i][0] - t_array[i-j][0], t_array[i][1] - t_array[i-j][1], t_array[i][2] - t_array[i-j][2]);
                        Quaterniond q_i_j = Quaterniond(q_array[i-j][0], q_array[i-j][1], q_array[i-j][2], q_array[i-j][3]);
                        Quaterniond q_i = Quaterniond(q_array[i][0], q_array[i][1], q_array[i][2], q_array[i][3]);
                        relative_t = q_i_j.inverse() * relative_t;
                        Quaterniond relative_q = q_i_j.inverse() * q_i;
                        ceres::CostFunction* vo_function = RelativeRTError::Create(relative_t.x(), relative_t.y(), relative_t.z(),
                                                                                relative_q.w(), relative_q.x(), relative_q.y(), relative_q.z(),
                                                                                0.1, 0.01);
                        problem.AddResidualBlock(vo_function, NULL, q_array[i-j], t_array[i-j], q_array[i], t_array[i]);
                    }
                }

                //add loop edge

                if((*it)->has_loop)
                {
                    assert((*it)->loop_index >= first_looped_index);
                    int connected_index = getKeyFrame((*it)->loop_index)->local_index;
                    Vector3d relative_t;
                    relative_t = (*it)->getLoopRelativeT();
                    Quaterniond relative_q;
                    relative_q = (*it)->getLoopRelativeQ();
                    ceres::CostFunction* loop_function = RelativeRTError::Create(relative_t.x(), relative_t.y(), relative_t.z(),
                                                                                relative_q.w(), relative_q.x(), relative_q.y(), relative_q.z(),
                                                                                0.1, 0.01);
                    problem.AddResidualBlock(loop_function, loss_function, q_array[connected_index], t_array[connected_index], q_array[i], t_array[i]);
                }

                if ((*it)->index == cur_index)
                    break;
                i++;
            }
            m_keyframelist.unlock();

            ceres::Solve(options, &problem, &summary);
            //std::cout << summary.BriefReport() << "\n";

            //printf("pose optimization time: %f \n", tmp_t.toc());
            /*
            for (int j = 0 ; j < i; j++)
            {
                printf("optimize i: %d p: %f, %f, %f\n", j, t_array[j][0], t_array[j][1], t_array[j][2] );
            }
            */
            m_keyframelist.lock();
            i = 0;
            for (it = keyframelist.begin(); it != keyframelist.end(); it++)
            {
                if ((*it)->index < first_looped_index)
                    continue;
                Quaterniond tmp_q(q_array[i][0], q_array[i][1], q_array[i][2], q_array[i][3]);
                Vector3d tmp_t = Vector3d(t_array[i][0], t_array[i][1], t_array[i][2]);
                Matrix3d tmp_r = tmp_q.toRotationMatrix();
                (*it)-> updatePose(tmp_t, tmp_r);

                if ((*it)->index == cur_index)
                    break;
                i++;
            }

            Vector3d cur_t, vio_t;
            Matrix3d cur_r, vio_r;
            cur_kf->getPose(cur_t, cur_r);
            cur_kf->getVioPose(vio_t, vio_r);
            const Matrix3d optimized_r_drift = cur_r * vio_r.transpose();
            const Vector3d optimized_t_drift =
                cur_t - optimized_r_drift * vio_t;
            {
                std::lock_guard<std::mutex> lock(m_drift);
                r_drift = optimized_r_drift;
                t_drift = optimized_t_drift;
            }
            //cout << "t_drift " << t_drift.transpose() << endl;
            //cout << "r_drift " << Utility::R2ypr(r_drift).transpose() << endl;

            it++;
            for (; it != keyframelist.end(); it++)
            {
                Vector3d P;
                Matrix3d R;
                (*it)->getVioPose(P, R);
                P = optimized_r_drift * P + optimized_t_drift;
                R = optimized_r_drift * R;
                (*it)->updatePose(P, R);
            }
            m_keyframelist.unlock();
            updatePath();
        }

        std::chrono::milliseconds dura(2000);
        std::this_thread::sleep_for(dura);
    }
    return;
}

void PoseGraph::updatePath()
{
    m_keyframelist.lock();
    list<KeyFrame*>::iterator it;
    for (int i = 1; i <= sequence_cnt; i++)
    {
        path[i].poses.clear();
    }
    base_path.poses.clear();
    posegraph_visualization->reset();

    if (SAVE_LOOP_PATH)
    {
        ofstream loop_path_file_tmp(VINS_RESULT_PATH, ios::out);
        loop_path_file_tmp.close();
    }

    for (it = keyframelist.begin(); it != keyframelist.end(); it++)
    {
        Vector3d P;
        Matrix3d R;
        (*it)->getPose(P, R);
        Quaterniond Q;
        Q = R;
//        printf("path p: %f, %f, %f\n",  P.x(),  P.z(),  P.y() );

        geometry_msgs::msg::PoseStamped pose_stamped;

        int sec_ts = (int)(*it)->time_stamp;
        uint nsec_ts = (uint)(((*it)->time_stamp - sec_ts) * 1e9);
        pose_stamped.header.stamp.sec = sec_ts;
        pose_stamped.header.stamp.nanosec = nsec_ts;

        pose_stamped.header.frame_id = WORLD_FRAME_ID;
        pose_stamped.pose.position.x = P.x() + VISUALIZATION_SHIFT_X;
        pose_stamped.pose.position.y = P.y() + VISUALIZATION_SHIFT_Y;
        pose_stamped.pose.position.z = P.z();
        pose_stamped.pose.orientation.x = Q.x();
        pose_stamped.pose.orientation.y = Q.y();
        pose_stamped.pose.orientation.z = Q.z();
        pose_stamped.pose.orientation.w = Q.w();
        if((*it)->sequence == 0)
        {
            base_path.poses.push_back(pose_stamped);
            base_path.header = pose_stamped.header;
        }
        else
        {
            path[(*it)->sequence].poses.push_back(pose_stamped);
            path[(*it)->sequence].header = pose_stamped.header;
        }

        if (SAVE_LOOP_PATH)
        {
            ofstream loop_path_file(VINS_RESULT_PATH, ios::app);
            loop_path_file.setf(ios::fixed, ios::floatfield);
            loop_path_file.precision(0);
            loop_path_file << (*it)->time_stamp * 1e9 << ",";
            loop_path_file.precision(5);
            loop_path_file  << P.x() << ","
                  << P.y() << ","
                  << P.z() << ","
                  << Q.w() << ","
                  << Q.x() << ","
                  << Q.y() << ","
                  << Q.z() << ","
                  << endl;
            loop_path_file.close();
        }
        //draw local connection
        if (SHOW_S_EDGE)
        {
            list<KeyFrame*>::reverse_iterator rit = keyframelist.rbegin();
            list<KeyFrame*>::reverse_iterator lrit;
            for (; rit != keyframelist.rend(); rit++)
            {
                if ((*rit)->index == (*it)->index)
                {
                    lrit = rit;
                    lrit++;
                    for (int i = 0; i < 4; i++)
                    {
                        if (lrit == keyframelist.rend())
                            break;
                        if((*lrit)->sequence == (*it)->sequence)
                        {
                            Vector3d conncected_P;
                            Matrix3d connected_R;
                            (*lrit)->getPose(conncected_P, connected_R);
                            posegraph_visualization->add_edge(P, conncected_P);
                        }
                        lrit++;
                    }
                    break;
                }
            }
        }
        if (SHOW_L_EDGE)
        {
            if ((*it)->has_loop && (*it)->sequence == sequence_cnt)
            {

                KeyFrame* connected_KF = getKeyFrame((*it)->loop_index);
                Vector3d connected_P;
                Matrix3d connected_R;
                connected_KF->getPose(connected_P, connected_R);
                //(*it)->getVioPose(P, R);
                (*it)->getPose(P, R);
                if((*it)->sequence > 0)
                {
                    posegraph_visualization->add_loopedge(P, connected_P + Vector3d(VISUALIZATION_SHIFT_X, VISUALIZATION_SHIFT_Y, 0));
                }
            }
        }

    }
    publish();
    m_keyframelist.unlock();
}


void PoseGraph::savePoseGraph()
{
    m_keyframelist.lock();
    TicToc tmp_t;
    FILE *pFile;
    printf("pose graph path: %s\n",POSE_GRAPH_SAVE_PATH.c_str());
    printf("pose graph saving... \n");
    string file_path = POSE_GRAPH_SAVE_PATH + "pose_graph.txt";
    pFile = fopen (file_path.c_str(),"w");
    //fprintf(pFile, "index time_stamp Tx Ty Tz Qw Qx Qy Qz loop_index loop_info\n");
    list<KeyFrame*>::iterator it;
    for (it = keyframelist.begin(); it != keyframelist.end(); it++)
    {
        std::string image_path, descriptor_path, brief_path, keypoints_path;
        if (DEBUG_IMAGE)
        {
            image_path = POSE_GRAPH_SAVE_PATH + to_string((*it)->index) + "_image.png";
            imwrite(image_path.c_str(), (*it)->image);
        }
        Quaterniond VIO_tmp_Q{(*it)->vio_R_w_i};
        Quaterniond PG_tmp_Q{(*it)->R_w_i};
        Vector3d VIO_tmp_T = (*it)->vio_T_w_i;
        Vector3d PG_tmp_T = (*it)->T_w_i;

        fprintf (pFile, " %d %f %f %f %f %f %f %f %f %f %f %f %f %f %f %f %d %f %f %f %f %f %f %f %f %d\n",(*it)->index, (*it)->time_stamp,
                                    VIO_tmp_T.x(), VIO_tmp_T.y(), VIO_tmp_T.z(),
                                    PG_tmp_T.x(), PG_tmp_T.y(), PG_tmp_T.z(),
                                    VIO_tmp_Q.w(), VIO_tmp_Q.x(), VIO_tmp_Q.y(), VIO_tmp_Q.z(),
                                    PG_tmp_Q.w(), PG_tmp_Q.x(), PG_tmp_Q.y(), PG_tmp_Q.z(),
                                    (*it)->loop_index,
                                    (*it)->loop_info(0), (*it)->loop_info(1), (*it)->loop_info(2), (*it)->loop_info(3),
                                    (*it)->loop_info(4), (*it)->loop_info(5), (*it)->loop_info(6), (*it)->loop_info(7),
                                    (int)(*it)->keypoints.size());

        // write keypoints, brief_descriptors   vector<cv::KeyPoint> keypoints vector<BRIEF::bitset> brief_descriptors;
        assert((*it)->keypoints.size() == (*it)->brief_descriptors.size());
        brief_path = POSE_GRAPH_SAVE_PATH + to_string((*it)->index) + "_briefdes.dat";
        std::ofstream brief_file(brief_path, std::ios::binary);
        keypoints_path = POSE_GRAPH_SAVE_PATH + to_string((*it)->index) + "_keypoints.txt";
        FILE *keypoints_file;
        keypoints_file = fopen(keypoints_path.c_str(), "w");
        for (int i = 0; i < (int)(*it)->keypoints.size(); i++)
        {
            brief_file << (*it)->brief_descriptors[i] << endl;
            fprintf(keypoints_file, "%f %f %f %f\n", (*it)->keypoints[i].pt.x, (*it)->keypoints[i].pt.y,
                                                     (*it)->keypoints_norm[i].pt.x, (*it)->keypoints_norm[i].pt.y);
        }
        brief_file.close();
        fclose(keypoints_file);
    }
    fclose(pFile);

    printf("save pose graph time: %f s\n", tmp_t.toc() / 1000);
    m_keyframelist.unlock();
}
void PoseGraph::loadPoseGraph()
{
    TicToc tmp_t;
    FILE * pFile;
    string file_path = POSE_GRAPH_SAVE_PATH + "pose_graph.txt";
    printf("lode pose graph from: %s \n", file_path.c_str());
    printf("pose graph loading...\n");
    pFile = fopen (file_path.c_str(),"r");
    if (pFile == NULL)
    {
        printf("lode previous pose graph error: wrong previous pose graph path or no previous pose graph \n the system will start with new pose graph \n");
        return;
    }
    int index;
    double time_stamp;
    double VIO_Tx, VIO_Ty, VIO_Tz;
    double PG_Tx, PG_Ty, PG_Tz;
    double VIO_Qw, VIO_Qx, VIO_Qy, VIO_Qz;
    double PG_Qw, PG_Qx, PG_Qy, PG_Qz;
    double loop_info_0, loop_info_1, loop_info_2, loop_info_3;
    double loop_info_4, loop_info_5, loop_info_6, loop_info_7;
    int loop_index;
    int keypoints_num;
    Eigen::Matrix<double, 8, 1 > loop_info;
    int cnt = 0;
    while (fscanf(pFile,"%d %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %d %lf %lf %lf %lf %lf %lf %lf %lf %d", &index, &time_stamp,
                                    &VIO_Tx, &VIO_Ty, &VIO_Tz,
                                    &PG_Tx, &PG_Ty, &PG_Tz,
                                    &VIO_Qw, &VIO_Qx, &VIO_Qy, &VIO_Qz,
                                    &PG_Qw, &PG_Qx, &PG_Qy, &PG_Qz,
                                    &loop_index,
                                    &loop_info_0, &loop_info_1, &loop_info_2, &loop_info_3,
                                    &loop_info_4, &loop_info_5, &loop_info_6, &loop_info_7,
                                    &keypoints_num) != EOF)
    {
        /*
        printf("I read: %d %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %lf %d %lf %lf %lf %lf %lf %lf %lf %lf %d\n", index, time_stamp,
                                    VIO_Tx, VIO_Ty, VIO_Tz,
                                    PG_Tx, PG_Ty, PG_Tz,
                                    VIO_Qw, VIO_Qx, VIO_Qy, VIO_Qz,
                                    PG_Qw, PG_Qx, PG_Qy, PG_Qz,
                                    loop_index,
                                    loop_info_0, loop_info_1, loop_info_2, loop_info_3,
                                    loop_info_4, loop_info_5, loop_info_6, loop_info_7,
                                    keypoints_num);
        */
        cv::Mat image;
        std::string image_path, descriptor_path;
        if (DEBUG_IMAGE)
        {
            image_path = POSE_GRAPH_SAVE_PATH + to_string(index) + "_image.png";
            image = cv::imread(image_path.c_str(), 0);
        }

        Vector3d VIO_T(VIO_Tx, VIO_Ty, VIO_Tz);
        Vector3d PG_T(PG_Tx, PG_Ty, PG_Tz);
        Quaterniond VIO_Q;
        VIO_Q.w() = VIO_Qw;
        VIO_Q.x() = VIO_Qx;
        VIO_Q.y() = VIO_Qy;
        VIO_Q.z() = VIO_Qz;
        Quaterniond PG_Q;
        PG_Q.w() = PG_Qw;
        PG_Q.x() = PG_Qx;
        PG_Q.y() = PG_Qy;
        PG_Q.z() = PG_Qz;
        Matrix3d VIO_R, PG_R;
        VIO_R = VIO_Q.toRotationMatrix();
        PG_R = PG_Q.toRotationMatrix();
        Eigen::Matrix<double, 8, 1 > loop_info;
        loop_info << loop_info_0, loop_info_1, loop_info_2, loop_info_3, loop_info_4, loop_info_5, loop_info_6, loop_info_7;

        if (loop_index != -1)
            if (earliest_loop_index > loop_index || earliest_loop_index == -1)
            {
                earliest_loop_index = loop_index;
            }

        // load keypoints, brief_descriptors
        string brief_path = POSE_GRAPH_SAVE_PATH + to_string(index) + "_briefdes.dat";
        std::ifstream brief_file(brief_path, std::ios::binary);
        string keypoints_path = POSE_GRAPH_SAVE_PATH + to_string(index) + "_keypoints.txt";
        FILE *keypoints_file;
        keypoints_file = fopen(keypoints_path.c_str(), "r");
        vector<cv::KeyPoint> keypoints;
        vector<cv::KeyPoint> keypoints_norm;
        vector<BRIEF::bitset> brief_descriptors;
        for (int i = 0; i < keypoints_num; i++)
        {
            BRIEF::bitset tmp_des;
            brief_file >> tmp_des;
            brief_descriptors.push_back(tmp_des);
            cv::KeyPoint tmp_keypoint;
            cv::KeyPoint tmp_keypoint_norm;
            double p_x, p_y, p_x_norm, p_y_norm;
            if(!fscanf(keypoints_file,"%lf %lf %lf %lf", &p_x, &p_y, &p_x_norm, &p_y_norm))
                printf(" fail to load pose graph \n");
            tmp_keypoint.pt.x = p_x;
            tmp_keypoint.pt.y = p_y;
            tmp_keypoint_norm.pt.x = p_x_norm;
            tmp_keypoint_norm.pt.y = p_y_norm;
            keypoints.push_back(tmp_keypoint);
            keypoints_norm.push_back(tmp_keypoint_norm);
        }
        brief_file.close();
        fclose(keypoints_file);

        KeyFrame* keyframe = new KeyFrame(time_stamp, index, VIO_T, VIO_R, PG_T, PG_R, image, loop_index, loop_info, keypoints, keypoints_norm, brief_descriptors);
        loadKeyFrame(keyframe, 0);
        if (cnt % 20 == 0)
        {
            publish();
        }
        cnt++;
    }
    fclose (pFile);
    printf("load pose graph time: %f s\n", tmp_t.toc()/1000);
    base_sequence = 0;
}

void PoseGraph::publish()
{
    for (int i = 1; i <= sequence_cnt; i++)
    {
        //if (sequence_loop[i] == true || i == base_sequence)
        if (1 || i == base_sequence)
        {
            pub_pg_path->publish(path[i]);
            pub_path[i]->publish(path[i]);
            posegraph_visualization->publish_by(pub_pose_graph, path[sequence_cnt].header);
        }
    }
    pub_base_path->publish(base_path);
    //posegraph_visualization->publish_by(pub_pose_graph, path[sequence_cnt].header);
}
