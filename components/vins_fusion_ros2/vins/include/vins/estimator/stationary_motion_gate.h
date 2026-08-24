#pragma once

#include <algorithm>
#include <cstddef>

namespace vins {

inline bool hasVisualTranslationEvidence(std::size_t track_count,
                                         double flow_median,
                                         double flow_p90,
                                         double flow_coherence) {
  if (track_count < 20 || flow_coherence < 0.55) return false;
  return flow_median > 0.010 ||
         (flow_median > 0.008 && flow_p90 > 0.08);
}

inline bool hasInertialTranslationEvidence(double accel_std,
                                           double gravity_norm_error) {
  // Hand tremor, desk vibration and USB batching produced 0.08--0.13 m/s^2
  // short-window standard deviation in a verified static HIL run. Those
  // disturbances must not release a position hold without coherent visual
  // translation. Keep a stronger IMU-only escape for texture-poor motion.
  return accel_std > 0.18 || gravity_norm_error > 0.25;
}

inline int updateStationaryConfidence(int confidence,
                                      bool stationary_candidate,
                                      bool translation_evidence,
                                      int activation_frames = 15) {
  if (translation_evidence) return 0;
  if (stationary_candidate) {
    return std::min(confidence + 1, activation_frames);
  }
  // A single noisy IMU/image interval must not discard almost one second of
  // accumulated stationary evidence. Genuine translation resets above.
  return std::max(confidence - 1, 0);
}

}  // namespace vins
