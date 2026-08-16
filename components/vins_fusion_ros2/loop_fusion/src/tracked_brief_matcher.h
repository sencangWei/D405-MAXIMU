#pragma once

#include <array>
#include <limits>
#include <vector>

#include "ThirdParty/DVision/BRIEF.h"

namespace tracked_brief
{
constexpr int MAX_HAMMING_DISTANCE = 80;
constexpr int RATIO_NUMERATOR = 9;
constexpr int RATIO_DENOMINATOR = 10;
constexpr size_t DESCRIPTOR_BITS = 256;

using DescriptorBlock = DVision::BRIEF::bitset::block_type;
constexpr size_t BITS_PER_BLOCK = sizeof(DescriptorBlock) * 8;
constexpr size_t DESCRIPTOR_BLOCKS =
	(DESCRIPTOR_BITS + BITS_PER_BLOCK - 1) / BITS_PER_BLOCK;
using PackedDescriptor = std::array<DescriptorBlock, DESCRIPTOR_BLOCKS>;

struct Match
{
	int current_index;
	int old_index;
};

inline bool packDescriptor(
	const DVision::BRIEF::bitset &descriptor,
	PackedDescriptor &packed)
{
	if (descriptor.size() != DESCRIPTOR_BITS)
		return false;
	packed.fill(0);
	boost::to_block_range(descriptor, packed.begin());
	return true;
}

inline int hammingDistance(
	const PackedDescriptor &first,
	const PackedDescriptor &second)
{
	int distance = 0;
	for (size_t block = 0; block < DESCRIPTOR_BLOCKS; ++block)
		distance += __builtin_popcountll(
			static_cast<unsigned long long>(first[block] ^ second[block]));
	return distance;
}

inline std::vector<Match> match(
	const std::vector<DVision::BRIEF::bitset> &current_descriptors,
	size_t current_descriptor_count,
	const std::vector<DVision::BRIEF::bitset> &old_descriptors,
	size_t old_descriptor_count)
{
	if (current_descriptor_count == 0 || old_descriptor_count < 2 ||
	    current_descriptor_count > current_descriptors.size() ||
	    old_descriptor_count > old_descriptors.size())
		return {};

	std::vector<PackedDescriptor> packed_current(current_descriptor_count);
	std::vector<PackedDescriptor> packed_old(old_descriptor_count);
	for (size_t index = 0; index < current_descriptor_count; ++index)
	{
		if (!packDescriptor(current_descriptors[index], packed_current[index]))
			return {};
	}
	for (size_t index = 0; index < old_descriptor_count; ++index)
	{
		if (!packDescriptor(old_descriptors[index], packed_old[index]))
			return {};
	}

	const int no_match = -1;
	std::vector<int> forward_best_index(current_descriptor_count, no_match);
	std::vector<int> forward_best_distance(
		current_descriptor_count, std::numeric_limits<int>::max());
	std::vector<int> forward_second_distance(
		current_descriptor_count, std::numeric_limits<int>::max());
	std::vector<int> reverse_best_index(old_descriptor_count, no_match);
	std::vector<int> reverse_best_distance(
		old_descriptor_count, std::numeric_limits<int>::max());

	for (size_t current_index = 0; current_index < current_descriptor_count;
	     ++current_index)
	{
		for (size_t old_index = 0; old_index < old_descriptor_count; ++old_index)
		{
			const int distance = hammingDistance(
				packed_current[current_index], packed_old[old_index]);
			if (distance < forward_best_distance[current_index])
			{
				forward_second_distance[current_index] =
					forward_best_distance[current_index];
				forward_best_distance[current_index] = distance;
				forward_best_index[current_index] = static_cast<int>(old_index);
			}
			else if (distance < forward_second_distance[current_index])
			{
				forward_second_distance[current_index] = distance;
			}

			if (distance < reverse_best_distance[old_index])
			{
				reverse_best_distance[old_index] = distance;
				reverse_best_index[old_index] = static_cast<int>(current_index);
			}
		}
	}

	std::vector<Match> matches;
	matches.reserve(current_descriptor_count);
	for (size_t current_index = 0; current_index < current_descriptor_count;
	     ++current_index)
	{
		const int old_index = forward_best_index[current_index];
		const int best_distance = forward_best_distance[current_index];
		const int second_distance = forward_second_distance[current_index];
		if (old_index == no_match || best_distance >= MAX_HAMMING_DISTANCE)
			continue;
		if (best_distance * RATIO_DENOMINATOR >=
		    second_distance * RATIO_NUMERATOR)
			continue;
		if (reverse_best_index[old_index] != static_cast<int>(current_index))
			continue;
		matches.push_back(
			{static_cast<int>(current_index), static_cast<int>(old_index)});
	}
	return matches;
}
} // namespace tracked_brief
