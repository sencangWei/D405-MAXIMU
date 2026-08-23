#pragma once

#include <algorithm>
#include <iterator>
#include <utility>
#include <vector>

// Keep DBoW freshness as the hard gate, but process an already-confirming
// candidate first when that exact historical keyframe is returned again in the
// current query. A pending candidate that disappeared is never reintroduced.
inline std::vector<std::pair<int, double>> prioritizeFreshPendingCandidate(
    const std::vector<std::pair<int, double>>& candidates,
    int pending_index) {
  std::vector<std::pair<int, double>> ordered = candidates;
  if (pending_index < 0) return ordered;
  const auto pending = std::find_if(
      ordered.begin(), ordered.end(), [pending_index](const auto& candidate) {
        return candidate.first == pending_index;
      });
  if (pending != ordered.end()) {
    std::rotate(ordered.begin(), pending, std::next(pending));
  }
  return ordered;
}
