#include <gtest/gtest.h>

#include <vins_fusion_ros2/pose_integrity_guard.h>

#include <limits>

namespace {
OdomData sample(double timestamp, double x, double y = 0.0,
                double z = 0.0) {
  OdomData result;
  result.timestamp = timestamp;
  result.position = Eigen::Vector3d(x, y, z);
  return result;
}
}  // namespace

TEST(PoseIntegrityGuard, DisabledPreservesHistoricalOutput) {
  PoseIntegrityGuard guard;

  EXPECT_EQ(guard.update(sample(0.0, 0.0)),
            PoseIntegrityDecision::kAccept);
  EXPECT_EQ(guard.update(sample(0.03, 0.5)),
            PoseIntegrityDecision::kAccept);
  EXPECT_FALSE(guard.failed());
}

TEST(PoseIntegrityGuard, AcceptsContinuousProductMotion) {
  PoseIntegrityGuard guard(0.10);

  EXPECT_EQ(guard.update(sample(0.00, 0.00)),
            PoseIntegrityDecision::kAccept);
  EXPECT_EQ(guard.update(sample(0.03, 0.04)),
            PoseIntegrityDecision::kAccept);
  EXPECT_EQ(guard.update(sample(0.06, 0.09)),
            PoseIntegrityDecision::kAccept);
  EXPECT_FALSE(guard.failed());
}

TEST(PoseIntegrityGuard, RejectsAndLatchesBeforePublishingLargeJump) {
  PoseIntegrityGuard guard(0.10);

  EXPECT_EQ(guard.update(sample(0.00, 0.00)),
            PoseIntegrityDecision::kAccept);
  EXPECT_EQ(guard.update(sample(0.03, 0.25, -0.15)),
            PoseIntegrityDecision::kLatchFailure);
  EXPECT_TRUE(guard.failed());
  EXPECT_EQ(guard.failureReason(), "position_step_exceeded");
  EXPECT_GT(guard.failureStepM(), 0.10);

  // Failure is deliberately irreversible inside one product session.  The
  // estimator state is already untrusted and cannot be declared recovered by
  // a later sample merely returning near the previous output.
  EXPECT_EQ(guard.update(sample(0.06, 0.01)),
            PoseIntegrityDecision::kRejectLatched);
}

TEST(PoseIntegrityGuard, RejectsNonFinitePoseAndNonMonotonicTime) {
  PoseIntegrityGuard non_finite_guard(0.10);
  OdomData invalid = sample(0.0, 0.0);
  invalid.position.x() = std::numeric_limits<double>::quiet_NaN();
  EXPECT_EQ(non_finite_guard.update(invalid),
            PoseIntegrityDecision::kLatchFailure);
  EXPECT_EQ(non_finite_guard.failureReason(), "non_finite_pose");

  PoseIntegrityGuard time_guard(0.10);
  EXPECT_EQ(time_guard.update(sample(1.0, 0.0)),
            PoseIntegrityDecision::kAccept);
  EXPECT_EQ(time_guard.update(sample(1.0, 0.001)),
            PoseIntegrityDecision::kLatchFailure);
  EXPECT_EQ(time_guard.failureReason(), "timestamp_not_increasing");
}

TEST(PoseIntegrityGuard, RejectsInvalidThreshold) {
  EXPECT_THROW(PoseIntegrityGuard(-0.1), std::invalid_argument);
  EXPECT_THROW(
      PoseIntegrityGuard(std::numeric_limits<double>::quiet_NaN()),
      std::invalid_argument);
}
