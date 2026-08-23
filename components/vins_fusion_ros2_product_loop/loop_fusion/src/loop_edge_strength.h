#pragma once

#include <cmath>
#include <stdexcept>

class LoopEdgeStrength
{
public:
    explicit LoopEdgeStrength(double weight) : weight_(weight)
    {
        if (!std::isfinite(weight_) || weight_ < 1.0 || weight_ > 20.0)
            throw std::invalid_argument(
                "loop_edge_weight must be finite and in [1, 20]");
    }

    double weight() const { return weight_; }

private:
    double weight_;
};
