#pragma once

#include <algorithm>
#include <cmath>
#include <stdexcept>

// A confirmed loop may correct more than the frozen 30 mm budget only when
// explicitly enabled. The extra budget scales with the observed spatial
// extent, not accumulated path length, so high-rate pose jitter cannot inflate
// it. Stereo geometry and consecutive-frame agreement are enforced upstream.
class LoopCorrectionPolicy
{
public:
    LoopCorrectionPolicy(
        const double base_limit_m = 0.03,
        const double extent_ratio = 0.0,
        const double ceiling_m = 0.03)
        : base_limit_m_(base_limit_m),
          extent_ratio_(extent_ratio),
          ceiling_m_(ceiling_m)
    {
        if (!std::isfinite(base_limit_m_) || base_limit_m_ <= 0.0 ||
            !std::isfinite(extent_ratio_) || extent_ratio_ < 0.0 ||
            !std::isfinite(ceiling_m_) || ceiling_m_ < base_limit_m_)
            throw std::invalid_argument("invalid loop correction policy");
    }

    double allowedCorrectionM(const double trajectory_extent_m) const
    {
        const double safe_extent =
            std::isfinite(trajectory_extent_m)
                ? std::max(0.0, trajectory_extent_m)
                : 0.0;
        return std::min(
            ceiling_m_,
            std::max(base_limit_m_, extent_ratio_ * safe_extent));
    }

    bool accepts(
        const double correction_m,
        const double trajectory_extent_m) const
    {
        return std::isfinite(correction_m) && correction_m >= 0.0 &&
               correction_m <= allowedCorrectionM(trajectory_extent_m);
    }

    bool hasRequiredRetrievalEvidence(
        const double correction_m,
        const double retrieval_score,
        const double expanded_minimum_score) const
    {
        if (!std::isfinite(correction_m) || correction_m < 0.0 ||
            !std::isfinite(retrieval_score) || retrieval_score < 0.0 ||
            !std::isfinite(expanded_minimum_score) ||
            expanded_minimum_score < 0.0 || expanded_minimum_score > 1.0)
            return false;
        return correction_m <= base_limit_m_ ||
               retrieval_score >= expanded_minimum_score;
    }

private:
    double base_limit_m_;
    double extent_ratio_;
    double ceiling_m_;
};
