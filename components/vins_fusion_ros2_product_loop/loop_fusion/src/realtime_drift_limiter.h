#pragma once

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include <eigen3/Eigen/Dense>

struct LimitedDriftTransform
{
    Eigen::Matrix3d rotation = Eigen::Matrix3d::Identity();
    Eigen::Vector3d translation = Eigen::Vector3d::Zero();
};

// The odometry stream is transformed from the estimator frame by
// ``applied = drift * world_alignment``.  Margin points are already expressed
// in the aligned world frame, so derive the equivalent drift-only transform
// rather than exposing the optimizer's instantaneous target correction.
inline LimitedDriftTransform driftTransformForAlignedFrame(
    const LimitedDriftTransform &applied,
    const LimitedDriftTransform &world_alignment)
{
    LimitedDriftTransform result;
    result.rotation = applied.rotation * world_alignment.rotation.transpose();
    result.translation =
        applied.translation - result.rotation * world_alignment.translation;
    return result;
}

// VINS-Fusion computes a 4DoF pose-graph correction and applies the new drift
// transform to /odometry_rect immediately.  That is geometrically valid, but a
// decimetre loop correction becomes a decimetre single-sample jump.  This
// limiter leaves the optimizer and its target transform untouched and only
// rate-limits how the real-time output approaches that target.
class RealtimeDriftLimiter
{
public:
    RealtimeDriftLimiter(
        double translation_rate_mps = 0.0,
        double rotation_rate_deg_s = 0.0)
        : translation_rate_mps_(translation_rate_mps),
          rotation_rate_rad_s_(rotation_rate_deg_s * M_PI / 180.0)
    {
        if (!std::isfinite(translation_rate_mps_) ||
            !std::isfinite(rotation_rate_rad_s_) ||
            translation_rate_mps_ < 0.0 || rotation_rate_rad_s_ < 0.0)
            throw std::invalid_argument("real-time drift rates must be finite and >= 0");
    }

    bool enabled() const
    {
        return translation_rate_mps_ > 0.0 || rotation_rate_rad_s_ > 0.0;
    }

    LimitedDriftTransform update(
        double timestamp_s,
        const LimitedDriftTransform &target)
    {
        if (!std::isfinite(timestamp_s) || !target.translation.allFinite() ||
            !target.rotation.allFinite())
            throw std::invalid_argument("non-finite real-time drift input");

        if (!initialized_ || !enabled())
        {
            applied_ = target;
            last_timestamp_s_ = timestamp_s;
            initialized_ = true;
            return applied_;
        }

        const double dt = timestamp_s - last_timestamp_s_;
        if (!(dt > 0.0))
            return applied_;
        last_timestamp_s_ = timestamp_s;

        if (translation_rate_mps_ <= 0.0)
        {
            applied_.translation = target.translation;
        }
        else
        {
            const Eigen::Vector3d delta = target.translation - applied_.translation;
            const double distance = delta.norm();
            const double maximum_step = translation_rate_mps_ * dt;
            if (distance <= maximum_step || distance <= 1e-12)
                applied_.translation = target.translation;
            else
                applied_.translation += delta * (maximum_step / distance);
        }

        if (rotation_rate_rad_s_ <= 0.0)
        {
            applied_.rotation = target.rotation;
        }
        else
        {
            const Eigen::AngleAxisd delta_rotation(
                applied_.rotation.transpose() * target.rotation);
            const double angle = std::abs(delta_rotation.angle());
            const double maximum_step = rotation_rate_rad_s_ * dt;
            if (angle <= maximum_step || angle <= 1e-12)
            {
                applied_.rotation = target.rotation;
            }
            else
            {
                const double signed_step = std::copysign(maximum_step, delta_rotation.angle());
                applied_.rotation = applied_.rotation *
                    Eigen::AngleAxisd(signed_step, delta_rotation.axis()).toRotationMatrix();
            }
        }
        return applied_;
    }

private:
    double translation_rate_mps_ = 0.0;
    double rotation_rate_rad_s_ = 0.0;
    bool initialized_ = false;
    double last_timestamp_s_ = 0.0;
    LimitedDriftTransform applied_;
};
