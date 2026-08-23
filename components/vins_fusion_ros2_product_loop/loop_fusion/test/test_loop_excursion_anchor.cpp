#include <gtest/gtest.h>

#include "loop_excursion_anchor.h"

TEST(LoopExcursionAnchor, SelectsFarthestPoseFromLoopOrigin)
{
    const std::vector<LoopAnchorPoint> points = {
        {10, 0.0, 0.0, 0.0},
        {11, 0.3, 0.0, 0.0},
        {12, 0.4, 0.5, 0.0},
        {13, 0.5, 0.0, 0.0},
    };
    EXPECT_EQ(selectLoopExcursionAnchor(points), 12);
}

TEST(LoopExcursionAnchor, KeepsFirstFarthestPoseOnTie)
{
    const std::vector<LoopAnchorPoint> points = {
        {4, 0.0, 0.0, 0.0},
        {5, 1.0, 0.0, 0.0},
        {6, -1.0, 0.0, 0.0},
    };
    EXPECT_EQ(selectLoopExcursionAnchor(points), 5);
}

TEST(LoopExcursionAnchor, RejectsInsufficientInput)
{
    EXPECT_THROW(
        selectLoopExcursionAnchor({{4, 0.0, 0.0, 0.0}}),
        std::invalid_argument);
}

TEST(LoopExcursionAnchor, FreezesOnlyEndpointsByDefault)
{
    EXPECT_TRUE(shouldFreezeLoopPose(4, 4, 12, false));
    EXPECT_FALSE(shouldFreezeLoopPose(8, 4, 12, false));
    EXPECT_TRUE(shouldFreezeLoopPose(12, 4, 12, false));
    EXPECT_FALSE(shouldFreezeLoopPose(13, 4, 12, false));
}

TEST(LoopExcursionAnchor, FreezesOutboundSegmentWhenEnabled)
{
    EXPECT_TRUE(shouldFreezeLoopPose(4, 4, 12, true));
    EXPECT_TRUE(shouldFreezeLoopPose(8, 4, 12, true));
    EXPECT_TRUE(shouldFreezeLoopPose(12, 4, 12, true));
    EXPECT_FALSE(shouldFreezeLoopPose(13, 4, 12, true));
}
