#pragma once

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/core/types.hpp>

namespace learned_loop
{
struct Match
{
	cv::Point2f current;
	cv::Point2f previous;
	float confidence = 0.0F;
};

struct Edge
{
	double current_timestamp_s = 0.0;
	double previous_timestamp_s = 0.0;
	std::vector<Match> matches;
};

class Database
{
public:
	bool loadCsv(const std::string &path, std::string *error = nullptr)
	{
		edges_.clear();
		if (path.empty())
			return true;

		std::ifstream stream(path);
		if (!stream.is_open())
			return fail(error, "cannot open learned-loop match CSV: " + path);

		std::string line;
		if (!std::getline(stream, line))
			return fail(error, "learned-loop match CSV is empty");
		if (!line.empty() && line.back() == '\r')
			line.pop_back();
		if (line != "current_timestamp_s,previous_timestamp_s,current_u,current_v,previous_u,previous_v,confidence")
			return fail(error, "learned-loop match CSV header is invalid");

		while (std::getline(stream, line))
		{
			if (line.empty())
				continue;
			std::vector<double> values;
			std::stringstream row(line);
			std::string cell;
			while (std::getline(row, cell, ','))
			{
				try
				{
					values.push_back(std::stod(cell));
				}
				catch (const std::exception &)
				{
					return fail(error, "learned-loop match CSV contains a non-numeric cell");
				}
			}
			if (values.size() != 7 ||
			    !std::all_of(values.begin(), values.end(), [](double value) {
				    return std::isfinite(value);
			    }))
				return fail(error, "learned-loop match CSV row must contain seven finite values");
			if (values[0] <= values[1] || values[2] < 0.0 || values[3] < 0.0 ||
			    values[4] < 0.0 || values[5] < 0.0 || values[6] < 0.0)
				return fail(error, "learned-loop match CSV row violates timestamp/coordinate constraints");

			Edge *edge = nullptr;
			for (Edge &candidate : edges_)
			{
				if (candidate.current_timestamp_s == values[0] &&
				    candidate.previous_timestamp_s == values[1])
				{
					edge = &candidate;
					break;
				}
			}
			if (edge == nullptr)
			{
				edges_.push_back({values[0], values[1], {}});
				edge = &edges_.back();
			}
			edge->matches.push_back(
				{{static_cast<float>(values[2]), static_cast<float>(values[3])},
				 {static_cast<float>(values[4]), static_cast<float>(values[5])},
				 static_cast<float>(values[6])});
		}
		if (edges_.empty())
			return fail(error, "learned-loop match CSV has no data rows");
		return true;
	}

	std::vector<double> candidateTimestamps(
		double current_timestamp_s, double tolerance_s) const
	{
		std::vector<double> candidates;
		for (const Edge &edge : edges_)
			if (std::abs(edge.current_timestamp_s - current_timestamp_s) <= tolerance_s)
				candidates.push_back(edge.previous_timestamp_s);
		return candidates;
	}

	const Edge *find(
		double current_timestamp_s,
		double previous_timestamp_s,
		double tolerance_s) const
	{
		const Edge *best = nullptr;
		double best_error = std::numeric_limits<double>::infinity();
		for (const Edge &edge : edges_)
		{
			const double current_error =
				std::abs(edge.current_timestamp_s - current_timestamp_s);
			const double previous_error =
				std::abs(edge.previous_timestamp_s - previous_timestamp_s);
			const double total_error = current_error + previous_error;
			if (current_error <= tolerance_s && previous_error <= tolerance_s &&
			    total_error < best_error)
			{
				best = &edge;
				best_error = total_error;
			}
		}
		return best;
	}

	bool empty() const { return edges_.empty(); }
	size_t edgeCount() const { return edges_.size(); }
	size_t matchCount() const
	{
		size_t count = 0;
		for (const Edge &edge : edges_)
			count += edge.matches.size();
		return count;
	}

private:
	static bool fail(std::string *error, const std::string &message)
	{
		if (error != nullptr)
			*error = message;
		return false;
	}

	std::vector<Edge> edges_;
};
} // namespace learned_loop
