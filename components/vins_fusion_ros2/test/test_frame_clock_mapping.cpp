#include <gtest/gtest.h>

#include "vins_fusion_ros2/frame_clock_mapping.h"

TEST(FrameClockMapping, PrefersColorWhenAvailable) {
  const std::vector<std::string> header{
      "depth_device_ms", "depth_mono", "color_device_ms", "color_mono"};
  const auto columns =
      vins_fusion_ros2::findFrameClockMappingColumns(header);
  EXPECT_EQ(columns.stream, "color");
  EXPECT_EQ(columns.device_ms, 2U);
  EXPECT_EQ(columns.monotonic_s, 3U);
}

TEST(FrameClockMapping, SupportsDepthOnlyCapture) {
  const std::vector<std::string> header{
      "set_index", "depth_frame_number", "depth_device_ms", "depth_mono"};
  const auto columns =
      vins_fusion_ros2::findFrameClockMappingColumns(header);
  EXPECT_EQ(columns.stream, "depth");
  EXPECT_EQ(columns.device_ms, 2U);
  EXPECT_EQ(columns.monotonic_s, 3U);
}

TEST(FrameClockMapping, FallsBackToLeftInfrared) {
  const std::vector<std::string> header{
      "infrared_left_device_ms", "infrared_left_mono"};
  const auto columns =
      vins_fusion_ros2::findFrameClockMappingColumns(header);
  EXPECT_EQ(columns.stream, "infrared_left");
}

TEST(FrameClockMapping, RejectsIncompletePair) {
  EXPECT_THROW(
      vins_fusion_ros2::findFrameClockMappingColumns({"depth_device_ms"}),
      std::runtime_error);
}
