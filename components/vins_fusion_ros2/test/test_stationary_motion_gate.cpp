#include <gtest/gtest.h>

#include <vins/estimator/stationary_motion_gate.h>

TEST(StationaryMotionGate, RejectsSparseTailNoiseWhileImuIsStill) {
  EXPECT_FALSE(vins::hasVisualTranslationEvidence(80, 0.00396, 0.1202, 0.20));
}

TEST(StationaryMotionGate, RejectsIncoherentMedianJitterWhileImuIsStill) {
  EXPECT_FALSE(vins::hasVisualTranslationEvidence(265, 0.0157, 0.0390, 0.49));
}

TEST(StationaryMotionGate, AcceptsSlowCoherentTranslation) {
  EXPECT_TRUE(vins::hasVisualTranslationEvidence(100, 0.011, 0.040, 0.80));
}

TEST(StationaryMotionGate, AcceptsCoherentTranslationVisibleInUpperTail) {
  EXPECT_TRUE(vins::hasVisualTranslationEvidence(100, 0.009, 0.120, 0.75));
}

TEST(StationaryMotionGate, RequiresEnoughTracks) {
  EXPECT_FALSE(vins::hasVisualTranslationEvidence(12, 0.20, 0.30, 1.0));
}
