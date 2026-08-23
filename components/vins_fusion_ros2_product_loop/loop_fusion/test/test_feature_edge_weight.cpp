#include <gtest/gtest.h>

#include "feature_edge_weight.h"

TEST(FeatureEdgeWeight, DisabledPolicyPreservesUnitWeight)
{
    const FeatureEdgeWeight policy;
    EXPECT_DOUBLE_EQ(policy.weightForSupport(1), 1.0);
}

TEST(FeatureEdgeWeight, ScalesWeakEdgesAndClampsFloor)
{
    const FeatureEdgeWeight policy(100, 0.05);
    EXPECT_DOUBLE_EQ(policy.weightForSupport(200), 1.0);
    EXPECT_DOUBLE_EQ(policy.weightForSupport(50), 0.5);
    EXPECT_DOUBLE_EQ(policy.weightForSupport(1), 0.05);
}

TEST(FeatureEdgeWeight, UsesWeakestSupportAcrossEdgeSpan)
{
    const FeatureEdgeWeight policy(100, 0.05);
    EXPECT_DOUBLE_EQ(policy.weightForSpan({180, 140, 20, 160}), 0.2);
    EXPECT_DOUBLE_EQ(policy.weightForSpan({180, 140, 120}), 1.0);
}

TEST(FeatureEdgeWeight, RejectsInvalidConfiguration)
{
    EXPECT_THROW(FeatureEdgeWeight(-1, 0.1), std::invalid_argument);
    EXPECT_THROW(FeatureEdgeWeight(100, 0.0), std::invalid_argument);
    EXPECT_THROW(FeatureEdgeWeight(100, 1.1), std::invalid_argument);
}
