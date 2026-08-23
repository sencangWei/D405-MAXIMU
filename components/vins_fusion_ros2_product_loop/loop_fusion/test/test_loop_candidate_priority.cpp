#include <gtest/gtest.h>

#include "loop_candidate_priority.h"

TEST(LoopCandidatePriority, PrioritizesExactPendingWhenFresh) {
  const std::vector<std::pair<int, double>> input{
      {2, 0.16}, {1, 0.15}, {0, 0.13}};
  const auto output = prioritizeFreshPendingCandidate(input, 0);
  ASSERT_EQ(output.size(), 3U);
  EXPECT_EQ(output[0].first, 0);
  EXPECT_DOUBLE_EQ(output[0].second, 0.13);
  EXPECT_EQ(output[1].first, 2);
  EXPECT_EQ(output[2].first, 1);
}

TEST(LoopCandidatePriority, NeverReintroducesStalePendingCandidate) {
  const std::vector<std::pair<int, double>> input{{2, 0.16}, {0, 0.13}};
  const auto output = prioritizeFreshPendingCandidate(input, 1);
  EXPECT_EQ(output, input);
}

TEST(LoopCandidatePriority, PreservesOrderWithoutPendingCandidate) {
  const std::vector<std::pair<int, double>> input{{4, 0.2}, {3, 0.1}};
  EXPECT_EQ(prioritizeFreshPendingCandidate(input, -1), input);
}
