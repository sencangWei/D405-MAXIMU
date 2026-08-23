#include <gtest/gtest.h>

#include <vins_fusion_ros2/odometry_spike_guard.h>

#include <limits>

namespace {
OdomData sample(double timestamp, double x, double y = 0.0) {
  OdomData result;
  result.timestamp = timestamp;
  result.position = Eigen::Vector3d(x, y, 0.0);
  return result;
}
}  // namespace

TEST(OdometrySpikeGuard, DisabledPreservesHistoricalOutput) {
  OdometrySpikeGuard guard;
  EXPECT_EQ(guard.update(sample(0.0, 0.0)).size(), 1U);
  EXPECT_EQ(guard.update(sample(0.03, 0.2)).size(), 1U);
}

TEST(OdometrySpikeGuard, RejectsSingleFrameOutAndBackSpike) {
  OdometrySpikeGuard guard(0.03);
  EXPECT_EQ(guard.update(sample(0.0, 0.0)).size(), 1U);
  EXPECT_TRUE(guard.update(sample(0.03, 0.08, 0.06)).empty());
  const auto output = guard.update(sample(0.06, 0.005, 0.002));
  ASSERT_EQ(output.size(), 1U);
  EXPECT_NEAR(output.front().position.x(), 0.005, 1e-12);
  EXPECT_FALSE(guard.hasPending());
}

TEST(OdometrySpikeGuard, ConfirmsSustainedFastMotionWithoutClipping) {
  OdometrySpikeGuard guard(0.03);
  guard.update(sample(0.0, 0.0));
  EXPECT_TRUE(guard.update(sample(0.03, 0.05)).empty());
  const auto output = guard.update(sample(0.06, 0.10));
  ASSERT_EQ(output.size(), 2U);
  EXPECT_NEAR(output[0].position.x(), 0.05, 1e-12);
  EXPECT_NEAR(output[1].position.x(), 0.10, 1e-12);
}

TEST(OdometrySpikeGuard, ConfirmsLargeStepFollowedByStop) {
  OdometrySpikeGuard guard(0.03);
  guard.update(sample(0.0, 0.0));
  EXPECT_TRUE(guard.update(sample(0.03, 0.06)).empty());
  const auto output = guard.update(sample(0.06, 0.061));
  ASSERT_EQ(output.size(), 2U);
  EXPECT_NEAR(output[0].position.x(), 0.06, 1e-12);
}

TEST(OdometrySpikeGuard, InvalidSampleIsNotPublished) {
  OdometrySpikeGuard guard(0.03);
  OdomData invalid = sample(0.0, 0.0);
  invalid.position.x() = std::numeric_limits<double>::quiet_NaN();
  EXPECT_TRUE(guard.update(invalid).empty());
}
