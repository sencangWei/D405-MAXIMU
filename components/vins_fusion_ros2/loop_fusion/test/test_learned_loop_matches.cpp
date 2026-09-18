#include <cstdio>
#include <fstream>

#include <gtest/gtest.h>

#include "learned_loop_matches.h"

namespace
{
std::string writeCsv(const std::string &body)
{
	const std::string path = "/tmp/vins_learned_loop_matches_test.csv";
	std::ofstream stream(path);
	stream << "current_timestamp_s,previous_timestamp_s,current_u,current_v,previous_u,previous_v,confidence\r\n";
	stream << body;
	return path;
}
} // namespace

TEST(LearnedLoopMatches, GroupsCorrespondencesByTimestampPair)
{
	const auto path = writeCsv(
		"12.0,2.0,10,20,30,40,2.5\n"
		"12.0,2.0,11,21,31,41,3.0\n"
		"13.0,3.0,12,22,32,42,4.0\n");
	learned_loop::Database database;
	std::string error;
	ASSERT_TRUE(database.loadCsv(path, &error)) << error;
	EXPECT_EQ(database.edgeCount(), 2U);
	EXPECT_EQ(database.matchCount(), 3U);
	const auto candidates = database.candidateTimestamps(12.005, 0.01);
	ASSERT_EQ(candidates.size(), 1U);
	EXPECT_DOUBLE_EQ(candidates[0], 2.0);
	const auto *edge = database.find(12.004, 1.996, 0.01);
	ASSERT_NE(edge, nullptr);
	EXPECT_EQ(edge->matches.size(), 2U);
	EXPECT_FLOAT_EQ(edge->matches[1].previous.x, 31.0F);
	std::remove(path.c_str());
}

TEST(LearnedLoopMatches, RejectsInvalidRows)
{
	const auto path = writeCsv("2.0,12.0,10,20,30,40,2.5\n");
	learned_loop::Database database;
	std::string error;
	EXPECT_FALSE(database.loadCsv(path, &error));
	EXPECT_FALSE(error.empty());
	std::remove(path.c_str());
}
