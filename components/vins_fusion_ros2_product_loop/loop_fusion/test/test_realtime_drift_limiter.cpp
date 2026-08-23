#include <gtest/gtest.h>

#include "realtime_drift_limiter.h"

TEST(RealtimeDriftLimiter, HistoricalDefaultAppliesTargetImmediately)
{
    RealtimeDriftLimiter limiter;
    LimitedDriftTransform target;
    target.translation.x() = 0.1;
    const auto result = limiter.update(1.0, target);
    EXPECT_NEAR(result.translation.x(), 0.1, 1e-12);
}

TEST(RealtimeDriftLimiter, LimitsTranslationPerElapsedTime)
{
    RealtimeDriftLimiter limiter(0.05, 10.0);
    LimitedDriftTransform identity;
    limiter.update(0.0, identity);
    LimitedDriftTransform target;
    target.translation.x() = 0.1;
    auto result = limiter.update(0.1, target);
    EXPECT_NEAR(result.translation.x(), 0.005, 1e-12);
    result = limiter.update(2.0, target);
    EXPECT_NEAR(result.translation.x(), 0.1, 1e-12);
}

TEST(RealtimeDriftLimiter, LimitsRotationPerElapsedTime)
{
    RealtimeDriftLimiter limiter(1.0, 10.0);
    LimitedDriftTransform identity;
    limiter.update(0.0, identity);
    LimitedDriftTransform target;
    target.rotation = Eigen::AngleAxisd(
        30.0 * M_PI / 180.0, Eigen::Vector3d::UnitZ()).toRotationMatrix();
    const auto result = limiter.update(0.5, target);
    const double angle_deg = Eigen::AngleAxisd(result.rotation).angle() * 180.0 / M_PI;
    EXPECT_NEAR(angle_deg, 5.0, 1e-9);
}

TEST(RealtimeDriftLimiter, IgnoresNonIncreasingTimestamp)
{
    RealtimeDriftLimiter limiter(0.05, 10.0);
    LimitedDriftTransform identity;
    limiter.update(1.0, identity);
    LimitedDriftTransform target;
    target.translation.x() = 0.1;
    const auto result = limiter.update(0.5, target);
    EXPECT_NEAR(result.translation.x(), 0.0, 1e-12);
}

TEST(RealtimeDriftLimiter, RejectsInvalidRates)
{
    EXPECT_THROW(RealtimeDriftLimiter(-0.1, 10.0), std::invalid_argument);
    EXPECT_THROW(RealtimeDriftLimiter(0.1, -10.0), std::invalid_argument);
}

TEST(RealtimeDriftLimiter, DerivesMatchingTransformForAlignedPointCloud)
{
    LimitedDriftTransform world_alignment;
    world_alignment.rotation = Eigen::AngleAxisd(
        12.0 * M_PI / 180.0, Eigen::Vector3d::UnitZ()).toRotationMatrix();
    world_alignment.translation = Eigen::Vector3d(0.2, -0.1, 0.03);
    LimitedDriftTransform applied;
    applied.rotation = Eigen::AngleAxisd(
        18.0 * M_PI / 180.0, Eigen::Vector3d::UnitZ()).toRotationMatrix();
    applied.translation = Eigen::Vector3d(0.4, 0.2, -0.02);

    const auto drift = driftTransformForAlignedFrame(applied, world_alignment);
    const Eigen::Vector3d point(0.3, -0.2, 0.1);
    const Eigen::Vector3d direct = applied.rotation * point + applied.translation;
    const Eigen::Vector3d aligned =
        world_alignment.rotation * point + world_alignment.translation;
    const Eigen::Vector3d through_drift =
        drift.rotation * aligned + drift.translation;

    EXPECT_NEAR((direct - through_drift).norm(), 0.0, 1e-12);
}
