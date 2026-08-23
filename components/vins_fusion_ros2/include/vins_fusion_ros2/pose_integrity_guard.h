#pragma once

#include <vins/common/sensor_data_type.h>

#include <cmath>
#include <stdexcept>
#include <string>

enum class PoseIntegrityDecision {
  kAccept,
  kLatchFailure,
  kRejectLatched,
};

// Fail-closed protection for the product pose stream.  This does not attempt
// to repair a VINS state after the static-world assumption has been violated.
// It prevents an implausible estimate from reaching visualization and loop
// closure, then requires a clean process restart before output resumes.
class PoseIntegrityGuard {
 public:
  explicit PoseIntegrityGuard(double maximum_position_step_m = 0.0)
      : maximum_position_step_m_(maximum_position_step_m) {
    if (!std::isfinite(maximum_position_step_m_) ||
        maximum_position_step_m_ < 0.0) {
      throw std::invalid_argument(
          "pose integrity step must be finite and non-negative");
    }
  }

  PoseIntegrityDecision update(const OdomData& sample) {
    if (failed_) return PoseIntegrityDecision::kRejectLatched;
    if (maximum_position_step_m_ == 0.0) {
      remember(sample);
      return PoseIntegrityDecision::kAccept;
    }
    if (!isFinite(sample)) {
      return latch("non_finite_pose", 0.0);
    }
    if (!has_last_accepted_) {
      remember(sample);
      return PoseIntegrityDecision::kAccept;
    }
    if (sample.timestamp <= last_accepted_.timestamp) {
      return latch("timestamp_not_increasing", 0.0);
    }

    const double step_m =
        (sample.position - last_accepted_.position).norm();
    if (step_m > maximum_position_step_m_) {
      return latch("position_step_exceeded", step_m);
    }

    remember(sample);
    return PoseIntegrityDecision::kAccept;
  }

  bool failed() const { return failed_; }
  const std::string& failureReason() const { return failure_reason_; }
  double failureStepM() const { return failure_step_m_; }
  double maximumPositionStepM() const { return maximum_position_step_m_; }
  double lastAcceptedTimestamp() const {
    return has_last_accepted_ ? last_accepted_.timestamp : 0.0;
  }

 private:
  static bool isFinite(const OdomData& sample) {
    return std::isfinite(sample.timestamp) && sample.position.allFinite() &&
           sample.velocity.allFinite() &&
           sample.orientation.coeffs().allFinite() &&
           sample.orientation.norm() > 1e-12;
  }

  void remember(const OdomData& sample) {
    last_accepted_ = sample;
    has_last_accepted_ = true;
  }

  PoseIntegrityDecision latch(const std::string& reason, double step_m) {
    failed_ = true;
    failure_reason_ = reason;
    failure_step_m_ = step_m;
    return PoseIntegrityDecision::kLatchFailure;
  }

  double maximum_position_step_m_ = 0.0;
  bool has_last_accepted_ = false;
  bool failed_ = false;
  OdomData last_accepted_;
  std::string failure_reason_;
  double failure_step_m_ = 0.0;
};
