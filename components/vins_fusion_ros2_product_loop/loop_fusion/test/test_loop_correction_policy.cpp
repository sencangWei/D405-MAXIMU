#include <gtest/gtest.h>

#include "loop_correction_policy.h"

TEST(LoopCorrectionPolicy, PreservesFrozenThirtyMillimeterDefault)
{
    const LoopCorrectionPolicy policy;
    EXPECT_DOUBLE_EQ(policy.allowedCorrectionM(10.0), 0.03);
    EXPECT_TRUE(policy.accepts(0.029, 10.0));
    EXPECT_FALSE(policy.accepts(0.031, 10.0));
}

TEST(LoopCorrectionPolicy, ScalesConfirmedBudgetWithTrajectoryExtent)
{
    const LoopCorrectionPolicy policy(0.03, 0.25, 0.50);
    EXPECT_DOUBLE_EQ(policy.allowedCorrectionM(0.08), 0.03);
    EXPECT_DOUBLE_EQ(policy.allowedCorrectionM(0.40), 0.10);
    EXPECT_DOUBLE_EQ(policy.allowedCorrectionM(4.00), 0.50);
}

TEST(LoopCorrectionPolicy, AcceptsObservedTenCentimeterDriftAtMeasuredExtent)
{
    const LoopCorrectionPolicy policy(0.03, 0.25, 0.50);
    EXPECT_TRUE(policy.accepts(0.108, 0.80));
    EXPECT_FALSE(policy.accepts(0.201, 0.80));
}

TEST(LoopCorrectionPolicy, RejectsInvalidConfiguration)
{
    EXPECT_THROW(LoopCorrectionPolicy(0.0, 0.25, 0.50), std::invalid_argument);
    EXPECT_THROW(LoopCorrectionPolicy(0.03, -0.1, 0.50), std::invalid_argument);
    EXPECT_THROW(LoopCorrectionPolicy(0.10, 0.25, 0.05), std::invalid_argument);
}

TEST(LoopCorrectionPolicy, ExpandedCorrectionRequiresStrongFreshRetrieval)
{
    const LoopCorrectionPolicy policy(0.03, 0.25, 0.50);
    EXPECT_TRUE(policy.hasRequiredRetrievalEvidence(0.029, 0.02, 0.10));
    EXPECT_TRUE(policy.hasRequiredRetrievalEvidence(0.108, 0.14, 0.10));
    EXPECT_FALSE(policy.hasRequiredRetrievalEvidence(0.041, 0.052, 0.10));
    EXPECT_FALSE(policy.hasRequiredRetrievalEvidence(0.041, NAN, 0.10));
}
