#include <gtest/gtest.h>

#include "tracked_brief_matcher.h"

namespace
{
DVision::BRIEF::bitset descriptor(std::initializer_list<size_t> set_bits)
{
	DVision::BRIEF::bitset value(256);
	for (const size_t bit : set_bits)
		value.set(bit);
	return value;
}
} // namespace

TEST(TrackedBriefMatcher, AcceptsDistinctExactMatches)
{
	const std::vector<DVision::BRIEF::bitset> current = {
		descriptor({}), descriptor({0})};
	const std::vector<DVision::BRIEF::bitset> old = {
		descriptor({}), descriptor({0})};

	const std::vector<tracked_brief::Match> matches =
		tracked_brief::match(current, current.size(), old, old.size());

	ASSERT_EQ(matches.size(), 2U);
	EXPECT_EQ(matches[0].current_index, 0);
	EXPECT_EQ(matches[0].old_index, 0);
	EXPECT_EQ(matches[1].current_index, 1);
	EXPECT_EQ(matches[1].old_index, 1);
}

TEST(TrackedBriefMatcher, RejectsAmbiguousRatio)
{
	DVision::BRIEF::bitset ten_bits(256);
	DVision::BRIEF::bitset eleven_bits(256);
	for (size_t bit = 0; bit < 10; ++bit)
	{
		ten_bits.set(bit);
		eleven_bits.set(bit);
	}
	eleven_bits.set(10);
	const std::vector<DVision::BRIEF::bitset> current = {descriptor({})};
	const std::vector<DVision::BRIEF::bitset> old = {ten_bits, eleven_bits};

	EXPECT_TRUE(
		tracked_brief::match(current, current.size(), old, old.size()).empty());
}

TEST(TrackedBriefMatcher, EnforcesMutualUniqueness)
{
	DVision::BRIEF::bitset distant(256);
	for (size_t bit = 0; bit < 50; ++bit)
		distant.set(bit);
	const std::vector<DVision::BRIEF::bitset> current = {
		descriptor({}), descriptor({0})};
	const std::vector<DVision::BRIEF::bitset> old = {descriptor({}), distant};

	const std::vector<tracked_brief::Match> matches =
		tracked_brief::match(current, current.size(), old, old.size());

	ASSERT_EQ(matches.size(), 1U);
	EXPECT_EQ(matches[0].current_index, 0);
	EXPECT_EQ(matches[0].old_index, 0);
}

TEST(TrackedBriefMatcher, RejectsDistanceAtThreshold)
{
	DVision::BRIEF::bitset distance_80(256);
	DVision::BRIEF::bitset distance_100(256);
	for (size_t bit = 0; bit < 100; ++bit)
	{
		if (bit < 80)
			distance_80.set(bit);
		distance_100.set(bit);
	}
	const std::vector<DVision::BRIEF::bitset> current = {descriptor({})};
	const std::vector<DVision::BRIEF::bitset> old = {distance_80, distance_100};

	EXPECT_TRUE(
		tracked_brief::match(current, current.size(), old, old.size()).empty());
}

TEST(TrackedBriefMatcher, RejectsInvalidDescriptorCounts)
{
	const std::vector<DVision::BRIEF::bitset> current = {descriptor({})};
	const std::vector<DVision::BRIEF::bitset> old = {
		descriptor({}), descriptor({0})};

	EXPECT_TRUE(tracked_brief::match(current, 2, old, old.size()).empty());
	EXPECT_TRUE(tracked_brief::match(current, current.size(), old, 3).empty());
}

TEST(TrackedBriefMatcher, RejectsNon256BitDescriptors)
{
	DVision::BRIEF::bitset short_descriptor(128);
	const std::vector<DVision::BRIEF::bitset> current = {short_descriptor};
	const std::vector<DVision::BRIEF::bitset> old = {
		descriptor({}), descriptor({0})};

	EXPECT_TRUE(
		tracked_brief::match(current, current.size(), old, old.size()).empty());
}
