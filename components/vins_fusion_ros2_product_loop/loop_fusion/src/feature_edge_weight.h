#pragma once

#include <algorithm>
#include <stdexcept>
#include <vector>

// Converts tracked-point support into a relative odometry-edge weight. A zero
// full-support threshold disables the policy and preserves the historical unit
// weight. Multi-keyframe edges use the weakest frame they span so they cannot
// bridge over a feature-collapse interval at full confidence.
class FeatureEdgeWeight
{
public:
    FeatureEdgeWeight(
        const int full_support_points = 0,
        const double minimum_weight = 1.0)
        : full_support_points_(full_support_points),
          minimum_weight_(minimum_weight)
    {
        if (full_support_points_ < 0 || minimum_weight_ <= 0.0 ||
            minimum_weight_ > 1.0)
            throw std::invalid_argument("invalid feature edge weight policy");
    }

    double weightForSupport(const int tracked_points) const
    {
        if (full_support_points_ == 0)
            return 1.0;
        const double ratio = static_cast<double>(std::max(0, tracked_points)) /
                             static_cast<double>(full_support_points_);
        return std::min(1.0, std::max(minimum_weight_, ratio));
    }

    double weightForSpan(const std::vector<int> &tracked_points) const
    {
        if (tracked_points.empty())
            throw std::invalid_argument("feature support span is empty");
        return weightForSupport(
            *std::min_element(tracked_points.begin(), tracked_points.end()));
    }

private:
    int full_support_points_;
    double minimum_weight_;
};
