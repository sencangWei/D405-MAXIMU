#include <gtest/gtest.h>

#include "loop_edge_strength.h"

TEST(LoopEdgeStrength, PreservesHistoricalUnitDefault)
{
    EXPECT_DOUBLE_EQ(LoopEdgeStrength(1.0).weight(), 1.0);
}

TEST(LoopEdgeStrength, AcceptsStrongerVerifiedLoop)
{
    EXPECT_DOUBLE_EQ(LoopEdgeStrength(4.0).weight(), 4.0);
}

TEST(LoopEdgeStrength, RejectsUnsafeConfiguration)
{
    EXPECT_THROW(LoopEdgeStrength(0.9), std::invalid_argument);
    EXPECT_THROW(LoopEdgeStrength(21.0), std::invalid_argument);
}
