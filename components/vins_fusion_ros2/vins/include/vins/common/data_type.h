#ifndef DATA_TYPE_H
#define DATA_TYPE_H
#include <Eigen/Core>
#include <Eigen/Geometry>
#include <algorithm>
#include <cstdint>
#include <map>
#include <vector>

enum class SolverState { INITIAL, NON_LINEAR };
enum class MarginalizationType { MARGIN_OLD, MARGIN_SECOND_NEW };

using Timestamp = double;
using FeatureFrame =
    std::map<int, std::vector<std::pair<int, Eigen::Matrix<double, 7, 1>>>>;

// Per-frame visual health telemetry.  The bounded measurement weight is used
// only to reduce the influence of visibly degraded observations; calibrated
// intrinsics, extrinsics and time delay remain unchanged.
enum VisualQualityFlag : uint8_t {
  VISUAL_LEFT_BLUR = 1u << 0,
  VISUAL_RIGHT_BLUR = 1u << 1,
  VISUAL_FEATURES_WEAK = 1u << 2,
  VISUAL_STEREO_WEAK = 1u << 3,
  VISUAL_HIGH_MOTION = 1u << 4,
};

struct VisualQuality {
  double left_sharpness = 0.0;
  double right_sharpness = 0.0;
  double left_contrast = 0.0;
  double right_contrast = 0.0;
  double flow_p90_px_s = 0.0;
  double stereo_ratio = 0.0;
  uint32_t tracked_features = 0;
  uint32_t stereo_features = 0;
  uint8_t flags = 0;
  bool degraded = false;
  bool severe = false;
  double measurement_weight = 1.0;
};

inline double visualMeasurementWeight(const VisualQuality &quality) {
  double weight = 1.0;
  if (quality.flags & VISUAL_LEFT_BLUR) weight *= 0.60;
  if (quality.flags & VISUAL_RIGHT_BLUR) weight *= 0.60;
  if (quality.flags & VISUAL_FEATURES_WEAK) weight *= 0.55;
  if (quality.flags & VISUAL_STEREO_WEAK) weight *= 0.70;
  if (quality.flags & VISUAL_HIGH_MOTION) weight *= 0.75;
  if (quality.severe) weight = std::min(weight, 0.20);
  return std::max(0.35, std::min(weight, 1.0));
}

struct TimestampedFeatureFrame {
  Timestamp timestamp = 0.0;
  FeatureFrame features;
  VisualQuality quality;
};
using TimestampedVector3d = std::pair<Timestamp, Eigen::Vector3d>;

#endif  //
