#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>

namespace vins_fusion_ros2 {

struct FrameClockMappingColumns {
  std::string stream;
  std::size_t device_ms;
  std::size_t monotonic_s;
};

inline FrameClockMappingColumns findFrameClockMappingColumns(
    const std::vector<std::string> &header) {
  const std::array<const char *, 4> streams{{
      "color", "depth", "infrared_left", "infrared_right"}};
  for (const char *stream : streams) {
    const std::string device_name = std::string(stream) + "_device_ms";
    const std::string monotonic_name = std::string(stream) + "_mono";
    const auto device = std::find(header.begin(), header.end(), device_name);
    const auto monotonic =
        std::find(header.begin(), header.end(), monotonic_name);
    if (device != header.end() && monotonic != header.end()) {
      return {stream,
              static_cast<std::size_t>(std::distance(header.begin(), device)),
              static_cast<std::size_t>(
                  std::distance(header.begin(), monotonic))};
    }
  }
  throw std::runtime_error(
      "frames CSV lacks color/depth/infrared clock mapping columns");
}

}  // namespace vins_fusion_ros2
