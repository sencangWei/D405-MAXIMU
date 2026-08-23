#pragma once

#include <stdexcept>
#include <vector>

struct LoopAnchorPoint
{
    int index;
    double x;
    double y;
    double z;
};

inline int selectLoopExcursionAnchor(
    const std::vector<LoopAnchorPoint> &points)
{
    if (points.size() < 2)
        throw std::invalid_argument(
            "loop excursion anchor needs at least two poses");
    const LoopAnchorPoint &origin = points.front();
    int farthest_index = points[1].index;
    double farthest_distance_squared = -1.0;
    for (size_t i = 1; i < points.size(); ++i)
    {
        const double dx = points[i].x - origin.x;
        const double dy = points[i].y - origin.y;
        const double dz = points[i].z - origin.z;
        const double distance_squared = dx * dx + dy * dy + dz * dz;
        if (distance_squared > farthest_distance_squared)
        {
            farthest_distance_squared = distance_squared;
            farthest_index = points[i].index;
        }
    }
    return farthest_index;
}

inline bool shouldFreezeLoopPose(
    int pose_index, int first_looped_index, int excursion_anchor_index,
    bool freeze_outbound_to_anchor)
{
    if (pose_index == first_looped_index)
        return true;
    if (!freeze_outbound_to_anchor || excursion_anchor_index < 0)
        return pose_index == excursion_anchor_index;
    return pose_index <= excursion_anchor_index;
}
