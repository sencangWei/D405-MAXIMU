#pragma once

#include <vins/common/sensor_data_type.h>

#include <cmath>
#include <stdexcept>
#include <vector>

// A causal one-sample confirmation guard for raw VIO output.  It never changes
// an accepted pose: a large innovation is either emitted intact after the next
// estimate confirms the motion, or discarded when the next estimate returns to
// the previous trajectory.  A zero limit preserves the historical behaviour.
class OdometrySpikeGuard {
 public:
  explicit OdometrySpikeGuard(double confirmation_step_m = 0.0)
      : confirmation_step_m_(confirmation_step_m) {
    if (!std::isfinite(confirmation_step_m_) || confirmation_step_m_ < 0.0) {
      throw std::invalid_argument(
          "odometry confirmation step must be finite and non-negative");
    }
  }

  std::vector<OdomData> update(const OdomData& sample) {
    if (!isFinite(sample)) return {};
    if (confirmation_step_m_ == 0.0) return accept(sample);
    if (!has_last_accepted_) return accept(sample);

    if (!has_pending_) {
      if (distance(last_accepted_, sample) <= confirmation_step_m_) {
        return accept(sample);
      }
      pending_ = sample;
      has_pending_ = true;
      return {};
    }

    if (distance(last_accepted_, sample) <= confirmation_step_m_) {
      // The estimate returned to the established trajectory: the held sample
      // was an isolated optimizer spike.
      has_pending_ = false;
      return accept(sample);
    }

    if (confirmsMotion(last_accepted_, pending_, sample)) {
      std::vector<OdomData> output{pending_, sample};
      last_accepted_ = sample;
      has_pending_ = false;
      return output;
    }

    // Keep only the newest unconfirmed estimate.  This is fail-closed: an
    // incoherent burst cannot leak into the published product trajectory.
    pending_ = sample;
    return {};
  }

  bool hasPending() const { return has_pending_; }

 private:
  static bool isFinite(const OdomData& sample) {
    return std::isfinite(sample.timestamp) && sample.position.allFinite() &&
           sample.velocity.allFinite() && sample.orientation.coeffs().allFinite() &&
           sample.orientation.norm() > 1e-12;
  }

  static double distance(const OdomData& lhs, const OdomData& rhs) {
    return (rhs.position - lhs.position).norm();
  }

  bool confirmsMotion(const OdomData& previous, const OdomData& held,
                      const OdomData& current) const {
    const Eigen::Vector3d first = held.position - previous.position;
    const Eigen::Vector3d second = current.position - held.position;
    const Eigen::Vector3d total = current.position - previous.position;

    // A real large step followed by a stop remains near the held pose.
    if (second.norm() <= confirmation_step_m_ &&
        total.dot(first) > 0.0) {
      return true;
    }

    // Sustained fast motion is accepted when two consecutive displacement
    // vectors agree in direction.  The isolated out-and-back spike observed in
    // the regression data has a cosine near -1 and is rejected.
    if (first.norm() <= 1e-12 || second.norm() <= 1e-12) return false;
    const double cosine = first.dot(second) / (first.norm() * second.norm());
    return cosine >= 0.5;
  }

  std::vector<OdomData> accept(const OdomData& sample) {
    last_accepted_ = sample;
    has_last_accepted_ = true;
    return {sample};
  }

  double confirmation_step_m_ = 0.0;
  bool has_last_accepted_ = false;
  bool has_pending_ = false;
  OdomData last_accepted_;
  OdomData pending_;
};
