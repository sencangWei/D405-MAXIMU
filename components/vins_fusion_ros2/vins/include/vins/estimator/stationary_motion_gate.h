#pragma once

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

}  // namespace vins
