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

TEST(StationaryMotionGate, RejectsVerifiedStaticHandAndDeskVibration) {
  EXPECT_FALSE(vins::hasInertialTranslationEvidence(0.128684, 0.08));
}

TEST(StationaryMotionGate, AcceptsStrongImuOnlyTranslation) {
  EXPECT_TRUE(vins::hasInertialTranslationEvidence(0.20, 0.08));
  EXPECT_TRUE(vins::hasInertialTranslationEvidence(0.10, 0.30));
}

TEST(StationaryMotionGate, AccumulatesAcrossOneAmbiguousInterval) {
  int confidence = 13;
  confidence = vins::updateStationaryConfidence(confidence, false, false);
  EXPECT_EQ(confidence, 12);
  confidence = vins::updateStationaryConfidence(confidence, true, false);
  confidence = vins::updateStationaryConfidence(confidence, true, false);
  confidence = vins::updateStationaryConfidence(confidence, true, false);
  EXPECT_EQ(confidence, 15);
}

TEST(StationaryMotionGate, TranslationImmediatelyClearsConfidence) {
  EXPECT_EQ(vins::updateStationaryConfidence(14, false, true), 0);
}

TEST(StationaryMotionGate, ConfidenceIsBounded) {
  EXPECT_EQ(vins::updateStationaryConfidence(15, true, false), 15);
  EXPECT_EQ(vins::updateStationaryConfidence(0, false, false), 0);
}
